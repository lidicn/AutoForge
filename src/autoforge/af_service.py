"""AutoForge 服务层（只读 API 的后端逻辑，**无 Web 依赖**）。

对齐《AutoForge-UI 开工令》附录 A 契约：每个函数返回可直接 JSON 化的 dict，
HTTP 层（`af_api.py`）只做路由与错误码映射。

Round 1 只读：不连真实 HA、不写设备、不需要令牌。故障注入只作用于本地 FakeHA 仿真。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import socket
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .af_adapters import DEFAULT_HA_URL, HAAdapter, HAStateProvider, HATransport
from .af_atomic import atomic_write_text
from .af_catalog import DeviceCatalog
from .af_conf import AUTO_MIN, SHADOW_LOW, ConfidenceStore, decision_for
from .af_env import env_int, env_number
from .af_expect import (
    evaluate_graph_expects,
    is_indirect_effect_action,
    is_side_effect_unobservable,
)
from .af_executor import classify_answer
from .af_flock import FileLock, serve_lock_path
from .af_fault import FAULT_META, FOUR_FAILURES, FaultKind
from .af_ir import Graph, IRValidationError, load_graph
from .af_nl import render_graph
from .af_runtime import Runtime, build_runtime
from .af_scanner import DeviceGuardRegistry, Diagnostic, ScanResult, StaticScanner
from .af_spec import SpecError, compile_spec, graph_to_raw, render_spec
from .af_store import DEFAULT_STORE_ROOT, GraphStore, diff_graphs
from .af_state import UnknownEntity
from .af_time import SystemTimeSource, VirtualTimeSource
from .af_undo import UndoStore, deploy_id as new_deploy_id
from .af_vhass import FakeHAAdapter, seed_from_graph
from .af_pending import PendingLimitExceeded, PendingStore
from .af_error_knowledge import ErrorKnowledge
from .af_experience import ExperienceStore
from .af_telemetry import TelemetryStore, attach as attach_telemetry

logger = logging.getLogger("autoforge.service")

__all__ = [
    "CONTRACT_VERSION",
    "API_VERSION",
    "MILESTONES",
    "ServiceError",
    "health",
    "write_gate",
    "WRITE_GATE_OPEN",
    "WRITE_GATE_BLOCKED",
    "WRITE_GATE_NO_LEASE",
    "list_graphs",
    "get_graph",
    "save_graph",
    "build",
    "simulate",
    "get_conf",
    "intervene",
    "diff",
    "spec_of",
    "compile_text",
    "faults",
    "bootstrap_examples",
    # Round 2-A：会话（ask 审批的人机回路）
    "create_session",
    "list_sessions",
    "get_session",
    "answer_session",
    "tick_session",
    "cancel_session",
    "delete_session",
    "SESSION_TTL_S",
    # Round 2-B：真机下发
    "live_status",
    "live_run",
    "get_metrics",
    # F7 残留（WebUI 撤销按钮）：撤销的 HTTP 服务面
    "undo_available",
    "undo_preview",
    "undo_deploy",
    # v1.4.0 待批队列（部署前写操作先入队，人审后回放落盘）
    "submit_pending",
    "list_pending",
    "approve_pending",
    "reject_pending",
    "LIVE_TRANSPORT_FACTORY",
    # v0.7.0 模板导出与备份恢复
    "export_store",
    "import_store",
    # v1.1.0 实体事实内建（设备目录 + 解析）
    "catalog_refresh",
    "catalog_resolve",
    "catalog_list",
    "catalog_state",
    "catalog_snapshot",
    # v1.5.0 经验闭环
    "get_telemetry",
    "get_experience",
    "export_experience",
    "catalog_resolve_metrics",
    # v1.6.0 决策智能
    "catalog_set_alias",
    "catalog_list_aliases",
    "catalog_remove_alias",
    "bind_ir",
]


class ServiceError(Exception):
    """服务层错误。HTTP 层据 `status` 映射状态码。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status

CONTRACT_VERSION = "1.0"
API_VERSION = "0.1.0"
MILESTONES = ("G1", "G2", "G3", "G4", "G5", "真机接线", "G6", "G7")

#: 一次可回放的 events 条数上限（8e8c725 的 DoS 面收敛：整场仿真/回放在内存里跑）。
#: 原先写成 pydantic v2 的 `Field(max_length=…)`——CI 解析到 pydantic **1.10**（被
#: `pytest-homeassistant-custom-component` 拖下来）时，这个 kwarg 让 `import af_api`
#: 当场 `ValueError: constraints set but not enforced`，9 个测试文件连收集都跑不起来。
#: 判据后来挪到 HTTP 请求边界，但那**只装了 HTTP**：`af_sim`/`af_live_run` 这两个 MCP 工具
#: 调的是同一批函数，上限装在入口上等于另一个入口没有。所以判据下移到本层，
#: 三个回放入口（`simulate_track`/`create_session`/`live_run`）各自把一次关。
MAX_REPLAY_EVENTS = 10000


def _check_events_cap(events: Sequence[Mapping[str, Any]] | None) -> None:
    if events is not None and len(events) > MAX_REPLAY_EVENTS:
        raise ServiceError(
            f"events 条数 {len(events)} 超过上限 {MAX_REPLAY_EVENTS}：整场回放/仿真在内存里跑",
            status=422,
        )


# ─────────────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────────────


def _diag(d: Diagnostic) -> dict[str, Any]:
    return {
        "code": d.code,
        "level": d.level,
        "message": d.message,
        "automation_id": d.automation_id,
        "node_id": d.node_id,
        # v1.2.0：怎么改（Agent 据此一次往返自修正）
        "hint": d.hint,
    }


def _diagnostics(result: ScanResult) -> list[dict[str, Any]]:
    return [_diag(d) for d in result.diagnostics]


def _load_ir(ir: Mapping[str, Any] | None) -> Graph:
    """从请求体里的 IR（单条或 `{"automations": [...]}`）构造 Graph。"""
    if not ir:
        raise IRValidationError("IR 为空")
    return load_graph(dict(ir))


def _scan(graph: Graph, known: Iterable[str] | None = None) -> list[dict[str, Any]]:
    return _diagnostics(StaticScanner(graph, known_entities=known).scan())


def _ir_payload(graph: Graph) -> Any:
    raws = graph_to_raw(graph)
    return raws[0] if len(raws) == 1 else {"automations": raws}


def _entities_of(graph: Graph) -> set[str]:
    out: set[str] = set()
    for auto in graph:
        out |= auto.reads() | auto.writes()
    return out


# ─────────────────────────────────────────────────────────────────────
# 1. 健康
# ─────────────────────────────────────────────────────────────────────


def _linkage_health(presence: Any) -> dict[str, Any]:
    """/health 的联动面读数（契约 §7.3 degrade-flag 档，裁定 20261007 §二 丁A）。

    没桥时**不** import `af_mqtt_bridge`（它要 homesdk），也不把"读不到"写成 online
    或 degraded——`unwired` 故意不是契约三态里的一档，就是为了防止被误读成其中任一档。
    """
    if presence is None:
        return {"wired": False, "state": "unwired", "degraded": False, "reasons": []}
    from .af_mqtt_bridge import linkage_status

    return linkage_status(presence)


#: `health()["write_gate"]` 的三档（裁定 20261008 §一 B）。取值口径与 `linkage.state` 同族：
#: 第三档专给"这一面没有闸的信息"，**不许**读成 `open` 或 `blocked` 中的任一个。
WRITE_GATE_OPEN = "open"
WRITE_GATE_BLOCKED = "blocked"
WRITE_GATE_NO_LEASE = "no_lease"


def write_gate(store: "GraphStore | None" = None, *, readonly: bool | None = None) -> str:
    """本面**受闸的写入口**（HTTP 写端点、MCP 真机下发）此刻收不收写。

    管的是单写者租约这一道闸，不是权限/作用域那一道：`light.turn_on` 能不能写设备由
    `live_allow` 与 Tier-0 守卫决定，这里只回答"这个实例会不会在闸上被拒"。归档类写入
    （`save_graph`）本来就不在这道闸里。

    - `blocked`：会拒。两条来源任一成立都要报 blocked——装配期就降级（serve 抢不到
      `serve_lock_path` 那把单写者租约，`_readonly_guard` 对所有写端点回 503），或运行期租约正被**别的进程**持有
      （`live_run` 里的 `_single_writer_check` 会抛 503，哪怕这个面启动时是 `readonly=False`）。
    - `open`：两条都不成立，写入口放行。本进程自己持着锁不算"被别人持有"（`_LOCAL_HELD`）。
    - `no_lease`：无从判断——没有 store 根可探、或探测本身失败，且装配期没给过降级标志。
      留这一档而不是退成 `open`/`blocked`，是因为把"没看"读成"能写"正是本次裁定要消掉的假读数。

    `readonly` 由知道自己档位的那一面显式传入（serve 从 `af_cli` 拿到）；它只能证明"启动时
    闸是关的"，闸后来自己开了不代表这个面会恢复收写，所以启动降级一律优先判 `blocked`。
    """
    blocked_at_boot = bool(readonly)
    if store is None:
        return WRITE_GATE_BLOCKED if blocked_at_boot else WRITE_GATE_NO_LEASE
    try:
        held_by_another = FileLock(serve_lock_path(Path(store.root))).held_by_other()
    except Exception:
        # 探测失败不当"能写"处理：这一格的作用是让读的人知道自己看到了什么。
        return WRITE_GATE_BLOCKED if blocked_at_boot else WRITE_GATE_NO_LEASE
    return WRITE_GATE_BLOCKED if (blocked_at_boot or held_by_another) else WRITE_GATE_OPEN


def health(
    store: "GraphStore | None" = None, *, presence: Any = None, readonly: bool | None = None
) -> dict[str, Any]:
    """WO-AF-004 打回 4：ok 由真实探测决定，不再硬编码 True。

    探 store 可读（list 归档历史）；store 为 None（测试/离线场景）时 ok=True
    保持向后兼容。`readonly` 仍是硬编码字面量（v1.x 只读服务层身份声明，语义不动）；
    运行期写闸真值由新键 `write_gate` 承载（裁定 20261008 §一 B）——现场已经出现过
    "health 报 readonly=true + 写面 200"的矛盾读数，旧键不能改（已有读方按它写判读），
    所以另开一格说真话。

    `presence`（裁定 20261007 §二 丁A）是联动桥本体，由调用面**每次请求时**取来递进来：
    契约 §7.3 的 degrade-flag 档明写「MQTT 断连 → health 报 degraded」，而此前 `/health`
    根本没有到桥的通道（`build_app` 早于 `start_from_env`），那一档只落在
    `counts.publish_errors` 一个本机计数器上——读 `/health` 的人看不见。
    没递桥 ⇒ `linkage.wired=False` 且状态读成 `unwired`，**不许读成健康**。
    """
    ok = True
    store_ok = None
    if store is not None:
        try:
            list(store.history())
            store_ok = True
        except Exception:
            ok = False
            store_ok = False
    # mimo TickSupervisor: expose tick health if a watch is running
    tick_health = None
    ticker_alive = None
    tick_exit_reason = None
    try:
        from .af_live import get_tick_supervisor, get_ticker_thread, get_tick_exit_reason
        sup = get_tick_supervisor()
        if sup is not None:
            tick_health = sup.health().snapshot(sup._clock)
        th = get_ticker_thread()
        if th is not None:
            ticker_alive = th.is_alive()
            tick_exit_reason = get_tick_exit_reason()
    except Exception:
        # 探测失败不能带崩 /api/health，但也不许咽得无声：具名码 + exc_info，与 EXPERIENCE_OBSERVE_SKIPPED 同族。
        logger.debug("TICK_HEALTH_PROBE_SKIPPED", exc_info=True)
    from .af_time import house_tz_status
    return {
        "ok": ok,
        "version": API_VERSION,
        "contract_version": CONTRACT_VERSION,
        "milestones": list(MILESTONES),
        # v1.x 身份声明：本服务层的能力面（"这是一个可被只读部署的服务"），不是运行期档位。
        # 运行期写闸真值看下一格 `write_gate`（裁定 20261008 §一 B：旧键语义不动，新键承载真值）。
        "readonly": True,
        "write_gate": write_gate(store, readonly=readonly),
        "store_ok": store_ok,
        "linkage": _linkage_health(presence),
        "tick_health": tick_health,
        "ticker_alive": ticker_alive,
        "tick_exit_reason": tick_exit_reason,
        # DCD 20261001·§五：时区口径必须可读到「env 生效了还是落到 fallback」，
        # 否则 AF_TZ 写错也只会静默 +8，与 MA 同批披露的假绿同型。
        "tz": house_tz_status(),
    }


# ─────────────────────────────────────────────────────────────────────
# 2/3. 归档列表 / 某版本 Graph
# ─────────────────────────────────────────────────────────────────────


def _archive_names(store: GraphStore) -> list[str]:
    """已归档的名字（去重、保持出现顺序）。"""
    seen: list[str] = []
    for record in store.history():
        name = str(record.get("name", ""))
        if name and name not in seen:
            seen.append(name)
    return seen


def list_graphs(store: GraphStore) -> dict[str, Any]:
    latest: dict[str, dict[str, Any]] = {}
    for record in store.history():
        name = str(record.get("name", ""))
        version = int(record.get("version") or 0)
        if name not in latest or version > int(latest[name]["latest_version"]):
            latest[name] = {
                "name": name,
                "latest_version": version,
                "saved_at": record.get("saved_at", ""),
                "note": record.get("note", ""),
                "mode": "",
                "owner": record.get("owner", ""),  # v1.3.0 归档归属
                "automation_ids": [],
                }
    # 补上 mode / automation_ids / tags（供列表页把"归档名 ↔ 自动化 id / 置信度"对上）
    for name, item in latest.items():
        try:
            graph = store.load(name, int(item["latest_version"]))
        except (FileNotFoundError, IRValidationError, OSError):
            continue
        autos = list(graph)
        item["automation_ids"] = [a.id for a in autos]
        item["mode"] = autos[0].mode if len(autos) == 1 else "multi"
        item["tags"] = store.get_tags(name)
    return {"ok": True, "items": sorted(latest.values(), key=lambda x: x["name"])}


# ─────────────────────────────────────────────────────────────────────
# v0.6.0 标签体系 + 批量启停
# ─────────────────────────────────────────────────────────────────────


def set_graph_tags(store: GraphStore, name: str, tags: list[str]) -> dict[str, Any]:
    """设置某归档名字的标签（覆盖式）。"""
    store.set_tags(name, tags)
    return {"ok": True, "name": name, "tags": store.get_tags(name)}


def graphs_by_tag(store: GraphStore, tag: str) -> dict[str, Any]:
    """返回带某标签的所有归档名（及其最新版本信息）。"""
    names = [n for n, ts in store.all_tags().items() if tag in ts]
    items = []
    for name in names:
        try:
            latest_version = store.latest(name)
            if latest_version is None:
                continue
            graph = store.load(name, latest_version)
        except (FileNotFoundError, IRValidationError, OSError):
            continue
        items.append(
            {
                "name": name,
                "version": latest_version,
                "automation_ids": [a.id for a in graph],
                "tags": store.get_tags(name),
            }
        )
    return {"ok": True, "tag": tag, "items": sorted(items, key=lambda x: x["name"])}


