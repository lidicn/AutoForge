"""结构化审计 —— 供 G4 回灌 MA 与人工排障。

G1 记录四类必填事件（IR §9.4 的失败类型）加上总线/配额类系统事件：
- `entity_drift`  实体漂移（引用了不存在的实体）→ 走 on_error 软失效
- `action_failed` 适配器调用失败（设备异常 / 5xx）
- `breaker_open` / `breaker_recover`  实体变更频率熔断
- `quota_exceeded` 并发配额超限
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from .af_atomic import atomic_write_text

__all__ = [
    "AUDIT_SCOPE_NOTE",
    "AuditEvent",
    "AuditLog",
    "DEPLOY_AUDIT",
    "DEPLOY_AUDIT_FILE",
    "WRITE_CONFLICT",
    "deploy_audit_sink",
    "record_conflict",
    "record_deploy",
]

logger = logging.getLogger("autoforge.audit")

ENTITY_DRIFT = "entity_drift"
ACTION_FAILED = "action_failed"
#: v0.3.0 跨自动化事件·发布侧（`emit` 发布的自定义事件）
EVENT_EMITTED = "event_emitted"
BREAKER_OPEN = "breaker_open"
BREAKER_RECOVER = "breaker_recover"
QUOTA_EXCEEDED = "quota_exceeded"
INSTANCE_REJECTED = "instance_rejected"
#: P2-4：触发被去抖（debounce）抑制，此前无任何观测记录
INSTANCE_DEBOUNCED = "instance_debounced"
INSTANCE_EXPIRED = "instance_expired"
#: P1 实例持久化：崩溃恢复时成功挂回 / 因图变更被丢弃
INSTANCE_RESTORED = "instance_restored"
INSTANCE_RESTORE_DROPPED = "instance_restore_dropped"
#: B.14：恢复出的挂起实例**重建不出一条可应答的会话**（挂起节点已不在当前图）。
#: 不静默跳过——这条实例会一直 suspended 且在任何问句面上都看不见。
INSTANCE_SESSION_LOST = "instance_session_lost"
#: v0.9.0 跨进程：写入版本冲突（expect_version 不匹配）与恢复时租约仍属其他进程
WRITE_CONFLICT = "write_conflict"
INSTANCE_LEASE_HELD = "instance_lease_held"
#: P1-3：事件总线订阅回调异常隔离（单个 handler 抛错不影响其他订阅者）
HANDLER_FAILED = "handler_failed"
#: v2 M1 首演码仪式：签发 / 消费 / 试演期开始 / 试演期暂停 / 试演期断言失败
PREMIERE_ISSUED = "premiere_issued"
PREMIERE_CONSUMED = "premiere_consumed"
PREMIERE_TRIAL_STARTED = "premiere_trial_started"
PREMIERE_TRIAL_PAUSED = "premiere_trial_paused"
TRIAL_ASSERT_FAILED = "trial_assert_failed"
#: 裁定 20261009 §三 硬前置：`requires_confirm` 的运行期确认。一次确认只放行一次下发，
#: 所以"通过"与"未通过"都要各自留一条能对上节点与动作的账（拒绝侧不许只落在会被截断的 trace 里）。
CONFIRM_GRANTED = "confirm_granted"
CONFIRM_DENIED = "confirm_denied"

#: 部署仪式台账的文件名（裁定 20261011 §3 Q13 丙：**只对 `DEPLOY_AUDIT` 这一族落盘**）。
#: 落点跟着本次部署的 store 根走（见 `deploy_audit_sink`），所以它与 `write_conflicts.jsonl`
#: 是同一家族——同一个挂载目录、同一条 `.gitignore`，不是新开的一类要轮转／要清理的对象。
DEPLOY_AUDIT_FILE = "deploy_audit.jsonl"
#: 面向读者（HTTP 响应与 CLI）的一句告示：**其余审计只有本进程活着时可查**。
#: 裁定 20261011 §3 Q13 把「写进文档承认易失」并进了丙档，而这半边的落点原写的是 `/api/state`
#: ——现读那一条路由不存在，`audit` 这一段实际出自 `Runtime.stats()` 与 `af_service` 三处响应装配，
#: 所以这句告示挂在那些脸上。它不写成「按设计不需要持久化」：那半句没被裁过。
AUDIT_SCOPE_NOTE = "仅本进程生命周期内可查；只有部署仪式台账落盘（deploy_audit.jsonl）"
#: 台账只留最近这些条。部署仪式是低频事件（一次部署四、五条），超出上限说明它在被人当日志刷；
#: 裁到 `DEPLOY_AUDIT_KEEP_LINES` 而不是清零——留下的仍是最近那一段，取证要的正是尾部。
DEPLOY_AUDIT_MAX_LINES = 2000
DEPLOY_AUDIT_KEEP_LINES = 1000
#: 不许进盘的 `data` 键：首演码是一次性凭据，落盘后在 TTL 内可被"能读到这个文件的人"重放。
#: 台账要证明的是"某月某日为这份 diff 签过码"，不是码本身。
PERSIST_REDACT_KEYS = frozenset({"code"})
REDACTED = "[redacted]"

ALL_EVENT_TYPES = (
    ENTITY_DRIFT,
    ACTION_FAILED,
    EVENT_EMITTED,
    BREAKER_OPEN,
    BREAKER_RECOVER,
    QUOTA_EXCEEDED,
    INSTANCE_REJECTED,
    INSTANCE_EXPIRED,
    INSTANCE_DEBOUNCED,
    INSTANCE_RESTORED,
    INSTANCE_RESTORE_DROPPED,
    INSTANCE_SESSION_LOST,
    WRITE_CONFLICT,
    INSTANCE_LEASE_HELD,
    HANDLER_FAILED,
    PREMIERE_ISSUED,
    PREMIERE_CONSUMED,
    PREMIERE_TRIAL_STARTED,
    PREMIERE_TRIAL_PAUSED,
    TRIAL_ASSERT_FAILED,
    CONFIRM_GRANTED,
    CONFIRM_DENIED,
)


@dataclass(frozen=True)
class AuditEvent:
    """一条审计记录。字段保持扁平可 JSON 化。"""

    type: str
    at: datetime
    message: str
    automation_id: str = ""
    instance_id: str = ""
    node_id: str = ""
    entity_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "at": self.at.isoformat(),
            "message": self.message,
            "automation_id": self.automation_id,
            "instance_id": self.instance_id,
            "node_id": self.node_id,
            "entity_id": self.entity_id,
            "data": dict(self.data),
        }


@dataclass
class AuditLog:
    """内存审计日志（结构保持不变，G2 可直接换持久化实现）。

    P1-1 修复：用有界 deque（保留最近 5000 条）防止常驻进程无限增长——
    审计数组随运行时间线性膨胀，且 `Runtime.stats()` 每次全量序列化，会成为性能杀手。

    **易失半边（裁定 20261011 §3 Q13 丙的读者告示）**：除 `DEPLOY_AUDIT` 之外的实例**只在本次
    进程生命周期内可查**，进程一死就没有。这不是"按设计不需要持久化"——持久化口径由那份裁定给出，
    它只裁了 `DEPLOY_AUDIT` 一族值得落盘（`add(..., sink=)`），其余三族维持易失。
    """

    events: deque[AuditEvent] = field(default_factory=lambda: deque(maxlen=5000))

    def add(self, event: AuditEvent, *, sink: Path | None = None) -> AuditEvent:
        """记一条；给了 `sink` 就同时追加一行 JSONL（落不成由 `_persist_deploy` 响，不静默）。"""
        self.events.append(event)
        if sink is not None:
            _persist_deploy(event, sink)
        return event

    def of_type(self, *types: str) -> list[AuditEvent]:
        return [e for e in self.events if e.type in types]

    def __iter__(self) -> Iterator[AuditEvent]:
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def clear(self) -> None:
        self.events.clear()


#: 部署链（`af_apply.issue_premiere` / `apply`）的进程级审计日志。
#: 它**不是** `runtime.audit`：apply 这条链上没有 runtime 实例可挂载。`d464f6b` 那一版把四处都写成
#: `AuditLog.add(event)`——实例方法当类方法调，`event` 落到了 `self` 上，运行即 `TypeError`，
#: "入队成功之后崩在记账上"；那次崩已由 `0dd57b2`（2026-10-02）改成绑定单例 `DEPLOY_AUDIT.add(...)`。
#: 本批接的是下一格：四站统一走 `record_deploy`，为的是台账能落盘，不是修那枚崩。
DEPLOY_AUDIT = AuditLog()


def deploy_audit_sink(store: Any) -> Path | None:
    """从本次部署用的 `store` 取台账落点；取不到返回 `None`，**由调用方响，不许静默**。

    为什么不新开一个环境变量或全局路径：落点跟着 store 根走就不引入第二份真值——NAS 上根在挂载
    目录，测试里根是 `tmp_path`，于是台账天然按实例隔离，也不会被测试写进仓根变成假凭据。
    """
    root = getattr(store, "root", None)
    if isinstance(root, (str, os.PathLike)) and str(root).strip():
        return Path(root) / DEPLOY_AUDIT_FILE
    return None


def _persistable(event: AuditEvent) -> dict[str, Any]:
    """落盘用的那份字典：一次性凭据（`PERSIST_REDACT_KEYS`）换成标记，其余原样。"""
    payload = event.to_dict()
    data = payload.get("data")
    if isinstance(data, dict):
        payload["data"] = {
            key: (REDACTED if key in PERSIST_REDACT_KEYS else value)
            for key, value in data.items()
        }
    return payload


def _trim_deploy_ledger(sink: Path) -> None:
    """只留最近 `DEPLOY_AUDIT_KEEP_LINES` 条（整份重写走 `atomic_write_text`）。"""
    try:
        lines = sink.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as exc:
        logger.warning(
            "DEPLOY_AUDIT_PERSIST_FAILED 部署台账读不回来，本轮跳过裁剪（%s）：%s", sink, exc
        )
        return
    if len(lines) <= DEPLOY_AUDIT_MAX_LINES:
        return
    atomic_write_text(sink, "\n".join(lines[-DEPLOY_AUDIT_KEEP_LINES:]) + "\n")


def _persist_deploy(event: AuditEvent, sink: Path) -> None:
    """追加一行 JSONL。单行 < 4KB，`O(1)` 追加在无锁并发下不撕裂（与 `record_conflict` 同族）。

    写失败**必须响**：这份台账是首演码／试演台账的凭据面，静默丢一条就等于事后无从对账。
    响的方式是记一条带具名码的 WARNING 并让部署继续——为"记账失败"把已经落地的部署打崩，
    操作员连撤销把手都拿不到（同 §二之八十五 那批的口径）。
    """
    try:
        line = json.dumps(_persistable(event), ensure_ascii=False) + "\n"
        sink.parent.mkdir(parents=True, exist_ok=True)
        with sink.open("a", encoding="utf-8") as handle:
            handle.write(line)
        _trim_deploy_ledger(sink)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning(
            "DEPLOY_AUDIT_PERSIST_FAILED 部署台账没写进去（%s，类型 %s）：%s",
            sink,
            event.type,
            exc,
        )


def record_deploy(event: AuditEvent, store: Any = None) -> AuditEvent:
    """部署仪式唯一的记账入口：内存照记 ＋ 能落盘就落盘，落不成必须响。

    四站点（`af_apply.issue_premiere` / `apply`）全部走这里，不再直接 `DEPLOY_AUDIT.add(...)`——
    否则"改了三个漏一个"会安静地少一条凭据。
    """
    sink = deploy_audit_sink(store)
    if sink is None:
        logger.warning(
            "DEPLOY_AUDIT_NOT_PERSISTED 本次部署没有可落盘的 store（%s），这条凭据只在进程内存里（类型 %s）",
            type(store).__name__,
            event.type,
        )
    return DEPLOY_AUDIT.add(event, sink=sink)


def record_conflict(
    journal: str | Path,
    *,
    name: str,
    expected: int | None,
    actual: int | None,
    writer: str = "",
    note: str = "",
) -> dict[str, Any]:
    """把一条 `write_conflict` 追加进跨进程共享的 JSONL 冲突日志（可审计）。

    追加单行（O_APPEND 语义，单行 <4KB）在无锁并发下也不会撕裂；
    返回写入的条目（供测试/调用方断言）。
    """
    entry = {
        "type": WRITE_CONFLICT,
        "at": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "expected_version": expected,
        "actual_version": actual,
        "writer": writer,
        "note": note,
    }
    path = Path(journal)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
