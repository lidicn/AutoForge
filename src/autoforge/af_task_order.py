# src/autoforge/af_task_order.py · 【任务】TaskOrder——v1.29.0 第二批
"""一次性定时任务单（TaskOrder）：到点按序执行、完成即销、留痕不灭。

依据（⛔ 本模块不发明产品语义）：
- DCD `decisions/20261111-AF-对话操控执行授权模型-裁定.md` Q5 甲（**TaskOrder 落 AF**，
  TVPilot `PlanStep` 退役/降级由 TVPilot 席自评估——本仓只落 AF 侧＋契约文档）、
  Q6 判例（**一次性 vs 重复**＝任务/自动化的分界：任务执行完即销，⛔ 不提供循环字段）、
  §2.3 甲（撤销＝完成当前步、取消未执行步）。
- 设计稿 v5 §二.场景2（字段/生命周期）＋§四.2-6b（`output` 输出通道）。

四条新红线（第二批卡 §三，逐条有判据）：
1. **⛔ 任务变相常驻**：本模块**没有** `every`／`repeat`／`cron` 字段，也没有"执行完再挂一单"
   的自续路径（判据：`create()` 的字段集断言 + 契约文档）。
2. **⛔ 整单放行**：执行面**每步**都过 grant（`af_grant.GrantStore.check` 在 `live_run` 里跑，
   每步独立调用），⛔ 没有"入册时校验一次、执行时整单放行"的旁路。
3. **⛔ 哑任务**：非即时任务创建时 `output` 必须可用；**所有请求通道都未接线 + 查询型** ⇒ 拒绝创建。
4. **⛔ 触发面混淆**：任务只**到点**执行＋**人工取消**，⛔ 不接定时/事件自动激活（那是自动化领地）。
   所以本模块没有 trigger 字段，也没有 watch 装载池入口——挂单面与 `GraphStore` 的自动化**物理隔离**。

状态机（⛔ 无第五态新增）：
    pending ──claim──▶ running ──全部步成功──▶ done      （移出活动池，留痕进 deploy_audit）
       │                  │  └──任一步失败──▶ failed    （同上）
       │                  └──撤销/取消──▶ cancelled      （其后未执行步不发起＝Q3 下半行）
       └──人工取消──▶ cancelled
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .af_atomic import atomic_write_text

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIVE_STATUSES",
    "DUE_GRACE_S",
    "MAX_ORDERS_PER_AGENT",
    "MAX_STEPS",
    "ORDER_FILE_REL",
    "OUTPUT_CHANNELS",
    "OUTPUT_DEFAULT",
    "OUTPUT_WIRED",
    "RUN_STALE_S",
    "STATUS_CANCELLED",
    "STATUS_DONE",
    "STATUS_EXPIRED",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "TaskOrderError",
    "TaskOrderStore",
    "build_step_ir",
    "output_block",
    "step_entity",
    "step_summary",
]

ORDER_FILE_REL = (".orders", "active.json")
ORDER_VERSION = 1

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"
#: 活动池＝还欠用户一个终态的两态；其余四态终态即销（留痕在 `deploy_audit.jsonl`）。
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_RUNNING)

#: 到点后允许迟到的宽限：引擎没跑（serve 重启/未起）导致错过，1 小时内仍补执行；
#: 超过就记 `expired`（⛔ 假装执行、也⛔ 无限等——一次性任务的语义是"那一次"）。
DUE_GRACE_S = 3600.0
#: `running` 单子超过这个时长没动 ⇒ 视为上次进程崩在中途，允许引擎**续跑**（跳过已有回执的步）。
RUN_STALE_S = 300.0

MAX_STEPS = 20
MAX_ORDERS_PER_AGENT = 20

#: output 三通道（设计稿 §四.2-6b）。`bark` 是**兜底**语义（TTS 受勿扰/播放吞话影响）。
OUTPUT_CHANNELS = ("tts", "bark", "mimo")
OUTPUT_DEFAULT = ("tts", "bark")
#: 每通道的现读接线状态（⛔ 无设施就写 unwired，别把"没发"记成"发了"）：
#: - `tts`  → **wired**：走既有 MQTT 桥 `inbox_publish("speak")`（与自动化播报同一条桥函数）
#: - `bark` → **unwired**：仓内无 bark 设施（全仓 grep 0 命中），落接口占位＋显式态
#: - `mimo` → **read**：用户端可查（`/api/orders`）即为达成，⛔ 不假装是推送
OUTPUT_WIRED = {"tts": "wired", "bark": "unwired", "mimo": "read"}

# ── 拒绝码（判据与被拒文案共用一份）───────────────────────────────────
ORDER_NOT_FOUND = "TASK_ORDER_NOT_FOUND"
ORDER_BAD_SHAPE = "TASK_ORDER_BAD_SHAPE"
ORDER_NO_STEPS = "TASK_ORDER_NO_STEPS"
ORDER_TOO_MANY_STEPS = "TASK_ORDER_TOO_MANY_STEPS"
ORDER_BAD_STEP = "TASK_ORDER_BAD_STEP"
ORDER_BAD_DUE = "TASK_ORDER_BAD_DUE"
ORDER_NEEDS_OUTPUT = "TASK_ORDER_NEEDS_OUTPUT"
ORDER_OUTPUT_UNWIRED = "TASK_ORDER_OUTPUT_UNWIRED"
ORDER_LIMIT = "TASK_ORDER_LIMIT"
ORDER_STORE_CORRUPT = "TASK_ORDER_STORE_CORRUPT"
ORDER_STORE_VERSION_MISMATCH = "TASK_ORDER_STORE_VERSION_MISMATCH"
ORDER_STATE = "TASK_ORDER_STATE"
ORDER_NO_TRIGGER_FIELD = "TASK_ORDER_NO_TRIGGER_FIELD"

#: 单子/单步禁用的字段：出现即拒（红线的**代码级**护栏，⛔ 靠文档自律）。
#: 前六个＝"变相常驻"；后三个＝"触发面混淆"（任务不是自动化）。
FORBIDDEN_KEYS = frozenset(
    {
        "every", "repeat", "interval", "cron", "schedule", "recurring",
        "trigger", "on", "events",
    }
)

#: 订单账本是**整份覆盖写**，serve 的线程池 + 引擎线程会并发 ⇒ 模块级锁串行化读-改-写
#: （与 `af_grant._GRANT_LOCK` 同一条理由：丢一笔"取消"就是取消没生效）。
_ORDER_LOCK = threading.Lock()


class TaskOrderError(Exception):
    """任务单面拒绝。`status` 供 HTTP/MCP 映射（400 形状／403 授权／404 不存在／500 盘坏）。"""

    def __init__(self, message: str, code: str = ORDER_BAD_SHAPE, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _now_iso(now: float | None = None) -> str:
    stamp = float(time.time() if now is None else now)
    return datetime.fromtimestamp(stamp, timezone.utc).replace(microsecond=0) \
        .isoformat().replace("+00:00", "Z")


def _iso_of(stamp: Any) -> str:
    try:
        return _now_iso(float(stamp))
    except (TypeError, ValueError):
        return ""


def step_entity(step: Mapping[str, Any]) -> str:
    """一步写到的实体（`params.entity_id`，缺省空串）。

    TaskOrder 的步是**结构化动作**，不是 IR 子图：`entity_id` 既是 scope 校验的输入，
    也是合成事件时的触发源（见 `build_step_ir`）。
    """
    params = step.get("params") if isinstance(step.get("params"), Mapping) else {}
    return str(params.get("entity_id") or "")


def step_summary(step: Mapping[str, Any]) -> str:
    """人话摘要（回执与播报用，⛔ 术语堆）。"""
    entity = step_entity(step)
    action = str(step.get("action") or "")
    return f"{action} → {entity}" if entity else action


def output_block(output: Mapping[str, Any] | None, *, kind: str = "control") -> dict[str, Any]:
    """`output` 归一：`{channels:[…], room, text, kind}`。

    - `channels` 缺省＝`OUTPUT_DEFAULT`（tts＋bark：TTS 主、bark 兜底——设计稿 §四.2-6b）；
    - ⛔ 不认识的通道拒收（静默忽略＝用户以为会收到推送）；
    - `kind="query"`（查询型）**必须**至少一个**已接线**通道，否则创建被拒（红线③哑任务）。
    """
    raw = dict(output or {})
    unknown = set(raw) - {"channels", "room", "text", "kind"}
    if unknown:
        raise TaskOrderError(f"output 有未声明的键：{sorted(unknown)}（只认 channels/room/text/kind）",
                             ORDER_BAD_SHAPE, 400)
    channels_raw = raw.get("channels")
    channels = [str(c) for c in (channels_raw if isinstance(channels_raw, (list, tuple)) else OUTPUT_DEFAULT)] \
        if channels_raw is not None else list(OUTPUT_DEFAULT)
    bad = [c for c in channels if c not in OUTPUT_CHANNELS]
    if bad:
        raise TaskOrderError(f"output.channels 有未知通道：{bad}（只认 {'/'.join(OUTPUT_CHANNELS)}）",
                             ORDER_BAD_SHAPE, 400)
    deduped: list[str] = []
    for channel in channels:
        if channel not in deduped:
            deduped.append(channel)
    kind_text = str(raw.get("kind") or kind or "control").strip().lower()
    if kind_text not in ("query", "control"):
        raise TaskOrderError(f"output.kind 只能是 query/control，收到 {kind_text!r}", ORDER_BAD_SHAPE, 400)
    block = {
        "channels": deduped,
        "room": str(raw.get("room") or "").strip(),
        "text": str(raw.get("text") or "").strip(),
        "kind": kind_text,
        #: 每通道的现读接线状态：`wired`／`unwired`／`read`（⛔ 无设施不许自称已发）
        "wired": {c: OUTPUT_WIRED.get(c, "unwired") for c in deduped},
    }
    if kind_text == "query" and not any(block["wired"][c] != "unwired" for c in deduped):
        raise TaskOrderError(
            "查询型任务必须有一条**已接线**的输出通道（当前 tts/read 可用、bark 未接线）——"
            "否则任务跑完结果没人看得见（哑任务，红线③）",
            ORDER_NEEDS_OUTPUT, 400,
        )
    return block


def _norm_step(step: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    if not isinstance(step, Mapping):
        raise TaskOrderError(f"第 {index + 1} 步不是对象", ORDER_BAD_STEP, 400)
    forbidden = set(step) & FORBIDDEN_KEYS
    if forbidden:
        raise TaskOrderError(
            f"第 {index + 1} 步出现禁用字段 {sorted(forbidden)}：任务⛔ 不提供循环/触发字段——"
            "要重复＝写成自动化（Q6 判例：一次性 vs 重复）",
            ORDER_NO_TRIGGER_FIELD, 400,
        )
    adapter = str(step.get("adapter") or "ha").strip()
    action = str(step.get("action") or "").strip()
    if not action:
        raise TaskOrderError(f"第 {index + 1} 步缺 action", ORDER_BAD_STEP, 400)
    params = step.get("params")
    if params is not None and not isinstance(params, Mapping):
        raise TaskOrderError(f"第 {index + 1} 步的 params 必须是对象", ORDER_BAD_STEP, 400)
    out = {
        "adapter": adapter,
        "action": action,
        "params": dict(params or {}),
    }
    for extra in ("requires_confirm", "canary"):
        if extra in step:
            out[extra] = step[extra]
    return out


def build_step_ir(step: Mapping[str, Any], *, order_id: str = "", index: int = 0) -> dict[str, Any]:
    """把一步摊成**最小 IR**——执行面复用 `live_run`（四重闸＋grant 逐条过＋回执＋readback）。

    ⚠️ 这里没有"绕过执行面"的旁路：本函数只造形状，闸门全在 `svc.live_run` 里。
    触发源用**合成事件**（`state` 触发 + 同实体 `on`），因为单发面本来就要一个可复现的
    触发输入；它不含任何时间/事件自动化语义（红线④：任务不是自动化）。
    """
    entity = step_entity(step)
    title = f"order:{order_id}:{index + 1}"
    nodes: list[dict[str, Any]] = [
        {"id": "t1", "kind": "on", "name": "task due",
         "trigger": {"type": "state", "entity_id": entity or f"task_order.{order_id}", "to": "on"}},
        {"id": "d1", "kind": "do", "name": title, "adapter": step.get("adapter") or "ha",
         "action": step.get("action"), "params": dict(step.get("params") or {})},
        {"id": "p1", "kind": "pass", "name": "done"},
    ]
    if step.get("requires_confirm"):
        nodes[1]["requires_confirm"] = True
    if step.get("canary"):
        nodes[1]["canary"] = dict(step["canary"])  # type: ignore[arg-type]
    return {
        "ir_version": "0.2.1",
        # IR 的 id 只认 `^[a-z][a-z0-9_]*$`（任务单号里的 `-` 会撞）⇒ 归一成下划线
        "id": "order_" + re.sub(r"[^a-z0-9_]", "_", f"{order_id}_{index + 1}".lower()),
        "name": title,
        "version": 1,
        "mode": "single",
        "nodes": nodes,
        "edges": [
            {"from": "t1", "to": "d1", "kind": "then"},
            {"from": "d1", "to": "p1", "kind": "then"},
        ],
    }


def step_event(step: Mapping[str, Any], *, order_id: str = "") -> dict[str, Any]:
    """与 `build_step_ir` 的触发源配对的合成事件（`_replay_live` 认这个形状）。"""
    entity = step_entity(step) or f"task_order.{order_id}"
    return {"entity_id": entity, "state": "on"}


@dataclass(frozen=True)
class Claim:
    """一次 tick 拿到的一张单（已置 running）。"""

    order: dict[str, Any]
    resumed_from: int

    @property
    def order_id(self) -> str:
        return str(self.order.get("order_id") or "")

    @property
    def done_steps(self) -> int:
        return self.resumed_from


class TaskOrderStore:
    """任务单活动池（`{store}/.orders/active.json`）。

    **终态即销**：`finish()` 把单子从活动池移除并返回它，由调用方把终态写进既有
    `deploy_audit.jsonl`（⛔ 本模块不留第二套审计面）。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    # ── 盘 ───────────────────────────────────────────────────────────
    def _load(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"version": ORDER_VERSION, "orders": {}}
        except OSError as exc:
            # 读不出 ≠ 没有任务：按最保守一侧拒（否则活动池会"凭空清空"，到点的单子永不执行）
            logger.error("TASK_ORDER_STORE_CORRUPT path=%s err=%s —— 任务单读不出，本次一律拒",
                         self.path, exc)
            raise TaskOrderError("任务单账本读不出（fail-closed）：请修复或隔离 .orders/active.json",
                                 ORDER_STORE_CORRUPT, 500) from exc
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            logger.error("TASK_ORDER_STORE_CORRUPT path=%s err=%s —— JSON 坏形，本次一律拒", self.path, exc)
            raise TaskOrderError("任务单账本已损坏（fail-closed）：请修复或隔离 .orders/active.json",
                                 ORDER_STORE_CORRUPT, 500) from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("orders"), dict):
            logger.error("TASK_ORDER_STORE_CORRUPT path=%s shape=%s", self.path, type(parsed).__name__)
            raise TaskOrderError("任务单账本形状不对（fail-closed）：期望 {\"orders\": {...}}",
                                 ORDER_STORE_CORRUPT, 500)
        version = parsed.get("version")
        if version is not None and version != ORDER_VERSION:
            logger.warning("TASK_ORDER_STORE_VERSION_MISMATCH path=%s on_disk=%r current=%r —— 请人工核对",
                           self.path, version, ORDER_VERSION)
            parsed["_mismatch"] = True
        parsed.setdefault("version", ORDER_VERSION)
        return parsed

    def _save(self, data: Mapping[str, Any]) -> None:
        payload = {k: v for k, v in data.items() if not str(k).startswith("_")}
        payload["version"] = ORDER_VERSION
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))

    def _write_guard(self, data: Mapping[str, Any]) -> None:
        if data.get("_mismatch"):
            raise TaskOrderError(
                "任务单账本版本号与当前程序不一致：拒写（fail-closed），请人工核对 .orders/active.json",
                ORDER_STORE_VERSION_MISMATCH, 500,
            )

    # ── 创建 / 读 / 取消 ────────────────────────────────────────────
    def create(
        self,
        *,
        agent: str,
        steps: Sequence[Mapping[str, Any]],
        output: Mapping[str, Any] | None = None,
        grant_id: str = "",
        at: float | None = None,
        delay_s: float | None = None,
        created_by: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """入册（挂单）。⛔ 只剩"到点一次"这一种时间形状，没有循环字段。"""
        stamp = float(time.time() if now is None else now)
        agent_text = str(agent or "").strip()
        if not agent_text:
            raise TaskOrderError("创建任务单需要 agent（执行主体）", ORDER_BAD_SHAPE, 400)
        if not str(grant_id or "").strip():
            raise TaskOrderError(
                "创建任务单需要 grant_id：任务到点会**逐动作**过执行授权（⛔ 整单放行）",
                ORDER_BAD_SHAPE, 403,
            )
        if not steps:
            raise TaskOrderError("任务单至少要一步", ORDER_NO_STEPS, 400)
        if len(steps) > MAX_STEPS:
            raise TaskOrderError(f"任务单步数 {len(steps)} 超过上限 {MAX_STEPS}",
                                 ORDER_TOO_MANY_STEPS, 400)
        normalized = [_norm_step(s, index=i) for i, s in enumerate(steps)]
        if at is not None and delay_s is not None:
            raise TaskOrderError("at 与 delay_s 只能给一个（都给＝时间点说不清）", ORDER_BAD_DUE, 400)
        if at is None and delay_s is None:
            raise TaskOrderError("任务单必须给 at（绝对时间点）或 delay_s（相对延时）",
                                 ORDER_BAD_DUE, 400)
        due_at = float(at) if at is not None else stamp + float(delay_s or 0)
        if due_at <= 0:
            raise TaskOrderError("目标时间不合法（必须 > 0 的 epoch 秒）", ORDER_BAD_DUE, 400)
        block = output_block(output, kind=str((output or {}).get("kind") or "control"))
        data = self._load()
        with _ORDER_LOCK:
            data = self._load()
            self._write_guard(data)
            mine = [o for o in data["orders"].values() if str(o.get("agent") or "") == agent_text]
            if len(mine) >= MAX_ORDERS_PER_AGENT:
                raise TaskOrderError(
                    f"每个 agent 的活动任务单上限 {MAX_ORDERS_PER_AGENT}（现有 {len(mine)}）："
                    "先取消旧单或等它到点执行",
                    ORDER_LIMIT, 429,
                )
            order_id = "o-" + secrets.token_hex(6)
            while order_id in data["orders"]:
                order_id = "o-" + secrets.token_hex(6)
            record = {
                "order_id": order_id,
                "agent": agent_text,
                "created_at": _now_iso(stamp),
                "created_by": str(created_by or "").strip(),
                "due_at": due_at,
                "due_kind": "at" if at is not None else "delay",
                "steps": normalized,
                "output": block,
                "grant_id": str(grant_id).strip(),
                # ⛔ 没有 every/repeat/cron：一次性语义写在字段集里，判据钉住（红线①）
                "status": STATUS_PENDING,
                "receipt": {"steps": [], "output": []},
                "started_at": "",
                "finished_at": "",
                "cancel": {},
            }
            data["orders"][order_id] = record
            self._save(data)
            return record

    def list(self, *, agent: str = "", now: float | None = None) -> list[dict[str, Any]]:
        """活动池（pending/running）。终态即销，历史走 `deploy_audit.jsonl` 的 `order_receipts`。"""
        stamp = float(time.time() if now is None else now)
        wanted = str(agent or "").strip()
        out: list[dict[str, Any]] = []
        for rec in self._load()["orders"].values():
            if wanted and str(rec.get("agent") or "") != wanted:
                continue
            item = dict(rec)
            item["due_in_s"] = round(float(rec.get("due_at") or 0) - stamp, 1)
            out.append(item)
        out.sort(key=lambda r: float(r.get("due_at") or 0))
        return out

    def get(self, order_id: str) -> dict[str, Any] | None:
        return self._load()["orders"].get(str(order_id or "").strip())

    def cancel(self, order_id: str, *, by: str = "", reason: str = "",
               now: float | None = None) -> dict[str, Any]:
        """人工取消（pending／running 均可）：返回终态记录（调用方负责写审计）。"""
        with _ORDER_LOCK:
            data = self._load()
            self._write_guard(data)
            oid = str(order_id or "").strip()
            rec = data["orders"].get(oid)
            if rec is None:
                raise TaskOrderError(f"没有这张任务单：{oid}（已执行完的单子会即销，历史查 order_receipts）",
                                     ORDER_NOT_FOUND, 404)
            if rec.get("status") not in ACTIVE_STATUSES:
                raise TaskOrderError(f"任务单已是终态 {rec.get('status')!r}，不能取消",
                                     ORDER_STATE, 409)
            rec["status"] = STATUS_CANCELLED
            rec["finished_at"] = _now_iso(now)
            rec["cancel"] = {"at": _now_iso(now), "by": str(by or "").strip(),
                             "reason": str(reason or "").strip()}
            data["orders"].pop(oid, None)
            self._save(data)
            return rec

    # ── 引擎面：认领 / 记步 / 结单 ──────────────────────────────────
    def claim_due(self, *, now: float | None = None, resume_stale: bool = True) -> list[Claim]:
        """认领到点的单子：pending→running；`running` 且卡住超 `RUN_STALE_S` 的**续跑**。

        `resumed_from` ＝ 已经记过回执的步数（崩溃续跑的断点）。认领在**一把锁**里完成，
        两个引擎实例（理论上不会，但进程重启重叠时可能）不会重复认领同一张单。
        """
        stamp = float(time.time() if now is None else now)
        claimed: list[Claim] = []
        with _ORDER_LOCK:
            data = self._load()
            self._write_guard(data)
            changed = False
            for oid, rec in list(data["orders"].items()):
                status = str(rec.get("status") or "")
                due_at = float(rec.get("due_at") or 0)
                if status == STATUS_PENDING and due_at <= stamp:
                    if stamp - due_at > DUE_GRACE_S:
                        # 错过太久：一次性任务的"那一次"已经过去 ⇒ expired（终态即销）
                        rec["status"] = STATUS_EXPIRED
                        rec["finished_at"] = _now_iso(stamp)
                        data["orders"].pop(oid, None)
                        claimed.append(Claim(order=rec, resumed_from=len((rec.get("receipt") or {}).get("steps") or [])))
                        changed = True
                        continue
                    rec["status"] = STATUS_RUNNING
                    rec["started_at"] = rec.get("started_at") or _now_iso(stamp)
                    claimed.append(Claim(order=rec, resumed_from=len((rec.get("receipt") or {}).get("steps") or [])))
                    changed = True
                elif status == STATUS_RUNNING and resume_stale:
                    started = rec.get("started_at") or ""
                    try:
                        age = stamp - datetime.fromisoformat(str(started).replace("Z", "+00:00")).timestamp()
                    except (TypeError, ValueError):
                        age = RUN_STALE_S + 1
                    if age > RUN_STALE_S:
                        claimed.append(Claim(order=rec,
                                             resumed_from=len((rec.get("receipt") or {}).get("steps") or [])))
                        changed = True
            if changed:
                self._save(data)
        return claimed

    def record_step(self, order_id: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
        """把一步的回执**当场落盘**（进程崩在中途时，续跑知道断在哪一步）。"""
        with _ORDER_LOCK:
            data = self._load()
            self._write_guard(data)
            rec = data["orders"].get(str(order_id or ""))
            if rec is None:
                raise TaskOrderError(f"没有这张任务单：{order_id}", ORDER_NOT_FOUND, 404)
            block = rec.setdefault("receipt", {"steps": [], "output": []})
            block.setdefault("steps", []).append(dict(receipt))
            self._save(data)
            return rec

    def record_output(self, order_id: str, receipt: Mapping[str, Any]) -> None:
        with _ORDER_LOCK:
            data = self._load()
            self._write_guard(data)
            rec = data["orders"].get(str(order_id or ""))
            if rec is None:
                raise TaskOrderError(f"没有这张任务单：{order_id}", ORDER_NOT_FOUND, 404)
            block = rec.setdefault("receipt", {"steps": [], "output": []})
            block.setdefault("output", []).append(dict(receipt))
            self._save(data)

    def finish(self, order_id: str, *, status: str, now: float | None = None,
               extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """结单：写终态、**移出活动池**、返回整份记录（调用方据此写审计）。"""
        if status not in (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED, STATUS_EXPIRED):
            raise TaskOrderError(f"finish 只接受终态，收到 {status!r}", ORDER_STATE, 400)
        with _ORDER_LOCK:
            data = self._load()
            self._write_guard(data)
            rec = data["orders"].get(str(order_id or ""))
            if rec is None:
                raise TaskOrderError(f"没有这张任务单：{order_id}", ORDER_NOT_FOUND, 404)
            rec["status"] = status
            rec["finished_at"] = _now_iso(now)
            if extra:
                rec["result"] = dict(extra)
            data["orders"].pop(str(order_id), None)
            self._save(data)
            return rec