def enable_by_tag(
    store: GraphStore, tag: str, enabled: bool, allow_bulk: bool = False
) -> dict[str, Any]:
    """批量启停：对某标签下所有归档的最新版本，翻转其中全部自动化的 `enabled` 并保存新版本。

    返回受影响（已落新版本）的归档名列表。未找到该标签 → 空列表。

    v1.3.0：默认受**爆炸半径**约束——先把受影响自动化条数算出来再决定要不要做，
    **绝不做一半才报错**。显式传 `allow_bulk=True` 绕过（真正想批量时用）。
    """
    names = [n for n, ts in store.all_tags().items() if tag in ts]
    # 先算清影响面（不落盘），超阈值直接拒——避免"改了一半才失败"
    planned: list[tuple[str, int, Any]] = []
    total = 0
    for name in names:
        try:
            version = store.latest(name)
            if version is None:
                continue
            graph = store.load(name, version)
        except (FileNotFoundError, IRValidationError, OSError):
            continue
        planned.append((name, int(version or 0), graph))
        total += len(list(graph))
    if not allow_bulk:
        _check_blast(total, f"按标签 {tag!r} 批量{'启用' if enabled else '禁用'}")

    affected: list[dict[str, Any]] = []
    for name, _version, graph in planned:
        for auto in graph:
            auto.enabled = enabled
            auto.raw["enabled"] = enabled  # 序列化形态同步翻转，保证 re-save 后 round-trip 一致
        new_version = store.save(
            graph,
            name,
            note=f"v0.6.0 批量{'启用' if enabled else '禁用'}（tag={tag}）",
            # 归属必须随版本走：store.save 的 owner 缺省是空串，不传就等于把别人建的归档
            # 改写成"无归属"——用户视角列表按 owner 分组，一次按标签批量启停会把整批自动化
            # 从原 agent 组里搬进「未归属/本地」，读数看起来像归档被重建过。
            owner=_existing_owner(store, name),
        )
        affected.append({"name": name, "version": new_version, "enabled": enabled})
    return {
        "ok": True,
        "tag": tag,
        "enabled": enabled,
        "affected": affected,
        "blast_radius": {"affected": total, "limit": blast_radius_limit()},
    }


def get_graph(store: GraphStore, name: str, version: int | None = None) -> dict[str, Any]:
    record = store.load_record(name, version)  # 不存在 → FileNotFoundError
    graph = load_graph(record["graph"])
    return {
        "ok": True,
        "name": record.get("name", name),
        "version": record.get("version"),
        "saved_at": record.get("saved_at", ""),
        "note": record.get("note", ""),
        "ir": _ir_payload(graph),  # 单条自动化 → 单 dict；多条 → {"automations": [...]}
        "nl": render_graph(graph).text,
        "diagnostics": _scan(graph),
    }


# ─────────────────────────────────────────────────────────────────────
# v1.9.0 用户视角 WebUI：卡片与启停
#
# 归档记录的形状是 `rec["graph"] = {"automations": [<automation raw>…]}`
# （`af_store._graph_raw`），而 nodes / enabled / 其余 IR 字段全住在 automation 级。
# 端点曾经直接在 `rec["graph"]` 这一层读 `nodes`、读 `enabled`、读 `nl`——那一层没有这些键，
# 于是每条自动化一律渲染成「无设备 / 已启用 / 预演效果空白」，且**三点"停用"不落**（旗子写在容器层，
# 运行期读的是 automation 级：`af_scheduler.py:87-89` 不注册触发、`:241-243` 不排空队列）。正源在下面两个函数。
# ─────────────────────────────────────────────────────────────────────


