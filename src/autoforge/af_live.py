"""真机常驻监听（Roadmap 收口「后续」项）。

把 `forge run --live` 从"回放式一次性执行"升级为"常驻运行"：
订阅 Home Assistant 的 SSE 事件流（`GET /api/stream`），把每个 `state_changed`
事件转换成 `BusEvent` 实时喂给 `Runtime.publish`，并周期性 `runtime.tick()`
驱动 `for` 持续条件与 `wait` 实例定时器的超时/到点。

设计要点（对齐 KICKOFF 红线）：
- **零新增依赖**：SSE 用标准库 `urllib` 流式读取，复用 `HATransport` 的 opener。
- **纯执行层不变**：本模块只做"事件采集 + 投递"，不下发动作；下发仍走既有的
  `do` → `HAAdapter(transport, dry_run=False)`，受 G2 静态闸 + G4 canary + 真机三重闸约束。
- **安全优先**：常驻监听会**持续**真实操作 HA，因此 `forge watch` 强制要求
  `--confirm` + HA 令牌 + 可写白名单（与 `run --live` 同口径的 `live_preflight`）。
- **韧性**：SSE 断流/异常后按退避重连（默认无限重连，可配置上限）。
- **可测**：解析与投递逻辑都是纯函数/可注入迭代器，单测零网络。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .af_config import Config  # BUG-07：前向引用需可被 get_type_hints() 解析

logger = logging.getLogger(__name__)

from .af_adapters import DEFAULT_HA_URL
from .af_adapters.http import guarded_open, host_of
from .af_atomic import atomic_write_text
from .af_bus import BusEvent
from .af_flock import FileLock, owner_id
from .af_tick_supervisor import TickSupervisor, SseReconnector, ExponentialBackoff, default_fault_policy

__all__ = [
    "iter_sse_blocks",
    "parse_ha_event",
    "HAEventStream",
    "run_watch",
    "start_ticker",
    "WatchCoordinator",
    "get_tick_supervisor",
    "get_ticker_thread",
    "get_tick_exit_reason",
    "tick_watchdog_pass",
]

# mimo TickSupervisor: current active tick supervisor (for /api/health)
_tick_supervisor = None

# D1（DCD 20261001《AF 三题》·H，方案 C）：tick 线程退出原因 + 守护判定。
#   退出仅剩三条语义明确路径：stop（人工停）/ SAFE HALT（安全闸，绝不自动重启）/
#   UNEXPECTED（try/except 未覆盖的意外终止——唯一可重启的情形）。
TICK_EXIT_RUNNING = "running"
TICK_EXIT_STOP = "stop"
TICK_EXIT_SAFE_HALT = "safe_halt"
TICK_EXIT_UNEXPECTED = "unexpected"
_ticker_thread = None
_tick_exit_reason = TICK_EXIT_RUNNING


def get_tick_supervisor():
    """Return current active TickSupervisor (None when no watch running)."""
    return _tick_supervisor


def get_ticker_thread():
    """当前 tick 守护线程（None 表示未启动）。"""
    return _ticker_thread


def get_tick_exit_reason() -> str:
    """tick 线程退出原因快照（running/stop/safe_halt/unexpected）。"""
    return _tick_exit_reason


def tick_watchdog_pass(*, restart=None) -> str:
    """主线程侧一次守护巡检（方案 C：区分退出原因，绝不让 SAFE HALT 被自愈抵消）。

    返回处置标签：
      alive         —— 线程仍存活，无需动作
      running       —— 尚未启动过 ticker
      held_safe_halt —— 因 SAFE HALT 停机：**不重启**（安全红线）
      stopped       —— 人工 stop：**不重启**
      restarted     —— 意外终止且提供 restart → 已重启
      unrecovered   —— 意外终止但未提供 restart 回调
    """
    global _tick_exit_reason
    th = _ticker_thread
    if th is None:
        return "running"
    if th.is_alive():
        return "alive"
    if _tick_exit_reason == TICK_EXIT_SAFE_HALT:
        return "held_safe_halt"
    if _tick_exit_reason == TICK_EXIT_STOP:
        return "stopped"
    # 唯一自愈分支：意外终止
    if restart is not None:
        _tick_exit_reason = TICK_EXIT_RUNNING
        restart()
        return "restarted"
    return "unrecovered"

SSE_STREAM_PATH = "/api/stream"


# ── v0.9.0 多实例 watch 协调 ───────────────────────────────────────────
class WatchCoordinator:
    """同一 store/persist 目录只允许一个活跃 watcher（跨进程互斥）。

    基于 `FileLock`（flock/msvcrt）：进程崩溃或退出时内核自动释放锁文件，
    **无陈旧锁问题**；锁文件内容记录持有者身份供拒绝方诊断。
    """

    def __init__(self, path: str | Path, holder_info: Mapping[str, Any] | None = None):
        self._info: dict[str, Any] = {
            "watcher": owner_id(),
            **(dict(holder_info) if holder_info else {}),
        }
        self._lock = FileLock(Path(path), timeout=0.0, info=self._info)

    def try_acquire(self) -> bool:
        """尝试成为唯一活跃 watcher；已有他者在位 → False。"""
        return self._lock.try_acquire()

    @property
    def holder(self) -> dict[str, Any]:
        """当前锁文件记录的持有者信息（诊断用）。"""
        return self._lock.holder()

    @property
    def info(self) -> dict[str, Any]:
        return dict(self._info)

    def release(self) -> None:
        self._lock.release()


# ── SSE 帧解析（纯函数，零网络）────────────────────────────────────────
def iter_sse_blocks(lines: Iterable[str]) -> Iterator[tuple[str, str, str]]:
    """从 SSE 行迭代器产出 `(event_type, data_str, sse_id)` 块。

    空行分隔事件；以 `:` 开头的注释行（HA 心跳 `: ping`）忽略；
    `data` 可跨多行（以换行拼接）。
    mimo 增量：捕获 SSE `id:` 行（HA event_id），用于跨重连去重。
    """
    event_type = ""
    data_parts: list[str] = []
    sse_id = ""
    for raw in lines:
        line = raw.rstrip("\n").rstrip("\r") if isinstance(raw, str) else raw
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        if line == "":
            if event_type or data_parts:
                yield (event_type, "\n".join(data_parts), sse_id)
                event_type = ""
                data_parts = []
                sse_id = ""
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[len("event:"):].lstrip()
        elif line.startswith("data:"):
            data_parts.append(line[len("data:"):].lstrip())
        elif line.startswith("id:"):
            sse_id = line[len("id:"):].lstrip()
        # retry: 忽略
    # 流在块中途结束（无尾随空行）时仍 flush 已累积内容
    if event_type or data_parts:
        yield (event_type, "\n".join(data_parts), sse_id)


def parse_ha_event(event_type: str, data_str: str, sse_id: str = "") -> BusEvent | None:
    """把一个 HA SSE 块转成 `BusEvent`；非 `state_changed` / 非法返回 `None`。

    时间戳取 HA 事件的 `new_state.last_changed`（与总线去重键一致），
    `attributes` 随包进 `payload` 供 `if` 条件等语义层按需使用。

    ⚠️ HA 的 `/api/stream` **不发送 SSE `event:` 行**，事件类型放在 `data`
    的 JSON `event_type` 字段里。因此这里同时接受 SSE `event:` 行（`event_type`
    参数）与 payload 内 `event_type`，两者其一为 `state_changed` 即可。
    """
    try:
        payload = json.loads(data_str)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    if (event_type or payload.get("event_type") or "") != "state_changed":
        return None
    data = payload.get("data") or {}
    new_state = data.get("new_state")
    entity_id = data.get("entity_id") or (new_state or {}).get("entity_id")
    if not entity_id or not isinstance(new_state, dict):
        return None
    state = new_state.get("state")
    if state is None:
        return None
    return BusEvent.of(
        entity_id,
        str(state),
        ha_event_id=sse_id,
        source="ha",
        last_changed=new_state.get("last_changed"),
        attributes=new_state.get("attributes", {}) or {},
    )


# ── HA SSE 事件流（生产采集）──────────────────────────────────────────
class HAEventStream:
    """订阅 HA SSE `/api/stream`，产出 `BusEvent` 的迭代器。

    断流/异常后按 `backoff_s` 退避重连；`max_retries<0` 表示无限重连（默认）。
    `opener` 可注入（测试用）；缺省走 `guarded_open`——SSE 是长连接，但首跳与它跟随的
    任何 3xx 都仍然只允许落在 `base_url` 那一台主机上（F15）。
    """

    def __init__(
        self,
        base_url: str = DEFAULT_HA_URL,
        token: str = "",
        opener: Callable[..., Any] | None = None,
        timeout: float = 30.0,
        max_retries: int = -1,
        backoff_s: float = 2.0,
        logger: Callable[[str], None] | None = None,
        cfg: "Config | None" = None,
    ):
        self.base_url = (base_url or DEFAULT_HA_URL).rstrip("/")
        self._cfg = cfg
        self.token = token or (cfg.get_ha_token() if cfg is not None else "")
        self._allowed_hosts = (host_of(self.base_url),)
        self._opener = opener
        self.timeout = float(timeout)
        self.max_retries = max_retries
        self.backoff_s = float(backoff_s)
        self._log = logger or (lambda _m: None)

    def _open(self) -> Any:
        if self._cfg is not None:
            self.token = self._cfg.get_ha_token()
        url = f"{self.base_url}{SSE_STREAM_PATH}"
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        # 长连接：不设短超时（读取逐行阻塞），仅在连接阶段用 timeout
        if self._opener is not None:
            return self._opener(req, timeout=self.timeout)
        return guarded_open(req, allowed_hosts=self._allowed_hosts, timeout=self.timeout)

    @staticmethod
    def _line_iter(resp: Any) -> Iterator[str]:
        for raw in resp:
            if isinstance(raw, bytes):
                yield raw.decode("utf-8", "replace")
            else:
                yield raw

    def events(self) -> Iterator[BusEvent]:
        """产出 `BusEvent` 的生成器；断流后自动重连（mimo SseReconnector：指数退避+稳定窗口清零）。"""
        from .af_time import SystemTimeSource
        _clock = SystemTimeSource()
        # max_retries 语义: <0 永不放弃, >=0 为重试次数(总尝试=max_retries+1)
        # SseReconnector max_attempts 语义: 0=永不放弃, >0=总尝试次数上限
        _max_attempts = 0 if self.max_retries < 0 else self.max_retries + 1
        reconnector = SseReconnector(
            _clock,
            backoff=ExponentialBackoff(base=self.backoff_s, cap=60.0),
            max_attempts=_max_attempts,
            escalate_after=10,
        )
        epoch = 0
        _seen_ids: dict[str, bool] = {}
        while True:
            drop_exc: BaseException | None = None
            try:
                resp = self._open()
                reconnector.on_open()
                self._log("EVENT_STREAM_CONNECTED")
                for block in iter_sse_blocks(self._line_iter(resp)):
                    ev = parse_ha_event(*block)
                    if ev is not None:
                        # mimo 增量：epoch:ha_event_id 去重（防跨 SSE 重连 event_id 回绕）
                        dedup_key = f"{epoch}:{ev.payload.get('ha_event_id', '')}" if ev.payload.get('ha_event_id') else None
                        if dedup_key and dedup_key in _seen_ids:
                            continue
                        if dedup_key:
                            _seen_ids[dedup_key] = True
                            if len(_seen_ids) > 4096:
                                # LRU: 清掉最早的一半
                                for k in list(_seen_ids.keys())[:2048]:
                                    del _seen_ids[k]
                        yield ev
                self._log("EVENT_STREAM_ENDED")
                drop_exc = RuntimeError("stream ended normally")
            except Exception as exc:
                self._log(f"EVENT_STREAM_ERROR {exc}")
                drop_exc = exc
            plan = reconnector.on_drop(drop_exc)
            epoch += 1  # mimo: 每次重连 epoch+1，防 HA event_id 跨连接回绕
            if plan.give_up:
                self._log("EVENT_STREAM_GAVE_UP")
                return
            if plan.escalate:
                self._log(f"EVENT_STREAM_ESCALATE attempt={plan.attempt}")
            time.sleep(plan.delay)


# ── 常驻监听主循环 ────────────────────────────────────────────────────
def run_watch(
    runtime: Any,
    events: Iterable[BusEvent],
    *,
    tick_each: int = 0,
    on_event: Callable[[BusEvent, Any], None] | None = None,
    stop: "threading.Event | None" = None,
    max_events: int | None = None,
) -> dict[str, Any]:
    """把事件流驱动进 Runtime（实时评估自动化）。

    - 每个事件：`runtime.publish(ev)`（经过总线去重/节流/熔断后进入调度器）。
    - `tick_each>0`：每处理 N 个事件调一次 `runtime.tick()`（时间/超时巡检）。
      真实常驻场景一般交由 `start_ticker` 的独立线程按墙钟周期 tick，此时传 0。
    - `stop` 被置位即退出；`max_events` 用于测试截断。
    返回累计统计。
    """
    published = 0
    total_fired = 0
    for i, ev in enumerate(events):
        if stop is not None and stop.is_set():
            break
        if ev is None:
            continue
        fired = runtime.publish(ev)
        published += 1
        total_fired += len(fired)
        if fired:
            for inst in fired:
                logger.debug("[FIRE] %s=%s → 实例 %s 节点 %s", ev.entity_id, ev.state, inst.instance_id, inst.current_node_id)
        if on_event is not None:
            on_event(ev, runtime)
        if tick_each and (i + 1) % tick_each == 0:
            runtime.tick()
        if max_events is not None and published >= max_events:
            break
    return {
        "published": published,
        "fired": total_fired,
        "stopped": bool(stop and stop.is_set()),
    }


def start_ticker(
    runtime: Any,
    interval_s: float,
    stop: "threading.Event",
    tick_fn: Callable[[], Any] | None = None,
    sidecar_dir: str | None = None,
) -> threading.Thread:
    """后台守护线程：每 `interval_s` 调一次 `runtime.tick()`，驱动 `for`/`wait` 计时。

    sidecar_dir 存在时：写 pending_asks.json（供 DB 轮询）+ 读 answer_inbox/（注回答案）。
    """
    fn = tick_fn or runtime.tick
    sc_dir = Path(sidecar_dir) if sidecar_dir else None
    inbox = sc_dir / "answer_inbox" if sc_dir else None
    if inbox:
        inbox.mkdir(parents=True, exist_ok=True)

    def _write_asks() -> None:
        if not sc_dir:
            return
        try:
            asks = []
            for aid, sess in runtime.executor.pending_asks.items():
                asks.append({
                    "ask_id": aid,
                    "instance_id": sess.instance_id,
                    "node_id": sess.node_id,
                    "room": sess.room,
                    "prompt": sess.prompt,
                    "automation_id": getattr(sess, "automation_id", ""),
                    # 与 /api/asks 同构：spec 为 IR 形态，control 为前端控件元数据
                    "spec": sess.ask_spec.to_dict() if sess.ask_spec else None,
                    "control": (
                        sess.ask_spec.control()
                        if sess.ask_spec
                        else {"widget": "input", "kind": "text", "prompt": sess.prompt}
                    ),
                })
            out = {"asks": asks, "ts": time.time()}
            # 走原子助手：这份文件是 butler 轮询读的待答清单，崩在半截会被读侧
            # 当成「没有待答」而静默丢弃这一轮提问（判据 E，审计 BUG-05）
            atomic_write_text(
                sc_dir / "pending_asks.json",
                json.dumps(out, ensure_ascii=False, indent=2),
            )
        except Exception as exc:
            # R14-01/R14-03 修复：写失败不得静默吞掉，否则 butler 永远读不到待答而静默丢失提问
            logger.warning("pending_asks 原子写入失败（已落原子临时文件，下次 tick 会重试）: %s", exc)

    # mimo TickSupervisor: fault classification + backoff + health + SAFE HALT
    global _tick_supervisor
    global _ticker_thread, _tick_exit_reason
    from .af_time import SystemTimeSource
    _clock = getattr(runtime, "clock", None) or SystemTimeSource()
    supervisor = TickSupervisor(
        clock=_clock,
        policy=default_fault_policy(),
        tick_interval_s=interval_s,
    )
    _tick_supervisor = supervisor

    def _loop() -> None:
        global _tick_exit_reason
        _tick_exit_reason = TICK_EXIT_RUNNING
        try:
            while not stop.is_set():
                if stop.wait(interval_s):
                    _tick_exit_reason = TICK_EXIT_STOP
                    break
                # B3-AF-01 + mimo: TickSupervisor 包装，异常分类/退避/SAFE HALT
                # 韧性（审计 AF-第二轮 缺陷2）：整轮包 try/except，任一未捕获异常只记日志并
                # 下一轮重试，不再静默杀死 daemon 线程（否则时间驱动自动化悄然停摆且无解信号）。
                try:
                    outcome = supervisor.run_once(fn)
                    if outcome.status.value == "halted":
                        # D1·C：SAFE HALT 是有意安全闸——标记退出原因，watchdog 绝不自动重启
                        _tick_exit_reason = TICK_EXIT_SAFE_HALT
                        logger.error("tick SAFE HALTED: %s", supervisor.health().halted_reason)
                        break
                    _write_asks()
                    read_answer_inbox(runtime, inbox_dir=inbox)
                except Exception:
                    logger.exception("ticker 循环异常，下一轮重试（线程保活）")
            else:
                # while 条件变 False（stop 被 set）自然结束
                _tick_exit_reason = TICK_EXIT_STOP
        except BaseException:
            # 逃逸出内层 catch 的终止：标记意外，交由 watchdog 自愈（区别于 SAFE HALT）
            _tick_exit_reason = TICK_EXIT_UNEXPECTED
            logger.exception("ticker 线程意外终止")
            raise

    t = threading.Thread(target=_loop, daemon=True)
    _ticker_thread = t
    t.start()
    return t


def read_answer_inbox(runtime: Any, inbox_dir: Optional[str] = None) -> None:
    """R-20：读 answer_inbox 并把应答注入 runtime。密钥校验（与 af_api 写侧共享 AUTOFORGE_INBOX_KEY）。

    v2 M3：结构化 answer（AskAnswer dict）走 executor.answer_structured（缺省即拒）；否则走自由文本 answer。
    校验失败/解析失败均不删除文件，保留审批证据链。
    """
    import hashlib
    import hmac
    import logging
    import os
    logger = logging.getLogger("autoforge.live")
    store = getattr(runtime, "store", None)
    if inbox_dir:
        inbox = Path(inbox_dir)
    else:
        root = store.root if store else None
        if not root:
            return
        inbox = Path(root) / "answer_inbox"
    if not inbox.exists():
        return
    key = (os.environ.get("AUTOFORGE_INBOX_KEY") or "").strip()

    def _expect_sig(ask_id: Any, text: str, room: Any, answer_json: str) -> str:
        msg = f"{ask_id}|{text}|{room}|{answer_json}".encode("utf-8")
        return hmac.new(key.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    try:
        for f in sorted(inbox.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    logger.warning("inbox reject: 非对象 JSON，未动作（保留证据文件）: %s", f.name)
                    continue
                ask_id = data.get("ask_id") or None
                text = data.get("text", "")
                room = data.get("room")
                answer = data.get("answer")  # v2 M3 结构化应答（AskAnswer dict）；缺省走自由文本
                answer_json = json.dumps(answer or {}, sort_keys=True, ensure_ascii=False)
                sig = str(data.get("sig", "") or "")
                if not key or not hmac.compare_digest(sig, _expect_sig(ask_id, text, room, answer_json)):
                    logger.warning(
                        "inbox reject: 签名缺失或不符，未动作（保留证据文件）: %s ask_id=%r",
                        f.name, ask_id,
                    )
                    continue
                if answer:
                    # 结构化应答：校验失败按缺省即拒走 no 边（executor 内处理）
                    result = runtime.executor.answer_structured(room=room, payload=answer, ask_id=ask_id)
                else:
                    result = runtime.executor.answer(room=room, text=text, ask_id=ask_id)
                if result is not None:
                    f.unlink(missing_ok=True)  # 成功消费后才清理
                    logger.warning("answer consumed: ask_id=%s room=%s → inst=%s", ask_id, room, result.instance_id)
                else:
                    logger.warning("answer not consumed (保留证据文件): ask_id=%s room=%s", ask_id, room)
            except Exception:
                logger.exception("answer processing failed (保留证据文件): %s", f.name)
                # 不删除文件，保留审批证据链
    except Exception:
        logger.exception("inbox scan failed")
