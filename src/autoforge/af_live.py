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
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from .af_adapters import DEFAULT_HA_URL
from .af_bus import BusEvent
from .af_flock import FileLock, owner_id

__all__ = [
    "iter_sse_blocks",
    "parse_ha_event",
    "HAEventStream",
    "run_watch",
    "start_ticker",
    "WatchCoordinator",
]

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
def iter_sse_blocks(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """从 SSE 行迭代器产出 `(event_type, data_str)` 块。

    空行分隔事件；以 `:` 开头的注释行（HA 心跳 `: ping`）忽略；
    `event:` / `data:` 之外的字段（id/retry）忽略。`data` 可跨多行（以换行拼接）。
    """
    event_type = ""
    data_parts: list[str] = []
    for raw in lines:
        line = raw.rstrip("\n").rstrip("\r") if isinstance(raw, str) else raw
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        if line == "":
            if event_type or data_parts:
                yield (event_type, "\n".join(data_parts))
                event_type = ""
                data_parts = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[len("event:"):].lstrip()
        elif line.startswith("data:"):
            data_parts.append(line[len("data:"):].lstrip())
        # 其他字段（id:、retry:）忽略
    # 流在块中途结束（无尾随空行）时仍 flush 已累积内容
    if event_type or data_parts:
        yield (event_type, "\n".join(data_parts))


def parse_ha_event(event_type: str, data_str: str) -> BusEvent | None:
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
        source="ha",
        last_changed=new_state.get("last_changed"),
        attributes=new_state.get("attributes", {}) or {},
    )


# ── HA SSE 事件流（生产采集）──────────────────────────────────────────
class HAEventStream:
    """订阅 HA SSE `/api/stream`，产出 `BusEvent` 的迭代器。

    断流/异常后按 `backoff_s` 退避重连；`max_retries<0` 表示无限重连（默认）。
    `opener` 可注入（测试用），否则复用 `urllib.request.urlopen`。
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
        self._opener = opener or urllib.request.urlopen
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
        return self._opener(req, timeout=self.timeout)

    @staticmethod
    def _line_iter(resp: Any) -> Iterator[str]:
        for raw in resp:
            if isinstance(raw, bytes):
                yield raw.decode("utf-8", "replace")
            else:
                yield raw

    def events(self) -> Iterator[BusEvent]:
        """产出 `BusEvent` 的生成器；断流后自动重连。"""
        retries = 0
        while self.max_retries < 0 or retries <= self.max_retries:
            try:
                resp = self._open()
                self._log("EVENT_STREAM_CONNECTED")
                for block in iter_sse_blocks(self._line_iter(resp)):
                    ev = parse_ha_event(*block)
                    if ev is not None:
                        yield ev
                self._log("EVENT_STREAM_ENDED")
            except Exception as exc:  # 网络抖动/连接失败：退避后重连
                self._log(f"EVENT_STREAM_ERROR {exc}")
            retries += 1
            if self.max_retries >= 0 and retries > self.max_retries:
                self._log("EVENT_STREAM_GAVE_UP")
                return
            time.sleep(self.backoff_s)


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
    for i, ev in enumerate(events):
        if stop is not None and stop.is_set():
            break
        if ev is None:
            continue
        runtime.publish(ev)
        published += 1
        if on_event is not None:
            on_event(ev, runtime)
        if tick_each and (i + 1) % tick_each == 0:
            runtime.tick()
        if max_events is not None and published >= max_events:
            break
    return {
        "published": published,
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
                })
            out = {"asks": asks, "ts": time.time()}
            (sc_dir / "pending_asks.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _read_inbox() -> None:
        if not inbox:
            return
        try:
            for f in sorted(inbox.glob("*.json")):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    ask_id = data.get("ask_id", "")
                    text = data.get("text", "")
                    room = data.get("room")
                    runtime.executor.answer(ask_id, text=text, room=room)
                except Exception:
                    pass
                f.unlink(missing_ok=True)
        except Exception:
            pass

    def _loop() -> None:
        while not stop.is_set():
            if stop.wait(interval_s):
                break
            fn()
            _write_asks()
            _read_inbox()

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t