def automation_card(
    store: GraphStore,
    name: str,
    rec: Mapping[str, Any],
    tags: Sequence[str] | None,
    pending_map: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """把一条归档记录渲染成用户视角卡片：字段全部按**automation 级**取数。

    归档解析失败不再让整张列表 500：坏记录只影响自己那一格（`preview_nl` 里当场说明原因），
    其余自动化照常可读——列表页是日常入口，一条坏归档不该把整个入口打死。
    """
    archived = "archived" in (tags or [])
    graph: Graph | None = None
    ir_error = ""
    try:
        graph = load_graph(rec.get("graph") or {})
    except (IRValidationError, KeyError, TypeError, ValueError) as exc:
        ir_error = str(exc)

    autos = list(graph) if graph is not None else []
    ids: list[str] = []
    seen: set[str] = set()
    for auto in autos:
        for eid in sorted(auto.reads() | auto.writes()):
            if eid not in seen:
                seen.add(eid)
                ids.append(eid)
    names = _catalog(store).display_names(ids) if ids else {}
    devices = [{"entity_id": eid, "friendly_name": names.get(eid) or eid} for eid in ids]

    # 空归档（automations 为空）按"未启用"呈现：没有一条规则在跑，这是真值而不是缺省。
    enabled = bool(autos) and all(auto.enabled for auto in autos)
    if graph is not None:
        preview = render_graph(graph).text
    else:
        preview = f"⚠ 归档无法解析（IR 校验失败）：{ir_error[:200]}"

    return {
        "id": name,
        "name": name,
        "agent": rec.get("owner", ""),
        "preview_nl": preview,
        "devices": devices,
        "enabled": enabled,
        "archived": archived,
        "status": "archived" if archived else ("disabled" if not enabled else "enabled"),
        "pending_op_id": (pending_map or {}).get(name),
        "saved_at": rec.get("saved_at"),
        "version": rec.get("version"),
        # 试演期与触发历史**没有可读取的落盘正源**：首演/试演台账按 diff 摘要记账
        # （af_premiere.enter_trial(store_diff_sha)），不是按自动化名；触发记录只在 watch 进程内存里。
        # 旧形状在这里硬写 `state: "auto"`，等于替每条自动化宣布"已经走到全自动档"——
        # 读不出就给 null，前端 trialMeta(null) 会渲染成不点亮的三档 + 「未进入试演」。
        "trial": None,
        "last_triggered": None,
        "trigger_7d": 0,
    }


def set_automation_enabled(store: GraphStore, name: str, enabled: bool) -> int:
    """翻转归档内**每一条自动化**的启停旗子，返回新版本号。

    写在 automation 级：运行期的读者是调度器（`af_scheduler.py:87-89` 不为禁用项注册触发、
    `:241-243` 不排空它的队列），容器层那个位置没有任何代码读取。落盘复用 `GraphStore.resave_raw`：
    锁、版本号、原子写与归属字段（owner / writer / note 之外的记录元数据）都留在 store 那一处，端点不碰。
    """

    def flip(graph_raw: Any) -> None:
        items = graph_raw.get("automations") if isinstance(graph_raw, dict) else None
        if not isinstance(items, list) or not items:
            # fail-closed：形状不认识就**不写新版本**。写在容器层会静默无效，
            # 抛在这里至少让调用方看到"这一条落不了"，而不是"点了没反应"。
            raise ServiceError(
                f"归档 {name!r} 的 graph 不是 {{'automations': [...]}} 形态，无法定位启停旗子",
                status=409,
            )
        for item in items:
            if not isinstance(item, dict):
                raise ServiceError(f"归档 {name!r} 内含非对象条目，拒绝翻转启停旗子", status=409)
            item["enabled"] = enabled

    return store.resave_raw(name, flip)


def save_graph(
    store: GraphStore,
    ir: Mapping[str, Any],
    name: str,
    note: str = "",
    tags: list[str] | None = None,
    expect_version: int | None = None,
    owner: str = "",
    allow_bulk: bool = False,
) -> dict[str, Any]:
    """归档一个 Graph 版本（**先过第一道闸**，与 MCP/HTTP/CLI 同源）。

    安全约定：**拒绝归档未通过静态扫描的 IR**（`ok=false`）——避免把坏自动化落盘。
    `tags` 非空时一并写入标签元数据；`expect_version` 非空时做乐观锁校验（v0.9.0，
    不匹配抛 `WriteConflictError`）。同时写入置信度种子快照，与 `forge store save` 等价。

    v1.3.0：
    - `owner`：**归档归属**（谁建的），多 agent 接入后用于所有权隔离；
    - `allow_bulk=False`（默认）时受**爆炸半径**约束——一次归档的自动化条数超限即拒。
      显式传 `allow_bulk=True` 表示「我确认这是一次批量操作」，绕过该限制。
    """
    if not name or not str(name).strip():
        raise ServiceError("归档名不能为空", status=400)
    graph = _load_ir(ir)
    scan = StaticScanner(
        graph,
        device_guard=load_device_guard(store),
        entity_health=load_entity_health(store),
    ).scan()
    if not scan.ok:
        raise ServiceError(
            f"拒绝归档：IR 未通过静态扫描（{len(scan.errors)} 个错误）。先修 IR 或改用 af_build 查看诊断。",
            status=400,
        )
    count = len(list(graph))
    if not allow_bulk:
        _check_blast(count, f"本次归档 {str(name).strip()!r} 含 {count} 条自动化")
    # v1.3.0 所有权隔离：已归属他人的归档不能覆盖（归属为空视为公共，放行）
    prev_owner = _existing_owner(store, str(name).strip())
    if prev_owner and owner and prev_owner != owner:
        raise ServiceError(
            f"归档 {str(name).strip()!r} 归属 {prev_owner!r}，当前主体 {owner!r} 无权覆盖（所有权隔离）",
            status=403,
        )
    version = store.save(
        graph, str(name).strip(), note=note, tags=tags,
        expect_version=expect_version, owner=owner,
    )
    store.save_conf(ConfidenceStore().seed(graph), str(name).strip(), note)
    # v1.5.0：**仅在成功落盘后**采集实体共现经验（失败样本不污染）
    try:
        ExperienceStore(store.root).observe(graph, ok=True)
    except (OSError, ValueError, KeyError, AttributeError):
        logger.debug("EXPERIENCE_OBSERVE_SKIPPED", exc_info=True)
    return {
        "ok": True,
        "name": str(name).strip(),
        "version": version,
        "note": note,
        "owner": owner,
        "tags": store.get_tags(str(name).strip()),
        "blast_radius": {"affected": count, "limit": blast_radius_limit()},
        "warnings": [_diag(d) for d in scan.warnings],
    }


# ─────────────────────────────────────────────────────────────────────
# 3.1 待批队列（v1.4.0，调研 §2.6）
#
# 部署前写操作（如 af_save）先入队 `pending/`，人审（HTTP / CLI）后回放 payload 落盘。
# **MCP 面绝不注册 approve**（agent 不能自批）：提交在 MCP，批准只在服务层。
# ─────────────────────────────────────────────────────────────────────


def _blast_radius_of(ir: Mapping[str, Any] | None) -> dict[str, Any]:
    """复用 v1.3.0 的爆炸半径口径：影响面 = 本 IR 自动化条数。"""
    if not ir:
        return {"affected": 0, "limit": blast_radius_limit()}
    try:
        graph = _load_ir(ir)
        affected = len(list(graph))
    except (IRValidationError, ValueError, KeyError, TypeError):
        affected = 0
    return {"affected": affected, "limit": blast_radius_limit()}


def submit_pending(
    store: GraphStore,
    tool: str,
    payload: Mapping[str, Any],
    *,
    summary: str | None = None,
    submitted_by: str = "",
    agent_key: str | None = None,
    authenticated_subject: str | None = None,
) -> dict[str, Any]:
    """把一条写操作入待批队列；per-agent 熔断达上限抛 `ServiceError(400)`。

    v1.4.0：提交前先过**第一道闸**（静态扫描）——未通过即拒绝入队（fail-fast），
    避免坏自动化占坑等人审；`approve` 回放时 `save_graph` 会**再校验一次**（双保险）。

    P0-13 修复：`authenticated_subject` 来自认证上下文（token subject），
    传入时强制覆盖 `submitted_by`，防止调用方伪造提交人身份。
    """
    ir = payload.get("ir")
    if ir is not None:
        try:
            graph = _load_ir(ir)
        except (IRValidationError, ValueError, KeyError) as exc:
            raise ServiceError(f"拒绝归档：IR 结构非法（{exc}）", status=400)
        scan = StaticScanner(
            graph,
            device_guard=load_device_guard(store),
            entity_health=load_entity_health(store),
        ).scan()
        if not scan.ok:
            raise ServiceError(
                f"拒绝归档：IR 未通过静态扫描（{len(scan.errors)} 个错误）。"
                f"先修 IR 或改用 af_build 查看诊断。",
                status=400,
            )
    # P0-13：认证主体强制覆盖调用方自报的 submitted_by
    effective_submitter = authenticated_subject or submitted_by
    ps = PendingStore(store.root)
    br = payload.get("blast_radius") or _blast_radius_of(payload.get("ir", {}))
    summary = summary or f"{tool}：{payload.get('name', '')}"
    try:
        op = ps.submit(
            tool, payload, summary=summary, blast_radius=br,
            submitted_by=effective_submitter, agent_key=agent_key,
        )
    except PendingLimitExceeded as exc:
        raise ServiceError(str(exc), status=400)
    return {
        "ok": True,
        "pending": op.op_id,
        "summary": op.summary,
        "blast_radius": op.blast_radius,
        "submitted_by": op.submitted_by,
        "submitted_at": op.submitted_at,
        "agent": op.agent,
        "note": "待人工审批（approve 后才会落盘）",
    }


def approve_insight(
    store: GraphStore, queue: Any, proposal_id: str, *, reviewer: str = ""
) -> dict[str, Any]:
    """裁定 20261002 §三 ④A 的交接点：只落盘的 MA 洞察提案 **approve 之后**才进 `af_pending`。

    三道刻意限制：
    ① 队列里查不到就报错——不代为创建提案；
    ② 只有自然语言、没有编译后 IR 的提案直接拒——服务端不凭空造一张可部署的图（要造先走 `af_draft`）；
    ③ 一条提案含多条自动化时也拒——一次交接只对应一条，不猜要哪条。
    通过后走的是 `submit_pending` 的常规路径：静态扫描第一道闸 + per-agent 熔断，与人工来源同闸。
    """
    rec = queue.get(proposal_id)
    if rec is None:
        raise ServiceError(f"洞察提案 {proposal_id!r} 在队列里查不到（不代为创建）", status=404)
    if rec.status != "pending":
        raise ServiceError(f"提案 {proposal_id} 已是 {rec.status}，不重复处理", status=409)
    ir = rec.suggested_ir
    if not ir:
        raise ServiceError(
            "该提案只有自然语言、没有编译后的 IR：approve 不代为造图，请先走 af_draft",
            status=400,
        )
    graph: Mapping[str, Any] = ir
    automations = ir.get("automations") if isinstance(ir, Mapping) else None
    if isinstance(automations, list):
        if len(automations) != 1 or not isinstance(automations[0], Mapping):
            raise ServiceError(
                f"提案含 {len(automations)} 条自动化，一次 approve 只交接一条（不猜要哪条）",
                status=400,
            )
        graph = automations[0]
    name = str(graph.get("name") or graph.get("id") or rec.hypothesis_id)
    res = submit_pending(
        store,
        "af_save",
        {"ir": dict(graph), "name": name, "note": f"ma_insight:{rec.hypothesis_id}"},
        summary=f"MA 洞察 {rec.proposal_id}（conf={rec.conf:.2f}）经批准后入待批",
        submitted_by=reviewer or "insight_approve",
    )
    moved = queue.move_to(
        rec, status="approved", decided_by=reviewer or "insight_approve",
        reason=f"pending_op={res.get('pending')}",
    )
    return {
        "proposal_id": moved.proposal_id,
        "pending": res.get("pending"),
        "name": name,
        "note": "已进待批队列；这一步**还没有部署**，仍需 af_pending 的 approve",
    }


def reject_insight(queue: Any, proposal_id: str, *, reviewer: str = "", reason: str = "") -> dict[str, Any]:
    """判定为"不做"：移出 pending 归档，**不产生任何待批操作**（拒绝也要留痕）。"""
    rec = queue.get(proposal_id)
    if rec is None:
        raise ServiceError(f"洞察提案 {proposal_id!r} 在队列里查不到", status=404)
    if rec.status != "pending":
        raise ServiceError(f"提案 {proposal_id} 已是 {rec.status}，不重复处理", status=409)
    moved = queue.move_to(rec, status="rejected", decided_by=reviewer or "insight_reject", reason=reason)
    return {"proposal_id": moved.proposal_id, "status": moved.status, "decided_at": moved.decided_at}


def list_pending(store: GraphStore, agent: str | None = None) -> dict[str, Any]:
    """列出待批（审批视图：含 summary / blast_radius / 完整 payload 供回放）。"""
    return {"ok": True, "items": PendingStore(store.root).list(agent)}


def approve_pending(store: GraphStore, op_id: str, reviewer: str = "human") -> dict[str, Any]:
    """批准并回放 payload 落盘（复用 `_t_save` 的落盘逻辑，无二次渲染）。

    reviewer 硬编码 `"human"`（服务层约束：agent 不能自批）；HTTP 层传令牌主体。
    """
    ps = PendingStore(store.root)
    # R4-03 修复 v2：load → 校验 → delete 全部在锁内，确保：
    # 1) 同一 op_id 只会被批准一次（并发安全）
    # 2) 禁止自批检查失败时不删除待批（拒绝后可重试，不再孤儿化）
    # FFL 20261008 发现：原实现先 delete 再做 P0-13 检查，被 403 拒绝的待批直接消失。
    with ps._lock:
        op = ps.load(op_id)
        if op is None:
            raise ServiceError(f"未找到待批操作 {op_id!r}", status=404)
        # P0-13：禁止自批（提交人不能批准自己提交的操作）
        # ADM B-11：submitted_by 为空时（无鉴权原型模式）不阻止，但标注 unverified
        submitter = str(op.get("submitted_by") or "")
        if reviewer and submitter and reviewer == submitter:
            raise ServiceError(
                f"禁止自批：提交人 {submitter!r} 不能批准自己提交的操作 {op_id!r}",
                status=403,
            )
        ps.delete(op_id)
    self_check = ""
    if not submitter:
        self_check = "unverified_submitter"  # ADM B-11：无提交人信息，自批检查无法生效
    payload = op["payload"]
    result = save_graph(
        store,
        payload.get("ir"),
        payload.get("name", ""),
        note=payload.get("note", ""),
        tags=payload.get("tags"),
        expect_version=payload.get("expect_version"),
        owner=payload.get("owner", ""),
        allow_bulk=bool(payload.get("allow_bulk", False)),
    )
    result["approved_by"] = reviewer
    result["pending"] = op_id
    if self_check:
        result["self_check"] = self_check  # ADM B-11
    return result


def reject_pending(store: GraphStore, op_id: str, reason: str = "") -> dict[str, Any]:
    """拒绝并删除待批操作（不落盘）。"""
    ps = PendingStore(store.root)
    op = ps.load(op_id)
    if op is None:
        raise ServiceError(f"未找到待批操作 {op_id!r}", status=404)
    ps.delete(op_id)
    if ps.load(op_id) is not None:
        # delete 之后回读确认"真的不在队列里了"。报 ok=True 而条目还在，等于对上层
        # 谎报已回滚，而 group 的原子性判据正是这句话（铁律 #5：回滚不了要 fail-closed）。
        raise ServiceError(f"待批操作 {op_id!r} 仍在队列中（删除未生效），请复查 pending", status=409)
    logger.warning("PENDING_REJECTED op=%s reason=%s", op_id, reason)
    return {"ok": True, "rejected": op_id, "reason": reason}


# ─────────────────────────────────────────────────────────────────────
# 3.2 经验闭环出口（v1.5.0）：遥测 + 实体共现
# ─────────────────────────────────────────────────────────────────────


def get_telemetry(store: GraphStore, days: int = 30) -> dict[str, Any]:
    """token/结果遥测四维聚合 + 错误知识库类别分布（v1.5.0）。"""
    out = TelemetryStore(store.root).usage(days=days)
    out["knowledge"] = ErrorKnowledge(store.root).counts()
    return out


def get_experience(store: GraphStore, *, limit: int = 10) -> dict[str, Any]:
    """实体共现经验摘要（v1.5.0，喂 MA 的采集事实面）。"""
    return ExperienceStore(store.root).summary(limit=limit)


def export_experience(store: GraphStore, *, limit: int = 200) -> dict[str, Any]:
    """实体共现经验结构化导出（v1.5.0）。"""
    return ExperienceStore(store.root).export(limit=limit)


# ─────────────────────────────────────────────────────────────────────
# 4. 静态扫描（第一道闸）
# ─────────────────────────────────────────────────────────────────────

#: v1.4.0：设备保护注册表文件名（放 store 根；可入库、可热更新，无需重启）。
DEVICE_ACL_FILENAME = "device_acl.json"


#: 进程级缓存 `{catalog_path: (mtime, size, health_map)}`。
#: 审计发现：原先**每次** build/save_graph/submit_pending 都 new 一个 DeviceCatalog
#: 并全量解析目录（NAS 上约 2888 实体，数 MB JSON），而实例级 mtime 缓存跨请求完全不命中。
_ENTITY_HEALTH_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
_ENTITY_HEALTH_LOCK = threading.Lock()


def load_entity_health(store: GraphStore | None) -> dict[str, Any]:
    """v1.6.0 P2 联动验证闸：从设备目录取「此刻可用性」视图。

    目录为空 / 读取异常 → 空 dict（离线编写 IR 场景**零误报**；核心能力不受损）。
    按目录文件的 `(mtime_ns, size)` 做**进程级缓存**，命中即复用，避免每次扫描全量重解析。
    （R19-02：秒级 mtime 在快速连续写入下会碰撞，改用纳秒精度。）
    """
    if store is None:
        return {}
    try:
        path = DeviceCatalog(store.root).catalog_path
        try:
            stat = path.stat()
        except OSError:
            return {}
        key = str(path)
        with _ENTITY_HEALTH_LOCK:
            cached = _ENTITY_HEALTH_CACHE.get(key)
        if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
        data = DeviceCatalog(store.root).health_map()
        with _ENTITY_HEALTH_LOCK:
            _ENTITY_HEALTH_CACHE[key] = (stat.st_mtime_ns, stat.st_size, data)
        return data
    except (OSError, ValueError, KeyError, AttributeError):
        logger.debug("ENTITY_HEALTH_MAP_SKIPPED", exc_info=True)
        return {}


def load_device_guard(store: GraphStore | None) -> DeviceGuardRegistry | None:
    """从 `{store.root}/device_acl.json` 加载设备保护注册表（v1.4.0）。

    - 文件不存在 → 返回 None（不启用分级保护，向后兼容）；
    - 每次调用**重新读盘**：规则文件改动后下次 `scan()` 自动生效（热更新，无需重启）；
    - 读盘/解析失败 → 记日志并返回 None（不让坏策略文件把服务打挂）。

    P0-7 修复：注入 DeviceCatalog，让 area 类型的保护规则能正确匹配
    （之前 catalog 始终为 None，area 规则永远不命中）。

    规则文件形态见 `DeviceGuardRegistry.from_file`（规则数组 / 旧 ACL 对象 / `{"rules":[…]}`）。
    """
    if store is None:
        return None
    path = Path(store.root) / DEVICE_ACL_FILENAME
    if not path.is_file():
        return None
    try:
        guard = DeviceGuardRegistry.from_file(path)
        # P0-7：注入 catalog，使 area 规则可匹配
        try:
            from .af_catalog import DeviceCatalog
            guard.catalog = DeviceCatalog(store.root)
        except Exception as exc:
            # 裁定 20261011《十三问》§3 Q8.3：安全面降级按介入档就是 ERROR，WARNING 会让人沉默。
            logger.error("DEVICE_GUARD_CATALOG_INJECT_FAILED err=%s", exc)
        return guard
    except (OSError, ValueError) as exc:
        logger.error("DEVICE_ACL_LOAD_FAILED path=%s err=%s", path, exc)
        return None


#: v2.1 契约冻结（决策 C）：build/simulate 返回必须带的 stage 契约版本标记。
#: 消费方据此判断是否已冻结；未带 schema 的旧生产方走过渡期宽松解析。
STAGE_SCHEMA = "af-stage/1"


def build(
    ir: Mapping[str, Any],
    known_entities: Iterable[str] | None = None,
    *,
    store: GraphStore | None = None,
    use_catalog: bool = False,
) -> dict[str, Any]:
    """第一道闸：静态扫描 + NL 渲染。

    v1.1.0：`use_catalog=True` 且调用方未显式给 `known_entities` 时，
    **自动取本机设备目录全集**，让「实体存在性校验」默认开启。
    目录为空时**静默退化**为不校验（避免把「没刷过目录」误判成「实体全不存在」）。
    """
    known = known_entities
    catalog_used = False
    if known is None and use_catalog and store is not None:
        ids = DeviceCatalog(store.root).known_entity_ids()
        if ids:
            known = ids
            catalog_used = True
    graph = _load_ir(ir)
    result = StaticScanner(
        graph,
        known_entities=known,
        device_guard=load_device_guard(store),
        entity_health=load_entity_health(store),
    ).scan()
    out = {
        "ok": result.ok,
        "errors": [_diag(d) for d in result.errors],
        "warnings": [_diag(d) for d in result.warnings],
        "diagnostics": _diagnostics(result),
        "nl": render_graph(graph).text,
        "entity_check": "catalog" if catalog_used else ("provided" if known is not None else "skipped"),
        "schema": STAGE_SCHEMA,
    }
    # v1.5.0：失败回执附归因 + 历史同类（写→读闭环）
    first_error = result.errors[0].message if result.errors else ""
    return attach_telemetry(
        out,
        tool="build",
        root=(store.root if store is not None else None),
        ok=result.ok,
        message=first_error,
    )


# ─────────────────────────────────────────────────────────────────────
# 5. 仿真回放（第二道闸）
# ─────────────────────────────────────────────────────────────────────


def _replay(runtime: Runtime, states: Any, events: Sequence[Mapping[str, Any]]) -> None:
    for item in events:
        advance = item.get("advance_s")
        if advance:
            secs = float(advance)
            if secs < 0 and hasattr(runtime.clock, "jump"):
                runtime.clock.jump(secs)
                runtime.tick()
            else:
                runtime.advance(secs)
        # mimo Clock 增量：ask answer 注入，让 yes/no/default 分支在 sim 里可测
        answer_text = item.get("answer")
        if answer_text is not None:
            runtime.executor.answer(
                room=item.get("room"),
                text=str(answer_text),
                ask_id=item.get("ask_id"),
            )
            continue
        entity_id = item.get("entity_id")
        if entity_id:
            state = str(item.get("state", ""))
            states.set(str(entity_id), state)
            runtime.emit(str(entity_id), state, last_changed=item.get("last_changed"))
        elif advance is None:
            runtime.tick()


def _apply_indirect_effects(graph, states) -> None:
    """v2.1 1.1 补域：把 scene/script 等间接触发动作的声明 ``effects`` 展开进仿真状态。

    仅作用于声明了 ``effects`` 且动作域属于间接效果域的 do 节点，使这些自动化的
    expect 可验证（离开 non_simulable）。不触碰其它动作，向后兼容。
    """
    for auto in graph:
        for node in auto.nodes.values():
            if node.kind != "do" or not node.effects:
                continue
            if not is_indirect_effect_action(node.action):
                continue
            for eff in node.effects:
                eid = eff.get("entity_id")
                if not eid:
                    continue
                st = eff.get("state")
                if st is not None:
                    states.set(str(eid), str(st))


def _expect_failure_message(report: Any) -> str:
    """从 `expect` 报告里取一条可归因的失败说明（没有失败则空串）。"""
    if not isinstance(report, dict):
        return ""
    for item in report.get("items", []) or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", item.get("result", ""))).lower()
        if status in ("fail", "failed", "false"):
            target = item.get("target") or item.get("entity_id") or item.get("var") or ""
            detail = item.get("reason") or item.get("detail") or item.get("message") or ""
            return f"expect 断言失败：{target} {detail}".strip()
    if report.get("ok") is False:
        return "expect 断言失败（详见报告）"
    return ""


def simulate_track(
    track: str,
    ir: Mapping[str, Any],
    seed: Mapping[str, str] | None = None,
    events: Sequence[Mapping[str, Any]] | None = None,
    *,
    store: GraphStore | None = None,
    clock: Any | None = None,
) -> dict[str, Any]:
    """第二道闸（双轨版）：在 FakeHA 或 HiFi 仿真底座下回放，并对 `expect` 逐条断言。

    `track`：
        - ``"fake"``：降级内存底座（FakeHAAdapter），动作立即生效；
        - ``"hifi"``：高仿真底座（HighFidelityHA + HighFidelityAdapter），动作入队后
          flush 才落位，且 `sun` 用真实太阳几何（PR 1.2 双轨对拍的两轨之一）。

    两轨共享 `fake.py` 唯一效果真值表（`SERVICE_STATE` / `service_effect` /
    `is_modeled`），故对建模动作的结果应当一致；唯一已知分歧点是 `sun` 触发器
    （FakeHA 固定 18:00/06:00 vs HiFi 真实几何），对拍时由 `compare_dual_track` 白名单豁免。

    返回里的 `expect` 是「跑**对**了吗」的答案：
    - `ok`：没有断言失败；
    - `fully_verified`：**声明过断言且全部验过**（无 fail 且无 unverified）——
      比 `ok` 更严；`ok=True` 只代表「没抓到反例」。
    """
    if track not in ("fake", "hifi"):
        raise ValueError(f"未知仿真轨：{track!r}（仅支持 'fake' / 'hifi'）")
    _check_events_cap(events)
    graph = _load_ir(ir)
    if track == "hifi":
        from datetime import datetime, timezone

        from .af_time import VirtualTimeSource
        from .af_vhass.high_fidelity import create_adapter, create_ha_from_graph

        if clock is None:
            clock = VirtualTimeSource(datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc))
        ha = create_ha_from_graph(
            graph, seed or {}, clock=clock, hifi=True,
            device_latency=0.0, sun_events=True,
        )
        states: Any = ha
        runtime = build_runtime(graph, states=states, clock=clock)
        adapter = create_adapter(ha, name="ha")
    else:
        runtime = build_runtime(graph, clock=clock)
        states = seed_from_graph(graph, seed or {}, clock=runtime.clock)
        runtime.states = states
        runtime.instances.states = states
        runtime.scheduler.states = states
        runtime.executor.states = states  # canary 漂移检测读同一份状态源
        adapter = FakeHAAdapter(states)
    runtime.adapters.register(adapter)

    _replay(runtime, states, list(events or []))

    if track == "hifi":
        # 高仿真：do 动作入队，需 flush 才落位；循环 flush 让 cover/light transition
        # 等中间态收敛到最终态（与 FakeHA 立即生效对齐），并同步真实太阳几何。
        for _ in range(1000):
            ha.flush()
            if not ha.queue.pending():
                break
        ha.tick()
        for _ in range(1000):
            ha.flush()
            if not ha.queue.pending():
                break

    # v2.1 1.1 补域：间接触发展开（scene/script 声明 effects → 仿真状态），使 expect 可验证
    _apply_indirect_effects(graph, states)

    # v1.2.0：后置条件断言求值（变量形态在任一实例的 vars 里成立即通过）
    var_sources = [inst.ctx.vars for inst in runtime.instances.all()]
    expect_report = evaluate_graph_expects(
        graph, states, var_sources, unmodeled_actions=getattr(adapter, "unmodeled", ())
    )

    final_states = {e: states.get(e) for e in sorted(_entities_of(graph))}
    out = {
        "ok": True,
        "instances": [i.to_dict() for i in runtime.instances.all()],
        "audit": [e.to_dict() for e in runtime.audit],
        "bus": runtime.bus.stats(),
        "final_states": final_states,
        "expect": expect_report,
        "exempted": expect_report.get("exempted_actions", []),  # v2.1 1.1：副作用不可观测动作真豁免清单
        "nl": render_graph(graph).text,
        "schema": STAGE_SCHEMA,
        "track": track,
    }
    # v2 M4 诚实报告分层：强制三栏，绝不把「没验到」当「验过了」
    out["report"] = honest_report(out)
    # v1.5.0：sim 的 `ok` 恒 True（失败以 `expect` 报告）；`_telemetry.ok` 反映**断言是否通过**。
    return attach_telemetry(
        out,
        tool=f"simulate:{track}",
        root=(store.root if store is not None else None),
        ok=bool(expect_report.get("ok", True)),
        message=_expect_failure_message(expect_report),
    )


def simulate(
    ir: Mapping[str, Any],
    seed: Mapping[str, str] | None = None,
    events: Sequence[Mapping[str, Any]] | None = None,
    *,
    store: GraphStore | None = None,
) -> dict[str, Any]:
    """第二道闸（默认 FakeHA 轨）：同 `simulate_track("fake", ...)`，向后兼容别名。"""
    return simulate_track("fake", ir, seed, events, store=store)


# ─────────────────────────────────────────────────────────────────────
# v2 M4 诚实报告分层（honest report）：verified / inferred / non_simulable
# ─────────────────────────────────────────────────────────────────────


def honest_report(out: Mapping[str, Any]) -> dict[str, Any]:
    """v2 M4 诚实报告分层：把仿真结果强制分成三栏，绝不把「没验到」当「验过了」。

    - verified：断言**实际跑过**的结果（status=pass / fail）。
    - inferred：仿真产出但**没有断言覆盖**的事实（final_states 里未被任何 expect target 覆盖的实体）。
    - non_simulable：断言**无法验证**（status=unverified）→ 显式标黄，绝不脑补默认值。
    - exempted：副作用不可观测、被影子系统**显式豁免验证**的自动化（决策 B 真豁免通道）；
      单列一档，绝不冒充 verified，也不计入 non_simulable（那是「想验但验不到」）。
    - verified_in_prod：生产态真实验证证据（shadow/canary/conflict 落点，由 af_watch 聚合，
      经 ``out["verified_in_prod"]`` 回灌）；是「真的跑过且对」的硬证据，与仿真推断分开。
    """
    from autoforge.af_watch import verified_in_prod_partition as _watch_partition
    expect = out.get("expect") or {}
    # 注意：evaluate_graph_expects 把断言项嵌在 expect["automations"][auto_id]["items"]，
    # 也兼容调用方直接给出顶层 expect["items"]（手工构造/测试）。两者合并，保证真实
    # simulate 输出的 verified / non_simulable 分区非空（此前只读顶层 items 导致恒空）。
    _auto_reports = expect.get("automations") or {}
    items = list(expect.get("items") or [])
    for _ar in _auto_reports.values():
        items.extend(_ar.get("items") or [])
    verified = [it for it in items if it.get("status") in ("pass", "fail")]
    non_simulable = [
        {**it, "flag": "yellow"}
        for it in items
        if it.get("status") == "unverified" and not it.get("exempt")
    ]  # 注：副作用不可观测（notify 等）的 expect 标记 exempt → 不进 non_simulable，已归 exempted 档
    # 仿真底座未建模的动作（如 vhass/FakeHA 不认识的服务）→ 同样是「不可仿真」，显式标黄
    for _act in expect.get("unmodeled_actions") or []:
        non_simulable.append(
            {"action": _act, "flag": "yellow", "reason": "仿真底座未建模动作，后果无法验证"}
        )
    # inferred：final_states 中未被任何 expect target（实体维度）覆盖的实体。
    # target 可能是实体形态（"light.a"）或属性形态（"light.a.brightness"），需按实体前缀匹配；
    # 注意实体 id 本身含点号，不能简单 split(".")[0]。
    targets = [str(it.get("target", "")) for it in items if it.get("target")]

    def _covered(eid: str) -> bool:
        return any(t == eid or t.startswith(eid + ".") for t in targets)

    final_states = out.get("final_states") or {}
    inferred = [
        {"entity": e, "state": final_states[e], "note": "仿真产出但无断言覆盖"}
        for e in final_states
        if not _covered(str(e))
    ]
    return {
        "verified": verified,
        "inferred": inferred,
        "non_simulable": non_simulable,
        # 决策 B 真豁免通道：副作用不可观测的自动化单列一档（由影子系统/IR 显式标注喂入，
        # 形如 [{"automation_id": "aN", "action": "notify", "reason": "..."}]）。
        "exempted": list(out.get("exempted", []) or []),
        # v2.1 F4：生产态真实验证证据（af_watch 聚合，运行时经 out["verified_in_prod"] 回灌）
        "verified_in_prod": out.get("verified_in_prod", _watch_partition()),
        "fully_verified": bool(items) and not non_simulable and expect.get("failed", 0) == 0,
    }


# ─────────────────────────────────────────────────────────────────────
# 6/7. 置信度分级（G4）
# ─────────────────────────────────────────────────────────────────────


def _conf_of(store: GraphStore, name: str, graph: Graph) -> ConfidenceStore:
    try:
        return store.load_conf(name)
    except FileNotFoundError:
        return ConfidenceStore().seed(graph)


def _conf_items(graph: Graph, conf: ConfidenceStore) -> list[dict[str, Any]]:
    return [
        {"automation_id": a.id, "confidence": conf.get(a.id), "band": conf.band(a.id)}
        for a in graph
    ]


#: `_all` / `all` 视为"全部归档"（列表页与置信度面板的聚合视图）
_ALL_NAMES = frozenset({"_all", "all"})

#: v1.3.0 **爆炸半径**：一次写操作最多影响的自动化条数。
#:
#: 动机（调研 §B2.4）：AutoForge 此前没有「一次能改多少」的上限——
#: `import_store` 可一次导入整个 bundle，`enable_by_tag` 可一次翻转一个 tag 下全部。
#: 防「agent 一次手滑改完全部自动化」最有效的一招就是把它变成一等公民参数。
#: `0` 表示不限（导入 bundle 这类**显式批量**操作需单独用 `allow_bulk=true` 放开）。
DEFAULT_BLAST_RADIUS = 8


def blast_radius_limit() -> int:
    """当前爆炸半径上限（可用 `AUTOFORGE_BLAST_RADIUS` 覆盖；`0` = 不限）。

    R9-02 修复：配负数时 clamp 到 0（=不限）并记 warning——负数与 0 等价但文档只说"0=不限"，
    用户配 -1 会以为护栏生效实际已失效，属于静默 fail-open。
    """
    try:
        val = int(os.getenv("AUTOFORGE_BLAST_RADIUS", str(DEFAULT_BLAST_RADIUS)))
    except (TypeError, ValueError):
        return DEFAULT_BLAST_RADIUS
    if val < 0:
        logger.warning("AUTOFORGE_BLAST_RADIUS=%s 为负数，clamp 到 0（=不限）；如需限制请设正数", val)
        return 0
    return val


def _existing_owner(store: GraphStore, name: str) -> str:
    """某归档**最近一次**记录的归属主体（v1.3.0 所有权隔离用）。"""
    for record in reversed(list(store.history())):
        if str(record.get("name", "")) == name and record.get("owner"):
            return str(record["owner"])
    return ""


def _check_blast(count: int, what: str) -> None:
    """超过爆炸半径即拒绝——**提示「请拆分」而不是硬拒到无法工作**。"""
    limit = blast_radius_limit()
    if limit > 0 and count > limit:
        raise ServiceError(
            f"{what} 会影响 {count} 条自动化，超过爆炸半径上限 {limit}；"
            f"请拆分为更小的操作（或调高 AUTOFORGE_BLAST_RADIUS；置 0 表示不限）",
            status=400,
        )


def get_conf(store: GraphStore, name: str) -> dict[str, Any]:
    if name in _ALL_NAMES:
        merged: dict[str, dict[str, Any]] = {}
        for archive in _archive_names(store):
            try:
                graph = store.load(archive)
            except (FileNotFoundError, IRValidationError, OSError):
                continue
            conf = _conf_of(store, archive, graph)
            for item in _conf_items(graph, conf):
                merged.setdefault(item["automation_id"], item)
        return {
            "ok": True,
            "name": name,
            "version": None,
            "items": sorted(merged.values(), key=lambda x: x["automation_id"]),
            "thresholds": {"auto": AUTO_MIN, "shadow_low": SHADOW_LOW},
        }

    record = store.load_record(name)
    graph = load_graph(record["graph"])
    conf = _conf_of(store, name, graph)
    return {
        "ok": True,
        "name": name,
        "version": record.get("version"),
        "items": _conf_items(graph, conf),
        "thresholds": {"auto": AUTO_MIN, "shadow_low": SHADOW_LOW},
    }


def intervene(store: GraphStore, name: str, automation_id: str) -> dict[str, Any]:
    if name in _ALL_NAMES:
        # 找到包含该 automation 的归档，干预落实到那个归档
        for archive in _archive_names(store):
            try:
                graph = store.load(archive)
            except (FileNotFoundError, IRValidationError, OSError):
                continue
            if automation_id in {a.id for a in graph}:
                name = archive
                break
        else:
            raise KeyError(automation_id)

    record = store.load_record(name)
    graph = load_graph(record["graph"])
    if automation_id not in {a.id for a in graph}:
        raise KeyError(automation_id)
    conf = _conf_of(store, name, graph)
    conf.record_negative(automation_id)
    store.save_conf(conf, name, note=f"manual intervene: {automation_id}")
    return {
        "ok": True,
        "name": name,
        "items": _conf_items(graph, conf),
        "thresholds": {"auto": AUTO_MIN, "shadow_low": SHADOW_LOW},
    }


# ─────────────────────────────────────────────────────────────────────
# 8. 版本 diff（G6）
# ─────────────────────────────────────────────────────────────────────


def _node_obj(address: str) -> dict[str, Any]:
    """`{automation_id}:{node_id}` → 结构化对象（供前端渲染）。"""
    automation_id, _, node_id = address.partition(":")
    return {"node_id": address, "address": address, "automation_id": automation_id, "node": node_id}


def _edge_obj(text: str) -> dict[str, Any]:
    """`{automation_id}:{from}->{to}:{kind}` → 结构化对象。"""
    automation_id, _, rest = text.partition(":")
    from_, _, tail = rest.partition("->")
    to, _, kind = tail.partition(":")
    return {"automation_id": automation_id, "from": from_, "to": to, "kind": kind, "edge": text}


def _record_note(store: GraphStore, name: str, version: int) -> str:
    """读取某版本的**归档备注**。

    `note` 属于归档记录元数据（`GraphStore.save(note=...)`），**不在 Graph 内**，
    因此不参与 `diff_graphs` 的图内容比对；此处单独回传，供前端在
    "内容无差异" 时说明"只是归档备注不同"。
    """
    try:
        return str(store.load_record(name, version).get("note", "") or "")
    except (OSError, ValueError, KeyError):
        return ""


def diff(store: GraphStore, name: str, old: int, new: int) -> dict[str, Any]:
    old_graph = store.load(name, old)
    new_graph = store.load(name, new)
    result = diff_graphs(old_graph, new_graph)
    return {
        "ok": True,
        "name": name,
        "old": old,
        "new": new,
        "render": result.render(),
        "notes": {
            "old": _record_note(store, name, old),
            "new": _record_note(store, name, new),
        },
        "structured": {
            "added_automations": result.added_automations,
            "removed_automations": result.removed_automations,
            "added_nodes": [_node_obj(a) for a in result.added_nodes],
            "removed_nodes": [_node_obj(a) for a in result.removed_nodes],
            "changed_nodes": [
                {"node_id": a, "address": a, "changes": f} for a, f in result.changed_nodes
            ],
            # v1.3.0：id 重命名（签名兜底配对），避免 Agent 改个 node id 就被误报成删+加
            "renamed_nodes": [
                {"from": o, "to": n, "matched_by": "signature"} for o, n in result.renamed_nodes
            ],
            "added_edges": [_edge_obj(e) for e in result.added_edges],
            "removed_edges": [_edge_obj(e) for e in result.removed_edges],
            "meta_changes": [
                {"key": f"{a}.{f}", "automation_id": a, "field": f, "old_value": o, "new_value": n}
                for a, f, o, n in result.meta_changes
            ],
        },
    }


# ─────────────────────────────────────────────────────────────────────
# 9/10. AF-Spec（G7）
# ─────────────────────────────────────────────────────────────────────


def spec_of(store: GraphStore, name: str, version: int | None = None) -> dict[str, Any]:
    graph = store.load(name, version)
    return {
        "ok": True,
        "name": name,
        "version": version if version is not None else store.latest(name),
        "spec": render_spec(graph),
    }


def compile_text(text: str) -> dict[str, Any]:
    try:
        graph = compile_spec(text)
    except (SpecError, IRValidationError) as exc:
        return {
            "ok": False,
            "ir": None,
            "nl": "",
            "diagnostics": [],
            "error": {"code": type(exc).__name__, "message": str(exc)},
        }
    result = StaticScanner(graph).scan()
    return {
        "ok": result.ok,
        "ir": _ir_payload(graph),
        "nl": render_graph(graph).text,
        "diagnostics": _diagnostics(result),
    }


# ─────────────────────────────────────────────────────────────────────
# 11. 故障注入图鉴（G5，静态元数据）
# ─────────────────────────────────────────────────────────────────────


def faults() -> dict[str, Any]:
    kinds = []
    for kind in FaultKind:
        meta = FAULT_META.get(kind.value, {})
        kinds.append(
            {
                "value": kind.value,
                "label": meta.get("label", kind.value),
                "inject_layer": meta.get("inject_layer", ""),
                "expected_handler": meta.get("expected_handler", ""),
            }
        )
    failures = [
        {
            "key": key,
            "label": info.get("label", key),
            "edge": info.get("edge", ""),
            "audit": info.get("audit", ""),
            "recoverable": info.get("recoverable", "") == "yes",  # 布尔，供前端直接进 v-if
        }
        for key, info in FOUR_FAILURES.items()
    ]
    return {"ok": True, "kinds": kinds, "failures": failures}


# ─────────────────────────────────────────────────────────────────────
# 启动引导：把 examples/ir 灌入归档（让只读控制台一开就有数据）
# ─────────────────────────────────────────────────────────────────────


def bootstrap_examples(store: GraphStore, examples_dir: str | Path) -> list[str]:
    """把样例 IR 归档进 store（幂等：同名已有版本则跳过）。返回新归档的名字。

    版本化样例：除主文件 `<name>.json`（存为 v1）外，若存在同级
    `<name>.v2.json` / `<name>.v3.json` …，会依次保存为 v2 / v3 …，
    让控制台「版本与 Diff」页开箱即可演示（R5：依赖 ≥2 版本）。
    """
    directory = Path(examples_dir)
    if not directory.is_dir():
        return []
    created: list[str] = []
    for path in sorted(directory.glob("*.json")):
        stem = path.stem
        # 跳过版本化样例（`<name>.v2.json` 等），它们由主样例 `<name>.json` 触发保存
        if ".v" in stem and stem.rsplit(".v", 1)[1].isdigit():
            continue
        name = stem
        if store.versions(name):
            continue
        try:
            graph = load_graph(path)
        except (IRValidationError, json.JSONDecodeError, OSError):
            continue
        # ADM B-06：bootstrap 样例也过静态扫描，不安全的跳过
        try:
            _scan = StaticScanner(graph, device_guard=load_device_guard(store), entity_health=load_entity_health(store)).scan()
            if not _scan.ok:
                continue
        except Exception:
            continue
        store.save(graph, name, note="bootstrap: examples/ir")
        created.append(name)
        # 版本化样例：若存在 `<name>.v2.json` / `.v3.json` … 依次存为 v2 / v3 …
        v = 2
        while True:
            vp = directory / f"{name}.v{v}.json"
            if not vp.exists():
                break
            try:
                vg = load_graph(vp)
            except (IRValidationError, json.JSONDecodeError, OSError):
                break
            # ADM B-06：版本化样例也过扫描
            try:
                _vscan = StaticScanner(vg, device_guard=load_device_guard(store), entity_health=load_entity_health(store)).scan()
                if not _vscan.ok:
                    break
            except Exception:
                break
            store.save(vg, name, note=f"bootstrap: examples/ir v{v}")
            v += 1
    return created


# ─────────────────────────────────────────────────────────────────────
# Round 2-A：会话（支撑 ask 审批的"人机回路"）
#
# `/api/sim` 是一次性回放；`ask` 需要"挂起 → 人工应答 → 继续"跨请求的状态，
# 因此引入**进程内会话**：创建后运行到挂起/终态，之后可反复 answer / tick。
# ⚠️ 会话在内存中（单进程、可配置 TTL），服务重启即丢失——原型期足够。
# ─────────────────────────────────────────────────────────────────────

#: 会话存活上限（秒），可用环境变量覆盖
#: 走 af_env 的 fail-safe 解析：裸 float()/int() 会让写错的 env 在 import 期抛，
#: 整个包起不来（新增审计 BUG-10）。
SESSION_TTL_S = env_number("AUTOFORGE_SESSION_TTL_S", 3600.0, lo=0.0)

#: 在途会话硬上限。TTL 只管"活了多久"，这一条管"同时有多少份 Runtime 常驻"。
SESSION_MAX = env_int("AUTOFORGE_SESSION_MAX", 128, lo=1)

_SESSIONS: dict[str, dict[str, Any]] = {}
_SESSIONS_LOCK = threading.Lock()


def _build_sim_runtime(graph: Graph, seed: Mapping[str, str] | None):
    """装配"仿真运行态"：FakeHA 状态源 + 会真改状态的 ha 适配器。"""
    runtime = build_runtime(graph)
    states = seed_from_graph(graph, seed or {}, clock=runtime.clock)
    runtime.states = states
    runtime.instances.states = states
    runtime.scheduler.states = states
    runtime.executor.states = states  # canary 漂移检测读同一份状态源
    runtime.adapters.register(FakeHAAdapter(states))
    return runtime, states


def _asks_of(runtime: Runtime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for session in runtime.executor.pending_asks.values():
        try:
            automation_id = runtime.instances.get(session.instance_id).ctx.automation_id
        except KeyError:  # pragma: no cover - 防御
            automation_id = ""
        spec = session.ask_spec
        out.append(
            {
                "ask_id": session.instance_id,  # 一个实例同一时刻只有一个挂起 ask
                "instance_id": session.instance_id,
                "node_id": session.node_id,
                "room": session.room,
                "prompt": session.prompt,
                "automation_id": automation_id,
                "spec": spec.to_dict() if spec is not None else None,
                # 自由文本 ask（无 spec）也给控件元数据，前端统一消费
                "control": (
                    spec.control()
                    if spec is not None
                    else {"widget": "input", "kind": "text", "prompt": session.prompt}
                ),
            }
        )
    return out


def asks_pending() -> dict[str, Any]:
    """v2 M3：聚合所有在途会话的挂起 ask，含 `spec` 与控件 `control` 元数据。

    与 `/api/asks/pending`（watch sidecar，进程间发现用）不同，本函数读**进程内
    会话**——仿真会话下的挂起 ask 同样可见（sidecar 只在 watch 真机模式写出）。
    含 prompt/room 等敏感信息，端点需 read 鉴权。
    """
    _purge_sessions()
    with _SESSIONS_LOCK:
        sessions = list(_SESSIONS.items())
    out: list[dict[str, Any]] = []
    for session_id, sess in sessions:
        runtime: Runtime = sess["runtime"]
        for ask in _asks_of(runtime):
            item = dict(ask)
            item["session_id"] = session_id
            out.append(item)
    return {"ok": True, "total": len(out), "asks": out}


def _session_view(sess: Mapping[str, Any]) -> dict[str, Any]:
    runtime: Runtime = sess["runtime"]
    return {
        "ok": True,
        "session_id": sess["id"],
        "mode": sess.get("mode", "sim"),
        "instances": [i.to_dict() for i in runtime.instances.all()],
        "audit": [e.to_dict() for e in runtime.audit],
        "bus": runtime.bus.stats(),
        "final_states": {e: sess["states"].get(e) for e in sorted(sess["entities"])},
        "nl": render_graph(sess["graph"]).text,
        "asks": _asks_of(runtime),
    }


def _purge_sessions() -> None:
    """先按 TTL 清，再把总量压回水位线之下。

    只做 TTL 不够：峰值内存仍等于「创建速率 × SESSION_TTL_S」，而一个会话挂着
    Runtime + FakeHA + Graph——一轮批量仿真就能把上千份常驻住（第六轮审计 R6-F1）。
    同族的 af_audit / af_preference / af_bus 都是"超时 + 硬上限"两条腿，这里补齐。
    """
    now = time.monotonic()
    with _SESSIONS_LOCK:
        for sid in [k for k, v in _SESSIONS.items() if now - float(v["created"]) > SESSION_TTL_S]:
            _SESSIONS.pop(sid, None)
        overflow = len(_SESSIONS) - SESSION_MAX
        if overflow > 0:
            # 最老的先走：正在等人工应答的会话被挤掉后拿到的是 404「可能已过期」，
            # 这比整进程 OOM 轻——所以水位线要留足，不是拿来当流控用的。
            oldest = sorted(_SESSIONS.items(), key=lambda kv: float(kv[1]["created"]))[:overflow]
            for sid, _sess in oldest:
                _SESSIONS.pop(sid, None)


def _get_session(session_id: str) -> dict[str, Any]:
    _purge_sessions()
    with _SESSIONS_LOCK:  # 读也要上锁：清/插两条写路径都在锁里，读侧例外就是读到半截
        sess = _SESSIONS.get(session_id)
    if sess is None:
        raise ServiceError(f"未找到会话 {session_id!r}（可能已过期或服务已重启）", status=404)
    return sess


def create_session(
    ir: Mapping[str, Any],
    seed: Mapping[str, str] | None = None,
    events: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """创建会话：回放事件并停在挂起点/终态，返回含 `asks` 的状态视图。"""
    _check_events_cap(events)
    # 第六轮审计 R6-F1：清理原本只挂在读路径上，纯"创建后不再读"的会话会一直常驻。
    # 创建前先清一次，硬上限才真的封得住峰值。
    _purge_sessions()
    graph = _load_ir(ir)
    runtime, states = _build_sim_runtime(graph, seed)
    _replay(runtime, states, list(events or []))
    session_id = secrets.token_hex(16)  # ADM C-10: 128-bit entropy (was 48-bit)
    sess = {
        "id": session_id,
        "runtime": runtime,
        "states": states,
        "graph": graph,
        "entities": _entities_of(graph),
        "created": time.monotonic(),
        "mode": "sim",
    }
    with _SESSIONS_LOCK:
        _SESSIONS[session_id] = sess
    return _session_view(sess)


def list_sessions() -> dict[str, Any]:
    _purge_sessions()
    with _SESSIONS_LOCK:
        ids = sorted(_SESSIONS)
    return {"ok": True, "session_ids": ids, "ttl_s": SESSION_TTL_S}


def get_session(session_id: str) -> dict[str, Any]:
    return _session_view(_get_session(session_id))


def answer_session(
    session_id: str,
    text: str = "",
    ask_id: str | None = None,
    room: str | None = None,
    answer: "Mapping[str, Any] | None" = None,
) -> dict[str, Any]:
    """WO-AF-002：人工应答。统一走 executor.resolve_ask，ask_id 非空且不匹配时 404，绝不 fallback。

    v2 M3：answer 为结构化 AskAnswer dict 时走 answer_structured（缺省即拒）；否则走自由文本 classify。
    """
    sess = _get_session(session_id)
    runtime: Runtime = sess["runtime"]

    ask = runtime.executor.resolve_ask(ask_id, room)
    if ask is None:
        if ask_id:
            raise ServiceError(f"ask_id={ask_id!r} 不匹配任何挂起 ask（绝不 fallback 到最旧）", status=404)
        raise ServiceError("没有匹配的待应答 ask（可能已超时或被取消）", status=404)

    instance = runtime.instances.get(ask.instance_id)
    if instance is None:
        raise ServiceError("ask 对应的实例不存在", status=404)
    if answer is not None:
        # 结构化应答：合法 → 落值 + then 边恢复；不合法 → 422，会话保持挂起可重答
        # （ask 已确认存在，故 validate 失败只可能是校验被拒，不是找不到）
        if not runtime.executor.validate_answer(room=room, payload=answer, ask_id=ask_id):
            raise ServiceError("结构化应答未通过校验（缺省即拒，会话保持挂起可重答）", status=422)
        runtime.executor.answer_structured(room=room, payload=answer, ask_id=ask_id)
    else:
        runtime.executor.resume(instance, classify_answer(text))
    return _session_view(sess)


def tick_session(session_id: str, advance_s: float) -> dict[str, Any]:
    """推进虚拟时钟（用于演示 `on_timeout`）。"""
    sess = _get_session(session_id)
    sess["runtime"].advance(float(advance_s))
    return _session_view(sess)


def cancel_session(session_id: str, reason: str = "") -> dict[str, Any]:
    sess = _get_session(session_id)
    runtime: Runtime = sess["runtime"]
    for instance in [i for i in runtime.instances.all() if not i.is_terminal]:
        runtime.cancel(instance, reason or "人工取消")
    return _session_view(sess)


def delete_session(session_id: str) -> dict[str, Any]:
    with _SESSIONS_LOCK:
        existed = _SESSIONS.pop(session_id, None) is not None
    if not existed:
        raise ServiceError(f"未找到会话 {session_id!r}", status=404)
    return {"ok": True, "session_id": session_id, "deleted": True}


# ─────────────────────────────────────────────────────────────────────
# Round 2-B：真机下发（受闸门保护的真实设备写入）
#
# 安全模型（三重闸，缺一不可）：
#   1. 服务端开关 `AUTOFORGE_LIVE_ENABLED=1`（默认**关闭**）
#   2. 服务端令牌 `AUTOFORGE_HA_TOKEN`（**绝不从请求体接收令牌**）
#   3. 请求体 `confirm=true` + 非空 `live_allow` 白名单，且 IR 的写目标 ⊆ 白名单
# 复用 `af_scanner.live_preflight` 的语义（与 CLI `forge run --live` 一致口径）。
# ─────────────────────────────────────────────────────────────────────

#: 测试可注入的 transport 工厂：`(ha_url, token) -> transport`；默认 None 表示用真实 HATransport
LIVE_TRANSPORT_FACTORY: Callable[[str, str], Any] | None = None


def _live_config() -> dict[str, Any]:
    return {
        "enabled": os.getenv("AUTOFORGE_LIVE_ENABLED", "").strip().lower() in ("1", "true", "yes"),
        "ha_url": os.getenv("AUTOFORGE_HA_URL", DEFAULT_HA_URL),
        "has_token": bool(os.getenv("AUTOFORGE_HA_TOKEN", "")),
    }


def live_status() -> dict[str, Any]:
    """真机下发的可用性（供前端渲染开关/禁用原因）。"""
    cfg = _live_config()
    reasons: list[str] = []
    if not cfg["enabled"]:
        reasons.append("服务端未启用真机下发（需设置 AUTOFORGE_LIVE_ENABLED=1）")
    if not cfg["has_token"]:
        reasons.append("服务端缺少 HA 令牌（需设置 AUTOFORGE_HA_TOKEN）")
    return {
        "ok": True,
        "enabled": cfg["enabled"] and cfg["has_token"],
        "ha_url": cfg["ha_url"],
        "has_token": cfg["has_token"],
        "allowlist_required": True,
        "confirm_required": True,
        "reasons": reasons,
    }


def _replay_live(runtime: Runtime, events: Sequence[Mapping[str, Any]]) -> None:
    """真机回放：只发布事件/推进时间，**不写本地状态**（真实状态由 HA 提供）。"""
    for item in events:
        advance = item.get("advance_s")
        if advance:
            secs = float(advance)
            if secs < 0 and hasattr(runtime.clock, "jump"):
                runtime.clock.jump(secs)
                runtime.tick()
            else:
                runtime.advance(secs)
        entity_id = item.get("entity_id")
        if entity_id:
            runtime.emit(str(entity_id), str(item.get("state", "")), last_changed=item.get("last_changed"))
        elif advance is None:
            runtime.tick()


def _live_final_states(
    provider, entities: Sequence[str]
) -> tuple[dict[str, Any], list[str]]:
    """取真机终态用于**报告**：缺哪个实体就显式列出来，不编值、也不把请求打崩。

    第七轮审计把 `StateProvider` 统一到 fail-closed（未知实体 → 抛 `UnknownEntity`），
    而这里是 `live_run` 返回体的读取点：动作**已经下发**、撤销句柄**已经落盘**。让它
    直接抛，等于一次成功部署返回 500，操作员连 `undo_deploy_id` 都拿不到——比"某个
    实体读不到"严重得多。所以异常在**边界**翻译成 `missing_entities`（值记 None），
    provider 侧的口径不动。

    每轮至少剔掉一个实体，所以循环必然收敛（正常路径只取一次快照）。
    """
    final: dict[str, Any] = {e: None for e in entities}
    missing: list[str] = []
    pending = list(entities)
    while pending:
        try:
            snap = provider.snapshot(pending)
        except UnknownEntity as exc:
            drift = str(exc.args[0]) if exc.args and str(exc.args[0]) in pending else pending[0]
            missing.append(drift)
            pending.remove(drift)
            continue
        final.update({e: snap.values.get(e) for e in pending})
        break
    return final, sorted(missing)


READONLY_DEGRADED_PREFIX = "READONLY_DEGRADED:"


def _single_writer_check(store: "GraphStore | None") -> None:
    """生产 serve 持租约时，**进程外**的写入口必须先拒（裁定 20261004 §一 1 A）。

    只 check 不 acquire：MCP 面不抢锁、不改归属，探测也不写 sidecar（盖了章就会把 serve
    的持有者诊断覆盖成自己）。判据只认内核锁；serve 自己那份由 `af_flock._LOCAL_HELD`
    认出"是本进程"，于是 503 不会打回给正在正常服务的写者本身。
    """
    root = Path(store.root) if store is not None else DEFAULT_STORE_ROOT
    if FileLock(serve_lock_path(root)).held_by_other():
        raise ServiceError(
            f"{READONLY_DEGRADED_PREFIX} 生产 serve 正持有单写者租约（{root}）：本次真机下发拒收。"
            "同一个 store 根目录上 HTTP 面已降级只读，MCP 面不抢锁、只读它（裁定 20261004 §一 1 A）",
            status=503,
        )


def live_run(
    ir: Mapping[str, Any],
    live_allow: Sequence[str],
    events: Sequence[Mapping[str, Any]] | None = None,
    confirm: bool = False,
    store: "GraphStore | None" = None,
    undo: bool = False,
    clock: Any | None = None,
) -> dict[str, Any]:
    """受闸门的真机下发：三重闸全通过才执行。

    安全约定（P0-4 修复）：
    - **必须先过 StaticScanner**，ERROR 级诊断硬拒绝（未扫描即拒绝）
    - `allow` 只能做**减法**（从扫描器算出的可写集合中再收窄），不能扩权
    - `target/device_id/area_id` 形式的写目标必须被展开后再分级（P0-5）
    - `undo=true`（或 `AUTOFORGE_UNDO=1`）时记录动作前快照并返回 `undo_deploy_id`，
      WebUI / `forge undo` 才能在窗口内撤销本次部署（决策 E）

    时间轴：真机路径默认锚在**家庭墙钟的"现在"**，不是 `build_runtime` 的仿真锚点
    （2026-09-14 08:00）——否则一次真实下发的 `audit[].at` 与 canary 证据 `at` 都落在仿真时间里，
    `at:19:30` 这类 time 触发也按锚点判定，与 `af_cli._make_runtime` 的 live/dry-live 分支口径相反。
    这里仍是**可推进的虚拟钟**而非 `SystemTimeSource`：`_replay_live` 的 `advance_s` 要能前跳/回跳。
    需要确定性的调用方显式传 `clock=`。
    """
    # 形状判据先于策略判据：超上限的请求在"live 未启用"的环境里也必须是 422，
    # 否则同一个请求在两套环境里给出两个理由（HTTP 面此前就是靠先判拿到的 422）。
    _check_events_cap(events)

    cfg = _live_config()
    if not cfg["enabled"]:
        raise ServiceError("真机下发未启用：服务端需设置 AUTOFORGE_LIVE_ENABLED=1", status=403)
    if not cfg["has_token"]:
        raise ServiceError("真机下发缺少 HA 令牌：服务端需设置 AUTOFORGE_HA_TOKEN", status=403)
    if not confirm:
        raise ServiceError("真机下发必须显式二次确认（confirm=true）", status=403)

    allow = {str(x) for x in (live_allow or []) if str(x)}
    if not allow:
        raise ServiceError("真机下发必须提供可写白名单（live_allow），先不开放全量", status=400)

    # ★ 单写者租约：生产 serve 在线时，进程外的真机写一律拒收（裁定 20261004 §一 1 A）。
    # 放在构造传输层之前——拒收的那条路径必须**根本不碰设备**。
    _single_writer_check(store)

    graph = _load_ir(ir)

    # ★ 第一道闸：静态扫描（P0-4 修复：未扫描即拒绝）
    scan = StaticScanner(
        graph,
        device_guard=load_device_guard(store) if store else None,
        entity_health=load_entity_health(store) if store else None,
    ).scan()
    if not scan.ok:
        errors = [f"{d.code}: {d.message}" for d in scan.errors[:5]]
        err_text = "; ".join(errors)
        raise ServiceError(
            f"拒绝真机下发：IR 未通过静态扫描（{len(scan.errors)} 个错误）：{err_text}",
            status=400,
        )
    factory = LIVE_TRANSPORT_FACTORY
    targets: set[str] = set()
    for auto in graph:
        targets |= auto.writes()
    outside = sorted(targets - allow)
    if outside:
        raise ServiceError(f"写目标不在白名单内：{outside}", status=400)

    token = os.getenv("AUTOFORGE_HA_TOKEN", "")
    ha_url = str(cfg["ha_url"])
    if factory is not None:
        transport = factory(ha_url, token)
        provider = HAStateProvider(transport=transport)
    else:
        transport = HATransport(base_url=ha_url, token=token)
        provider = HAStateProvider(transport=transport)

    if clock is None:
        clock = VirtualTimeSource(SystemTimeSource().now())
    runtime = build_runtime(graph, clock=clock)
    runtime.states = provider
    runtime.instances.states = provider
    runtime.scheduler.states = provider
    runtime.executor.states = provider
    adapter = HAAdapter(transport=transport, dry_run=False)
    undo_enabled = bool(undo) or os.getenv("AUTOFORGE_UNDO", "") == "1"
    undo_id = ""
    undo_store: UndoStore | None = None
    if undo_enabled:
        undo_store = _undo_store(store.root if store else None)
        undo_id = new_deploy_id()

        def _record_pre_snapshot(action, params, pre, _s=undo_store, _d=undo_id):
            _s.record_merge(_d, pre)

        adapter.undo_recorder = _record_pre_snapshot
    runtime.adapters.register(adapter)

    _replay_live(runtime, list(events or []))

    entities = sorted(_entities_of(graph))
    final_states, missing_entities = _live_final_states(provider, entities)
    logger.warning(
        "LIVE RUN：automation(s)=%s writes=%s events=%d",
        [a.id for a in graph],
        sorted(targets),
        len(events or []),
    )
    return {
        "ok": True,
        "mode": "live",
        "live": True,
        "ha_url": ha_url,
        "allowlist": sorted(allow),
        "writes": sorted(targets),
        # F7：只有真的写下了动作，才存在可撤销的快照（没落快照就不承诺可撤）
        "undo_enabled": undo_enabled,
        "undo_deploy_id": undo_id if (undo_store is not None and undo_store.exists(undo_id)) else None,
        "undo_window_s": undo_store.window_s if undo_store is not None else None,
        "instances": [i.to_dict() for i in runtime.instances.all()],
        "audit": [e.to_dict() for e in runtime.audit],
        "bus": runtime.bus.stats(),
        "final_states": final_states,
        # 漂移在这里**报告**而不是把请求打崩：动作已经下发、undo_deploy_id 已经落盘，
        # 500 会让操作员连撤销把手都拿不到（口径见 `_live_final_states`）。
        "missing_entities": missing_entities,
        "nl": render_graph(graph).text,
        "asks": _asks_of(runtime),
    }


# ─────────────────────────────────────────────────────────────────────
# F7 残留（WebUI 撤销按钮）：撤销的 HTTP 服务面。
#   撤销 = 对真实设备**再下发一次**，所以服务端闸门口径与 `live_run` 完全对齐：
#   ① `AUTOFORGE_LIVE_ENABLED=1`；② 令牌只从服务端 `AUTOFORGE_HA_TOKEN` 取，绝不从请求体收。
#   时间窗 / 风险域二次确认 / 未知 deploy_id 这些**判定仍归 `af_undo.UndoStore`**
#   （单一真值源，本层不重算，只转发并把结果结构化给前端）。
# ─────────────────────────────────────────────────────────────────────

def _undo_store(store_root: "str | Path | None") -> UndoStore:
    return UndoStore(str(store_root) if store_root else DEFAULT_STORE_ROOT)


def undo_available(store_root: "str | Path | None" = None) -> dict[str, Any]:
    """窗口内仍可撤销的部署清单（前端据此决定按钮是否可点）。

    不返 `ok`：查询成功与否由 HTTP 状态表达，判据是 `items`/`window_s` 本身
    （字面量 `ok=True` 会被 AST 门禁判成 fake-ok）。
    """
    store = _undo_store(store_root)
    return {"window_s": store.window_s, "items": store.available()}


def undo_preview(deploy_id: str, store_root: "str | Path | None" = None) -> dict[str, Any]:
    """撤销前盘点：窗口/风险域/实体清单，判定口径与 revert 同源（`exists`/`undoable`）。"""
    return dict(_undo_store(store_root).inspect(deploy_id))


# ─────────────────────────────────────────────────────────────────────
# F4：生产态验证证据的只读面（`af_watch` 进程内聚合）
# ─────────────────────────────────────────────────────────────────────

def watch_summary() -> dict[str, Any]:
    """`verified_in_prod` 分区 + summary，供监护视图直接渲染。

    三档一起给（verified / failed / unmodeled）：只报 verified 会让「生产验过 10 次
    全 MISS」和「一次都没跑过」读数相同（铁律 #5）。同样**不返字面量 `ok`**——
    查询成功与否由 HTTP 状态表达，判据是 `automations`/`summary` 本身。
    """
    from autoforge.af_watch import verified_in_prod_partition

    return dict(verified_in_prod_partition())


def undo_deploy(
    deploy_id: str,
    confirm: bool = False,
    store_root: "str | Path | None" = None,
) -> dict[str, Any]:
    """受闸门撤销：真机回放动作前快照（决策 E）。

    被 `UndoStore.revert` 拒绝（未知/过期/风险域未确认）时**不抛异常**，
    原样返回 `{ok: false, reason, …}`——前端要渲染的是"哪几个实体需要二次确认"，
    不是笼统的 4xx。
    """
    cfg = _live_config()
    if not cfg["enabled"]:
        raise ServiceError("撤销未启用：真机回滚需要服务端设置 AUTOFORGE_LIVE_ENABLED=1", status=403)
    if not cfg["has_token"]:
        raise ServiceError("撤销缺少 HA 令牌：服务端需设置 AUTOFORGE_HA_TOKEN", status=403)

    store = _undo_store(store_root)
    token = os.getenv("AUTOFORGE_HA_TOKEN", "")
    ha_url = str(cfg["ha_url"])
    factory = LIVE_TRANSPORT_FACTORY
    transport = factory(ha_url, token) if factory is not None else HATransport(base_url=ha_url, token=token)
    adapter = HAAdapter(transport=transport, dry_run=False)

    logger.warning("UNDO：deploy_id=%s confirm=%s ha_url=%s", deploy_id, confirm, ha_url)
    result = store.revert(deploy_id, adapter, confirm=confirm)
    result["results"] = [
        {"action": r.data.get("action"), "success": r.success, "data": r.data, "error": r.error}
        for r in result.get("results", [])
    ]
    result["ha_url"] = ha_url
    return result


# ─────────────────────────────────────────────────────────────────────
# v0.7.0 模板导出与备份恢复
# ─────────────────────────────────────────────────────────────────────


def export_store(store: GraphStore) -> dict[str, Any]:
    """导出整个 store 为可携带 bundle（含 tags，依赖 v0.6.0）。

    返回可直接 JSON 化的 dict（GraphStore.export_bundle 的形态）。
    """
    bundle = store.export_bundle()
    return {
        "ok": True,
        "format": bundle.get("format"),
        "version": bundle.get("version"),
        "checksum": bundle.get("checksum"),
        "exported_at": bundle.get("exported_at"),
        "names": [e["name"] for e in bundle.get("entries", [])],
        "bundle": bundle,
    }


def _bundle_automation_count(bundle: Mapping[str, Any]) -> int:
    """估算 bundle 会带来多少条自动化（取每个归档的**最新版本**计一次）。"""
    total = 0
    for entry in bundle.get("entries") or ():
        versions = entry.get("versions") or ()
        if not versions:
            continue
        graph = versions[-1].get("graph") or {}
        if isinstance(graph, Mapping) and isinstance(graph.get("automations"), list):
            total += len(graph["automations"])
        elif isinstance(graph, Mapping) and graph.get("id"):
            total += 1
    return total


def _existing_automation_count(store: GraphStore, name: str) -> int:
    """R-21: overwrite 会删掉的存量归档里有多少条自动化（分母含存量）。"""
    try:
        graph = store.load(name)
    except Exception:
        return 0
    autos = graph.get("automations") if isinstance(graph, Mapping) else None
    if isinstance(autos, list):
        return len(autos)
    if isinstance(graph, Mapping) and graph.get("id"):
        return 1
    return 0


def import_store(
    store: GraphStore,
    bundle: Mapping[str, Any],
    strategy: str = "skip",
    allow_bulk: bool = False,
    owner: str = "",
) -> dict[str, Any]:
    """导入 bundle（v0.7.0）。冲突策略 skip/overwrite/rename，见 GraphStore.import_bundle。

    返回导入报告 {imported, skipped, renamed, errors, residual_backups}。最后一格是
    「新归档已落盘、但 overwrite 的让位备份没能回收」的如实读数（`import_bundle` 里写清语义：
    它不进 `errors`，因为导入这件事确实做成了）。

    v1.3.0：默认受**爆炸半径**约束。导入天然是批量动作，故**常见做法是显式传
    `allow_bulk=True`**——但默认关着，能让「误导入整个 bundle」这类事故先被拦一下。

    R20-01：加 `owner` 参数并下传 store.import_bundle——overwrite 时与 save_graph
    对称做所有权隔离校验，导入后记录携带归属主体。
    """
    count = _bundle_automation_count(bundle)
    clobbered = 0
    if strategy == "overwrite":
        existing_names = set(store.names())
        for entry in bundle.get("entries") or ():
            nm = str(entry.get("name", ""))
            if nm and nm in existing_names:
                clobbered += _existing_automation_count(store, nm)
    affected = count + clobbered
    if not allow_bulk:
        note = f"（含覆盖销毁存量 {clobbered} 条）" if clobbered else ""
        _check_blast(affected, f"本次导入含约 {affected} 条自动化{note}")
    report = store.import_bundle(bundle, strategy, owner=owner)
    if isinstance(report, dict):
        report.setdefault("blast_radius", {
            "affected": affected, "incoming": count, "clobbered": clobbered,
            "limit": blast_radius_limit(),
        })
    return report


# ─────────────────────────────────────────────────────────────────────
# v0.5.0 运行指标（回灌 MA 的数据源）
# ─────────────────────────────────────────────────────────────────────


def get_metrics(store: GraphStore) -> dict[str, Any]:
    """聚合运行指标，输出与 `MetricsAggregator.snapshot` 同构的 dict。

    原型期服务层为无状态（每次请求建临时 runtime），故此处以**持久化置信度**为正源
    （conf 维度真实），执行次数 / 成功率 / 审计分布需常驻进程（forge serve / watch）
    才累计，此处暂置 0/空。`GET /api/metrics` 与 CLI `forge metrics push` 共用此结构。
    """
    per_auto: dict[str, Any] = {}
    for record in store.history():
        name = record.get("name", "")
        if not name:
            continue
        try:
            conf = store.load_conf(name)
        except FileNotFoundError:
            continue
        for aid, val in conf.values.items():
            band = decision_for(val)
            per_auto[aid] = {
                "id": aid,
                "runs": 0,
                "success": 0,
                "failed": 0,
                "success_rate": None,
                "audit_distribution": {},
                "confidence": {"value": val, "band": band},
                "last_event_at": None,
            }
    return {
        "generated_at": SystemTimeSource().local_now().isoformat(),
        "source": "conf-store",
        "overall": {
            "total_runs": 0,
            "total_success": 0,
            "total_failed": 0,
            "overall_success_rate": None,
            "audit_distribution": {},
        },
        "automations": per_auto,
    }


# ─────────────────────────────────────────────────────────────────────
# v1.1.0 实体事实内建（设备目录 + 解析）
#
# 目的：把「自然语言设备名 → 真实 entity_id」做进平台自身，**切断对 MA 的硬依赖**。
# 与 MA 分工不重叠：AF 回答「执行事实」（ID/域/可能状态/当前状态），
# MA 回答「语义身份」（谁在家/习惯/历史）。
# ─────────────────────────────────────────────────────────────────────


def _catalog(store: GraphStore, **kwargs: Any) -> DeviceCatalog:
    """从 store 根目录构造设备目录（缓存与归档同根，便于整体搬运）。"""
    return DeviceCatalog(store.root, **kwargs)


def catalog_refresh(
    store: GraphStore,
    *,
    full: bool = True,
    domain: str = "",
    area: str = "",
) -> dict[str, Any]:
    """从 HA 重新快照全屋设备进本地缓存（之后解析毫秒级返回）。"""
    return _catalog(store).refresh(full=full, domain=domain, area=area)


def catalog_resolve(
    store: GraphStore,
    name: str,
    *,
    area: str = "",
    domain: str = "",
    top_n: int = 8,
) -> dict[str, Any]:
    """自然语言设备名 → Top-N 候选 `entity_id`（写 IR 前必调）。"""
    return _catalog(store).resolve(name, area=area, domain=domain, top_n=top_n)


def catalog_list(
    store: GraphStore,
    *,
    domain: str = "",
    area: str = "",
    keyword: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """全屋实体目录·过滤浏览（强制分页 + 透明截断回报）。"""
    return _catalog(store).list_entities(domain=domain, area=area, keyword=keyword, limit=limit, offset=offset)


def catalog_state(store: GraphStore, entity_id: str) -> dict[str, Any]:
    """单实体状态（实时优先，失败回退目录缓存并标注）。"""
    return _catalog(store).get_state(entity_id)


def catalog_snapshot(store: GraphStore) -> dict[str, Any]:
    """目录摘要（按域统计 + 区域列表 + 新鲜度；不 dump 全量实体）。"""
    return _catalog(store).snapshot()


def catalog_resolve_metrics(store: GraphStore) -> dict[str, Any]:
    """v1.5.0：解析成功率漏斗（五档 exact/medium/low/ambiguous/none）。"""
    return _catalog(store).resolve_metrics()


def catalog_set_alias(store: GraphStore, name: str, entity_id: str) -> dict[str, Any]:
    """v1.6.0 P0：沉淀「设备名 → entity_id」精确映射（下次解析直中，high 置信）。"""
    return _catalog(store).set_alias(name, entity_id)


def catalog_list_aliases(store: GraphStore) -> dict[str, Any]:
    """v1.6.0 P0：列出已沉淀的别名映射。"""
    return _catalog(store).list_aliases()


def catalog_remove_alias(store: GraphStore, name: str) -> dict[str, Any]:
    """v1.6.0 P0：删除某条别名映射。"""
    return _catalog(store).remove_alias(name)


# ─────────────────────────────────────────────────────────────────────
# v1.6.0（决策 3）：可选 binding —— 设备描述占位符 → 真实 entity_id
# ─────────────────────────────────────────────────────────────────────

#: 占位符前缀：`entity_id` 以 `?` 开头表示「待绑定的设备描述」。
BIND_PREFIX = "?"
#: 占位符形态：`?书房吊灯` 或 `?light:书房吊灯`（可选域收窄）
_PLACEHOLDER_RE = re.compile(r"^\?(?:(?P<domain>[a-z_][a-z0-9_]*):)?(?P<name>.+)$")


def _bind_target(catalog: Any, placeholder: str) -> tuple[str, str, str]:
    """解析一个占位符 → `(entity_id, matched_by, reason)`；未定 → `("", "", reason)`。"""
    match = _PLACEHOLDER_RE.match(str(placeholder).strip())
    if not match:
        return "", "", "占位符格式非法（应为 ?设备名 或 ?domain:设备名）"
    domain = match.group("domain") or ""
    name = (match.group("name") or "").strip()
    if not name:
        return "", "", "占位符缺设备名"
    res = catalog.resolve(name, domain=domain)
    cands = res.get("candidates", []) or []
    if not cands:
        return "", "", "无候选（目录未刷新或设备名有误）"
    # fail-closed 纪律（同 resolve_best）：唯一候选 / top=high 才自动采纳
    if len(cands) == 1 or cands[0].get("confidence") == "high":
        return str(cands[0]["entity_id"]), str(cands[0].get("matched_by", "")), ""
    return "", "", (
        "歧义（{} 个候选：{}…）需明确指定".format(
            len(cands), ", ".join(str(c.get("entity_id")) for c in cands[:3])
        )
    )


def bind_ir(store: GraphStore, ir: Mapping[str, Any]) -> dict[str, Any]:
    """v1.6.0（决策 3）：把 IR 里的「设备描述占位符」解析回填为真实 entity_id。

    - **可选**（不强制）：不启用时 IR 必须写精确 entity_id（行为与 v1.1.0 一致，
      保住「离线编写 IR」与 G7 AF-Spec 零有损往返）；
    - 占位符约定：`entity_id` 以 `?` 开头，形如 `?书房吊灯` / `?light:书房吊灯`；
    - **fail-closed 纪律不变**：歧义 / 无候选 → **不回填**（保留占位符），
      交由既有安全闸（未知实体）拒编译；**绝不静默猜域/猜实体**。
    """
    catalog = _catalog(store)
    payload = json.loads(json.dumps(dict(ir), ensure_ascii=False, default=str))
    bound: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    def _try(node_id: str, field: str, value: Any) -> Any:
        if not isinstance(value, str) or not value.startswith(BIND_PREFIX):
            return value
        eid, matched, reason = _bind_target(catalog, value)
        if eid:
            bound.append(
                {"node": node_id, "field": field, "from": value, "to": eid, "matched_by": matched}
            )
            return eid
        unresolved.append({"node": node_id, "field": field, "from": value, "reason": reason})
        return value

    items = payload.get("automations")
    if not isinstance(items, list):
        items = [payload]
    for auto in items:
        if not isinstance(auto, dict):
            continue
        for node in auto.get("nodes", []) or []:
            if not isinstance(node, dict):
                continue
            nid = str(node.get("id", ""))
            trigger = node.get("trigger")
            if isinstance(trigger, dict) and "entity_id" in trigger:
                trigger["entity_id"] = _try(nid, "trigger.entity_id", trigger["entity_id"])
            params = node.get("params")
            if isinstance(params, dict) and "entity_id" in params:
                raw = params["entity_id"]
                if isinstance(raw, list):
                    params["entity_id"] = [_try(nid, "params.entity_id", r) for r in raw]
                else:
                    params["entity_id"] = _try(nid, "params.entity_id", raw)
        for exp in auto.get("expect", []) or []:
            if isinstance(exp, dict) and "entity_id" in exp:
                exp["entity_id"] = _try("", "expect.entity_id", exp["entity_id"])

    return {
        "ok": not unresolved,
        "ir": payload,
        "bound": bound,
        "unresolved": unresolved,
        "total": len(bound) + len(unresolved),
        "note": (
            "binding 完成：全部占位符已回填"
            if not unresolved
            else f"binding 未完全：{len(unresolved)} 个占位符因歧义/无候选未回填"
                 f"（fail-closed，保留占位符交由安全闸拒编译）"
        ),
    }



def _graph_summary(graph_path: str) -> dict[str, Any]:
    """从 IR 文件提取名称/触发源/动作目标，供 UI 展示。"""
    from pathlib import Path
    gp = Path(graph_path)
    if not gp.is_absolute():
        # 容器内 graph 路径是 /app/examples/...，相对路径无法定位时返回空
        gp = Path("/app") / graph_path.lstrip("/")
    try:
        data = json.loads(gp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    auto = data
    if isinstance(data, dict) and "automations" in data:
        autos = data["automations"]
        auto = autos[0] if autos else {}
    triggers: list[str] = []
    actions: list[str] = []
    for node in (auto.get("nodes") or []):
        kind = node.get("kind", "")
        if kind == "on":
            trg = node.get("trigger") or {}
            eid = trg.get("entity_id", "")
            if eid:
                triggers.append(f"{eid} → {trg.get('to', '')}")
        elif kind == "do":
            params = node.get("params") or {}
            eid = params.get("entity_id", "")
            if eid:
                actions.append(f"{node.get('action', '')} {eid}")
    return {
        "name": auto.get("name", auto.get("id", "")),
        "automation_id": auto.get("id", ""),
        "triggers": triggers,
        "actions": actions,
        "node_count": len(auto.get("nodes") or []),
    }


def list_watches(store_root: str | None = None) -> dict[str, Any]:
    """列出正在跑的 watch 实例（读 persist dir 下的 watch.lock.info sidecar）。

    watch 是独立 CLI 进程，API server 不持有其 Runtime；这里只读 sidecar 文件
    列出当前在跑的自动化，供 UI「运行中」页展示。
    """
    from pathlib import Path
    root = Path(store_root) if store_root else Path(".forge")
    watches: list[dict[str, Any]] = []
    candidates = [root] + list(root.glob("*/watch.lock.info"))
    seen: set[str] = set()
    for path in candidates:
        if path.is_dir():
            path = path / "watch.lock.info"
        if not path.is_file() or str(path) in seen:
            continue
        seen.add(str(path))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        graph_path = data.get("graph", "")
        summary = _graph_summary(graph_path) if graph_path else {}
        watches.append({
            "owner": data.get("owner", ""),
            "acquired_at": data.get("acquired_at", ""),
            "graph": graph_path,
            "ha_url": data.get("ha_url", ""),
            "sidecar": str(path),
            "name": summary.get("name", ""),
            "automation_id": summary.get("automation_id", ""),
            "triggers": summary.get("triggers", []),
            "actions": summary.get("actions", []),
            "node_count": summary.get("node_count", 0),
        })
    return {"ok": True, "watches": watches, "total": len(watches)}


#: `start_watch` 探测 sidecar 的退避表（秒）。旧实现是固定 15×1 秒：绝大多数成功启动在数百毫秒
#: 内就写出 sidecar，1 秒的粒度既让成功路径白等，又让失败路径在 Starlette 的同步线程池里占满
#: 15 秒（审计 BUG-05）。表长仍 15 次、总预算 11.9 秒——**首次探测提前到 0.1 秒**才是这一格的要点。
_WATCH_PROBE_DELAYS_S: tuple[float, ...] = (0.1, 0.2, 0.3, 0.5, 0.8) + (1.0,) * 10
_WATCH_PROBE_TOTAL_S = sum(_WATCH_PROBE_DELAYS_S)

#: `stop_watch` 发完 SIGTERM 后等待协调锁空闲的退避表（总预算 ≈4.4 秒）。
_WATCH_EXIT_WAIT_S: tuple[float, ...] = (0.1, 0.2, 0.3, 0.5, 0.8, 1.0, 1.0, 1.0)
_WATCH_EXIT_WAIT_TOTAL_S = sum(_WATCH_EXIT_WAIT_S)


def _owner_pid_from_sidecar(owner: str) -> "tuple[str, int] | None":
    """把 sidecar 的 `owner`（`{hostname}-{pid}-{uuid8}`，见 `af_flock.owner_id`）拆成 (主机, PID)。

    **从右往左拆**：主机名本身可以含 `-`（`NAS-Server` 这类），只有尾两段的形状是稳的
    ——uuid 片段 8 位十六进制、PID 纯数字。拆不出 = 身份未知 = 不许按 PID 动刀。
    """
    parts = str(owner).rsplit("-", 2)
    if len(parts) != 3:
        return None
    host, pid_str, tail = parts
    if not pid_str.isdigit() or not re.fullmatch(r"[0-9a-f]{8}", tail):
        return None
    return host, int(pid_str)


def _verified_pid(pid_file: Path, holder: Mapping[str, Any]) -> "tuple[int | None, str]":
    """读 PID 文件并核验它现在指向的**就是** sidecar 记录的那个 watcher。

    返回 `(可杀的 PID | None, 理由)`。理由永远非空，`"verified"` 是唯一可杀的那一格。
    """
    if not pid_file.exists():
        return None, "no_pid_file"
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return None, f"pid_file_unreadable({exc})"
    if not raw.isdigit():
        return None, f"pid_file_not_a_number({raw!r})"
    pid = int(raw)
    identity = _owner_pid_from_sidecar(str(holder.get("owner", "")))
    if identity is None:
        return None, "sidecar_identity_missing"
    host, owner_pid = identity
    if owner_pid != pid:
        return None, f"pid_mismatch_with_sidecar(pid_file={pid},sidecar={owner_pid})"
    if host != socket.gethostname():
        # 共享盘上的 PID 文件对**本机**没有意义：容器里的小 PID 在宿主机上是别的进程。
        return None, f"pid_belongs_to_another_host(sidecar_host={host},this_host={socket.gethostname()})"
    return pid, "verified"


def _cleanup_watch_files(*, info: Path, pid_file: Path) -> dict[str, Any]:
    """删诊断件（sidecar + PID 文件），**不碰锁文件**；失败逐条具名报出，不再 `pass`。"""
    removed: dict[str, bool] = {}
    errors: list[str] = []
    for label, path in (("sidecar", info), ("pid_file", pid_file)):
        if not path.exists():
            removed[label] = False
            continue
        try:
            path.unlink()
            removed[label] = True
        except OSError as exc:
            removed[label] = False
            errors.append(f"{label}:{type(exc).__name__}")
    return {"sidecar_removed": removed.get("sidecar", False),
            "pid_file_removed": removed.get("pid_file", False),
            "lock_file_kept": True,
            "errors": errors}


def _lock_is_free(lock: Path) -> bool:
    """协调锁现在**没有**被别的进程持有 ⇒ True（内核真值，不是猜）。

    用 `FileLock.held_by_other()`：另开一个句柄去探，拿得到就是空的。锁文件不存在也算空——
    并且**不去创建它**（探测本身不该在盘上留东西）。

    射程：只看**跨进程**的持有。`af_flock._LOCAL_HELD` 会让本进程自己持着的锁短路报"没被占"
    （那是 serve 不把"生产写者在线"读成"别人在写"的同一枚设计），所以这条不是通用的"锁有没有人
    持着"探针。`stop_watch` 用它是对的：协调锁的持有者从来是 watcher 子进程，不是本进程。
    """
    if not lock.exists():
        return True
    return not FileLock(lock, timeout=0.0).held_by_other()


def stop_watch(owner: str | None = None, store_root: str | None = None) -> dict[str, Any]:
    """停止正在跑的 watch 进程：先核验身份 → 发 SIGTERM → 等协调锁真的空闲 → 才清诊断件。

    第六轮审计 BUG-04／BUG-06 两格都收在这里，口径如下：

    - **不再 unlink `watch.lock`**。锁挂在打开的文件描述符上（`af_flock.FileLock`：POSIX
      `flock`／Windows `msvcrt.locking`），删文件从来不是释放锁的手段。删了之后新 watcher 会在
      同一路径新建一个 inode 并"拿锁成功"，而旧 watcher 仍持着旧 inode 的锁——两个 watcher 同时
      监听，正是 `WatchCoordinator` 声称要防住的局面，而且没有任何日志会记录这次失效。
    - **不再按裸 PID 动刀**。PID 会被复用，命中复用后的无关进程就是不可撤销的误杀（Windows 上
      `os.kill(pid, 15)` 等价于 `TerminateProcess`，目标连忽略的机会都没有）。身份来源用系统
      自己的信号：sidecar 的 `owner` 里就带着 `{hostname}-{pid}-{uuid8}`，两处对不上就拒杀。
    - **等目标真的退场再收尾**：发完信号后轮询协调锁是否已空闲（内核真值），仍被持着就报
      `exit_unconfirmed` 并把文件留着，让下一个 `start_watch` 还能读出持有者。
      刻意**不**升级成 SIGKILL：身份已核验，但对方可能正在写持久化，强杀会把在途写入丢在半截。
    - **清理失败必须看得见**：旧实现那句 `except OSError: pass` 让"停成功且清干净"与
      "停成功但什么都没清"对外表现逐字相同。现在 `cleanup` 那一格具名报出。

    Windows 上本函数只能走到"核验 + 发信号"：`os.kill` 在这里是强杀、没有优雅收尾可言，
    所以这条链路的真机语义在 POSIX（docker 面）上才完整——不在这里假装两边一样。
    """
    root = Path(store_root) if store_root else Path(".forge")
    lock = root / "watch.lock"
    info = root / "watch.lock.info"
    pid_file = root / "watch.pid"
    # 读当前持有者
    holder: dict[str, Any] = {}
    try:
        holder = json.loads(info.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if owner and holder.get("owner") and holder["owner"] != owner:
        return {"ok": False, "error": f"当前持有者 {holder['owner']} 与请求 {owner} 不符"}

    pid, identity = _verified_pid(pid_file, holder)
    if pid is None:
        # 没核验过就不动刀，也一个文件都不删：现场留着才读得懂为什么没停成。
        hint = (
            "watcher 是手工 `forge watch` 起的（没有 PID 文件）：请对它自己 Ctrl+C，"
            "或对持有进程发信号——服务层不再猜 PID"
            if identity == "no_pid_file"
            else "PID 文件与 sidecar 记录对不上：先按 `watch.lock.info` 查明持有者，再决定"
        )
        return {
            "ok": False,
            "reason": "refused_to_kill",
            "why": identity,
            "stopped": holder.get("owner", ""),
            "graph": holder.get("graph", ""),
            "hint": hint,
            "cleanup": {"sidecar_removed": False, "pid_file_removed": False,
                        "lock_file_kept": True, "errors": []},
        }

    already_gone = False
    try:
        os.kill(pid, 15)  # SIGTERM
    except ProcessLookupError:
        already_gone = True
    except PermissionError as exc:
        return {
            "ok": False,
            "reason": "kill_not_permitted",
            "why": str(exc),
            "pid": pid,
            "stopped": holder.get("owner", ""),
            "graph": holder.get("graph", ""),
            "cleanup": {"sidecar_removed": False, "pid_file_removed": False,
                        "lock_file_kept": True, "errors": []},
        }

    exit_state = "exited" if already_gone else "unconfirmed"
    if not already_gone:
        for wait_s in _WATCH_EXIT_WAIT_S:
            time.sleep(wait_s)
            if _lock_is_free(lock):
                exit_state = "exited"
                break
        else:
            exit_state = "exit_unconfirmed"
    cleanup = _cleanup_watch_files(info=info, pid_file=pid_file) if exit_state == "exited" else {
        "sidecar_removed": False,
        "pid_file_removed": False,
        "lock_file_kept": True,
        "errors": [],
    }
    result: dict[str, Any] = {
        "ok": exit_state == "exited",
        "stopped": holder.get("owner", ""),
        "graph": holder.get("graph", ""),
        "pid": pid,
        "identity": identity,
        "exit": exit_state,
        "cleanup": cleanup,
    }
    if exit_state == "exit_unconfirmed":
        result["reason"] = "exit_unconfirmed"
        result["error"] = (
            f"已向 PID {pid} 发 SIGTERM，但协调锁在 {_WATCH_EXIT_WAIT_TOTAL_S:.1f} 秒预算内仍被持着"
            "——watcher 还在收尾（或信号被平台吞了）。诊断件一律保留，**没有**再动锁文件。"
        )
    return result


def start_watch(ir: dict, store_root: str | None = None, dry_live: bool = True) -> dict[str, Any]:
    """启动 watch 进程跑指定 IR。

    P1-19 修复：
    - HA 令牌经环境变量 AUTOFORGE_HA_TOKEN 传递（不出现在 /proc/pid/cmdline）
    - 用 PID 文件精确终止旧 watch（不用 pkill -f 模式匹配）
    - 返回值基于真实健康探测（子进程 poll）

    第六轮审计 BUG-05／BUG-06 追加两格：
    - 探测改成退避表 `_WATCH_PROBE_DELAYS_S`（首次 0.1 秒，合计 11.9 秒）。固定 1×15 秒的旧写法
      让成功路径至少白等 1 秒、失败路径把一个同步线程池工位占满 15 秒。
    - 停旧 watch 前先核验 PID 身份（`_verified_pid`）；核验不过就**不杀**，由新 watcher 自己去
      抢协调锁，抢不到就如实回 `coord_lock_held_by_other`（带 `previous_watch` 说明没杀成的原因）。
      "端点立刻返回 job id、由前端轮询"那一半要改 HTTP 面（`af_api.py`），不在这次的射程内。
    """
    import subprocess
    import tempfile
    root = Path(store_root) if store_root else Path(".forge")
    pid_file = root / "watch.pid"
    # 先停旧 watch：**只在 PID 与 sidecar 身份核验一致时**才发信号（审计 BUG-06）。
    # 核验不过就一律不杀，让新建的 watcher 自己去抢协调锁——抢不到会如实回
    # `coord_lock_held_by_other`，这比"照着盘上的数字动刀"安全，因为那个数字随时可能是别人。
    previous_holder: dict[str, Any] = {}
    try:
        previous_holder = json.loads((root / "watch.lock.info").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    old_pid, old_pid_identity = _verified_pid(pid_file, previous_holder)
    if old_pid is not None:
        try:
            os.kill(old_pid, 15)  # SIGTERM
        except (ProcessLookupError, PermissionError, OSError):
            old_pid_identity = "old_watch_signal_failed"
    previous_watch = {"pid": old_pid, "identity": old_pid_identity}
    # 写 IR 到临时文件
    tmp = Path(tempfile.mkdtemp(dir=str(root))) / "deployed_ir.json"
    # 走原子助手：这份 IR 随后被独立进程读取，崩在半截会让部署侧读到半个图
    # （判据 E，审计 BUG-05）
    atomic_write_text(tmp, json.dumps(ir, ensure_ascii=False, indent=2))
    # P1-19：令牌从 credentials.json 读出，经环境变量传递（不传 --ha-token argv）
    ha_token = ""
    cred = root / "credentials.json"
    if cred.exists():
        try:
            ha_token = json.loads(cred.read_text(encoding="utf-8")).get("ha_token", "")
        except (OSError, ValueError):
            pass
    # 起 watch（不传 --ha-token，令牌走环境变量）
    cmd = [
        "forge", "watch", str(tmp),
        "--persist-dir", str(root),
        "--ha-url", os.environ.get("AUTOFORGE_HA_URL", ""),
    ]
    if dry_live:
        cmd.append("--dry-live")
    # P1-19：构造子进程环境，注入 AUTOFORGE_HA_TOKEN
    child_env = dict(os.environ)
    if ha_token:
        child_env["AUTOFORGE_HA_TOKEN"] = ha_token
    # 日志句柄在**父进程侧**必须关掉（审计 BUG-14）：`Popen` 已经把 fd 复制给子进程，父进程再
    # 持着一份只会让 Windows 上的 watch.log 无法轮转/删除，而 `Popen` 抛错的异常路径原来连
    # 引用计数回收都走不到。
    try:
        with (root / "watch.log").open("ab") as logf:
            proc = subprocess.Popen(
                cmd,
                stdout=logf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=child_env,
            )
    except Exception as e:
        return {"ok": False, "error": f"启动 watch 失败: {e}"}
    # 写 PID 文件（供 `stop_watch` 用）。内容仍是裸数字——**可信身份不在这里**，而在 sidecar 的
    # `owner`（`{hostname}-{pid}-{uuid8}`）：停之前两边必须对得上，见 `_verified_pid`。
    try:
        atomic_write_text(pid_file, str(proc.pid))
    except OSError:
        pass
    # 等 sidecar 出现，同时做健康探测。
    # **sidecar 身份必须对得上本次这份 IR**：它是目录里唯一的一个文件，上一条 watch 的残留会被读成
    # "本次启动成功"（2026-10-09 现场实测：1.18s 就回 ok=true，带的是 9-29 另一条 watch 的 graph 路径）。
    import time
    info = root / "watch.lock.info"
    mine = str(tmp)
    # 档位如实命名，且**从本函数自己拼给 CLI 的旗子里读回来**（不另立第二套说法）。
    # `forge watch` 没有 `--live` 这个旗子：它的真机档是"不带 `--dry-live`"（af_cli.py:585
    # `live=not dry_live`），而缺 `--confirm` 时 CLI 直接拒启动（af_cli.py:565-566）。
    # 本层从不传 `--confirm`/`--live-allow` ⇒ `dry_live=False` 这一档只会得到子进程退出；
    # HTTP/用户界面到真机那条**常驻**通道因此今天不存在，是否开它归 DCD 裁（20261009-AF 申请）。
    # 一次性真机下发 `POST /api/live/run` 是另一条已存在的通道（要 confirm + live_allow），
    # 它不在这格的射程内——那一档下发完就退出，不监听。
    tier = "dry_live" if "--dry-live" in cmd else "live_unconfirmed"
    other: dict[str, Any] = {}
    for delay_s in _WATCH_PROBE_DELAYS_S:
        time.sleep(delay_s)
        # P1-19：子进程已死 → 立即返回失败（不再假成功）
        exited = proc.poll() is not None
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if data is not None and data.get("graph") == mine:
            return {
                "ok": True,
                "pid": proc.pid,
                "owner": data.get("owner", ""),
                "graph": data.get("graph", ""),
                "acquired_at": data.get("acquired_at", ""),
                "dry_live": dry_live,
                "tier": tier,
                # 两档都不落真机：dry_live 只记意图，live_unconfirmed 被 CLI 拒启动（走不到这一格）。
                "real_device": False,
            }
        if data is not None:
            other = data
        if exited:
            return {
                "ok": False,
                "reason": "child_exited",
                "error": f"watch 子进程启动后立即退出（exit_code={proc.returncode}），请检查 watch.log",
                "holder": other or None,
            }
    # 退避表跑完（合计 `_WATCH_PROBE_TOTAL_S` 秒）：分三种如实结论
    if proc.poll() is not None:
        return {
            "ok": False,
            "reason": "child_exited",
            "error": f"watch 子进程已退出（exit_code={proc.returncode}），请检查 watch.log",
            "holder": other or None,
        }
    if other:
        return {
            "ok": False,
            "reason": "coord_lock_held_by_other",
            "error": (
                f"sidecar 属于别的 watcher（graph={other.get('graph', '?')} @ "
                f"{other.get('acquired_at', '?')}），本次这份没拿到协调锁"
            ),
            "pid": proc.pid,
            "holder": other,
            # 上一条 watch 有没有被停掉，如实带出来：核验不过就是没杀，别让它读成"已经清了"。
            "previous_watch": previous_watch,
        }
    return {
        "ok": False,
        "reason": "not_registered",
        "error": (
            f"子进程仍在跑，但 {_WATCH_PROBE_TOTAL_S:.1f} 秒内没写出自己的 sidecar"
            "——**不能证明它在监听**（旧版本这里回 ok=true）"
        ),
        "pid": proc.pid,
    }
