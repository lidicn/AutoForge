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
from datetime import datetime, timezone
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
from .af_ownership import (
    DenyKind,
    Subject,
    TokenState,
    broken_identity_error,
    decide,
    mutator_error,
    scope_names,
    visible_error,
)
from .af_runtime import Runtime, build_runtime
from .af_runtime_facts import recent_ask_answers
from .af_scanner import DeviceGuardRegistry, Diagnostic, ScanResult, StaticScanner
from .af_scene_kit import (
    SCENE_MAX_DEFS,
    SceneConditionNotMet,
    SceneDef,
    SceneDefError,
    SceneRunner,
)
from .af_spec import SpecError, compile_spec, graph_to_raw, render_spec
from .af_store import DEFAULT_STORE_ROOT, GraphStore, _bundle_checksum, diff_graphs
from .af_state import UnknownEntity
from .af_time import SystemTimeSource, VirtualTimeSource
from .af_undo import UndoStore, deploy_id as new_deploy_id
from .af_vhass import FakeHAAdapter, seed_from_graph
from .af_pending import PendingLimitExceeded, PendingStore
from .af_error_knowledge import ErrorKnowledge
from .af_experience import ExperienceStore
from .af_grant import GRANT_FILE_REL, GrantError, GrantStore, collect_actions
from .af_task_order import (
    OUTPUT_WIRED,
    ORDER_FILE_REL,
    TaskOrderError,
    TaskOrderStore,
    build_step_ir,
    step_entity,
    step_event,
    step_summary,
)
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
    # v1.29.0 第一批：执行授权（grant）面——签发/列表/撤销/续期/留痕读面
    "grant_issue",
    "grant_list",
    "grant_revoke",
    "grant_renew",
    "grant_receipts",
    # v1.29.0 第二批：【任务】TaskOrder —— 到点执行、逐动作过 grant、终态即销
    "order_create",
    "order_list",
    "order_cancel",
    "order_receipts",
    "order_tick",
    "order_engine_start",
    "order_engine_stop",
    "order_engine_health",
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
#: 版本线随代码里程碑走（v1.9.0 登录正规化 / v1.10.0 系统自更新 / v1.11.0 开发端登录
# 与登录面加固）。此前 0.1.0 是建仓初值，与文档体系（v1.x）脱节——出资人 2026-10-21
# 定案：版本号直接归真，不再等 DCD 二次裁定（10-01 交接卡 D2 的等裁定前提作废）。
# AF-128（审计 2026-10-30）：这里曾是第四处版本字面量，两版没人更 ⇒ af_health 报
# 旧版本、审计据此差点误判"批次未部署"。改为从 `__version__`（pyproject 同源）取，
# ⛔ 不再存在可以单独漂移的抄本。
from . import __version__ as _APP_VERSION  # noqa: E402
API_VERSION = _APP_VERSION
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


#: ── watch 周期对账与心跳（PM 卡 20261102）：sidecar 名与节奏 ──────────────
#: 间隔取 600s（10 分钟）的理由：归档变更是低频人工动作（审批驱动），10 分钟内把
#: 「改了自动化不生效」从不可见变成 health 可读数；单轮成本＝读几十个小 JSON，NAS 可忽略。
#: catalog-auto 的 6h 节奏对目录刷新够用，对「假绿窗口」太宽（AF-714/追加③ 的现场证据：
#: watch 挂的是启动时快照，store 后来归档了四条而读数仍是"1 条"那次的形状）。
WATCH_RECONCILE_FILE = "watch.reconcile.json"
WATCH_RECONCILE_INTERVAL_S = 600.0


def ir_deployment_sha(ir: Any) -> str:
    """一组自动化**内容**的部署指纹（watch 对账单源）。

    规范化＝逐条 automation dict 的 canonical JSON（sort_keys、紧凑分隔符），按串排序后
    换行拼接再 sha256。语义钉在「跑的内容」而不是「包装」：
    - envelope 键（`version`／容器 vs 单条）不参与——漂移的定义是自动化集合变了，
      不是同一个集合换了容器形状（AF-719/AF-723 的形状不对称教训）；
    - 集合顺序不参与——store 归档出现顺序与 watch 文件里顺序本就无关；
    - 单条形状按 AF-719 同族口径归一（判别只认容器键 `automations`）。
    空/None/无有效条目 ⇒ `""`（没有可对照的部署，⛔ 不拿哈希差异冒充漂移）。
    """
    import hashlib
    if not isinstance(ir, Mapping) or not ir:
        return ""
    autos = ir.get("automations")
    if not isinstance(autos, list):
        autos = [ir]
    parts = sorted(
        json.dumps(a, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for a in autos if isinstance(a, Mapping)
    )
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _watch_health(store: "GraphStore | None") -> dict[str, Any]:
    """`/api/health` 的 watch 心跳读数（PM 卡 20261102 腿 2）。

    只读两件现成件——watch 进程自己周期写的对账 sidecar ＋ 协调锁（内核真值）；
    ⛔ 不扫归档、⛔ 不重建 merged IR、⛔ 不探 HA（那些都在 watch 侧循环里做，
    health 保持轻）。三问各对一格：① `present`/`alive`＝在不在；② `drifted`/
    `matches_store`＝跑的是不是 store 现状那份；③ `checked_at`/`stale`＝上次对账
    何时跑、结果还在不在有效期。`stale`＝心跳超过 2.5×间隔没更新（watch 死了没人
    再写 sidecar，⛔ 让旧读数冒充新鲜）。跨容器 flock 可见性由现场实证过
    （api 恢复 watch 因 sync 持协调锁而 ok=False），`alive` 才敢用锁真值。
    """
    out: dict[str, Any] = {"present": False}
    if store is None:
        return out
    rec_path = Path(store.root) / WATCH_RECONCILE_FILE
    if not rec_path.is_file():
        return out
    try:
        data = json.loads(rec_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return out
    except (OSError, ValueError):
        logger.debug("WATCH_HEALTH_SIDECAR_SKIPPED", exc_info=True)
        return out
    try:
        interval = float(data.get("interval_s") or WATCH_RECONCILE_INTERVAL_S)
    except (TypeError, ValueError):
        interval = WATCH_RECONCILE_INTERVAL_S
    out = {
        "present": True,
        "watch_ir_sha": str(data.get("watch_ir_sha") or ""),
        "startup_store_sha": str(data.get("startup_store_sha") or ""),
        "store_sha": str(data.get("store_sha") or ""),
        "drifted": bool(data.get("drifted")),
        "matches_store": bool(data.get("matches_store")),
        "checked_at": str(data.get("checked_at") or ""),
        "drift_since": data.get("drift_since"),
        "stale": True,
    }
    try:
        checked = datetime.fromisoformat(out["checked_at"])
        out["stale"] = (datetime.now(timezone.utc) - checked).total_seconds() > interval * 2.5
    except (TypeError, ValueError):
        pass
    try:
        out["alive"] = (not out["stale"]) and not _lock_is_free(Path(store.root) / "watch.lock")
    except Exception:  # noqa: BLE001 —— 存活探测不许弄坏 health，但也不许静默
        logger.debug("WATCH_ALIVE_PROBE_SKIPPED", exc_info=True)
        out["alive"] = False
    return out


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
    # 裁定 20261011（过期授权码与触发历史保留策略）§3 的那一枚只读读数接进健康面：
    # 它把「码空间何时会紧」从形容词变成可读的数。⛔ 这一格只读，不触发任何裁剪／删除动作。
    premiere_stats = None
    try:
        from . import af_premiere
        premiere_stats = af_premiere.stats()
    except Exception:
        logger.debug("PREMIERE_HEALTH_PROBE_SKIPPED", exc_info=True)
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
        "watch": _watch_health(store),
        "linkage": _linkage_health(presence),
        "tick_health": tick_health,
        "ticker_alive": ticker_alive,
        "tick_exit_reason": tick_exit_reason,
        "premiere": premiere_stats,
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


def list_graphs(
    store: GraphStore, caller: str = "", is_owner_face: bool = False,
    token_identity: bool | None = None,
) -> dict[str, Any]:
    """归档列表。出资人裁定 2026-10-29：普通 agent **只看得到自己写的**，owner 面看全部。

    `caller` 为空 = 未携带身份的旧调用方（HTTP 控制台/CLI），维持全量——与
    `_assert_archive_mutator` 同一渐进口径：先收带身份的路径，⛔ 不一刀切把控制台打空。

    C-3 C4：归属读取单源 `_existing_owner`——最新版本记录没写 owner 时，列表的
    owner 字段与范围过滤都跟 `_existing_owner` 同值（R6 对账，⛔ 内联第二份解析）。
    R4/R8：读不出的归档不再静默隐身，与 `graphs_by_tag` 同形进 `unavailable`；
    `ok` 从计数合拢派生（items + unavailable + hidden == 全集）。
    AF-730（2026-11-02）：REST 面开始下传身份 ⇒ 补 `token_identity` 形参——
    BROKEN（持令牌但缺 subject）⇒ 403 残缺身份（R1：⛔ 不得退化成匿名全量）；
    旧调用方（MCP `_t_list_graphs`）不传该参 ⇒ None ⇒ 行为逐字不变（⛔ MCP 零变化）。
    """
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
                "owner": _existing_owner(store, name),  # C4：归属单源（与过滤同值）
                "automation_ids": [],
                }
    sub = Subject.from_caller(caller, owner_face=is_owner_face, token_identity=token_identity)
    if sub.token_state is TokenState.BROKEN:
        raise ServiceError(broken_identity_error(), status=403)
    # 范围判定单源（C-3）：带身份先收份额，别人的既不加载也不点名
    visible, hidden = scope_names(lambda n: _existing_owner(store, n), latest.keys(), sub)
    items: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for name in visible:
        item = latest[name]
        try:
            graph = store.load(name, int(item["latest_version"]))
        except (FileNotFoundError, IRValidationError, OSError):
            # R8：与 graphs_by_tag 同形——读不出进 unavailable，⛔ 一面显示一面隐身
            unavailable.append(name)
            continue
        autos = list(graph)
        item["automation_ids"] = [a.id for a in autos]
        item["mode"] = autos[0].mode if len(autos) == 1 else "multi"
        try:
            item["tags"] = store.get_tags(name)
        except Exception as exc:  # AF71：tags 只是展示层附属，读不出降级为空继续，不能炸掉整个列表
            logger.warning("LIST_GRAPHS_TAGS_FAILED name=%s err=%s —— tags 降级为空继续", name, exc)
            item["tags"] = []
        items.append(item)
    items.sort(key=lambda x: x["name"])
    # 单一出口：写两个 `return {"ok": True, ...}` 会让 AST 棘轮多出一枚"没派生自校验"的
    # 站点（今天真的涨了 1 条、把只降不升的上限顶红）。收口成一个出口，`ok` 派生自计数合拢。
    out: dict[str, Any] = {"ok": len(items) + len(unavailable) + hidden == len(latest),
                           "items": items, "count": len(items)}
    if caller and not is_owner_face:
        # 别人的条目**不进列表也不报总数**（裁定要的是"看不到"，不是"看到了不能改"）；
        # `hidden_count` 必须存在：agent 要能知道自己看到的不是全量。
        out["hidden_count"] = hidden
        out["scope"] = f"owner:{caller}"
    if unavailable:
        out["unavailable"] = sorted(unavailable)
    return out


def list_automation_cards(
    store: GraphStore, *, caller: str = "", caller_is_owner: bool = False,
    token_identity: bool | None = None,
) -> dict[str, Any]:
    """AF-731（2026-11-02，并 AF-730 批）：自动化卡片读面的**范围**单源薄入口。

    形状照 `list_graphs`：身份走 `Subject.from_caller`、归属走 `_existing_owner`
    （C3/C4 归属单源）、范围走 `scope_names`（⛔ 不在此内联第二份规则）；卡片渲染
    不属归属逻辑，仍归 API 面（`automation_card`）。

    - 非 owner 面（`caller` 非空且非 owner）：只返回自己名下归档的 history 条目；
      `hidden_count`/`scope` 与 `list_graphs` 同口径（仅非 owner 面报，缺口必须可见）；
    - BROKEN（持令牌但缺 subject）⇒ 403 残缺身份（R1：读面 ⛔ 退化成匿名全量）；
    - owner 面／无身份豁免：条目原样全量返回，行为与收口前逐字一致（⛔ 误伤 owner＝
      AF-716 反向同族，测试钉住）。

    返回 {"ok": 派生, "records": 可见的 history 条目, "hidden_count"?, "scope"?}；
    渲染所需字段由调用方补齐。
    """
    sub = Subject.from_caller(caller, owner_face=caller_is_owner, token_identity=token_identity)
    if sub.token_state is TokenState.BROKEN:
        raise ServiceError(broken_identity_error(), status=403)
    # 去重保序（同一归档多版本只出一张卡）；范围判定走 `scope_names` 单源（⛔ 手写过滤）。
    unique: list[str] = []
    seen: set[str] = set()
    for h in store.history():
        n = str(h.get("name", ""))
        if n and n not in seen:
            seen.add(n)
            unique.append(n)
    visible_names, hidden = scope_names(lambda n: _existing_owner(store, n), unique, sub)
    visible = set(visible_names)
    # 去重：每名只出一条（最新版本）。现网实证：history() 多版本条目会让卡片页出现
    # 重复卡（线上 total=6 vs 唯一归档 5，AF-Verify L4b 独立复现）⇒ 按唯一归档数出卡，
    # 版本选择与 store.latest 同源（最新）。
    latest_by_name: dict[str, dict[str, Any]] = {}
    for h in store.history():
        n = str(h.get("name", ""))
        if n not in visible:
            continue
        v = int(h.get("version") or 0)
        if n not in latest_by_name or v > int(latest_by_name[n].get("version") or 0):
            latest_by_name[n] = h
    records = [latest_by_name[n] for n in sorted(latest_by_name)]
    # ok 派生自计数合拢（⛔ 字面量 True——AST 棘轮口径，与 list_graphs 同纪律）
    out: dict[str, Any] = {"ok": len(latest_by_name) + hidden == len(unique),
                           "records": records}
    if caller and not caller_is_owner:
        out["hidden_count"] = hidden
        out["scope"] = f"owner:{caller}"
    return out


def delete_graph(
    store: GraphStore, name: str, *, caller: str = "", caller_is_owner: bool = False,
    note: str = "", token_identity: bool | None = None,
) -> dict[str, Any]:
    """删除归档（移入隔离区，可人工恢复）。

    出资人裁定 2026-10-29：普通 agent 可以删**自己写的**归档；别人的**看不到也删不掉**；
    owner 面（开发者 agent / 用户）可删全部。删除不要求授权码——方向判断：
    删一条自动化是让能力**变少**（与"启用要码、禁用免码"同一条理），
    真正需要码的是往真机上加动作。⛔ 但删除必须可见可追：隔离区清单 + 审计留痕。
    """
    _assert_archive_mutator(store, name, caller=caller, caller_is_owner=caller_is_owner,
                            action="删除", token_identity=token_identity)
    try:
        moved, into = store.quarantine(name)
    except FileNotFoundError as exc:
        raise ServiceError(f"归档 {str(name)!r} 不存在（没删任何东西）", status=404) from exc
    tag_path = store.tags_path(name) if hasattr(store, "tags_path") else None
    try:
        if tag_path is not None and Path(tag_path).is_file():
            # 目标名带时间戳：固定名 `.deleted` 遇上并发删除会互相覆盖——那才是本门要拦的形状。
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            # 同样是"搬走保留现场"，不是写文本；目标名带微秒戳，⛔ 不留固定名给并发删除互覆。
            # fixed-tmp: exempt(tags 文件搬家非原子写：目标名唯一戳，无固定 tmp 截断风险)
            Path(tag_path).rename(Path(tag_path).with_name(f"{Path(tag_path).name}--{stamp}.deleted"))
    except OSError as exc:
        logger.warning("DELETE_GRAPH_TAGS_MOVE_FAILED name=%s err=%s —— tags 留在原地，归档已隔离", name, exc)
    # AST 门两次判我 `ok: True` 是"我说成功"——它说得对，而且第一轮我只加了回读
    # 校验、回执里仍写字面量 True（门照样红）。真正的形状是：`ok` 必须是那个校验本身。
    # 失败也不抛异常：删除半途而废时，调用方需要看到"源目录还在/隔离区没出现"这两格
    # 状态自己判断怎么办，500 只会把现场信息吞掉。
    src_dir = Path(store.root) / store.dir_name(name)
    src_gone = not src_dir.exists()
    landed = into in set(store.list_quarantined())
    verified = src_gone and landed
    out: dict[str, Any] = {
        "ok": verified,
        "verified_by_readback": True,
        "readback": {"source_removed": src_gone, "in_quarantine": landed},
        "name": name,
        "deleted": verified,
        "method": "quarantine",
        "quarantined_as": into,
        "restorable": True,
        "note": note or "归档已移入隔离区（.deleted/），人工可捞回；不是物理删除",
    }
    if not verified:
        out["error"] = {
            "code": "QUARANTINE_INCOMPLETE",
            "message": (f"归档 {name!r} 的隔离未完成（源目录已移除={src_gone}，"
                        f"隔离区已出现={landed}）——没有报告成功，请人工核对目录状态"),
            "fix": {"quarantine_dir": str(Path(store.root) / GraphStore.TRASH_DIR),
                    "next_action": "af_quarantine 列隔离区，或人工检查 store.root 下该归档目录"},
        }
    return out


def list_quarantined(store: GraphStore, *, caller: str = "", caller_is_owner: bool = False) -> dict[str, Any]:
    """隔离区清单（AF-501）：`af_delete_graph` 的回执一直把 next_action 指向
    一个不存在的工具——删除能力上线却看不到隔离区，等于「删了就黑箱」。
    owner 面看全量；普通 agent 只看自己名下被隔离的。归属从**隔离目录里的版本
    记录**读（`history()` 只扫活目录 `*/v*.json`，隔离后的归档不在其中）。
    """
    names = store.list_quarantined()
    trash = Path(store.root) / GraphStore.TRASH_DIR

    def _quarantined_owner(qname: str) -> str:
        # C-3 D6（设计稿 af-ownership-audit-model-v3-v3 §4 D6，PM 裁定）：归档名可含
        # `--`（normalize_archive_name 允许 -/_），旧 `split("--",1)[0]` 会从第一个
        # `--` 截断 ⇒ `foo--bar--<戳>` 解析成 `foo` 而非 `foo--bar`，真主看不到自己的
        # 隔离项。改为**尾部戳反解**：`rpartition("--")` 取最后一段，戳形校验
        # ^\d{8}T\d{6}\d{6}$（GraphStore.quarantine 落戳 %Y%m%dT%H%M%S%f＝20 位、
        # 不含 `--`）。戳形不匹配时 ⛔ 不回退 split、⛔ 不猜原名——按「原名不可解析」
        # 返回 ""（走既有不可解析路径，不静默返回错误归属者）。
        import re as _re
        head, _sep, tail = qname.rpartition("--")
        if head and _re.fullmatch(r"\d{8}T\d{6}\d{6}", tail):
            original = head
        else:
            original = ""  # 戳形不匹配 ⇒ 原名不可解析，⛔ 不猜
        if not original:
            return ""
        # C4：隔离面归属＝**删除时点真值**（隔离目录自己的版本记录）。活目录
        # `_existing_owner` 在这里不参与——删除后同名重建时，旧隔离项不能被记到
        # 新主头上；判定/范围的单源语义见 af_ownership（下方过滤走 scope_names）。
        qdir = trash / qname
        if not qdir.is_dir():
            return ""
        try:
            records = sorted(qdir.glob("v*.json"))
            if not records:
                return ""
            import json as _json
            rec = _json.loads(records[-1].read_text(encoding="utf-8"))
            return str(rec.get("owner") or "")
        except (ValueError, OSError):
            return ""

    # C-3 C1：范围过滤单源 `scope_names`（⛔ 内联第二份 owner==caller 判断）。
    # fake-ok-const 门：`ok` 必须派生自读数本身（len(items) == len(想要的范围)），
    # 不是字面量 True——对照 delete_graph 的 verified_by_readback 同款纪律
    sub = Subject.from_caller(caller, owner_face=caller_is_owner)
    items, hidden = scope_names(_quarantined_owner, names, sub)
    ok = len(names) == len(items) + hidden
    out: dict[str, Any] = {"ok": ok, "items": items, "count": len(items)}
    if caller and not caller_is_owner:
        out["hidden_count"] = hidden
        out["scope"] = f"owner:{caller}"
    return out


# ─────────────────────────────────────────────────────────────────────
# v0.6.0 标签体系 + 批量启停
# ─────────────────────────────────────────────────────────────────────


def set_graph_tags(
    store: GraphStore, name: str, tags: list[str],
    *, caller: str = "", caller_is_owner: bool = False,
    token_identity: bool | None = None,
) -> dict[str, Any]:
    """设置某归档名字的标签（覆盖式）。D4：改标签前做归属判定。

    AF-701 界线钉（20261031 §2.1）：`token_identity` 语义见 `_assert_archive_mutator`。
    """
    _assert_archive_mutator(store, name, caller=caller, caller_is_owner=caller_is_owner,
                            token_identity=token_identity)
    store.set_tags(name, tags)
    return {"ok": True, "name": name, "tags": store.get_tags(name)}


def graphs_by_tag(
    store: GraphStore, tag: str, *, caller: str = "", caller_is_owner: bool = False,
    token_identity: bool | None = None,
) -> dict[str, Any]:
    """返回带某标签的所有归档名（及其最新版本信息）。

    审计 AF-504：带身份的非 owner 面按归属过滤（别人的不进列表），
    并像 `list_graphs` 一样报 `hidden_count`——agent 要能知道看到的不是全量；
    校验失败的归档不再静默消失，进 `unavailable` 名单（原来"同一条数据在
    `af_list_graphs` 可见、在 `af_graphs_by_tag` 隐身"还无解释）。
    C-3 收口：范围判定单源 `scope_names`（⛔ 内联第二份 owner==caller）；
    BROKEN（持令牌缺 subject）⇒ 403 残缺身份（R1）；`ok` 派生自计数合拢（R4）。
    """
    sub = Subject.from_caller(caller, owner_face=caller_is_owner, token_identity=token_identity)
    if sub.token_state is TokenState.BROKEN:
        raise ServiceError(broken_identity_error(), status=403)
    names = [n for n, ts in store.all_tags().items() if tag in ts]
    # 带身份先做范围判定（单源 scope_names），未带身份的旧路径维持全量（渐进收口）
    scoped_names, hidden = scope_names(lambda n: _existing_owner(store, n), names, sub)
    items = []
    unavailable: list[str] = []
    for name in scoped_names:
        try:
            latest_version = store.latest(name)
            if latest_version is None:
                unavailable.append(name)
                continue
            graph = store.load(name, latest_version)
        except (FileNotFoundError, IRValidationError, OSError):
            unavailable.append(name)
            continue
        try:
            tags = store.get_tags(name)
        except Exception as exc:  # AF71：标签视图不能因为 tags 读不出整体 500（带标签归档消失）
            logger.warning("GRAPHS_BY_TAG_TAGS_FAILED name=%s err=%s —— tags 降级为空继续", name, exc)
            tags = []
        items.append(
            {
                "name": name,
                "version": latest_version,
                "automation_ids": [a.id for a in graph],
                "tags": tags,
            }
        )
    # R4：`ok` 派生自计数合拢——items + unavailable + hidden 必须收得回全集，
    # ⛔ 字面量 True（有归档被静默漏掉时必须能读出 False）。
    out: dict[str, Any] = {"ok": len(items) + len(unavailable) + hidden == len(names),
                           "tag": tag,
                           "items": sorted(items, key=lambda x: x["name"])}
    # 范围透明（AF-504）：缺口要么说数量（别人的），要么点名（读不出的）——都不许静默
    if caller and not caller_is_owner:
        out["hidden_count"] = hidden
        out["scope"] = f"owner:{caller}"
    if unavailable:
        out["unavailable"] = sorted(unavailable)
    return out


def _ensure_watcher(store: GraphStore, *, enabled: bool):
    """启用后确保"真的有人在监听"（出资人裁定 2026-10-29 方案 A，契约 R4-4 假武装）。

    为什么这是最小闭环而不是大改：缺陷从来不是"只有一个 watch 进程"——单写者是对的，
    两个监听会重复下发；缺的是**期望态与实际态之间没有对账**：`enabled` 翻成 true 之后，
    没有任何一条路径保证有进程在盯这份合并 IR，进程死了也没人补。

    三种结果都如实回，⛔ 不静默：`reused`（复用活监听，不重启免得打断在跑的实例）、
    `started`（起了新的）、`refused/failed/probe_failed:<原因>`（起不动必须回出去——
    否则就又回到"已启用但永不触发"的假武装）。

    **停用面（AF-PM 裁定 2026-11-13 起）不再走上面这套**：停用是安全阀，恒回
    `{"action": "hot_flipped", "overrides": N, "running": M}` —— 只翻启停位，
    ⛔ 不 stop、⛔ 不 start、⛔ 不重装 IR（详见本函数 `if not enabled:` 支的理由）。
    """
    root = str(store.root)
    try:
        live = list_watches(root)
        running = [w for w in (live.get("watches") or []) if w.get("alive")]
    except Exception as exc:  # 探活失败也不能静默
        return {"action": "probe_failed", "reason": f"{type(exc).__name__}: {exc}"}
    if not enabled:
        # AF-PM 裁定 2026-11-13「停用是安全阀，必须即时生效」：停用**只翻一位**——
        # 走启停旁车热翻转（`af_watch_toggle.EnabledOverrideGate`），
        # ⛔ 不 stop、⛔ 不 start、⛔ 不重装 IR。
        #
        # 本支取代 D4（2026-11-05「先停后起」）的四条理由：
        # ① 重启会**打断在跑实例**（af_ask_purifier 正在等的 60s ask 被腰斩），
        #    而关一条自动化本不该影响别的自动化；
        # ② 重启会重写部署 IR ⇒ `ir_deployment_sha` 变，「停用没重装 IR」无从证明；
        # ③ 重启把「关安全阀」变成一次「部署」，撞「一次审批即生效」的授权契约；
        # ④ D4 要治的症状（被禁用的档继续触发）现在由 `af_scheduler.handle_event`
        #    **派发前**读旁车即时消除 —— 时效从「重启完」缩到「下一次事件」，
        #    比 D4 更严：D4 那版在重启窗口内仍会触发。
        from .af_watch_toggle import read_overrides as _read_overrides

        return {"action": "hot_flipped", "overrides": len(_read_overrides(root)),
                "running": len(running)}
    if running:
        return {"action": "reused", "watchers": len(running)}
    merged = _build_merged_ir(store)
    if not merged:
        return {"action": "none_to_watch"}
    try:
        # 凭据语义：enable_by_tag 启用路径的 `_auth_code_check` 在先——同一动作链上
        # 码已收过一次，对账拉监听不另收。（四路径清单见 DCD 裁定 20261031 §2.2。）
        res = _start_watch_impl(ir=merged, store_root=root, dry_live=False)
    except ServiceError:
        # ⛔ 原样上抛。`READONLY_DEGRADED:` 那一族靠**异常文案前缀**让 DB 判读
        #（裁定 20261004 §一 1 A，判据 test_serve_lease_single_writer 钉着）；
        # 在这里吞成 {"action":"failed"} 就是把一条对外契约静默改没了——
        # 我第一版正是这么写的，回归当场红了这条腿。
        raise
    except Exception as exc:
        return {"action": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    if not res.get("ok"):
        return {"action": "refused", "reason": str(res.get("reason") or res),
                "previous_watch": res.get("previous_watch")}
    return {"action": "started", "pid": res.get("pid"), "raw": res}


def enable_by_tag(
    store: GraphStore, tag: str, enabled: bool, allow_bulk: bool = False,
    *, caller: str = "", caller_is_owner: bool = False, auth_code: str = "",
    token_identity: bool | None = None,
) -> dict[str, Any]:
    """批量启停：对某标签下所有归档的最新版本，翻转其中全部自动化的 `enabled` 并保存新版本。

    返回受影响（已落新版本）的归档名列表。未找到该标签 → 空列表。

    v1.3.0：默认受**爆炸半径**约束——先把受影响自动化条数算出来再决定要不要做，
    **绝不做一半才报错**。显式传 `allow_bulk=True` 绕过（真正想批量时用）。
    C-3 R5（PM 裁定 20261101 §②）：归属判定改**份额化**——混合归属标签上，带身份的
    非 owner 只动自己名下的条目，别人的**不进 affected/failed 也不点名**，缺口只报数
    （`hidden_count`）；⛔ 不改既有键的语义/形状（affected/failed 仍是 list-of-dict）。
    「调用方自报名」写面（set_tags/delete/单条启停）的既有 403 文案经
    `_assert_archive_mutator` 逐字保持，⛔ 本面借用它拒批。
    """
    names = [n for n, ts in store.all_tags().items() if tag in ts]
    # D3（2026-11-05 实证）：**archived 标签＝写闸**。带 archived 的归档已从界面"收走"，
    # 但底层仍可被批量启停翻转/继续真跑（今晨实证：归档件被 MCP 覆盖部署后持续触发 74+19 次）。
    # 解档的唯一路径是显式 `set_tags` 摘掉 archived 标签。⇒ 批量启停面直接拒，
    # 而不是"翻转成功但 watch 不认"这种半吊子状态。
    archived_names = sorted(n for n, ts in store.all_tags().items() if "archived" in ts and n in names)
    if archived_names:
        raise ServiceError(
            f"归档带 archived 标签，⛔ 不许按标签批量启停（先 set_tags 摘标签解档）："
            f"{archived_names[:5]}{'…' if len(archived_names) > 5 else ''}",
            status=403,
        )
    # C-3：范围判定单源 scope_names（D4 的逐名 _assert 在批量面被份额化取代——
    # 逐名 403 会把「别人有一条同标签」放大成整批拒绝，还把他人归档名回给调用方）。
    sub = Subject.from_caller(caller, owner_face=caller_is_owner, token_identity=token_identity)
    if sub.token_state is TokenState.BROKEN:
        raise ServiceError(broken_identity_error(), status=403)
    visible, hidden = scope_names(lambda n: _existing_owner(store, n), names, sub)
    # 先算清影响面（不落盘），超阈值直接拒——避免"改了一半才失败"
    planned: list[tuple[str, int, Any]] = []
    failed: list[dict[str, Any]] = []
    total = 0
    for name in visible:
        try:
            version = store.latest(name)
            if version is None:
                # AF64：有标签但连最新版本都读不出，原来被静默跳过——"要求停用的那个恰恰没停"
                # 还照样回 ok=True。如实记为失败项。
                failed.append({"name": name, "reason": "no_latest_version"})
                continue
            graph = store.load(name, version)
        except (FileNotFoundError, IRValidationError, OSError) as exc:
            failed.append({"name": name, "reason": f"load_failed:{type(exc).__name__}"})
            continue
        planned.append((name, int(version or 0), graph))
        total += len(list(graph))
    if not allow_bulk:
        _check_blast(total, f"按标签 {tag!r} 批量{'启用' if enabled else '禁用'}")

    # AF-401/403：启用要码、禁用免码（关安全阀不该被钥匙挡住）。闸在服务层 ⇒
    # REST（/api/graphs/enable）与 MCP 同闸；放行凭据标签写进回执。
    code_tag = ""
    if enabled:
        _action = f"启用自动化（tag={tag!r}）"
        code_tag = _auth_code_check(store, auth_code, action=_action)
        # AF-778（2026-11-14，§1.216 裁定）：过闸后**立即消耗**一次额度，fail-closed。
        # 修前这里只 validate 不 consume ⇒ 一枚短期码可以**无限次重放**（同一枚码反复启用
        # 不同标签/不同批次），账本上的 `consumed` 记号永远不落下；而 MCP 部署面
        #（`af_mcp._t_save`）是"过闸即 consume"，两个面口径不一 ⇒ 谁走哪条路决定了码能烧几次。
        # 位置纪律：**紧随 validate、save 循环之前**——先把判定与消耗走完再动盘；
        # 但排在爆炸半径检查之后（超额拒时不烧码，否则"被拒还扣额度"）。
        # consume 按 kind 分型由 `AuthCodeStore` 自己决定（短期码置 consumed、长期码
        # use_count+1，af_auth.py 既有语义），⛔ 这里**不加**"拒短期码"：DCD 双轨授权里
        # 短期码本就是部署凭据，能部署就能启用。
        from .af_auth import AuthCodeStore as _ACS

        _acs = _ACS(Path(store.root) / ".auth" / "auth_codes.json")
        if not _acs.consume(str(auth_code or "").strip()):
            # validate 与 consume 是两步，中间可能被别的进程抢走（MCP 面同一形状，R17-01）
            raise ServiceError(
                f"{_action} 无法消耗授权码（可能已被并发消耗/已撤销/已过期）：拒绝执行（fail-closed）",
                status=403,
            )

    affected: list[dict[str, Any]] = []
    # AF-PM 裁定 2026-11-13：本轮翻转涉及的自动化 id 全集 —— 启停旁车按**全部** id 写，
    # 停用即时生效（watch 在派发前读它），⛔ 不再靠重启 watcher 让内存图对齐 store。
    touched_ids: list[str] = []
    for name, _version, graph in planned:
        for auto in graph:
            auto.enabled = enabled
            auto.raw["enabled"] = enabled  # 序列化形态同步翻转，保证 re-save 后 round-trip 一致
            touched_ids.append(str(auto.id))
        try:
            new_version = store.save(
                graph,
                name,
                note=f"v0.6.0 批量{'启用' if enabled else '禁用'}（tag={tag}）",
                # 归属必须随版本走：store.save 的 owner 缺省是空串，不传就等于把别人建的归档
                # 改写成"无归属"——用户视角列表按 owner 分组，一次按标签批量启停会把整批自动化
                # 从原 agent 组里搬进「未归属/本地」，读数看起来像归档被重建过。
                owner=_existing_owner(store, name),
            )
        except Exception as exc:
            # AF64：单条 save 失败不能让异常穿成 500、也不能让前面已落盘的条目把这次批量
            # 掩盖成全 ok。如实记失败项，继续处理其余归档；整体 ok 由"有无失败项"决定。
            logger.warning("enable_by_tag: 批量%s 落盘失败 name=%s err=%r",
                           "启用" if enabled else "禁用", name, exc)
            failed.append({"name": name, "reason": f"save_failed:{type(exc).__name__}"})
            continue
        affected.append({"name": name, "version": new_version, "enabled": enabled})
    # 启停旁车（AF-PM 裁定 2026-11-13）：写失败必须让 `ok` 变假 ——
    # 「停用没生效」不许看起来像成功（安全阀写不进去＝安全阀关不上）。
    override_error = ""
    if touched_ids:
        try:
            from .af_watch_toggle import merge_overrides as _merge_overrides

            _merge_overrides(store.root, dict.fromkeys(touched_ids, bool(enabled)),
                             note=f"enable_by_tag tag={tag!r} enabled={enabled}")
        except Exception as exc:  # noqa: BLE001 —— 写不进去如实回，⛔ 不静默
            override_error = f"{type(exc).__name__}: {exc}"
            # 具名码（AF-PM 2026-11-13 裁定）：与 `set_automation_enabled` 同一个**承诺**
            # ——「本层把启停旁车写进盘失败 ⇒ 安全阀关不上、停用不即时生效」。两站共用
            # 一枚码，与 `WATCH_ENABLED_OVERRIDE_READ_FAILED` 的 stat／parse 两站同构。
            logger.warning("WATCH_ENABLED_OVERRIDE_WRITE_FAILED enable_by_tag: 启停旁车写入失败 "
                           "tag=%s enabled=%s err=%r",
                           tag, enabled, exc)
    # 启停旁车的写结果：`_merge_overrides` 写不进去就抛，能走到下一行就是真写进去了。
    # ⛔ 不许写成字面量 `ok: True`——那等于「没人核对也报成功」，正是假 ok 的门禁判据。
    override_ok = not override_error
    out: dict[str, Any] = {
        "ok": not failed and not override_error,
        "tag": tag,
        "enabled": enabled,
        "affected": affected,
        "failed": failed,
        "blast_radius": {"affected": total, "limit": blast_radius_limit()},
        # 契约 R4-4（2026-10-29「假武装」）：翻转 enabled **不等于**有人在监听——
        # watch 进程死亡/未起时，这里返回 ok:true + enabled:true，而自动化永不触发。
        # enable 要不要自动拉 watch 是单写者租约语义的裁定（待裁），但"armed 无人监听"
        # 必须在这一步说出来，⛔ 别让调用方以为生效了。
        "authorized_by": code_tag or None,
        # AF-PM 裁定 2026-11-13：旧文案「禁用不触碰监听」是**错的**（现场：停用后
        # 19 分钟仍在触发）。停用走启停旁车热翻转，**即时**生效且不重装 IR；
        # 启用面才需要「有人在监听」，那一格看 watch_reconcile。
        "watch_note": (None if enabled else
                       "停用即时生效（只翻启停位，不重装 IR、不打断在跑实例）；"
                       "启用时的实际监听动作看 watch_reconcile"),
        # 启停旁车回执：`ok=false` ⇒ 停用没写到 watch 能看见的地方，必须当失败处理。
        "enabled_override": (
            {"ok": override_ok, "ids": len(touched_ids)} if override_ok else
            {"ok": False, "ids": len(touched_ids), "reason": override_error}
        ),
        # R4-4 收口（裁定方案 A）：启用后做对账——有活监听就复用、没有就起、起不动就回原因。
        # 停用面（2026-11-13 起）不再停起，只回 `hot_flipped` 的读数。
        "watch_reconcile": _ensure_watcher(store, enabled=enabled),
    }
    if caller and not caller_is_owner:
        # R5 份额化回执：无权条目不出现（⛔ 泄名），缺口只报数；新增键、旧键原样
        out["hidden_count"] = hidden
        out["scope"] = f"owner:{caller}"
    return out


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


def _runtime_facts_fields(store: GraphStore, name: str) -> dict[str, Any]:
    """从运行期事实旁车读 `trial` / `last_triggered` / `trigger_7d`（PM 卡 2026-11-04）。

    读侧 fail-soft：旁车缺/损坏/无字段 ⇒ 回落旧硬编码值（None/None/0），向后兼容。
    ⛔ 绝不让"运行事实读不出"变成"列表 500"。

    D10 第二面（2026-11-06）：额外暴露 `last_met`（本轮全条件是否已成立并派发）＋
    `last_dispatched_at`（上次派发时刻 ISO|null）——供 UI 详情页三态显示。
    ⛔ 分层铁律：计数是事实、派发是决策——`last_met` 与 `trigger_7d` 独立，⛔ 不从计数反推。
    """
    fallback = {"trial": None, "last_triggered": None, "trigger_7d": 0,
                "last_met": None, "last_dispatched_at": None, "vars": {}}
    try:
        from .af_runtime_facts import RuntimeFacts

        snap = RuntimeFacts(store.root, name).snapshot()
        return {
            "trial": snap.get("trial"),
            "last_triggered": snap.get("last_triggered"),
            "trigger_7d": int(snap.get("trigger_7d") or 0),
            "last_met": snap.get("last_met"),
            "last_dispatched_at": snap.get("last_dispatched_at"),
            # AF-746（2026-11-08）：持久变量读数（`vars.<名>`）。加上它是为了让「重启后归属还在
            # 不在」能从**既有列表读面**看，而不是只能进容器翻 `.runtime.json`。
            # 缺段 ⇒ 空表（与改前形状等价，⛔ 不影响旧前端）。
            "vars": dict(snap.get("vars") or {}),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("RUNTIME_FACTS_READ_FAILED archive=%r err=%r", name, exc)
        return fallback


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
        # 试演期与触发历史改从**运行期事实旁车** `<archive>.runtime.json` 现读
        # （PM 卡 2026-11-04）。此前三字段硬编码 None/None/0；现读缺文件/损坏/无字段时
        # 回落旧值（None/None/0），向后兼容；读侧 fail-soft，⛔ 不因运行事实读不出让列表 500。
        **_runtime_facts_fields(store, name),
    }


def set_automation_enabled(
    store: GraphStore, name: str, enabled: bool,
    *, caller: str = "", caller_is_owner: bool = False,
    token_identity: bool | None = None,
) -> int:
    """翻转归档内**每一条自动化**的启停旗子，返回新版本号。

    写在 automation 级：运行期的读者是调度器（`af_scheduler.py:87-89` 不为禁用项注册触发、
    `:241-243` 不排空它的队列），容器层那个位置没有任何代码读取。落盘复用 `GraphStore.resave_raw`：
    锁、版本号、原子写与归属字段（owner / writer / note 之外的记录元数据）都留在 store 那一处，端点不碰。
    D4：翻转前先做归属判定。

    AF-PM 裁定 2026-11-13（停用即时生效）：落盘成功后**必须**把启停位写进启停旁车
    （`af_watch_toggle.ENABLED_OVERRIDE_FILE`）——watch 进程在派发前读它。只写 store
    不写旁车＝回到现场那个形状（store 说停用、watch 咬旧快照、停用后 19 分钟仍在触发）。
    旁车写不进去 ⇒ 抛 `ServiceError`（500）：安全阀关不上⛔ 不许回 ok。
    """
    _assert_archive_mutator(store, name, caller=caller, caller_is_owner=caller_is_owner,
                            token_identity=token_identity)

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

    new_version = store.resave_raw(name, flip)

    # 启停旁车（AF-PM 裁定 2026-11-13）：按**归档内全部自动化 id**写，只翻这一位。
    # 先落盘再写旁车：旁车是「store 现状的通告」，⛔ 不许比 store 更早。
    try:
        from .af_watch_toggle import merge_overrides as _merge_overrides

        record = store.load_record(name)
        ids = [str(item.get("id") or "") for item in
               ((record.get("graph") or {}).get("automations") or []) if isinstance(item, dict)]
        ids = [i for i in ids if i]
        if ids:
            _merge_overrides(store.root, dict.fromkeys(ids, bool(enabled)),
                             note=f"set_automation_enabled name={name!r} enabled={enabled}")
    except Exception as exc:  # noqa: BLE001 —— 安全阀关不上必须让调用方看见
        # 具名码（AF-PM 2026-11-13 裁定）：与 `enable_by_tag` 同一句承诺，两站共用
        # `WATCH_ENABLED_OVERRIDE_WRITE_FAILED`——同一件事两个入口，码不许各写一个。
        logger.warning("WATCH_ENABLED_OVERRIDE_WRITE_FAILED set_automation_enabled: "
                       "启停旁车写入失败 name=%s enabled=%s err=%r",
                       name, enabled, exc)
        raise ServiceError(
            f"归档 {name!r} 已落盘 v{new_version}，但 watch 侧启停旁车写失败"
            f"（{type(exc).__name__}: {exc}）——停用不会即时生效，请重试",
            status=500,
        ) from exc
    return new_version


def _catalog_known_entities(store: GraphStore) -> set[str] | None:
    """归档面实体校验白名单——store 的设备目录全集（AF-704，假绿族第二面）。

    `af_build` 走目录时幽灵实体会被 `ENTITY_NOT_FOUND` 拒，但 `submit_pending` 与
    `save_graph` 的 `StaticScanner` 从不传 `known_entities` ⇒ 存在性校验恒 skipped
    ⇒ 同一份 IR 入队/归档一路绿，动作打 HA 404 静默无效。两处扫描统一从本 store 的
    设备目录取全集，与 build 的 use_catalog 同源。

    目录为空 / 读不出 ⇒ None：空目录是合法首建态，fail-open 不拦截，但**必须说话**
    ——scanner 会发 `ENTITY_CHECK_DEGRADED`，`submit_pending` 回执把它转述给人审。
    """
    try:
        ids = DeviceCatalog(store.root).known_entity_ids()
    except Exception:  # noqa: BLE001 - 目录读不出等同空目录：fail-open 但要说话
        return None
    return ids or None


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
    # AF-704：归档面实体校验——白名单取 store 设备目录（空则 None，fail-open 但说话）
    known = _catalog_known_entities(store)
    scan = StaticScanner(
        graph,
        known_entities=known,
        device_guard=load_device_guard(store),
        entity_health=load_entity_health(store),
    ).scan()
    if not scan.ok:
        # AF-704：同 submit_pending——拒绝消息带具名码（含 ENTITY_NOT_FOUND）。
        codes = sorted({d.code for d in scan.errors})
        raise ServiceError(
            f"拒绝归档：IR 未通过静态扫描（{len(scan.errors)} 个错误：{'、'.join(codes)}）。先修 IR 或改用 af_build 查看诊断。",
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
    # D3（2026-11-05 实证）：**archived 标签＝写闸**。归档件的语义是"已从界面收走、不该再动"，
    # 但此前只有展示层认这个标签 ⇒ 归档件照样被覆盖部署（今晨 v4 就是覆盖了一份归档件），
    # 然后底下持续真跑。解档唯一路径＝显式 `set_tags` 摘标签。
    if "archived" in (store.get_tags(str(name).strip()) or []):
        raise ServiceError(
            f"归档 {str(name).strip()!r} 带 archived 标签，⛔ 不许覆盖部署（先 set_tags 摘标签解档）",
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


def precheck_save_ir(store: GraphStore, ir: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """**只读预检**：IR 装载 + 静态扫描（⛔ 不写 pending、⛔ 不碰授权码）。

    D3（2026-11-05，DCD/Chief 口径）：`af_mcp` 的授权码快速通道此前**先 consume 再进
    `submit_pending`**，而扫描/校验在后者里 ⇒ 结构非法的 IR 会**先把码烧掉再报错**
    （实证：`32892011` 白烧一枚）。授权码消耗必须排在「确认这次提交真的能过闸」之后。
    本函数就是那道前置闸——与 `submit_pending` **同一份扫描实现**，⛔ 不另写一套。

    返回 build_warnings（供调用方回执）；不通过即抛 `ServiceError(400)`（与入队闸逐字同文案）。
    """
    if ir is None:
        return []
    try:
        graph = _load_ir(ir)
    except (IRValidationError, ValueError, KeyError) as exc:
        raise ServiceError(f"拒绝归档：IR 结构非法（{exc}）", status=400)
    known = _catalog_known_entities(store)
    scan = StaticScanner(
        graph,
        known_entities=known,
        device_guard=load_device_guard(store),
        entity_health=load_entity_health(store),
    ).scan()
    warnings = [_diag(d) for d in scan.warnings]
    if not scan.ok:
        codes = sorted({d.code for d in scan.errors})
        raise ServiceError(
            f"拒绝归档：IR 未通过静态扫描（{len(scan.errors)} 个错误：{'、'.join(codes)}）。"
            f"先修 IR 或改用 af_build 查看诊断。",
            status=400,
        )
    return warnings


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
    entity_check: str | None = None
    build_warnings: list[dict[str, Any]] = []
    if ir is not None:
        # D3：与 `precheck_save_ir` 同一份扫描实现（⛔ 不另写一套）——预扫描与入队闸必须
        # 判出同样的结果，否则「预检通过、入队被拒」会让授权码照样白烧。
        build_warnings = precheck_save_ir(store, ir)
        entity_check = "catalog" if _catalog_known_entities(store) else "skipped"
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
        # AF-704：实体校验读数必须随回执到人审眼前——旧回执没有 entity_check，
        # 「这条没做存在性校验」在 WebUI/CLI 上读不出来，幽灵实体靠人眼兜底也兜不住。
        # skipped 时 build_warnings 里会有 ENTITY_CHECK_DEGRADED（scanner 发的）。
        "entity_check": entity_check,
        "build_warnings": build_warnings,
        # DCD 裁定 20261031·Q2 甲（矩阵行 2）：待批回执也带 L2 摘要——人审在批准前
        # 就要看见"本条含哪些 L2 动作"（机提取，⛔ 人肉手写；空列表照给）。
        "l2_summary": l2_summary_of(ir or {}),
        # 契约 R2-4（P1，2026-10-29）：`ok:true` + `pending` 被 agent 读成"已保存"，
        # 转头就去 `af_get_graph` / `af_diff` 取它 ⇒ 全部未找到（实测）。写闸 `open`
        # 只表示"能提交"，不表示"已落盘"——提交与落盘是两件事，⛔ 别让调用方自己猜。
        "persisted": False,
        "landing": "pending_approval",
        "next_action": "在 WebUI/mimo 待批队列人工批准；批准前 af_get_graph / af_diff / af_conf 都读不到它",
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
    """列出待批（审批视图：含 summary / blast_radius / 完整 payload 供回放）。

    每行附 `l2_summary`（`l2_summary_of` 单一真源机提取，⛔ 前端不自算）：DCD 矩阵
    行 2 的判据是「**审批时**」人必须看见本条含哪些 L2——待批列表就是批准前那一刻
    的审批面，批准后回执上的那份只补「批准动作」本身。空列表＝合法「本条无 L2」
    （前端显式占位，⛔ 留白＝没读出来）。
    """
    items = PendingStore(store.root).list(agent)
    for item in items:
        payload = item.get("payload")
        ir = payload.get("ir") if isinstance(payload, Mapping) else None
        item["l2_summary"] = l2_summary_of(ir or {})
    return {"ok": True, "items": items}


def l2_summary_of(ir_payload: Mapping[str, Any]) -> list[dict[str, str]]:
    """部署审批摘要的 L2 动作清单（DCD 裁定 20261031·Q2 甲：审批时人必须看见本条含哪些 L2）。

    ★ 机提取（⛔ 人肉手写）：由 IR 的 do 节点经 `classify_action` 判级，L2 命中即列。
    中文别名复用 `af_nl._VERB_ZH` 的既有词汇表（单一真源，不另抄一份）。
    返回形如 ``[{"action": "climate.turn_on", "alias": "打开空调", "automation": "空调",
    "node": "d1"}]`` 的清单；前端/回执照此渲染「本条含：`climate.turn_on`（打开空调）」，
    列表为空也要显式显示占位（⛔ 空白＝"没读出来"）。

    AF-719（v1.15.1，PM 裁定 20261101）：**入口形状归一**——pending/REST 面落的是
    **单条** IR dict（`af_mcp._t_save` 把 `args["ir"]` 原样入 payload），容器形状才带
    `automations` 键；单条进来旧代码循环零次 ⇒ `l2_summary` 恒 `[]`（漏报：恰含 L2
    的那条审批时看不见）。归一判别只认容器键 `automations`（⛔ 靠 `nodes` 键判——
    那是对消费者形状的第二份猜测）；单条 ⇒ `[ir_payload]`，两种形状**产出必须相等**。
    """
    from . import af_nl
    from .af_adapters import classify_action
    from .af_adapters.base import unrecognized_service as _unrec

    autos = ir_payload.get("automations")
    if not isinstance(autos, list):
        autos = [ir_payload]  # AF-719：单条形状归一（唯一归一点，⛔ 三处调用点各判）
    out: list[dict[str, str]] = []
    for auto in autos:
        if not isinstance(auto, Mapping):
            continue
        for node in auto.get("nodes") or []:
            if not isinstance(node, Mapping) or node.get("kind") != "do":
                continue
            action = str(node.get("action") or "")
            if _unrec(action) is not None:
                continue  # 认不出的动作名交给 L2_NEEDS_CONFIRM 的另一支文案，不列进摘要
            if classify_action(str(node.get("adapter") or ""), action, node.get("params")) == "L2":
                out.append({
                    "action": action,
                    "alias": str(getattr(af_nl, "_ACTION_VERBS", {}).get(action, "")),
                    "automation": str(auto.get("name") or auto.get("id") or ""),
                    "node": str(node.get("id") or ""),
                })
    return out


def _build_merged_ir(store: GraphStore) -> dict[str, Any] | None:
    """把所有用户部署的 graph 最新版本合并成一个 IR，供 watch 一次性挂全部自动化。"""
    # 1. 取每个 graph 名的最新版本，跳过 bootstrap examples（note 字段标记）
    latest: dict[str, int] = {}
    for record in store.history():
        name = str(record.get("name", ""))
        ver = int(record.get("version") or 0)
        note = str(record.get("note") or "")
        if note.startswith("bootstrap"):
            continue
        if name not in latest or ver > latest[name]:
            latest[name] = ver
    # 2. 加载所有 automations（用 auto.raw，nodes/edges/meta 都在顶层）
    all_autos: list[dict] = []
    # D3（2026-11-05 实证）：**archived 标签＝装载闸**——归档件不该被 watch 装进运行时。
    # 此前只有展示层认这个标签，结果「界面上归档了、底下还在真跑」（今晨实证持续触发）。
    archived = {n for n, ts in store.all_tags().items() if "archived" in ts}
    for name, ver in latest.items():
        if name in archived:
            continue
        try:
            graph = store.load(name, ver)
            for auto in getattr(graph, "automations", []) or []:
                d = getattr(auto, "raw", None)
                if d:
                    all_autos.append(d)
        except Exception as e:
            logger.warning("_build_merged_ir: 跳过 graph %s@%s: %r", name, ver, e)
    if not all_autos:
        return None
    # 3. 收集 entity → [auto_id]，再统一分配递增 priority（避免遍历 do 节点时反复覆盖）
    writers: dict[str, list[dict]] = {}
    for auto in all_autos:
        for node in auto.get("nodes", []):
            if node.get("kind") == "do":
                eid = node.get("params", {}).get("entity_id", "")
                if eid:
                    writers.setdefault(eid, []).append(auto)
    for eid, autos in writers.items():
        if len(autos) < 2:
            continue
        for i, auto in enumerate(autos, start=1):
            auto.setdefault("meta", {})["priority"] = i
    return {"version": 1, "automations": all_autos}


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
    # DCD 裁定 20261031·Q2 甲（矩阵行 2）：部署审批摘要显式列出本条含哪些 L2 动作——
    # ★ 机提取（`l2_summary_of`，⛔ 人肉手写）。空列表照给（前端渲染占位，⛔ 空白＝没读出来）。
    result["l2_summary"] = l2_summary_of(payload.get("ir") or {})
    # AF-725（PM 卡 20261102 追加②）：approve 本体必须有**以 approve 为主体**的审计留痕
    # ——此前只有 APPROVE_WATCH_AUTO_STARTED 这个派生事件间接带出 pending id，且该行带
    # watch_ok，会把「approve 成功」与「watch 启动失败」混成一件（AF-710 同族反向：
    # 那是记了不该记的，这是该记的没记）。本条只记 approve 事实（op_id／对象／审批人／
    # 落点／结果），⛔ 不含 watch 结果（那是 APPROVE_WATCH_AUTO_STARTED 的职责，两件事
    # 必须能分开读）、⛔ 不含任何令牌/授权码真值。记账失败不回滚已落盘的归档
    # （口径同下方 APPROVE_WATCH_AUDIT_RECORD_FAILED）。
    try:
        from .af_audit import (
            APPROVE_PENDING_APPLIED,
            AuditEvent as _AE0,
            record_deploy as _rd0,
        )
        _rd0(
            _AE0(APPROVE_PENDING_APPLIED, at=datetime.now(timezone.utc),
                 message="待批操作批准并落盘（approve 本体的独立留痕）",
                 data={"approved_by": reviewer, "pending": op_id,
                       "name": result.get("name", ""),
                       "version": result.get("version"),
                       "self_check": self_check or None}),
            store,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("APPROVE_PENDING_AUDIT_RECORD_FAILED 批准已落盘但记账失败：%r", exc)
    # approve 后自动起 watch（真机常驻监听）——合并所有已部署自动化，不只这一条。
    # 凭据语义：MCP 路径 reviewer=auth_code:xx***（码已消费）、HTTP 路径 reviewer=令牌
    # 主体/人审（人审那一票）——这条是四条起监听路径之一，其余三条见
    # `start_watch` / `_restore_watch_after_restart` / `_ensure_watcher` 各自的凭据语义。
    # （路径清单与凭据口径：DCD 裁定 20261031 §2.2「起监听＝四路径」。）
    # approve 后自动起真机 watch 的审计留痕（20261031 裁定 §2.2 附带②）：这一跳含
    # 真机常驻监听，凭据语义在 reviewer 里（MCP=码已消费、HTTP=人审/令牌主体）。
    try:
        merged = _build_merged_ir(store)
        if merged:
            watch_res = _start_watch_impl(ir=merged, store_root=str(store.root), dry_live=False)
            result["watch"] = watch_res
            # 记账失败不回滚已起的监听（口径同 start_watch 的 WATCH_START_AUDIT_RECORD_FAILED）。
            try:
                from .af_audit import AuditEvent as _AE2, record_deploy as _rd2
                _rd2(
                    _AE2("APPROVE_WATCH_AUTO_STARTED", at=datetime.now(timezone.utc),
                         message="approve 后自动起真机 watch（含真机常驻监听）",
                         data={"approved_by": reviewer, "pending": op_id,
                               "live_watch": True, "dry_live": False,
                               "watch_ok": bool(watch_res.get("ok")),
                               "pid": watch_res.get("pid")}),
                    store,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("APPROVE_WATCH_AUDIT_RECORD_FAILED 批准后监听已起但记账失败：%r", exc)
    except Exception as e:
        logger.warning("approve_pending: 自动起 watch 失败（不影响落盘）: %r", e)
        result["watch_error"] = str(e)
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
    - 读盘/解析失败 → 记日志并返回**全拒注册表**（AF49-R，fail-closed）：
      一切实体命中 Tier-0 必须人审。与"文件不存在"（None=不启用）刻意区分——
      坏策略文件不再被当成"没配保护"而静默放行。

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
        # AF49-R：读失败不再返回 None（=fail-open，与"没配保护"不可区分），
        # 改为全拒注册表（fail-closed）。副作用：坏 device_acl.json 会让每次 scan 全量报
        # Tier-0 ERROR——这是刻意取舍（错误可见度 > 可用性，与 AF16b 同口径）。
        logger.error("DEVICE_ACL_LOAD_FAILED path=%s err=%s —— 按最严处理（全拒注册表）", path, exc)
        return DeviceGuardRegistry.deny_all_registry(
            f"device_acl.json 读取失败（{type(exc).__name__}: {exc}）"
        )


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
    # 反序列化/Schema/整篇深度这一关本身可能抛 IRValidationError（含 IRDepthError）。
    # 旧实现让它冒泡成 500，调用方拿不到结构化错误（2026-10-17 敲打 N04：100 层嵌套在
    # load_graph 即被 128 深度上限拒收，但对外是 500 而不是 build 回执里的 ok=false）。
    try:
        graph = _load_ir(ir)
    except IRValidationError as exc:
        out = {
            "ok": False,
            "errors": [{"code": "IR_SCHEMA_INVALID", "message": str(exc)}],
            "warnings": [],
            "diagnostics": [{"code": "IR_SCHEMA_INVALID", "severity": "error", "message": str(exc)}],
            "nl": "（IR 结构非法，未生成自然语言预览）",
            "entity_check": "catalog" if catalog_used else ("provided" if known is not None else "skipped"),
            "schema": STAGE_SCHEMA,
        }
        return attach_telemetry(out, tool="build", root=(store.root if store is not None else None),
                                ok=False, message=str(exc))
    result = StaticScanner(
        graph,
        known_entities=known,
        device_guard=load_device_guard(store),
        entity_health=load_entity_health(store),
    ).scan()
    # NL 渲染必须对坏图兜底（2026-10-17 敲打 N04 真根因）：扫描判红后，旧实现仍无条件
    # render_graph，而渲染器会重新走表达式/图结构——40 层嵌套表达式在 nl._expr_text 直接抛
    # ExprError，把整个 build 打成未受控 500，扫描本已给出的 EXPR_INVALID 反而被异常吞掉。
    # 规则：有 ERROR 不渲染（给降级文案）；无 ERROR 时渲染也包 try，渲染器任何异常都不得反噬
    # 第一道闸的结论。
    nl_text = ""
    if result.ok:
        try:
            nl_text = render_graph(graph).text
        except Exception as exc:  # noqa: BLE001 - 渲染只是展示层，不能盖掉扫描结论
            logger.warning("BUILD_NL_RENDER_FAILED NL 渲染失败（不影响扫描结论）err=%r", exc)
            nl_text = f"（自然语言预览生成失败：{type(exc).__name__}: {exc}）"
    else:
        nl_text = "（图存在错误，未生成自然语言预览，请先修复下方 errors）"
    out = {
        "ok": result.ok,
        "errors": [_diag(d) for d in result.errors],
        "warnings": [_diag(d) for d in result.warnings],
        "diagnostics": _diagnostics(result),
        "nl": nl_text,
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
            # 与 CLI 版（af_cli._replay）对齐：`to` 可代替 `state`（新状态），`from` 注入为
            # old_state（trigger.from 校验需要）。旧版只认 `state`，导致调用方按触发词汇写
            # `to` 时事件状态恒为空串，Scheduler 永远不匹配 → 仿真"跑过了"其实是空跑
            #（2026-10-17 敲打 S01 实测 af_service 与 af_cli 未同步）。
            new_state = str(item.get("state", item.get("to", "")))
            payload = {
                k: v
                for k, v in item.items()
                if k not in {
                    "entity_id", "state", "to", "from", "last_changed", "advance_s", "answer"
                }
            }
            if "from" in item and "old_state" not in payload:
                payload["old_state"] = item["from"]
            states.set(str(entity_id), new_state)
            runtime.emit(
                str(entity_id),
                new_state,
                last_changed=item.get("last_changed"),
                **payload,
            )
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


def _cancelled_rows(insts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """AF-782：被取消实例的回执行（reason 取 `context.cancel_reason`，⛔ 不读 fail_reason）。

    两者是不同来源：`fail_reason` 只在求值/动作失败时写，cancelled 实例没有那个键
    ——修前共用一列，回执里 cancelled 的 reason 恒为空串。
    """
    return [
        {"instance_id": i.get("instance_id"),
         "node": i.get("current_node"),
         "reason": str(((i.get("context") or {}).get("cancel_reason")) or "")[:220]}
        for i in insts
    ]


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
    # 契约泳道 B3/B4（2026-10-29）：`ok` 恒 True 的旧口径把两件不同的事混成一件——
    # 「仿真过程没抛异常」被读成「这条自动化是好的」。实例 `state:"failed"`（求值失败、
    # 无 on_error 兜底）时回执照样 ok:true，而 af_apply 顶层又直接继承它 ⇒ 预演假绿。
    # 现在 ok 派生自实例终态；「压根没跑出实例」是另一档，用 verdict 明说（⛔ 不混成失败，
    # 也不许继续冒充通过——那是 D 类：回执缺"没验到"这一档）。
    inst_states = [str(i.get("state") or "") for i in (out.get("instances") or [])]
    # AF-782（2026-11-14，§1.220 裁定）：**cancelled 不是 failed**，必须分开列。
    # 修前两者并进 `failed_insts` ⇒ `mode=restart` 的自动化每预演一次就报 `verdict=failed`
    # （第二次触发抢占第一次实例就会产生 cancelled），现场于是出现"预演永远红"，
    # 真失败反而淹在噪声里（QA-MCP R6 的 restart 双触发腿就是这么被误判的）。
    # 但⛔ 也不许把 cancel 悄悄丢掉（2026-10-29 反假绿意图）：它单列成
    # `cancelled_instances`，reason 取 `context.cancel_reason`（修前读 `fail_reason`，
    # cancelled 实例根本没有那个键 ⇒ 回执里恒空串，等于"取消了但不知道为什么"）。
    failed_insts = [i for i in (out.get("instances") or [])
                    if str(i.get("state") or "") == "failed"]
    cancelled_insts = [i for i in (out.get("instances") or [])
                       if str(i.get("state") or "") == "cancelled"]
    if failed_insts:
        out["ok"] = False
        out["verdict"] = "failed"
        out["failed_instances"] = [
            {"instance_id": i.get("instance_id"),
             "node": i.get("current_node"),
             "reason": str(((i.get("context") or {}).get("fail_reason")) or "")[:220]}
            for i in failed_insts
        ]
        if cancelled_insts:
            # 混合档：真失败压过取消（verdict=failed），但两清单都在（⛔ 不丢信息）
            out["cancelled_instances"] = _cancelled_rows(cancelled_insts)
    elif cancelled_insts:
        # 纯 cancelled ⇒ 设计内动作（restart 抢占/人工取消）：跑过了，不是失败。
        out["ok"] = True
        out["verdict"] = "ran"
        out["cancelled_instances"] = _cancelled_rows(cancelled_insts)
        out["note"] = ("有实例被取消（设计内：restart 抢占旧实例／人工取消），已与失败分开"
                       "列入 cancelled_instances ⇒ 不算失败；⚠️ 但也不等于「验过了」——"
                       "要验链路请看过 expect 栏。")
    elif not inst_states:
        out["ok"] = True          # 没跑出失败，但也没验到任何东西
        out["verdict"] = "nothing_triggered"
        out["nothing_verified"] = True
        out["note"] = ("仿真没产生任何实例（多半是没合成触发事件）。这不等于通过："
                       "要验链路过调用 af_apply(stage=dry_run)，它会从图自身触发源合成事件。")
    else:
        out["ok"] = True
        out["verdict"] = "ran"
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


def _assert_archive_mutator(
    store: GraphStore, name: str, *, caller: str = "", caller_is_owner: bool = False,
    action: str = "修改其标签/启停", token_identity: bool | None = None,
) -> None:
    """D4 归属判定：改标签/启停/批量按标签启停前，确认调用方有权动这条归档。

    - 归档已归属他人：调用方须是归属人本人或 owner 面（`owner`/`shared`）；
      未携带身份（旧调用方/MCP 未透传 `current`）不阻断，留给后续批次收口。
    - 归档无归属：仅 owner 面可改（fail-closed，不因 D5 未修而漏）；未携带身份的旧调用方放行。

    ⛔ 拒绝文案不点归属者的名（审计 AF-509）：`set_tags/enable/delete` 共用这条文案，
    写上"归属 'agent-b'"等于给调用方一条批量采集全库归属者名单的通道——与同批
    `get_graph` 的「与不存在逐字同形」自相矛盾。归属者是谁，owner 面自己查去。

    界线判据（AF-701 收口，20261031 裁定 §2.1）：豁免只对"根本没有令牌身份"的本地
    CLI／内部调用保留。`token_identity` 的判定规则：

    - `token_identity is None` → 调用方没声明自己是否持令牌（旧调用方/纯本地路径），
      按"无令牌身份"处理，豁免维持；
    - `token_identity=True` → 持有令牌。此时 `caller==""` 即"持令牌却缺 subject"（残缺身份），
      界线之内**不放行**——这一档在 MCP/HTTP 入口已被拒，这里写判据钉住，⛔ 靠注释。

    ⛔ 不再把服务层豁免整体删除：那会打断本地 CLI／内部调用（裁定 §四 ⛔ 清单第 1 条）。
    """
    prev = _existing_owner(store, name)
    sub = Subject.from_caller(caller, owner_face=caller_is_owner, token_identity=token_identity)
    # C-3：verdict 单源 decide（判定表六行在 af_ownership，⛔ 这里再内联第二份归属规则）；
    # 文案单源三选一：残缺身份（broken_identity_error）／写拒不点名（mutator_error，AF-509）／
    # 无归属 fail-closed（既有专文案，判据在钉，⛔ 并进 mutator_error 丢掉"无归属"这一因）。
    if sub.token_state is TokenState.BROKEN:
        raise ServiceError(broken_identity_error(), status=403)
    if decide(lambda _n: prev, name, sub, write=True) is DenyKind.FORBIDDEN:
        if prev:
            raise ServiceError(mutator_error(name, action), status=403)
        raise ServiceError(
            f"归档 {name!r} 无归属，仅 owner 面可{action}（所有权隔离）",
            status=403,
        )


def _assert_archive_visible(
    store: GraphStore, name: str, *, caller: str, caller_is_owner: bool,
    token_identity: bool | None = None,
) -> None:
    """读面归属判定（审计 AF-504）：非 owner 面读别人的归档时抛 404——
    文案与「归档不存在」逐字同形（`visible_error` 单源，与 `_t_get_graph` 收口面
    同款，⛔ 不回"无权查看"：那等于承认存在）。未携带身份的旧调用路径不阻断
    （渐进收口）。C-3：verdict 走 `decide` 单源；BROKEN（持令牌缺 subject）⇒
    403 残缺身份（R1：读面 ⛔ 不得退化成匿名全量）。
    """
    sub = Subject.from_caller(caller, owner_face=caller_is_owner, token_identity=token_identity)
    if sub.token_state is TokenState.BROKEN:
        raise ServiceError(broken_identity_error(), status=403)
    if decide(lambda _n: _existing_owner(store, name), name, sub,
              write=False) is DenyKind.NOT_FOUND:
        raise ServiceError(visible_error(name), status=404)


def _check_blast(count: int, what: str) -> None:
    """超过爆炸半径即拒绝——**提示「请拆分」而不是硬拒到无法工作**。"""
    limit = blast_radius_limit()
    if limit > 0 and count > limit:
        raise ServiceError(
            f"{what} 会影响 {count} 条自动化，超过爆炸半径上限 {limit}；"
            f"请拆分为更小的操作（或调高 AUTOFORGE_BLAST_RADIUS；置 0 表示不限）",
            status=400,
        )


def get_conf(
    store: GraphStore, name: str, *, caller: str = "", caller_is_owner: bool = False,
    token_identity: bool | None = None,
) -> dict[str, Any]:
    """置信度读面。C-3 收口：BROKEN（持令牌缺 subject）⇒ 403 残缺身份（R1）。

    审计 AF-504：单名面非 owner 读别人的归档回 404 同形文案（verdict 走 decide 单源）。
    R2：`_all` 合并面**按份额聚合**——非 owner 只含自己名下归档的行，读不出的进
    `unavailable`；⛔ 整面 404（那会把整个置信度面板对 agent 关死）、⛔ 全库聚合。
    `ok` 派生自展开合拢（合并面＝contributed+unavailable==visible；单名面＝每条
    自动化都有置信度行），⛔ 字面量 True。
    """
    sub = Subject.from_caller(caller, owner_face=caller_is_owner, token_identity=token_identity)
    if sub.token_state is TokenState.BROKEN:
        raise ServiceError(broken_identity_error(), status=403)
    owner_of = lambda n: _existing_owner(store, n)
    if name in _ALL_NAMES:
        names = _archive_names(store)
        visible, hidden = scope_names(owner_of, names, sub)
        merged: dict[str, dict[str, Any]] = {}
        unavailable: list[str] = []
        loaded = 0
        for archive in visible:
            try:
                graph = store.load(archive)
            except (FileNotFoundError, IRValidationError, OSError):
                unavailable.append(archive)
                continue
            loaded += 1
            conf = _conf_of(store, archive, graph)
            for item in _conf_items(graph, conf):
                merged.setdefault(item["automation_id"], item)
        out: dict[str, Any] = {
            "ok": loaded + len(unavailable) == len(visible),
            "name": name,
            "version": None,
            "items": sorted(merged.values(), key=lambda x: x["automation_id"]),
            "thresholds": {"auto": AUTO_MIN, "shadow_low": SHADOW_LOW},
        }
        if caller and not caller_is_owner:
            out["hidden_count"] = hidden
            out["scope"] = f"owner:{caller}"
        if unavailable:
            out["unavailable"] = sorted(unavailable)
        return out

    # 单名面：verdict 落在名字上（他人/无归属 ⇒ 404 同形，⛔ 整面 404 只属于旧形状）
    if decide(owner_of, name, sub, write=False) is DenyKind.NOT_FOUND:
        raise ServiceError(visible_error(name), status=404)
    record = store.load_record(name)
    graph = load_graph(record["graph"])
    conf = _conf_of(store, name, graph)
    items = _conf_items(graph, conf)
    return {
        "ok": len(items) == len(list(graph)),
        "name": name,
        "version": record.get("version"),
        "items": items,
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


def _load_or_404(store: GraphStore, name: str, version: int):
    """归档读不到 ⇒ 类型化 404，⛔ 不让 FileNotFoundError 冲到工具面。

    体验者 P0-2 实测：`af_diff` 对不存在的名字直接崩成 `ADM_ERR_INTERNAL`，
    message 里还回显了服务器绝对路径（`/data/<name>/v1.json`）——把部署布局
    交给了对端。这里两处一起堵：错误分类正确 + 文案不含路径。
    """
    try:
        return store.load(name, version)
    except FileNotFoundError as exc:
        raise ServiceError(
            f"归档 {str(name)!r} 没有版本 v{version}（原始错误：{type(exc).__name__}）",
            status=404,
        ) from exc


def diff(
    store: GraphStore, name: str, old: int, new: int, *,
    caller: str = "", caller_is_owner: bool = False,
    token_identity: bool | None = None,
) -> dict[str, Any]:
    # 审计 AF-504：版本差异读面按归属收口——diff 会吐他人 IR 的增删行，不只是元数据
    # C-3：verdict 走 _assert_archive_visible 内的 decide 单点；BROKEN ⇒ 403（R1）。
    _assert_archive_visible(store, name, caller=caller, caller_is_owner=caller_is_owner,
                            token_identity=token_identity)
    old_graph = _load_or_404(store, name, old)
    new_graph = _load_or_404(store, name, new)
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
                # v1.28 R1（PM 契约）：来源标只在"已答未消费"时有值（如拒收后保持挂起），
                # 其余为 None ⇒ UI 不显示标签、⛔ 不猜。
                "answered_via": getattr(session, "answered_via", None),
            }
        )
    return out


def asks_pending(store_root: str | None = None) -> dict[str, Any]:
    """v2 M3：聚合所有在途会话的挂起 ask，含 `spec` 与控件 `control` 元数据。

    与 `/api/asks/pending`（watch sidecar，进程间发现用）不同，本函数读**进程内
    会话**——仿真会话下的挂起 ask 同样可见（sidecar 只在 watch 真机模式写出）。
    含 prompt/room 等敏感信息，端点需 read 鉴权。

    v1.28 R1（PM 2026-11-06 拍板）：额外附带 `answered`——最近 **5** 条已应答
    （新→旧，含 `answered_via` ＋ `answered_at`），正源是 `<archive>.runtime.json`
    的 `ask_answers` 环形。⛔ 不为此新建持久化面；`store_root` 为 None 时
    （单测/无 store 装配）`answered` 如实给空表，⛔ 不伪造。
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
    return {
        "ok": True,
        "total": len(out),
        "asks": out,
        "answered": recent_ask_answers(store_root),
    }


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
#   2. 服务端令牌 `HA_LONG_LIVED_TOKEN`（旧 `AUTOFORGE_HA_TOKEN`，**绝不从请求体接收令牌**）
#   3. 请求体 `confirm=true` + 非空 `live_allow` 白名单，且 IR 的写目标 ⊆ 白名单
# 复用 `af_scanner.live_preflight` 的语义（与 CLI `forge run --live` 一致口径）。
# ─────────────────────────────────────────────────────────────────────

#: 测试可注入的 transport 工厂：`(ha_url, token) -> transport`；默认 None 表示用真实 HATransport
LIVE_TRANSPORT_FACTORY: Callable[[str, str], Any] | None = None


def _ha_token(store_root: str | Path | None = None) -> str:
    """B 类 AF→HA 长期令牌——**统一入口**，⛔ 各路径不得自取。

    契约 R4-1（2026-10-29 实测）：写路径（live_run/undo）此前只读 env，读路径（catalog）
    与 watch 子进程却走 `Config.get_ha_token()`（credentials.json 优先）——同一服务器
    两套 token 源。env 那枚失效后：读工具一直"健康"、写工具一直 401、watch 起不来，
    三面互相对不上，运维无从察觉（体验者原话：防线是运气不是设计）。
    统一到 `Config.get_ha_token()`：credentials.json 优先（WebUI 填的那份，实测有效），
    env 回退——与读路径同一把钥匙。`store_root` 给出时读该 store 的凭据，缺省读 cwd。
    """
    from .af_config import Config
    try:
        return Config(store_root).get_ha_token() if store_root else Config().get_ha_token()
    except Exception:  # Config 构造失败（无 store 根等）→ 退回 env 旧口径，行为不比以前差
        return os.getenv("HA_LONG_LIVED_TOKEN") or os.getenv("AUTOFORGE_HA_TOKEN") or ""


def ha_token_sources(store_root: str | Path | None = None) -> dict[str, Any]:
    """双源健康暴露（R4-1 的运维侧）：谁在读哪把钥匙，一目了然。"""
    env_tok = os.getenv("HA_LONG_LIVED_TOKEN") or os.getenv("AUTOFORGE_HA_TOKEN") or ""
    file_tok = ""
    try:
        from .af_config import Config
        file_tok = Config(store_root).get_ha_token() if store_root else Config().get_ha_token()
    except Exception:
        file_tok = ""
    return {
        "env_token_present": bool(env_tok),
        "config_token_present": bool(file_tok),
        "sources_agree": (bool(env_tok) == bool(file_tok)) or (env_tok == file_tok),
        "note": "写路径与读路径已统一走 Config（credentials.json 优先）；"
                "sources_agree=False 表示 env 与文件只配了一边——能起但另一面会退化",
    }


def _live_config() -> dict[str, Any]:
    return {
        "enabled": os.getenv("AUTOFORGE_LIVE_ENABLED", "").strip().lower() in ("1", "true", "yes"),
        "ha_url": os.getenv("AUTOFORGE_HA_URL", DEFAULT_HA_URL),
        "has_token": bool(_ha_token()),
    }


def _advance_canary_windows(runtime: Any, *, max_rounds: int = 8) -> int:
    """D1 后半（2026-11-05）：**单发模式有界推进 canary 观察窗**。

    `live_run` 是同步单发：跑完一段就返回，**没有任何 ticker** ⇒ 挂在 `canary_observe` 的实例
    永远等不到 `due_timers()`，L2 动作就此停在 `pending_canary`（真机实证：d2 set_temperature
    卡死、温度写从未到空调）。这里在返回前把观察窗**有界地**走完：每轮把虚拟钟推到最近一个
    到期点再 `tick()`，让 D8 的恢复腿把结局落成 verified／rolled_back／unmodeled。

    ⛔ 只推进处于观察期的实例；无 `advance` 能力（真时钟）⇒ 直接 no-op（不伪造时间）。
    """
    advanced = 0
    clock = getattr(runtime, "clock", None)
    if clock is None or not hasattr(clock, "advance"):
        return 0
    for _ in range(max(0, int(max_rounds))):
        waiting = [
            i for i in runtime.instances.all()
            if str(getattr(i, "state", "")) == "suspended"
            and isinstance(i.ctx.context.get("pending_canary"), dict)
        ]
        if not waiting:
            break
        wait = 0.0
        for inst in waiting:
            timer = getattr(inst, "timer", None)
            if timer is not None:
                wait = max(wait, float(getattr(timer, "due_at", 0.0)) - clock.monotonic())
        clock.advance(wait + 0.001)
        runtime.tick()
        advanced += 1
    return advanced


def _dispatch_verdict(runtime) -> dict[str, Any]:
    """live_run 的执行裁决：下发动作的**实际结果**，⛔ 不许让 `writes`（声明清单）冒充。

    R4-2 实测：动作被 HA 401 拒后回执仍 ok:true + writes:[吊灯]——四组证据
    （instances/bus/final_states/audit）与顶层结论打架。审计里的 `action_failed`
    / `af_action_dispatched` 是唯一执行真值源，从那儿算。
    """
    audit = [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in runtime.audit]
    # AF-738（2026-11-06）：`af_action_dispatched` 由影子/干预层在"要发"时写，而 `live_run`
    # 不接影子层 ⇒ 只认它会恒报 `nothing_dispatched`（真实下发看不见的假红）。
    # 执行器现在在**真调用适配器**那一刻写 `af_action_called`（watch 与单发都有）⇒ 优先认它。
    called = [a for a in audit if a.get("type") == "af_action_called"]
    dispatched = called or [a for a in audit if a.get("type") == "af_action_dispatched"]
    failed = [a for a in audit if a.get("type") == "action_failed"]
    # 审计 AF-301：我上一版写出 `"failed" if dispatched else "failed"` 这种两支同值的
    # 退化三元——读起来像有分支、实际没有，而且注释承诺过 partially 档却永远出不了。
    # 真语义：有成有败=partially_failed（⛔ 不许被成功那半掩盖）；全败=failed；
    # 只成无败=executed；两者皆空=nothing_dispatched。
    if failed and dispatched:
        verdict = "partially_failed"
    elif failed:
        verdict = "failed"
    elif dispatched:
        verdict = "executed"
    else:
        verdict = "nothing_dispatched"
    # AF-738 补：`dispatched` 是「尝试下发」数，`failed` 是其中失败数。**一次尝试一次失败**
    # 必须报 `failed`，⛔ 不许因为「尝试数>0 且 失败数>0」就报 partially_failed（那是把单次失败
    # 说成"有成功有失败"）。partially_failed 只在**确有成功**时成立。
    if verdict == "partially_failed" and len(dispatched) <= len(failed):
        verdict = "failed"
    return {
        "verdict": verdict,
        "dispatched": len(dispatched),
        "failed": len(failed),
        "failed_detail": [str(a.get("message"))[:160] for a in failed[:3]],
    }


#: 点火面读数（AF-745 追加，2026-11-08 台账 §1.184 三发实测）。`live_run` 不是"提交就跑"：
#: 触发器只在回放 events 时才被喂到，**零 events ⇒ 图根本没跑**（`instances=[]`）。而回执此前
#: 只给 `dispatch_result=nothing_dispatched`——这一个档同时罩住"没点火"、"点了没匹配到条件"、
#: "跑了但没动作"三种情形，操作员分不清是「我的调用方式错了」还是「设备/下发失败」。
IGNITION_NO_EVENTS = "no_events"
IGNITION_NO_TRIGGER = "events_no_trigger"


def _ignition_of(events: Sequence[Mapping[str, Any]] | None, runtime: Any) -> dict[str, Any]:
    """把"图到底跑没跑"单独量出来：`ignited`／`reason`／`events`／`instances`。

    ⛔ 与 `_dispatch_verdict`（跑起来之后动作成没成）**正交**，两面各自如实报：
    - 零 events ⇒ `no_events`（调用方漏注入 events，图没跑，⛔ 不许当成下发失败）；
    - 有 events 但一个实例都没起 ⇒ `events_no_trigger`（触发条件未满足，去查实体/条件）；
    - 起了实例 ⇒ `ignited=True`；动作成败仍看 `dispatch_result`（"点火成功＋执行失败"是合法组合）。
    """
    n_events = len(events or [])
    n_instances = len(runtime.instances.all())
    if n_events <= 0:
        return {"ignited": False, "reason": IGNITION_NO_EVENTS, "events": 0, "instances": n_instances}
    if n_instances <= 0:
        return {"ignited": False, "reason": IGNITION_NO_TRIGGER, "events": n_events, "instances": 0}
    return {"ignited": True, "reason": "", "events": n_events, "instances": n_instances}


def live_status() -> dict[str, Any]:
    """真机下发的可用性（供前端渲染开关/禁用原因）。"""
    cfg = _live_config()
    reasons: list[str] = []
    if not cfg["enabled"]:
        reasons.append("服务端未启用真机下发（需设置 AUTOFORGE_LIVE_ENABLED=1）")
    if not cfg["has_token"]:
        reasons.append("服务端缺少 HA 长期令牌（需设置 HA_LONG_LIVED_TOKEN，旧 AUTOFORGE_HA_TOKEN 亦可）")
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


def _auth_code_check(store: "GraphStore | None", code: str, *, action: str) -> str:
    """服务层授权码闸（审计 AF-401/408，2026-10-30）。

    为什么从 af_mcp 的 dispatch 下移到这层：闸装在工具层时 REST 直连
    （`/api/live/run`、`/api/graphs/enable`）不收 auth_code ⇒ 换条路就绕过；
    而且它排在 `svc.live_run` 外层最先跑，把两条既有不变量打穿了——
    ① events 形状检查（422「形状先于策略」）读不到；② 单写者租约的
    `READONLY_DEGRADED:` 前缀（DB 按前缀判读，裁定 20261004 §一 1 A）被抢先。
    测试红了，当时的"修法"是给测试配码让它过——那是致盲哨兵，不是修问题。

    位置纪律（调用方遵守）：排在 `_check_events_cap` 与 `_single_writer_check`
    **之后**——形状错误与"服务不可写"比"缺凭据"更早该被看见。
    返回脱敏凭据标签（`auth_code:xx***`），调用方必须写进回执与审计
    （AF-403：过闸无痕 = 事后查不到哪把凭据放行）。
    """
    from .af_auth import AuthCodeStore
    code = str(code or "").strip()
    root = getattr(store, "root", None)
    if root is None:
        # AF-404：store=None（CLI/测试形状）——闸不能因缺根而 AttributeError
        # （AF-121 误因族新证例），也不该静默放行：没有账本就没有凭据。
        raise ServiceError(
            f"{action} 需要授权码，但本调用未携带 store（无凭据账本可查）——"
            f"HTTP/MCP 面都会带 store；CLI 直调请传入 store 或改走服务面",
            status=403,
        )
    if not code:
        raise ServiceError(f"{action} 需要授权码：在 WebUI/mimo 生成一枚，随 auth_code 传入",
                           status=403)
    acs = AuthCodeStore(Path(root) / ".auth" / "auth_codes.json")
    if not acs.validate(code):
        acs.record_failure(code)  # 连续 10 错锁定走既有机制
        # ⛔ 四因合一（不存在/已消耗/已过期/已锁定）：外传具体哪一因是授权码枚举面
        raise ServiceError(f"授权码无效（不存在/已消耗/已过期/已锁定之一），{action} 已拒绝",
                           status=403)
    return f"auth_code:{code[:2]}***"


def live_run(
    ir: Mapping[str, Any],
    live_allow: Sequence[str],
    events: Sequence[Mapping[str, Any]] | None = None,
    confirm: bool = False,
    store: "GraphStore | None" = None,
    undo: bool = False,
    clock: Any | None = None,
    auth_code: str = "",
    caller: str = "",
    caller_is_owner: bool = False,
    grant_id: str = "",
    grant_confirm: str = "",
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
        raise ServiceError("真机下发缺少 HA 令牌：服务端需设置 HA_LONG_LIVED_TOKEN（旧 AUTOFORGE_HA_TOKEN）", status=403)
    if not confirm:
        raise ServiceError("真机下发必须显式二次确认（confirm=true）", status=403)

    allow = {str(x) for x in (live_allow or []) if str(x)}
    if not allow:
        raise ServiceError("真机下发必须提供可写白名单（live_allow），先不开放全量", status=400)

    # ★ 单写者租约：生产 serve 在线时，进程外的真机写一律拒收（裁定 20261004 §一 1 A）。
    # 放在构造传输层之前——拒收的那条路径必须**根本不碰设备**。
    _single_writer_check(store)

    # ★ 授权闸（v1.29.0 起**两条互斥通道**：授权码 / 执行授权 grant）
    #   位置纪律不变（AF-401/408 下移服务层）：排在租约**之后**——只读降级的
    #   `READONLY_DEGRADED:` 前缀必须先于缺凭据被看见（DB 按前缀判读）；events 形状
    #   检查在本函数更早。放行凭据写进回执（AF-403：过闸无痕=事后查不到哪把放行）。
    #
    #   为什么 grant 是一条**与码并列**的通道，而不是又一道码：DCD 20261111 Q1 的
    #   双轨授权——码管**部署**、grant 管**执行**。用户给出长期码并"授予即时执行"是
    #   授权动作（计一次 use_count），此后日常执行不再找码（Q1 三件套③）。
    #   `auth_code` 与 `grant_id` 同时在场时一律拒：事后追查要的正是"这次谁放行的"，
    #   两把凭据并存那句话就说不清了（fail-closed）。
    grant_record: dict[str, Any] | None = None
    if grant_id:
        if auth_code:
            raise ServiceError(
                "真机下发一次只能带一种授权：grant_id 与 auth_code 不可同传（fail-closed）",
                status=403,
            )
        grant_record = _grant_resolve(store, grant_id, caller)
        code_tag = f"grant:{grant_record['grant_id']}"
    else:
        code_tag = _auth_code_check(store, auth_code, action="真机下发（live_run）")

        # D5 三门之③（2026-11-05，AF-PM 裁定）：**单发只认短期码**——长期码只准用于部署。
        # 理由：长期码可复用、只计 use_count，用它开「手动真机单发」等于把一条可反复使用的旁路
        # 长期挂在手上；短期码一次性的语义与「临时手动开一下」正好对齐。
        # ⚠️ 边界（Chief 口径）：本条只管**单发通道**，⛔ 不碰 watch 常驻执行与部署主路。
        # ⚠️ v1.29 补充：本条与 grant **不冲突**——grant 正是"长期码换来的执行授权"，
        # 长期码本身仍然一次也不进 live_run（它在签 grant 那一步就计过次了）。
        if auth_code and store is not None:
            from .af_auth import AuthCodeStore as _ACS

            acs = _ACS(Path(store.root) / ".auth" / "auth_codes.json")
            try:
                _recs = acs.list(reveal=True)
            except Exception:  # noqa: BLE001 —— 查不出 kind 时 fail-closed，⛔ 不猜测放行
                raise ServiceError("真机单发无法判定授权码类型：拒绝执行（fail-closed）", status=403)
            _kind = next((r.get("kind") for r in _recs if r.get("code") == auth_code), None)
            if _kind != "short":
                raise ServiceError(
                    "真机单发只认短期码：长期码只准用于部署，⛔ 不用于手动单发（D5 三门之③）",
                    status=403,
                )
            # AF-753（v1.29 收口修）：短期码是**一次性**凭据——上面的 `_auth_code_check`
            # 只 validate 不消费，不在这里核销，同一枚短期码就能反复单发（TTL 60s 只是把
            # 窗口压短，不是闭环）。与 grant 路径（`_grant_channel_tag` 的 `acs.consume`）
            # 同形：消费失败即 fail-closed——宁可拒掉这次下发，也不放行一枚已失效的凭据。
            if not acs.consume(auth_code):
                raise ServiceError(
                    "真机单发无法核销短期码（状态已变）：拒绝执行（fail-closed，AF-753）",
                    status=403,
                )

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

    # ★ v1.29.0：grant 通道的**范围／档位**判定。判定真源只有 `af_grant.GrantStore.check`，
    #   本层只负责喂它三样东西（动作清单／写目标／实体→区域的取值回调）并翻译成 ServiceError。
    #   位置刻意在**白名单之后**：形状错（写目标越出 live_allow）比策略拒更早该被看见，
    #   与授权码闸"排在形状检查之后"的同一条顺序纪律。
    grant_receipt: dict[str, Any] | None = None
    if grant_record is not None:
        grant_receipt = _grant_authorize_execution(
            store, grant_record, caller, graph, targets, grant_confirm=grant_confirm,
        )
        code_tag = f"grant:{grant_record['grant_id']}"

    # store.root 是凭据所在（credentials.json 与归档同根）；live_run 签名里没有
    # store_root 形参——上一版我写成 `_ha_token(store_root)` 会 NameError 被 except
    # 兜回 env（R4-1 未真修）。正确取法：从 `store` 形参取根。
    token = _ha_token(store.root if store is not None else None)
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
        "LIVE RUN：automation(s)=%s writes=%s events=%d authorized_by=%s",
        [a.id for a in graph],
        sorted(targets),
        len(events or []),
        code_tag,
    )
    # D1 后半（2026-11-05）：单发模式没有 ticker ⇒ 返回前把 canary 观察窗有界走完，
    # 否则 L2 动作永远停在 pending_canary（真机实证 d2 卡死）。⛔ 只推进观察期实例。
    try:
        _advance_canary_windows(runtime)
    except Exception as exc:  # noqa: BLE001 —— 推进失败不许把已发生的下发回滚掉
        logger.warning("LIVE_RUN_CANARY_ADVANCE_FAILED 观察窗推进异常（不影响已发生的下发）：%s", exc)
    # AF-746b（v1.28.6，出资人 22:01 A+ 裁决）：写后回读**只结到点的**（非 force）。
    # 单发是同步一次跑完、不等 3s 驻留 ⇒ 这里通常 `checks=0`，回执里如实是 0 而不是假绿的
    # `unverified`。回读真正生效面在 watch/sim（有 ticker 或虚拟钟推进）。
    try:
        runtime.executor.check_due_readbacks()
    except Exception as exc:  # noqa: BLE001 —— 回读结账失败不许把已发生的下发打崩
        logger.warning("LIVE_RUN_READBACK_FAILED 回读结账异常（不影响已发生的下发）：%s", exc)
    # AF-745 追加（2026-11-08）：点火面读数取在**下发与观察窗之后**——它要反映"图最终跑出哪些
    # 实例"，⛔ 不是"events 列表长度"。同一枚事实进两处：审计流水（列表面据它分档）与回执。
    ignition = _ignition_of(events, runtime)
    # DCD 裁定 2026-10-30（AF-403 后半）：过闸凭据标签进审计流水（deploy_audit.jsonl
    # 随 store 根落盘）——回执给调用方，流水给事后追查；过闸无痕＝查不到哪把凭据放行。
    # 记账失败不回滚已发生的下发（口径同 _persist_deploy：操作员连撤销把手都拿不到更糟）。
    try:
        from datetime import datetime as _dt, timezone as _tz
        from .af_audit import AuditEvent as _AE, record_deploy as _rd
        _data = {"authorized_by": code_tag, "writes": sorted(targets),
                 "verdict": _dispatch_verdict(runtime),
                 "actions": len(sorted(targets)),
                 # AF-745 追加：点火档进流水，列表面才分得开「没点火」与「跑了没动作」
                 "ignition": str(ignition["reason"] or "ignited"),
                 "graph": [_auto_name(a) for a in graph],
                 "subject": str(caller or "")}
        if grant_receipt is not None:
            # v1.29.0 Q4（执行留痕与查询留痕同形）：which grant 就记在这一条既有事件里，
            # ⛔ 不再写一枚 GRANT_EXECUTED——两条流水会立刻各自漂成两套口径。
            _data["grant"] = grant_receipt
        _rd(
            _AE("LIVE_RUN_EXECUTED", at=_dt.now(_tz.utc),
                message=f"live_run executed ({len(events or [])} events)",
                # v1.28 R3（PM 核准）：回执读面要「图名」与归属主体 ⇒ 事件里补两枚键；
                # `subject` 缺它就是非 owner 过滤与 hidden_count 都无从算起（卡 §四点名）。
                data=_data),
            store,
        )
    except Exception as exc:
        logger.warning("LIVE_RUN_AUDIT_RECORD_FAILED 下发已执行但记账失败：%s", exc)
    return {
        "ok": True,
        "mode": "live",
        "live": True,
        "authorized_by": code_tag,
        # v1.29.0：grant 通道的回执块（which grant／档位／是否走了单次放行）。
        # 码通道**不出现这枚键**——回执形状要能一眼分出"这次是码放行还是授权放行"。
        **({"grant": grant_receipt} if grant_receipt is not None else {}),
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
        # 契约 R4-2（2026-10-29）：`writes` 是 IR **声明**的写目标清单，不是执行记录；
        # HA 401 那次回执 ok:true + writes:[吊灯] —— 自相矛盾。这里把"实际执行裁决"
        # 显式化：全部实例终态决定 dispatched/failed/partially/nothing。
        # AF-745 追加（2026-11-08，卡面新增证据）：**点火面与执行面分开报**。零 events 时
        # `instances=[]`＋`dispatch_result=nothing_dispatched`，此前回执读起来像"跑了但没动作"，
        # 实情是**图根本没跑**（`_replay_live` 只在有 events 时才 emit）。`ignition.ignited=false`
        # 直接指向调用方式/触发条件，⛔ 不改 `ok`／`dispatch_result` 语义（未点火也不是下发失败）。
        "ignition": ignition,
        "dispatch_result": _dispatch_verdict(runtime),
        # AF-745（2026-11-08）：**播报去回执**。现场（出资人三连跑纯播报 IR）实例全部走到
        # e1 done、收件账零新增、docker logs 零命中——播报是纯旁路，失败时三条腿都静默
        # （无桥 no-op／房间解析不到拒投／发布异常只吞 warning），回执里连"有没有试过"都看不出。
        # 这里把三条腿的事实计数原样带回：requested／published／failed／last_error／last_room。
        # ⛔ 不改变 `ok`／`dispatch_result` 语义（播报失败不等于下发失败），⛔ 不静默。
        "speech": dict(getattr(runtime.executor, "speak_stats", None) or {}),
        # AF-746b（v1.28.6，出资人 22:01 A+ 裁决）：**写后回读**读数。两档语义写死在这里：
        # `mismatch`＝读到别的值（有牙，已记 ACTION_FAILED）；`unverified`＝读到写入值，
        # **⛔ 不是 passed**（HA 回显与设备推送同形，卡 §十.4.1）。单发不等驻留 ⇒ `checks` 常为 0。
        "readback": dict(getattr(runtime.executor, "readback_stats", None) or {}),
    }


# ─────────────────────────────────────────────────────────────────────
# v1.29.0 第一批：执行授权（grant）服务面——通道、留痕、读面
#   判定真源只有 `af_grant.GrantStore`（check/issue_confirm/consume_confirm）；
#   本层只做三件事：**跨通道凭据校验**、把拒绝翻译成 ServiceError、把事实写进既有台账。
# ─────────────────────────────────────────────────────────────────────

def _grant_store(store: "GraphStore | None") -> GrantStore:
    """授权账本句柄。位置与 `af_auth` 的码账本同目录（`.auth/`）——凭据与授权一处，不开第二处根。"""
    root = getattr(store, "root", None)
    if root is None:
        raise ServiceError(
            "执行授权需要 store 根（账本在 .auth/grants.json）——本调用未携带 store，无法判定授权",
            status=403,
        )
    return GrantStore(Path(root).joinpath(*GRANT_FILE_REL))


def _grant_room_of(store: "GraphStore | None"):
    """实体 → 区域名的取值回调（**值源**：设备目录快照，离线可读，⛔ 不打网络）。

    形状与 `af_ownership.decide(owner_of=…)` 同族：判定在 `af_grant`、取值在这里注入，
    ⛔ 两边各存一份归属/区域解析。`DeviceCatalog.area_of` 只读 `.catalog/catalog.json`。
    """
    catalog = DeviceCatalog(Path(str(getattr(store, "root", DEFAULT_STORE_ROOT))))
    cache: dict[str, str] = {}

    def room_of(entity_id: str) -> str:
        key = str(entity_id or "")
        if key not in cache:
            cache[key] = catalog.area_of(key)
        return cache[key]

    return room_of


def _grant_error(exc: GrantError) -> ServiceError:
    """把授权面异常翻成服务层错误（status 原样保留：403 要凭据／400 请求形状／500 盘坏了）。"""
    return ServiceError(str(exc), status=int(getattr(exc, "status", 400) or 400))


def _grant_resolve(store: "GraphStore | None", grant_id: str, caller: str) -> dict[str, Any]:
    try:
        return _grant_store(store).resolve(grant_id, agent=caller)
    except GrantError as exc:
        raise _grant_error(exc) from exc


def _grant_channel_tag(
    store: "GraphStore | None",
    auth_code: str,
    *,
    caller: str,
    caller_is_owner: bool,
    action: str,
    consume: bool,
) -> str:
    """签发/撤销/续期 grant 的**凭据通道**：owner 面直通，其余**只认长期码**。

    依据 DCD 20261111 §4.1 双轨授权：执行授权的授予只认长期码（短期码是一次性部署凭据）。
    `consume=True` 时按 Q1 三件套③计一次 `use_count`（授权动作计次、日常执行不再耗码）；
    撤销/续期这类**缩小或维持**权限的动作 `consume=False`——撤销还要耗一次额度就没人敢撤销了。
    """
    if not auth_code:
        if caller_is_owner:
            return f"owner_token:{caller or 'owner'}"
        raise ServiceError(
            f"{action} 需要长期授权码（或 owner 令牌）：执行授权⛔ 不接受短期码——"
            f"短期码是一次性的部署凭据，不能授予执行权限（DCD 20261111 Q1 双轨授权）",
            status=403,
        )
    # 四因合一的校验与失败计数走既有那一枚（⛔ 另写一套凭据判定）。
    tag = _auth_code_check(store, auth_code, action=action)
    from .af_auth import AuthCodeStore as _ACS

    if store is None:  # pragma: no cover —— _auth_code_check 已先拒，留作形状自证
        raise ServiceError(f"{action} 需要授权码，但本调用未携带 store", status=403)
    acs = _ACS(Path(store.root) / ".auth" / "auth_codes.json")
    try:
        recs = acs.list(reveal=True)
    except Exception:  # noqa: BLE001 —— 判不出类型就 fail-closed，⛔ 不猜
        raise ServiceError(f"{action} 无法判定授权码类型：拒绝执行（fail-closed）", status=403)
    kind = next((r.get("kind") for r in recs if r.get("code") == auth_code), None)
    if kind != "long":
        raise ServiceError(
            f"{action} 只认长期码：短期码不能授予执行权限（DCD 20261111 Q1 双轨授权）",
            status=403,
        )
    if consume and not acs.consume(auth_code):
        raise ServiceError(f"{action} 无法计次（授权码状态已变）：拒绝执行（fail-closed）", status=403)
    return tag


def _grant_confirm_question(check: Any) -> str:
    """越界确认的**一句人话**（agent 直接念给用户听的那种，⛔ 术语堆）。"""
    names = "、".join(check.out_of_scope) if check.out_of_scope else "这个动作"
    return f"{names} 不在当前执行授权范围内。要这次放行一次吗？（⛔ 不会扩大授权范围）"


def _grant_authorize_execution(
    store: "GraphStore | None",
    grant: Mapping[str, Any],
    caller: str,
    graph: Any,
    targets: Iterable[str],
    *,
    grant_confirm: str = "",
) -> dict[str, Any]:
    """执行前的 grant 判定：硬拒 → 越界（出票/验票）→ 计数并返回回执块。

    ⛔ 越界不放行、也⛔ 升级 grant：这里唯一的出路是"用户确认 → 单次放行票"，
    票用完即销、`grant` 本体一个字段都不改（Q2 甲；判例①"确认即升级"被否死的原因）。
    """
    gs = _grant_store(store)
    try:
        check = gs.check(
            grant,
            agent=caller,
            actions=collect_actions(list(graph)),
            targets=sorted({str(t) for t in targets}),
            room_of=_grant_room_of(store),
        )
    except GrantError as exc:
        raise _grant_error(exc) from exc
    if check.hard_denied:
        detail = "；".join(f"{d['code']}={d['detail']}" for d in check.denied)
        raise ServiceError(f"执行授权拒绝（{check.code}）：{detail}", status=403)
    confirmed = False
    if check.needs_confirm:
        if not grant_confirm:
            try:
                ticket = gs.issue_confirm(check)
            except GrantError as exc:
                raise _grant_error(exc) from exc
            payload = {
                "code": "GRANT_CONFIRM_REQUIRED",
                "grant_id": check.grant_id,
                "risk": check.risk,
                "out_of_scope": list(check.out_of_scope),
                "ticket": ticket["ticket"],
                "expires_at": ticket["expires_at"],
                "question": _grant_confirm_question(check),
                "how": "用户确认后，用同一份请求加 grant_confirm=<ticket> 重发一次；"
                       "票单次有效，⛔ 不会因此扩大 grant 范围",
            }
            # 固定前缀 + JSON：DB/agent 两侧都能机器读（同 `READONLY_DEGRADED:` 的既有先例），
            # ⛔ 不新造一个 HTTP 状态码语义（428 在 MCP 面会落成 PAYLOAD_INVALID，读成"改参数"）。
            raise ServiceError(
                "GRANT_CONFIRM_REQUIRED: " + json.dumps(payload, ensure_ascii=False),
                status=403,
            )
        try:
            ok_ticket = gs.consume_confirm(
                grant_confirm, agent=caller, targets=list(check.out_of_scope),
                grant_id=check.grant_id,
            )
        except GrantError as exc:
            raise _grant_error(exc) from exc
        if not ok_ticket:
            raise ServiceError(
                "越界放行票无效（不存在／已用过／已过期／与本次请求不匹配）：请重新确认一次",
                status=403,
            )
        confirmed = True
    try:
        uses = gs.bump_use(str(grant.get("grant_id") or ""))
    except GrantError as exc:
        raise _grant_error(exc) from exc
    return {
        "grant_id": str(grant.get("grant_id") or ""),
        "agent": str(grant.get("agent") or ""),
        "risk": check.risk,
        "granted_risk": str(grant.get("action_risk") or ""),
        "confirmed": confirmed,
        "out_of_scope": list(check.out_of_scope),
        "uses": uses,
        "expires_at": _grant_iso(grant.get("expires_at")),
    }


def _grant_iso(value: Any) -> str:
    """epoch 秒 → ISO（回执只给人读，⛔ 不参与判定）。坏值返回空串，不抛。"""
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(stamp, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _grant_audit(store: "GraphStore | None", event_type: str, **data: Any) -> None:
    """授权事实写进**既有**部署台账（⛔ 第二套审计面）。记账失败不回滚已发生的授权动作。"""
    try:
        from .af_audit import AuditEvent as _AE, record_deploy as _rd

        _rd(_AE(event_type, at=datetime.now(timezone.utc), message=event_type.lower(), data=dict(data)), store)
    except Exception as exc:  # noqa: BLE001 —— 记账失败不许把授权动作打崩（口径同 _persist_deploy）
        logger.warning("GRANT_AUDIT_RECORD_FAILED type=%s 授权已生效但记账失败：%s", event_type, exc)


def grant_issue(
    store: GraphStore,
    *,
    agent: str,
    auth_code: str = "",
    rooms: Sequence[str] | None = None,
    device_scope: Mapping[str, Any] | None = None,
    action_risk: str = "L1",
    ttl_days: float | None = None,
    caller: str = "",
    caller_is_owner: bool = False,
) -> dict[str, Any]:
    """签发一张执行授权（MVP 六字段）。

    默认 `action_risk=L1`：**缺省只放行 L0/L1**（通知／灯／开关／风扇）。理由与 DCD §三.1
    同向——"范围授权 ≠ 动作全权"：中危（cover 窗帘／climate 空调）必须显式写 `action_risk=L2`，
    忘了写的人拿到的是更小的权限，⛔ 不是更大的（fail-closed 的默认值方向）。
    """
    tag = _grant_channel_tag(
        store, auth_code, caller=caller, caller_is_owner=caller_is_owner,
        action="签发执行授权（grant）", consume=True,
    )
    try:
        record = _grant_store(store).create(
            agent=agent,
            issued_by=caller or tag,
            rooms=rooms or (),
            device_scope=device_scope,
            action_risk=action_risk,
            ttl_days=ttl_days,
            issued_via=tag,
        )
    except GrantError as exc:
        raise _grant_error(exc) from exc
    _grant_audit(
        store, "GRANT_ISSUED",
        grant_id=record["grant_id"], agent=record["agent"],
        rooms=list(record["rooms"]), device_scope=dict(record["device_scope"]),
        action_risk=record["action_risk"], expires_at=_grant_iso(record["expires_at"]),
        authorized_by=tag, subject=str(caller or ""), issued_by=record["issued_by"],
    )
    return record


def grant_list(
    store: GraphStore, *, caller: str = "", caller_is_owner: bool = False,
    token_identity: bool | None = None, agent: str = "",
) -> dict[str, Any]:
    """授权列表（读面）。归属形状按 list 面纪律：owner 面全量⛔ 不报 hidden_count、
    非 owner 面只看自己名下且**必报** `hidden_count`（⑩ 两条腿同族）。

    `agent` 只作**收窄**用（owner 查某个 agent），⛔ 不是"替别人看"的开关：非 owner 面
    传别人的 `agent` 仍然只能看到自己的（范围判定在 `scope_names`，不在这行）。
    """
    sub = Subject.from_caller(caller, owner_face=caller_is_owner, token_identity=token_identity)
    if sub.token_state is TokenState.BROKEN:
        raise ServiceError(broken_identity_error(), status=403)
    try:
        records = _grant_store(store).list()
    except GrantError as exc:
        raise _grant_error(exc) from exc
    by_id = {str(r.get("grant_id") or ""): str(r.get("agent") or "") for r in records}
    visible, hidden = scope_names(lambda gid: by_id.get(gid, ""), list(by_id), sub)
    keep = set(visible)
    items = [r for r in records if str(r.get("grant_id") or "") in keep]
    if str(agent or "").strip():
        items = [r for r in items if str(r.get("agent") or "") == str(agent).strip()]
    out: dict[str, Any] = {"ok": len(visible) + hidden == len(records), "items": items, "count": len(items)}
    if caller and not caller_is_owner:
        out["hidden_count"] = hidden
        out["scope"] = f"owner:{caller}"
    return out


def grant_revoke(
    store: GraphStore, *, grant_id: str, auth_code: str = "", reason: str = "",
    caller: str = "", caller_is_owner: bool = False,
) -> dict[str, Any]:
    """撤销（Q3 上半：立即生效，此后任何新执行请求即拒）。`consume=False`——撤销不耗额度。"""
    tag = _grant_channel_tag(
        store, auth_code, caller=caller, caller_is_owner=caller_is_owner,
        action="撤销执行授权（grant）", consume=False,
    )
    try:
        record = _grant_store(store).revoke(grant_id, reason=reason, by=caller or tag)
    except GrantError as exc:
        raise _grant_error(exc) from exc
    _grant_audit(
        store, "GRANT_REVOKED", grant_id=record["grant_id"], agent=record["agent"],
        reason=str(reason or ""), authorized_by=tag, subject=str(caller or ""),
        revoked_at=str((record.get("revoke") or {}).get("at") or ""),
    )
    return record


def grant_renew(
    store: GraphStore, *, grant_id: str, auth_code: str = "", ttl_days: float | None = None,
    caller: str = "", caller_is_owner: bool = False,
) -> dict[str, Any]:
    """续期（Q1：长期码不过期 ≠ 授权不过期）。续期是授权动作 ⇒ 计一次。"""
    tag = _grant_channel_tag(
        store, auth_code, caller=caller, caller_is_owner=caller_is_owner,
        action="续期执行授权（grant）", consume=True,
    )
    try:
        record = _grant_store(store).renew(grant_id, ttl_days=ttl_days, by=caller or tag)
    except GrantError as exc:
        raise _grant_error(exc) from exc
    _grant_audit(
        store, "GRANT_RENEWED", grant_id=record["grant_id"], agent=record["agent"],
        expires_at=_grant_iso(record["expires_at"]), authorized_by=tag, subject=str(caller or ""),
    )
    return record


def grant_receipts(
    store: "GraphStore | None", *, caller: str = "", caller_is_owner: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """执行授权的**留痕读面**（mimo/UI 用）：签发／撤销／续期／带 grant 的执行／查询。

    源＝`<store>/deploy_audit.jsonl`（既有那一份，⛔ 不新建持久化面）。归属过滤与
    `live_run_receipts` 同形：`data.subject` 为空的老流水按"无身份"处理，owner 面看全量。
    """
    root = getattr(store, "root", None)
    if root is None:
        return {"ok": False, "count": 0, "items": [], "read_error": "no_store"}
    # 条数上限复用 `live_run_receipts` 的同一对常量（本函数定义在它们之前 ⇒ 运行期取）。
    try:
        n = LIVE_RECEIPTS_LIMIT if limit is None else int(limit)
    except (TypeError, ValueError):
        n = LIVE_RECEIPTS_LIMIT
    n = min(n if n > 0 else LIVE_RECEIPTS_LIMIT, LIVE_RECEIPTS_MAX)

    from .af_audit import DEPLOY_AUDIT_FILE, GRANT_ISSUED, GRANT_RENEWED, GRANT_REVOKED, QUERY_READ

    path = Path(root) / DEPLOY_AUDIT_FILE
    text: str | None = None
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "count": 0, "items": [],
                    "read_error": f"ledger_unreadable:{type(exc).__name__}"}
    wanted = {GRANT_ISSUED, GRANT_RENEWED, GRANT_REVOKED, QUERY_READ}
    events: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue  # 坏行跳过：append-only 流水，单行损坏不该让整份读数变空
        if not isinstance(row, dict):
            continue
        kind = str(row.get("type") or "")
        data = row.get("data") if isinstance(row.get("data"), Mapping) else {}
        if kind in wanted or (kind == "LIVE_RUN_EXECUTED" and isinstance(data.get("grant"), Mapping)):
            events.append(row)
    total = len(events)
    items: list[dict[str, Any]] = []
    for row in reversed(events):  # 落盘是时间序 ⇒ 倒序即"新→旧"
        data = row.get("data") if isinstance(row.get("data"), Mapping) else {}
        subject = str(data.get("subject") or "")
        if caller and not caller_is_owner and subject != caller:
            continue
        items.append({
            "type": str(row.get("type") or ""),
            "at": str(row.get("at") or row.get("timestamp") or ""),
            "grant": dict(data.get("grant")) if isinstance(data.get("grant"), Mapping)
            else {"grant_id": str(data.get("grant_id") or "")},
            "agent": str(data.get("agent") or data.get("subject") or ""),
            "summary": str(data.get("message") or row.get("message") or ""),
            "what": sorted(data.get("writes") or []) if data.get("writes") else [],
            "risk": str(data.get("action_risk") or data.get("risk") or ""),
        })
        if len(items) >= n:
            break
    out: dict[str, Any] = {"ok": total >= len(items), "count": len(items), "items": items}
    if caller and not caller_is_owner:
        out["hidden_count"] = max(0, total - len(items))
        out["scope"] = f"owner:{caller}"
    return out


def record_query(store: "GraphStore | None", *, entity_id: str, caller: str = "") -> None:
    """查询留痕（Q4 甲：读面免 grant，但**免授权 ≠ 免审计**）。

    只记"谁在什么时候问了哪个实体"，⛔ 不记返回的状态值（那会把家庭状态写进台账）。
    """
    if store is None:
        return
    _grant_audit(store, "QUERY_READ", entity_id=str(entity_id or ""), subject=str(caller or ""),
                 agent=str(caller or ""))


# ─────────────────────────────────────────────────────────────────────
# v1.29.0 第二批：【任务】TaskOrder —— 到点执行、逐动作过 grant、终态即销
#   依据：DCD 20261111 Q5 甲（TaskOrder 落 AF）／Q6（一次性 vs 重复）／§2.3 甲（撤销下半行）
#   口径三条（卡 §三 红线）：
#   ① ⛔ 变相常驻：账本里没有 every/repeat/cron（`af_task_order.FORBIDDEN_KEYS` 代码级拒收）；
#   ② ⛔ 整单放行：每步都走 `live_run`（它内部带 grant 判定），撤销/过期在**每一步之前**重读；
#   ③ ⛔ 触发面混淆：任务只到点执行＋人工取消，⛔ 不进 watch 装载池、⛔ 接定时/事件触发。
# ─────────────────────────────────────────────────────────────────────

#: 引擎轮询间隔（秒）。5s 足够覆盖"到点 ±几秒"，又不至于把 store 读成热点。
ORDER_ENGINE_TICK_S = 5.0
#: 一轮最多跑几张单（防止一次 tick 被积压单子拖死；剩下的下一轮接着来）。
ORDER_ENGINE_MAX_PER_TICK = 5
#: 引擎线程句柄（只有 serve 会起它；MCP/CLI 进程⛔ 起——那里没有单写者租约，live_run 必 503）。
_ORDER_ENGINE: dict[str, Any] = {"thread": None, "stop": None, "root": "", "ticks": 0, "last_error": ""}


def _order_store(store: "GraphStore | None") -> TaskOrderStore:
    root = getattr(store, "root", None)
    if root is None:
        raise ServiceError("任务单需要 store 根（账本在 .orders/active.json）——本调用未携带 store",
                           status=403)
    return TaskOrderStore(Path(root).joinpath(*ORDER_FILE_REL))


def _order_error(exc: TaskOrderError) -> ServiceError:
    """把任务单面异常翻成服务层错误：**码名进 message 开头**（机器可读，同 `READONLY_DEGRADED:` 先例）。"""
    code = str(getattr(exc, "code", "") or "")
    text = f"{code}: {exc}" if code else str(exc)
    return ServiceError(text, status=int(getattr(exc, "status", 400) or 400))


def _order_assert_scope(order: Mapping[str, Any], *, caller: str, caller_is_owner: bool,
                        action: str) -> None:
    """任务单的**归属面**：owner 面可动全部；agent 只能动自己名下的单（同 grant 口径）。"""
    agent = str(order.get("agent") or "")
    if caller_is_owner:
        return
    if not caller:
        # 无身份（CLI/内部）沿用既有渐进口径：放行，但只有本进程能造出这种调用
        return
    if agent != str(caller):
        raise ServiceError(f"{action}：任务单不属于当前主体", status=403)


def _order_precheck_steps(store: "GraphStore | None", *, grant_id: str, agent: str,
                          steps: Sequence[Mapping[str, Any]],
                          room_of: Any = None) -> dict[str, Any]:
    """**创建期**把每一步都过一遍 grant（⛔ 整单放行）。

    为什么创建期也要过：到点执行时没人可问——越界的步如果等到那一刻才发现，用户只会看到
    "任务失败了"。所以越界/高危的步**在入册时就拒**，让 agent 当场改计划或补授权。
    ⚠️ 偏差记录（第二批报告 §三）：卡面写"越界步走批 1 单次放行票"，但放行票 TTL 是
    **300 秒**，而任务单可能在几小时后才到点 ⇒ 票必然过期，兑现不了。本批按 fail-closed
    处理：越界步**拒绝创建**并回可读原因；「创建时确认、执行时放行」需要一套长期票设计，
    属批 3+ 的拍板项（已在报告里报 PM）。
    """
    if store is None:
        raise ServiceError("任务单需要 store 根才能校验执行授权", status=403)
    gs = _grant_store(store)
    try:
        grant = gs.resolve(grant_id, agent=agent)
    except GrantError as exc:
        raise _grant_error(exc) from exc
    if room_of is None:
        room_of = _grant_room_of(store)
    for index, step in enumerate(steps):
        entity = step_entity(step)
        check = gs.check(
            grant,
            agent=agent,
            actions=[(str(step.get("adapter") or "ha"), str(step.get("action") or ""),
                      dict(step.get("params") or {}))],
            targets=[entity] if entity else [],
            room_of=room_of,
        )
        if check.hard_denied:
            detail = "；".join(f"{d['code']}={d['detail']}" for d in check.denied)
            raise ServiceError(
                f"任务单第 {index + 1} 步（{step_summary(step)}）越过执行授权：{detail}——"
                "任务到点无人可问，越界步一律在入册时拒（⛔ 整单放行）",
                status=403,
            )
        if check.needs_confirm:
            detail = "；".join(d.get("detail") or d["code"] for d in check.denied)
            raise ServiceError(
                f"任务单第 {index + 1} 步（{step_summary(step)}）需要越界确认（{detail}）："
                "定时任务到点无人可确认，请补授权范围或改动作（⛔ 用单次票挂单：票 TTL 300s，到点必过期）",
                status=403,
            )
    return {"grant_id": grant["grant_id"], "action_risk": grant.get("action_risk"),
            "expires_at": _grant_iso(grant.get("expires_at"))}


def order_create(
    store: GraphStore,
    *,
    steps: Sequence[Mapping[str, Any]],
    agent: str = "",
    output: Mapping[str, Any] | None = None,
    grant_id: str = "",
    at: float | None = None,
    delay_s: float | None = None,
    caller: str = "",
    caller_is_owner: bool = False,
) -> dict[str, Any]:
    """入册一张任务单（挂单）。

    `agent` 缺省取调用主体；owner 面可以显式指定（替谁挂单）。没有 agent 的主体（owner 令牌
    直连且没给 agent）⇒ 拒：任务到点要按某个 agent 的 grant 执行，主体必须明确。
    """
    agent_text = str(agent or caller or "").strip()
    if not agent_text:
        raise ServiceError(
            "创建任务单需要 agent（执行主体）：任务到点按该 agent 的执行授权逐动作放行", status=400)
    precheck = _order_precheck_steps(store, grant_id=grant_id, agent=agent_text, steps=steps)
    try:
        record = _order_store(store).create(
            agent=agent_text, steps=steps, output=output, grant_id=grant_id,
            at=at, delay_s=delay_s, created_by=caller or agent_text,
        )
    except TaskOrderError as exc:
        raise _order_error(exc) from exc
    _grant_audit(
        store, "TASK_ORDER_CREATED",
        order_id=record["order_id"], agent=record["agent"],
        due_at=_grant_iso(record["due_at"]), steps=[step_summary(s) for s in record["steps"]],
        output=dict(record["output"]), grant_id=record["grant_id"],
        subject=str(caller or agent_text), precheck=precheck,
    )
    return record


def order_list(
    store: GraphStore, *, caller: str = "", caller_is_owner: bool = False,
    token_identity: bool | None = None,
) -> dict[str, Any]:
    """活动池（pending/running）。归属形状照 list 面纪律（owner 全量、非 owner 只看自己）。"""
    sub = Subject.from_caller(caller, owner_face=caller_is_owner, token_identity=token_identity)
    if sub.token_state is TokenState.BROKEN:
        raise ServiceError(broken_identity_error(), status=403)
    try:
        records = _order_store(store).list()
    except TaskOrderError as exc:
        raise _order_error(exc) from exc
    by_id = {str(r.get("order_id") or ""): str(r.get("agent") or "") for r in records}
    visible, hidden = scope_names(lambda oid: by_id.get(oid, ""), list(by_id), sub)
    keep = set(visible)
    items = [r for r in records if str(r.get("order_id") or "") in keep]
    out: dict[str, Any] = {"ok": len(visible) + hidden == len(records), "items": items,
                           "count": len(items)}
    if caller and not caller_is_owner:
        out["hidden_count"] = hidden
        out["scope"] = f"owner:{caller}"
    return out


def order_cancel(
    store: GraphStore, *, order_id: str, caller: str = "", caller_is_owner: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    """人工取消（pending／running 均可）。终态即销，历史进 `TASK_ORDER_CANCELLED` 留痕。"""
    ostore = _order_store(store)
    current = ostore.get(order_id)
    if current is None:
        raise ServiceError(f"没有这张任务单：{order_id}（已执行完的单子会即销，历史查 order_receipts）",
                           status=404)
    _order_assert_scope(current, caller=caller, caller_is_owner=caller_is_owner, action="取消任务单")
    try:
        record = ostore.cancel(order_id, by=caller, reason=reason)
    except TaskOrderError as exc:
        raise _order_error(exc) from exc
    _grant_audit(
        store, "TASK_ORDER_CANCELLED", order_id=record["order_id"], agent=record["agent"],
        reason=str(reason or ""), due_at=_grant_iso(record["due_at"]),
        steps_done=len((record.get("receipt") or {}).get("steps") or []),
        subject=str(caller or ""), by=str(caller or ""),
    )
    return record


def _deliver_order_output(store: "GraphStore | None", order: Mapping[str, Any]) -> list[dict[str, Any]]:
    """按 `output.channels` 逐通道交付，**每通道如实记状态**（⛔ 没发就说没发）。

    现读映射（仓内既有设施，⛔ 不发明新外部集成）：
    - `tts` → `AfMqttBridge.publish_speak`（与自动化播报同一条桥方法；无桥 ⇒ `no_bridge`）
    - `mimo` → **读面**：`/api/orders` 可查即为达成（不假装是推送）
    - `bark` → **未接线**：仓内 grep 0 命中，落占位＋`unwired`；它是设计稿里的**兜底**通道，
      所以 TTS 失败时这条链当前是断的——回执里显式带 `fallback_unavailable`
    """
    block = order.get("output") if isinstance(order.get("output"), Mapping) else {}
    channels = list(block.get("channels") or [])
    text = str(block.get("text") or "").strip() or _order_default_text(order)
    room = str(block.get("room") or "")
    out: list[dict[str, Any]] = []
    tts_failed = False
    for channel in channels:
        wired = OUTPUT_WIRED.get(channel, "unwired")
        if channel == "tts":
            entry = {"channel": "tts", "wired": wired, "room": room, "text": text}
            try:
                from . import af_mqtt_bridge

                bridge = af_mqtt_bridge.current_bridge()
                if bridge is None:
                    entry.update({"status": "failed", "error": "no_bridge"})
                    tts_failed = True
                else:
                    res = bridge.publish_speak(
                        trace_id=f"order:{order.get('order_id')}",
                        text=text, room=room, source="autoforge",
                    )
                    ok = bool((res or {}).get("published", True)) if isinstance(res, dict) else True
                    entry.update({
                        "status": "sent" if ok else "failed",
                        "trace_id": str((res or {}).get("trace_id") or "") if isinstance(res, dict) else "",
                        # 播报**发出**不等于被听见（勿扰/播放吞话）——这是"未验证"的诚实标注
                        "verified": False,
                    })
                    if not ok:
                        entry["error"] = str((res or {}).get("code") or "not_published")
                        tts_failed = True
            except Exception as exc:  # noqa: BLE001 —— 播报旁路失败⛔ 冒到执行链
                entry.update({"status": "failed", "error": f"raised:{type(exc).__name__}"})
                tts_failed = True
            out.append(entry)
        elif channel == "mimo":
            out.append({"channel": "mimo", "wired": wired, "status": "readable",
                        "endpoint": f"/api/orders/{order.get('order_id')}"})
        else:
            out.append({"channel": channel, "wired": wired, "status": "unwired",
                        "note": "仓内无该通道设施（bark 未接线）：已如实记录，⛔ 不算投递成功"})
    if tts_failed and "bark" in channels:
        out.append({"channel": "bark", "wired": OUTPUT_WIRED["bark"], "status": "fallback_unavailable",
                    "note": "TTS 失败时的兜底通道当前未接线（拍板项，见第二批报告 §三）"})
    return out


def _order_default_text(order: Mapping[str, Any]) -> str:
    """没有 `output.text` 时的一句话播报（⛔ 术语堆，用户听的是结果）。"""
    steps = order.get("steps") or []
    return f"你订的{len(steps)}步任务已经按顺序做完了"


def _order_step_receipt(index: int, step: Mapping[str, Any], result: Mapping[str, Any] | None,
                        *, error: str = "") -> dict[str, Any]:
    base = {"index": index + 1, "action": str(step.get("action") or ""),
            "entity": step_entity(step), "at": _grant_iso(time.time())}
    if error:
        base.update({"ok": False, "error": error})
        return base
    data = result or {}
    base.update({
        # ok 取自 `live_run` 的真实裁决（它自己由实例终态派生），⛔ 不在这里写常量 True
        "ok": bool(data.get("ok")),
        "authorized_by": str(data.get("authorized_by") or ""),
        "verdict": str(data.get("dispatch_result") or ""),
        "writes": list(data.get("writes") or []),
        "grant": data.get("grant") if isinstance(data.get("grant"), Mapping) else None,
    })
    return base


def _run_claimed_order(store: "GraphStore | None", ostore: TaskOrderStore,
                       claim: Any, *, now: float | None = None) -> dict[str, Any]:
    """跑一张已认领的单：逐步过 grant → 逐动作执行 → 交付 output → 结单。

    撤销语义（Q3 下半行，本批闭合矩阵第 6 行）：**每一步之前**重读 grant；一旦发现被撤销，
    当前已完成的步保留、**其后所有未执行步不发起**，状态翻 `cancelled`。
    """
    order = claim.order
    oid = str(order.get("order_id") or "")
    steps = list(order.get("steps") or [])
    start = int(claim.resumed_from or 0)
    gs = _grant_store(store)
    try:
        gs.resolve(str(order.get("grant_id") or ""), agent=str(order.get("agent") or ""))
    except GrantError as exc:
        record = ostore.finish(oid, status="cancelled",
                               extra={"reason": f"grant 不可用：{exc.code}", "steps_done": start})
        _grant_audit(store, "TASK_ORDER_CANCELLED", order_id=oid, agent=order.get("agent"),
                     reason=f"grant_unusable:{exc.code}", steps_done=start, due_at=_grant_iso(order.get("due_at")))
        return {"order_id": oid, "status": "cancelled", "record": record}

    status = "running"
    failure: dict[str, Any] = {}
    for index in range(start, len(steps)):
        step = steps[index]
        fresh = gs.get(str(order.get("grant_id") or ""))
        if fresh is None or bool((fresh.get("revoke") or {}).get("revoked")):
            record = ostore.finish(
                oid, status="cancelled",
                extra={"reason": "grant_revoked_mid_flight", "cancelled_from_step": index + 1,
                       "steps_done": index},
            )
            _grant_audit(store, "TASK_ORDER_CANCELLED", order_id=oid, agent=order.get("agent"),
                         reason="grant_revoked_mid_flight", cancelled_from_step=index + 1,
                         steps_done=index, due_at=_grant_iso(order.get("due_at")))
            return {"order_id": oid, "status": "cancelled", "record": record}
        entity = step_entity(step)
        try:
            result = live_run(
                build_step_ir(step, order_id=oid, index=index),
                [entity] if entity else [f"task_order.{oid}"],
                [step_event(step, order_id=oid)],
                confirm=True,
                store=store,
                grant_id=str(order.get("grant_id") or ""),
                caller=str(order.get("agent") or ""),
            )
        except ServiceError as exc:
            failure = {"index": index + 1, "error": str(exc), "status": int(getattr(exc, "status", 400) or 400)}
            ostore.record_step(oid, _order_step_receipt(index, step, None, error=str(exc)))
            status = "failed"
            break
        ostore.record_step(oid, _order_step_receipt(index, step, result))

    if status == "running":
        # 全部步成功 ⇒ 交付 output（交付失败**不改**任务状态：动作已经做完了）
        try:
            deliveries = _deliver_order_output(store, order)
            for entry in deliveries:
                ostore.record_output(oid, entry)
        except Exception as exc:  # noqa: BLE001 —— 播报旁路失败⛔ 把已完成的动作记成失败
            logger.warning("TASK_ORDER_OUTPUT_DELIVERY_FAILED order=%s err=%s", oid, exc)
        status = "done"

    record = ostore.finish(oid, status=status,
                           extra={"reason": failure.get("error", ""), "failed_step": failure.get("index", 0)})
    _grant_audit(
        store, "TASK_ORDER_FINISHED", order_id=oid, agent=order.get("agent"),
        status=status, due_at=_grant_iso(order.get("due_at")),
        steps_total=len(steps), steps_done=len((record.get("receipt") or {}).get("steps") or []),
        failed_step=failure.get("index", 0), error=failure.get("error", ""),
        subject=str(order.get("agent") or ""),
    )
    return {"order_id": oid, "status": status, "record": record}


def order_tick(store: "GraphStore | None", *, now: float | None = None,
               limit: int = ORDER_ENGINE_MAX_PER_TICK) -> dict[str, Any]:
    """引擎单跑：认领到点单 → 逐张执行。返回本轮读数（serve 引擎线程与 `forge order tick` 同源）。"""
    if store is None:
        raise ServiceError("任务单引擎需要 store", status=400)
    ostore = _order_store(store)
    try:
        claims = ostore.claim_due(now=now)
    except TaskOrderError as exc:
        raise _order_error(exc) from exc
    expired = [c.order_id for c in claims if str(c.order.get("status")) == "expired"]
    runnable = [c for c in claims if str(c.order.get("status")) != "expired"][: max(int(limit), 1)]
    results: list[dict[str, Any]] = []
    for claim in runnable:
        try:
            results.append(_run_claimed_order(store, ostore, claim, now=now))
        except Exception as exc:  # noqa: BLE001 —— 单张单子的意外不许把引擎线程打死
            logger.warning("TASK_ORDER_RUN_FAILED order=%s err=%s", claim.order_id, exc)
            results.append({"order_id": claim.order_id, "status": "error", "error": str(exc)})
    if expired:
        for oid in expired:
            _grant_audit(store, "TASK_ORDER_EXPIRED", order_id=oid, reason="missed_due_window")
    return {
        # ok 派生自不变式：本轮认领的单子没有一张落到 error 分支
        "ok": all(str(r.get("status") or "") != "error" for r in results),
        "claimed": len(claims),
        "expired": len(expired),
        "ran": len(results),
        "results": results,
    }


def order_receipts(
    store: "GraphStore | None", *, caller: str = "", caller_is_owner: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """任务单留痕读面（终态即销之后的唯一查法）：源＝`<store>/deploy_audit.jsonl`。"""
    root = getattr(store, "root", None)
    if root is None:
        return {"ok": False, "count": 0, "items": [], "read_error": "no_store"}
    try:
        n = LIVE_RECEIPTS_LIMIT if limit is None else int(limit)
    except (TypeError, ValueError):
        n = LIVE_RECEIPTS_LIMIT
    n = min(n if n > 0 else LIVE_RECEIPTS_LIMIT, LIVE_RECEIPTS_MAX)

    from .af_audit import (DEPLOY_AUDIT_FILE, TASK_ORDER_CANCELLED, TASK_ORDER_CREATED,
                           TASK_ORDER_EXPIRED, TASK_ORDER_FINISHED)

    path = Path(root) / DEPLOY_AUDIT_FILE
    text: str | None = None
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "count": 0, "items": [],
                    "read_error": f"ledger_unreadable:{type(exc).__name__}"}
    wanted = {TASK_ORDER_CREATED, TASK_ORDER_FINISHED, TASK_ORDER_CANCELLED, TASK_ORDER_EXPIRED}
    events: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and str(row.get("type") or "") in wanted:
            events.append(row)
    total = len(events)
    items: list[dict[str, Any]] = []
    for row in reversed(events):
        data = row.get("data") if isinstance(row.get("data"), Mapping) else {}
        subject = str(data.get("subject") or data.get("agent") or "")
        if caller and not caller_is_owner and subject != caller and str(data.get("agent") or "") != caller:
            continue
        items.append({
            "type": str(row.get("type") or ""),
            "at": str(row.get("at") or ""),
            "order_id": str(data.get("order_id") or ""),
            "agent": str(data.get("agent") or ""),
            "status": str(data.get("status") or ""),
            "reason": str(data.get("reason") or ""),
            "steps": data.get("steps") or [],
        })
        if len(items) >= n:
            break
    out: dict[str, Any] = {"ok": total >= len(items), "count": len(items), "items": items}
    if caller and not caller_is_owner:
        out["hidden_count"] = max(0, total - len(items))
        out["scope"] = f"owner:{caller}"
    return out


def _order_engine_loop(store_root: str, stop: Any, tick_s: float) -> None:
    """serve 内的任务单引擎循环（daemon）。异常只留痕，⛔ 不把线程打死。"""
    store = GraphStore(store_root)
    while not stop.wait(tick_s):
        try:
            order_tick(store)
            _ORDER_ENGINE["ticks"] = int(_ORDER_ENGINE.get("ticks") or 0) + 1
            _ORDER_ENGINE["last_error"] = ""
        except Exception as exc:  # noqa: BLE001 —— 引擎线程活着比"干净地死掉"重要
            _ORDER_ENGINE["last_error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("TASK_ORDER_TICK_FAILED tick 异常（引擎继续跑）：%s", exc)


def order_engine_start(store_root: str, *, tick_s: float | None = None) -> dict[str, Any]:
    """在 serve 里起任务单引擎（幂等）。⛔ 只在持单写者租约的进程里起——MCP/CLI 起＝每步 503。"""
    if _ORDER_ENGINE.get("thread") is not None:
        return order_engine_health()
    stop = threading.Event()
    thread = threading.Thread(
        target=_order_engine_loop, args=(str(store_root), stop, float(tick_s or ORDER_ENGINE_TICK_S)),
        daemon=True, name="task-order-engine",
    )
    _ORDER_ENGINE.update({"thread": thread, "stop": stop, "root": str(store_root), "ticks": 0,
                          "last_error": ""})
    thread.start()
    return order_engine_health()


def order_engine_stop(*, timeout: float = 5.0) -> dict[str, Any]:
    """停引擎（serve 退出前调）。返回停机读数，⛔ 吞掉"没停干净"这一事实。"""
    stop = _ORDER_ENGINE.get("stop")
    thread = _ORDER_ENGINE.get("thread")
    if stop is None or thread is None:
        return {"ok": not (stop or thread), "running": False}
    stop.set()
    thread.join(timeout)
    alive = bool(getattr(thread, "is_alive", lambda: False)())
    _ORDER_ENGINE.update({"thread": None, "stop": None})
    return {"ok": not alive, "running": alive, "root": _ORDER_ENGINE.get("root") or ""}


def order_engine_health() -> dict[str, Any]:
    """引擎读数（health 面用；⛔ 不写"健康"这种没判据的字）。"""
    thread = _ORDER_ENGINE.get("thread")
    return {
        "running": bool(thread is not None and getattr(thread, "is_alive", lambda: False)()),
        "root": _ORDER_ENGINE.get("root") or "",
        "ticks": int(_ORDER_ENGINE.get("ticks") or 0),
        "last_error": str(_ORDER_ENGINE.get("last_error") or ""),
        "tick_s": ORDER_ENGINE_TICK_S,
    }


# ─────────────────────────────────────────────────────────────────────
# F7 残留（WebUI 撤销按钮）：撤销的 HTTP 服务面。
#   撤销 = 对真实设备**再下发一次**，所以服务端闸门口径与 `live_run` 完全对齐：
#   ① `AUTOFORGE_LIVE_ENABLED=1`；② 令牌只从服务端 `HA_LONG_LIVED_TOKEN`（旧 AUTOFORGE_HA_TOKEN）取，绝不从请求体收。
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
# v1.28 R3：最近单发（live_run）回执读面（UI「最近单发回执」的数据源）
#   源＝`<store>/deploy_audit.jsonl` 的 `LIVE_RUN_EXECUTED` 事件（已落盘，⛔ 不新建持久化面）。
#   归属形状按 **list 面纪律**（PM 2026-11-06 22:5x 核准）：owner 面全量、⛔ 不报 hidden_count；
#   非 owner 面只看自己名下、**hidden_count 必报**（与 list_graphs 正反形状一致）。
# ─────────────────────────────────────────────────────────────────────

#: 回执默认条数（PM 卡 §四：最近 20 条）与硬上限（越界钳到上限，⛔ 不无限读）
LIVE_RECEIPTS_LIMIT = 20
LIVE_RECEIPTS_MAX = 100


def _auto_name(automation: Any) -> str:
    """回执里的「图名」：优先 `automation.name`，缺失回退 `id`（⛔ 不投影成空串让回执读不出是谁）。"""
    return str(getattr(automation, "name", "") or getattr(automation, "id", "") or "")


def _receipt_of(event: Mapping[str, Any]) -> dict[str, Any]:
    """把一条 `LIVE_RUN_EXECUTED` 审计事件折成一枚回执（时间／图名／动作数／verdict／码掩码）。

    `code_mask` 直接取事件里的 `authorized_by`——它本来就是 `_auth_code_check` 产出的脱敏标签
    （`auth_code:xx***`），⛔ 这里不碰、更不重新拼真码（群规⑤：码值不进任何回读面）。
    """
    data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
    verdict = data.get("verdict")
    if isinstance(verdict, Mapping):
        verdict_text = str(verdict.get("verdict") or "")
    else:
        verdict_text = str(verdict or "")
    graph = data.get("graph")
    actions = data.get("actions")
    if not isinstance(actions, int) or isinstance(actions, bool):
        writes = data.get("writes")
        actions = len(writes) if isinstance(writes, (list, tuple)) else 0
    return {
        "at": event.get("at"),
        "graph": [str(g) for g in graph] if isinstance(graph, (list, tuple)) else [],
        "actions": int(actions),
        "verdict": verdict_text,
        # AF-745 追加：点火档（`ignited`／`no_events`／`events_no_trigger`）。老流水里没有这枚键
        # ⇒ 空串＝**未知**，⛔ 不许回退成 `ignited`（那会把"查不出"说成"跑过"）。
        "ignition": str(data.get("ignition") or ""),
        "code_mask": str(data.get("authorized_by") or ""),
    }


def live_run_receipts(
    store_root: "str | Path | None" = None,
    *,
    caller: str = "",
    caller_is_owner: bool = False,
    limit: int = LIVE_RECEIPTS_LIMIT,
) -> dict[str, Any]:
    """最近单发回执（新→旧）。源＝`deploy_audit.jsonl`；读失败如实空表（⛔ 不伪装成"没有回执"）。

    归属（PM 核准的 list 面纪律）：
    - owner 面（或无身份调用方）：全量，**不带** `hidden_count`；
    - 非 owner 面：只返回 `subject == caller` 的那些，**必报** `hidden_count`（全量−可见）＋`scope`。
    ⛔ 单对象读面的「404 逐字同形」不适用本端点（PM 明确修正过卡面口径）。
    """
    if store_root is None:
        # 没有 store 就没有台账可读 ⇒ 如实 ok=False（⛔ 不返 ok=True 让调用方以为"空 = 没有回执"）
        return {"ok": False, "count": 0, "items": [], "read_error": "no_store"}
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = LIVE_RECEIPTS_LIMIT
    if n <= 0:
        n = LIVE_RECEIPTS_LIMIT
    n = min(n, LIVE_RECEIPTS_MAX)

    from .af_audit import DEPLOY_AUDIT_FILE

    path = Path(store_root) / DEPLOY_AUDIT_FILE
    # 三档要在**同一个出口**分开（⛔ 不给缺文件那一档单独 `return {"ok": True}`：字面量 ok
    # 会被 AST 棘轮判成 fake-ok，而且多出口正是"两档被压成一档"的老形状）：
    # 缺文件 ⇒ `text=None` ⇒ 空表（ok 由后面的不变式算）；OSError ⇒ 显式 `read_error`。
    text: str | None = None
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            # v1.28 R3 补（AF-UI 2026-11-07 校准）：此前 `read_jsonl_bounded` 的 fail-soft 会把
            # "台账读不出"与"没有回执"压成同一个空表，UI 无法分辨 ⇒ 这里显式冒出来给第三档。
            return {
                "ok": False,
                "count": 0,
                "items": [],
                "read_error": f"ledger_unreadable:{type(exc).__name__}",
            }
    events: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue  # 坏行跳过：台账是 append-only 流水，单行损坏不该让整份读数变空
        if isinstance(row, dict) and str(row.get("type") or "") == "LIVE_RUN_EXECUTED":
            events.append(row)
    total = len(events)
    visible: list[dict[str, Any]] = []
    for event in reversed(events):  # 落盘是时间序 ⇒ 倒序即"新→旧"
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        subject = str(data.get("subject") or "")
        if caller and not caller_is_owner and subject != caller:
            continue
        visible.append(_receipt_of(event))
        if len(visible) >= n:
            break
    # `ok` 派生自不变式（可见 ⊆ 全量），⛔ 不写字面量 True：字面量 ok 会被 AST 棘轮判成
    # fake-ok（"不来自任何实际校验"），而这里真有一条可校验的不变式（与 list_graphs 同姿势）。
    out: dict[str, Any] = {
        "ok": total >= len(visible),
        "count": len(visible),
        "items": visible,
    }
    if caller and not caller_is_owner:
        out["hidden_count"] = max(0, total - len(visible))
        out["scope"] = f"owner:{caller}"
    return out


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
    auth_code: str = "",
) -> dict[str, Any]:
    """受闸门撤销：真机回放动作前快照（决策 E）。

    被 `UndoStore.revert` 拒绝（未知/过期/风险域未确认）时**不抛异常**，
    原样返回 `{ok: false, reason, …}`——前端要渲染的是"哪几个实体需要二次确认"，
    不是笼统的 4xx。

    DCD 裁定 2026-10-30（AF-601）：撤销补授权码——它是**真机回放下发**，
    不是"关"：方向取决于下发前快照（下发前设备开着、有人误关，undo 会把它开回来）。
    「禁用免码」的豁免只对方向恒定收敛的动作成立，undo 借不了。闸走与
    live_run 同一条 `_auth_code_check`（⛔ 另造一套）。
    """
    cfg = _live_config()
    if not cfg["enabled"]:
        raise ServiceError("撤销未启用：真机回滚需要服务端设置 AUTOFORGE_LIVE_ENABLED=1", status=403)
    if not cfg["has_token"]:
        raise ServiceError("撤销缺少 HA 令牌：服务端需设置 HA_LONG_LIVED_TOKEN（旧 AUTOFORGE_HA_TOKEN）", status=403)

    # ★ undo 的码闸（AF-601）：位置在 LIVE_ENABLED/has_token 之后（与 live_run 同序——
    # 环境没配好时先报环境），校验与放行标签同一条通道。
    _real_root = store_root if isinstance(store_root, (str, os.PathLike)) else None
    _gate_store = GraphStore(str(_real_root)) if _real_root else None
    code_tag = _auth_code_check(_gate_store, auth_code, action=f"撤销部署（undo {str(deploy_id)[:12]}…）")

    store = _undo_store(store_root)
    token = _ha_token(store_root)
    ha_url = str(cfg["ha_url"])
    factory = LIVE_TRANSPORT_FACTORY
    transport = factory(ha_url, token) if factory is not None else HATransport(base_url=ha_url, token=token)
    adapter = HAAdapter(transport=transport, dry_run=False)

    logger.warning("UNDO：deploy_id=%s confirm=%s ha_url=%s authorized_by=%s", deploy_id, confirm, ha_url, code_tag)
    result = store.revert(deploy_id, adapter, confirm=confirm)
    result["results"] = [
        {"action": r.data.get("action"), "success": r.success, "data": r.data, "error": r.error}
        for r in result.get("results", [])
    ]
    result["ha_url"] = ha_url
    result["authorized_by"] = code_tag
    # DCD 裁定 2026-10-30（AF-403 后半）：过闸凭据标签必须进审计流水——
    # 回执是给调用方看的，审计才是给事后追查看的；过闸无痕＝查不到哪把凭据放行。
    try:
        from .af_audit import AuditEvent as _AE, record_deploy as _rd
        from datetime import datetime as _dt, timezone as _tz
        _rd(
            _AE("UNDO_DEPLOYED", at=_dt.now(_tz.utc),
                message=f"undo deploy {str(deploy_id)[:12]}… executed",
                data={"deploy_id": str(deploy_id), "authorized_by": code_tag,
                      "restored": bool(result.get("fully_restored"))}),
            _gate_store,
        )
    except Exception as exc:  # 记账失败不回滚已发生的撤销（口径同 _persist_deploy）
        logger.warning("UNDO_AUDIT_RECORD_FAILED 撤销已执行但记账失败：%s", exc)
    return result


# ─────────────────────────────────────────────────────────────────────
# v0.7.0 模板导出与备份恢复
# ─────────────────────────────────────────────────────────────────────


def export_store(
    store: GraphStore, *, caller: str = "", caller_is_owner: bool = False,
    token_identity: bool | None = None,
) -> dict[str, Any]:
    """导出 store 为可携带 bundle（含 tags，依赖 v0.6.0）。

    审计 AF-504（🔴 实测）：此前任何 read 令牌可整店导出**他人归档**。现在带身份的
    非 owner 面导出自己名下的（别人的既不进 entries 也不进 names，`hidden_count`
    报缺口）；owner 面仍是全量——导出的是「你的份额」，不是「全库」。
    未携带身份的旧调用路径维持全量（渐进收口，与 list_graphs 同口径）。
    C-3 收口：范围判定单源 `scope_names`；BROKEN ⇒ 403 残缺身份（R1）；
    R3：`checksum` 与**返回的** bundle 一致——份额化后按 scoped bundle 重算
    （⛔ 留全量指纹：导入侧复算对不上，调用方也无法自校验拿到的是哪份）；
    R4：`ok` 派生自计数合拢（names + hidden == 全集 entries）。
    """
    sub = Subject.from_caller(caller, owner_face=caller_is_owner, token_identity=token_identity)
    if sub.token_state is TokenState.BROKEN:
        raise ServiceError(broken_identity_error(), status=403)
    bundle = store.export_bundle()
    entries = list(bundle.get("entries", []))
    entry_names = [str(e.get("name", "")) for e in entries]
    visible, hidden = scope_names(lambda n: _existing_owner(store, n), entry_names, sub)
    if caller and not caller_is_owner:
        visible_set = set(visible)
        scoped = [e for e, n in zip(entries, entry_names) if n in visible_set]
        bundle = {**bundle, "entries": scoped}
        # R3：指纹跟份额走（store._bundle_checksum 单源，⛔ 本面另写第二份哈希）
        bundle["checksum"] = _bundle_checksum(bundle)
    out_names = [e["name"] for e in bundle.get("entries", [])]
    return {
        "ok": len(out_names) + hidden == len(entries),
        "format": bundle.get("format"),
        "version": bundle.get("version"),
        "checksum": bundle.get("checksum"),
        "exported_at": bundle.get("exported_at"),
        "names": out_names,
        **({"hidden_count": hidden, "scope": f"owner:{caller}"}
           if (caller and not caller_is_owner) else {}),
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
    owner_surface: bool = False,
    token_identity: bool | None = None,
) -> dict[str, Any]:
    """导入 bundle（v0.7.0）。冲突策略 skip/overwrite/rename，见 GraphStore.import_bundle。

    返回导入报告 {imported, skipped, renamed, errors, residual_backups}。最后一格是
    「新归档已落盘、但 overwrite 的让位备份没能回收」的如实读数（`import_bundle` 里写清语义：
    它不进 `errors`，因为导入这件事确实做成了）。

    v1.3.0：默认受**爆炸半径**约束。导入天然是批量动作，故**常见做法是显式传
    `allow_bulk=True`**——但默认关着，能让「误导入整个 bundle」这类事故先被拦一下。

    R20-01：加 `owner` 参数并下传 store.import_bundle——overwrite 时与 save_graph
    对称做所有权隔离校验，导入后记录携带归属主体。

    界线判据（AF-701 收口，20261031 裁定 §2.1）：`token_identity=True` 且 `owner==""`
    即"持令牌却缺 subject"（残缺身份）——与 `_assert_archive_mutator` 同一道界线，
    本函数的豁免（owner 面 admin 或无令牌身份的旧 CLI/MCP）⛔ 不放行残缺身份档。
    `token_identity is None`＝未声明（旧调用方），按无令牌身份处理。
    """
    if token_identity is True and not str(owner or "").strip():
        raise ServiceError(
            "拒绝：令牌缺少 subject（残缺身份）——豁免只对没有令牌身份的本地调用有效",
            status=403,
        )
    count = _bundle_automation_count(bundle)
    clobbered = 0
    if strategy == "overwrite":
        existing_names = set(store.names())
        for entry in bundle.get("entries") or ():
            nm = str(entry.get("name", ""))
            if nm and nm in existing_names:
                # D3：HTTP 导入面传 owner 后，非归属人 overwrite 他人归档直接 403
                # （不等 store.import_bundle 把它折进 report["errors"] 回成 200）。
                # owner 面（admin）或未携带身份（旧 CLI/MCP）不拦。
                if owner and not owner_surface:
                    prev = _existing_owner(store, nm)
                    if prev and prev != owner:
                        raise ServiceError(
                            f"归档 {nm!r} 归属 {prev!r}，当前主体 {owner!r} 无权覆盖（所有权隔离）",
                            status=403,
                        )
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
    # 巡检 #4：DeviceCatalog 只读环境变量，不读 credentials.json——WebUI 填的 token
    # 写进 credentials.json 但 catalog 拿不到。这里从 Config 读 token/url 传进去。
    # 审计 AF-304：`store` 会是 None（build/CLI/测试面不接 store 的调用形状），
    # 旧写法在 `Config(store.root)` 抛错后被 except 吞掉、却在 try **外面**直接
    # `DeviceCatalog(store.root)` → AttributeError 冒成 ADM_ERR_INTERNAL。
    # 一把崩点分两处写（一处有兜底一处没有）正是它活了这么久的原因。
    root = getattr(store, "root", None) or DEFAULT_STORE_ROOT
    if "ha_token" not in kwargs:
        try:
            from .af_config import Config
            cfg = Config(root)
            tok = cfg.get_ha_token()
            if tok:
                kwargs["ha_token"] = tok
            # 审计 AF-303：这里曾回落硬编码 `http://192.168.2.200:8123`——
            # P0-11 在 `af_adapters/ha.py` 收了默认地址硬编码，这一处是漏网的第二真源
            # （内网拓扑写进代码，且与 ha.py 的 env 口径分叉）。统一走 DEFAULT_HA_URL
            # （它本身就是 env 读数），⛔ 不在这里再写一个字面量。
            kwargs.setdefault("ha_url", DEFAULT_HA_URL)
        except Exception:
            # ARCH-01（关键模块不许 except-pass-broad）：读不出就退回 env/缺省行为，
            # 但必须留痕——静默吞会让"WebUI 填的 token 为什么没生效"无从查起。
            logger.debug("CATALOG_TOKEN_SOURCE_SKIPPED", exc_info=True)
    return DeviceCatalog(root, **kwargs)


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
    caller: str = "",
) -> dict[str, Any]:
    """全屋实体目录·过滤浏览（强制分页 + 透明截断回报）。"""
    # v1.29.0 批 2（批 1 裁定②顺延）：列表浏览也是"谁看过家里"的一部分 ⇒ 同形留痕。
    # 只记过滤条件与命中数，⛔ 不记任何实体状态值。
    record_query(store, entity_id=f"list:{domain or '*'}:{area or '*'}:{keyword or '*'}",
                 caller=caller)
    return _catalog(store).list_entities(domain=domain, area=area, keyword=keyword, limit=limit, offset=offset)


def catalog_state(store: GraphStore, entity_id: str, caller: str = "") -> dict[str, Any]:
    """单实体状态（实时优先，失败回退目录缓存并标注）。

    v1.29.0 Q4 甲：查询面**免执行授权**（配对 agent 默认可查），但「免授权 ≠ 免审计」——
    每次查询落一条同形痕（who/what/when/which agent）。只记"谁问了哪个实体"，
    ⛔ 不记返回的状态值：台账要能回答"谁看过家里"，不该顺手把家里状态抄一份进去。
    """
    state = _catalog(store).get_state(entity_id)
    record_query(store, entity_id=entity_id, caller=caller)
    return state


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
        # AF71c：遍历 watch 列表时，单个 sidecar 形态损坏（JSON 不是 dict / 指向的 IR
        # 形状不对）绝不能让整个 /api/watch/list 500——跳过这一条并留痕，其余照常列出。
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"sidecar 不是 JSON object: {type(data).__name__}")
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
        except Exception as exc:  # noqa: BLE001 —— 单条坏 sidecar 不拖垮整表
            logger.warning("list_watches: 跳过无法解析的 sidecar path=%s err=%r", path, exc)
            continue
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


def _process_alive(pid: int) -> bool:
    """进程 PID 现在是否**真正存活**（僵尸不算存活）。

    孤儿锁判定的关键：watch 被 SIGKILL 后，若父进程（serve）没有 wait，它会变成
    僵尸（state=Z）——此时 ``/proc/<pid>`` 仍在、``os.kill(pid,0)`` 也成功，但它已经
    不持任何资源（flock 随 fd 关闭早已释放）。旧逻辑只看 ``/proc`` 存在与否，会把
    僵尸误判成"持有者还活着"，导致孤儿锁永远无法回收（2026-10-17 实测 PID 36=Z）。

    - POSIX：``/proc/<pid>/stat`` 第 3 字段是进程状态，``Z`` 即僵尸 → 视为不存活；
      ``/proc`` 不存在 → 不存活。
    - Windows：没有 /proc，保守地用 ``os.kill(pid,0)`` 探活（无法区分僵尸，Windows
      上 watch 生命周期本就不完整，见 stop_watch docstring）。
    """
    if pid <= 0:
        return False
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists():
        try:
            data = stat_path.read_text(encoding="utf-8")
            # comm 字段含括号且可能含空格，要从最后一个 ')' 之后切；状态是其后第一个 token
            after = data[data.rfind(")") + 1:].split()
            state = after[0] if after else "?"
            return state != "Z"
        except (OSError, IndexError):
            # 读不到状态时保守判定为存活（交给后续的内核锁真值裁决）
            return True
    if os.name == "nt":
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
    return False


def _orphan_lock_recoverable(holder: Mapping[str, Any], lock: Path) -> "tuple[bool, str]":
    """判定当前锁是否为**本机孤儿锁**（持有者已死 + 内核锁已空闲），可在 force 下安全回收。

    与 ``_verified_pid`` 的拒杀保护互补：那条保护针对"PID 对不上、可能误杀活进程"；
    本函数针对的是**没有任何活进程可杀**的故障态——watch 崩溃/被杀后诊断件残留。

    安全口径（必须同时满足，缺一不可）：
    - sidecar owner 能解析出身份；
    - 持有者主机 == 本机（跨主机的 PID 在本机 /proc 里查不到，无权对它的死活下结论）；
    - sidecar owner 里的 PID 已不存活（含僵尸 Z）；
    - 协调锁内核真值为空（``_lock_is_free``，flock 已随持有者 fd 关闭而释放）。

    锁若仍被某个活进程持有（PID 复用/fd 被孙进程继承），哪怕 sidecar 的 PID 死了也绝不回收。
    本函数只做"能不能安全回收"的**判定**，真正执行回收需要调用方显式 ``force``（见
    ``stop_watch``）：默认档位维持审计 BUG-06 的保守口径，PID 对不上不自动删任何文件。
    """
    identity = _owner_pid_from_sidecar(str(holder.get("owner", "")))
    if identity is None:
        return False, "sidecar_identity_missing"
    host, owner_pid = identity
    if host != socket.gethostname():
        return False, f"pid_belongs_to_another_host(sidecar_host={host},this_host={socket.gethostname()})"
    if _process_alive(owner_pid):
        return False, f"holder_alive(pid={owner_pid})"
    if not _lock_is_free(lock):
        return False, "kernel_lock_still_held"
    return True, f"orphan(holder_pid={owner_pid}_dead_or_zombie,lock_free)"


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


def stop_watch(
    owner: str | None = None,
    store_root: str | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
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
    - **退场证据只有那把锁，三档信号状态都不算**（第七轮审计补格，运行期腿见
      `tests/unit/test_watch_lifecycle_runtime.py`）：`os.kill` 抛 `ProcessLookupError` 只证明
      "盘上那个数字已经不是 watcher"，而锁仍可能被另一个活进程持着（PID 复用、或持锁 fd 被
      子进程继承）。旧写法在该分支直接写 `exited` 并删掉 sidecar／PID——对外报 `ok=true`，
      同时把下一个 watcher 撞锁时唯一读得懂的现场清光。现在三档（`delivered`／`already_gone`／
      `not_delivered(异常名)`）一律走同一圈轮询，并把档名回传在 `signal` 那一格。
    - **平台层异常不许抛穿**：`os.kill` 在 Windows 上对失效句柄抛的不一定是 `ProcessLookupError`
      或 `PermissionError`，而 `start_watch` 那条同形状链早收成一串 `OSError`。抛穿的后果是
      HTTP 面读成 500、serve 退出路径读成"收尾链炸了"，盘上状态却一点没变——现在如实记成
      `not_delivered(...)` 后继续问内核。
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
        # 孤儿锁回收（2026-10-17 敲打实测的 P0 死锁）：watch 崩溃/被杀后，pid_file 与 sidecar
        # 对不上（失败的 start_watch 污染了 pid_file）或只剩僵尸，默认档位仍按审计 BUG-06 口径
        # **一个文件都不删、一个信号都不发**（PID 对不上时无法保证没有活进程，这是测试钉死的
        # 安全线，连探活用的 os.kill(0) 都不发）。
        # 仅在调用方显式 ``force=True`` 时，才做孤儿判定（此时才探活）：身份在本机、持有者已死
        # （含僵尸）、内核锁空闲三条同时成立，才回收 sidecar/PID 诊断件。**协调锁文件本身从不删**
        #（flock 挂在 inode 上，空锁文件保留不影响新 watcher 重新拿锁，与 BUG-04 口径一致）。
        if force:
            orphan, orphan_why = _orphan_lock_recoverable(holder, lock)
            identity_missing = orphan_why == "sidecar_identity_missing"
            force_ok = orphan or (identity_missing and _lock_is_free(lock))
            if force_ok:
                cleanup = _cleanup_watch_files(info=info, pid_file=pid_file)
                logger.warning(
                    "WATCH_ORPHAN_RECOVERED force 回收本机孤儿锁 why=%s cleanup=%s",
                    orphan_why, cleanup,
                )
                return {
                    "ok": True,
                    "reason": "orphan_recovered",
                    "why": orphan_why,
                    "stopped": holder.get("owner", ""),
                    "graph": holder.get("graph", ""),
                    "hint": "force 回收：持有者已不存在（崩溃/被杀/僵尸）且协调锁已空闲，"
                            "已清理 sidecar/PID 诊断件（协调锁文件保留、原地复用）；未对任何活进程动刀。",
                    "cleanup": cleanup,
                }
        # 默认（无 force）或 force 但判定不成立：一个文件都不删，现场留着才读得懂为什么没停成。
        hint = (
            "watcher 是手工 `forge watch` 起的（没有 PID 文件）：请对它自己 Ctrl+C，"
            "或对持有进程发信号——服务层不再猜 PID；若确认无活 watcher，可传 force=true 回收。"
            if identity == "no_pid_file"
            else "PID 文件与 sidecar 记录对不上（或持有者在别的主机）：先按 `watch.lock.info` "
                 "查明持有者，再决定；若确认持有者已死且无活 watcher，可传 force=true 回收孤儿锁。"
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

    #: 信号投递的三种结果分开记。`delivered` 只说明"发出去了"，`already_gone`／`not_delivered(…)`
    #: 说明"没发出去"——**三种都不含"对方退了场"**，那句话只能由下面那圈内核锁轮询来说。
    signal_state = "delivered"
    signal_note = f"已向 PID {pid} 发 SIGTERM"
    try:
        os.kill(pid, 15)  # SIGTERM
    except ProcessLookupError:
        signal_state = "already_gone"
        signal_note = f"PID {pid} 早已不在：信号发不动，也就没有任何「收好了」的证据"
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
    except OSError as exc:
        # Windows 对失效句柄抛的不是上面两枚子类（`start_watch` 那条同形状链早就收成一串
        # `OSError`）。让它抛出去＝HTTP 面读成 500、退出路径读成"收尾链炸了"，而盘上状态一点没变。
        signal_state = f"not_delivered({type(exc).__name__})"
        signal_note = f"对 PID {pid} 发信号失败（{type(exc).__name__}）"

    # 退场证据只有内核那把锁——**包括"PID 早就不在了"这一格**。锁可以被别的活进程持着（PID 复用、
    # 或者持 fd 的其实是孙进程），那时按"PID 没了"就清诊断件，会让对外报 `ok=true`、把下一个
    # watcher 撞锁要用的现场一起删掉。空退避表落 `exit_unconfirmed`，与"预算花完"是同一条路径。
    exit_state = "exit_unconfirmed"
    for wait_s in _WATCH_EXIT_WAIT_S:
        time.sleep(wait_s)
        if _lock_is_free(lock):
            exit_state = "exited"
            break
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
        "signal": signal_state,
        "exit": exit_state,
        "cleanup": cleanup,
    }
    if exit_state == "exit_unconfirmed":
        result["reason"] = "exit_unconfirmed"
        result["error"] = (
            f"{signal_note}，而协调锁在 {_WATCH_EXIT_WAIT_TOTAL_S:.1f} 秒预算内仍被别的进程持着"
            "——持锁者不是刚核验过的那枚 PID 能解释的（对方还在收尾／信号被平台吞了／锁 fd 被子进程继承）。"
            "诊断件一律保留，**没有**再动锁文件，也**没有**再对任何 PID 动刀。"
        )
    return result


#: watch 的部署 IR 落在**每次新建的一次性目录**里，目录名必须每回不同：它是 sidecar `graph`
#: 字段的身份锚点（`start_watch` 靠"sidecar 里那条路径就是我这次写的"证明锁归本次），
#: 固定名会把上一条 watch 的残留读成"本次启动成功"（2026-10-09 现场实测撞过）。
#: 前缀同时是清扫器的射程——只有这一个形状会被本层删。
WATCH_IR_PREFIX = "watch-ir-"
WATCH_IR_FILENAME = "deployed_ir.json"
#: 清扫跳过比这更年轻的目录：并发的另一次 `start_watch` 可能正处在"已建目录、还没写出 sidecar"
#: 的窗口里（那窗口就是 `_WATCH_PROBE_TOTAL_S` ≈ 11.9 秒），这里取五倍余量。
WATCH_SCRATCH_MIN_AGE_S = 60.0
#: `watch.log` 的大小闸：超限轮转一档（旧那份进 `watch.log.1`）。watch 子进程逐条触发写日志，
#: 没有闸就是常驻服务里一个无界增长的磁盘文件（20261012 审计席确证的那一族）。
WATCH_LOG_MAX_BYTES = 8 * 1024 * 1024


def _reap_stale_watch_scratch(
    root: Path, keep_graph: "str | None", *, now: "float | None" = None
) -> int:
    """删掉历次 `start_watch` 留下的 `watch-ir-*` 目录，返回删掉的份数。

    三条例外一律留下：① sidecar 现在指着的那份部署 IR（在役 watcher 的身份，不是垃圾）；
    ② 建出来还不足 `WATCH_SCRATCH_MIN_AGE_S` 的；③ 目录里不是**单个本层 IR 文件**的——形状
    不是本层写出的就是归属未知，按第十八轮 F12 的口径**不动**。
    单文件接受两种本层形态：`deployed_ir.json`（现版）与 `ir.json`（watch 侧/旧版，
    2026-10-17 实测现场 4 个孤儿目录就是 ir.json，旧逻辑只认 deployed_ir.json 导致永不清理）。
    删不掉只出声、不抛穿：磁盘卫生不值得让一次 watch 启动失败。
    """
    deleted = 0
    try:
        candidates = sorted(root.glob(f"{WATCH_IR_PREFIX}*"))
    except OSError as exc:
        logger.warning("WATCH_SCRATCH_SWEEP_FAILED root=%s err=%s —— 本次不清扫", root, exc)
        return 0
    keep = Path(keep_graph).parent if keep_graph else None
    moment = time.time() if now is None else now
    known_ir_files = {WATCH_IR_FILENAME, "ir.json"}
    for scratch in candidates:
        if keep is not None and scratch == keep:
            continue
        try:
            if not scratch.is_dir():
                continue
            if moment - scratch.stat().st_mtime < WATCH_SCRATCH_MIN_AGE_S:
                continue
            entries = sorted(p.name for p in scratch.iterdir())
        except OSError as exc:
            logger.warning("WATCH_SCRATCH_DIR_UNREADABLE path=%s err=%s —— 跳过这一份", scratch, exc)
            continue
        if len(entries) != 1 or entries[0] not in known_ir_files:
            logger.warning(
                "WATCH_SCRATCH_SHAPE_FOREIGN path=%s 内容=%s —— 不是本层写出的形状，不动",
                scratch,
                entries,
            )
            continue
        try:
            (scratch / entries[0]).unlink()
            scratch.rmdir()
        except OSError as exc:
            logger.warning("WATCH_SCRATCH_DELETE_FAILED path=%s err=%s", scratch, exc)
            continue
        deleted += 1
    if deleted:
        logger.info("WATCH_SCRATCH_REAPED count=%d root=%s", deleted, root)
    return deleted


def _drop_watch_scratch(scratch: Path) -> bool:
    """子进程**确定没起来**那一档（`Popen` 抛错）就地收回本次目录，不留到下一次启动才扫。"""
    try:
        ir = scratch / WATCH_IR_FILENAME
        if ir.exists():
            ir.unlink()
        scratch.rmdir()
    except OSError as exc:
        logger.warning(
            "WATCH_SCRATCH_DELETE_FAILED path=%s err=%s —— 本次目录留下，由下一次启动清扫", scratch, exc
        )
        return False
    return True


def _gate_watch_log(root: Path) -> None:
    """开新 watch 之前给 `watch.log` 上大小闸：超限就轮转一档，旧那份进 `watch.log.1`。

    此刻父进程已给旧 watch 发过 SIGTERM，但它可能还持着日志句柄 ⇒ Windows 上 `rename` 会失败。
    轮转失败只出声、不拦启动：宁可不轮转，也不能让日志卫生把一次 watch 启动打死。成功轮转会丢掉
    上一档 `watch.log.1`，那是日志的删除，所以按 WARNING 出声。
    """
    log = root / "watch.log"
    try:
        size = log.stat().st_size
    except OSError:
        return  # 文件不在（或读不出）＝没有超限可言
    if size < WATCH_LOG_MAX_BYTES:
        return
    backup = log.with_name("watch.log.1")
    try:
        if backup.exists():
            backup.unlink()
        log.rename(backup)  # fixed-tmp: exempt(这张替换脸搬运的是已经写完的整份 watch.log，不是半成品 tmp ⇒ 没有截断窗口；正名那一份由本次 start_watch 独占开新，不存在第二个同名写者)
    except OSError as exc:
        logger.warning(
            "WATCH_LOG_ROTATE_FAILED path=%s size=%d err=%s —— 本轮不轮转，继续追加", log, size, exc
        )
        return
    logger.warning(
        "WATCH_LOG_ROTATED path=%s size=%d cap=%d backup=%s",
        log,
        size,
        WATCH_LOG_MAX_BYTES,
        backup,
    )


def start_watch(ir: dict, store_root: str | None = None, dry_live: bool = True,
                auth_code: str = "") -> dict[str, Any]:
    """启动 watch 进程跑指定 IR（**手动入口**——要码；手动入口属于四条起监听路径之一，
    路径清单与"码只收一次"口径见 DCD 裁定 20261031 §2.2）。

    起监听的四条路径在函数层各归各：
    - 公开手动入口（HTTP `/api/watch/start`、MCP、CLI 显式调用）＝**本函数**，要码；
    - serve 进程重启自动恢复（无人值守）＝走 `_restore_watch_after_restart`，免码；
    - enable 后对账 / approve 后自动起 ＝ 凭据在 enable/approve 那一跳已收（码只收一次）。

    过闸的码分两型（af_auth 口径）：
    - 短期码：一次性，consume 后置 `consumed`，不能再用于本闸或下一跳；
    - 长期码：计 `use_count`、不撤销、TTL 默认 180 天（`AUTOFORGE_AUTH_LONGCODE_TTL_DAYS`）。

    P1-19 修复：
    - HA 令牌经环境变量 HA_LONG_LIVED_TOKEN（旧 AUTOFORGE_HA_TOKEN）传递（不出现在 /proc/pid/cmdline）
    - 用 PID 文件精确终止旧 watch（不用 pkill -f 模式匹配）
    - 返回值基于真实健康探测（子进程 poll）

    第六轮审计 BUG-05／BUG-06 追加两格：
    - 探测改成退避表 `_WATCH_PROBE_DELAYS_S`（首次 0.1 秒，合计 11.9 秒）。固定 1×15 秒的旧写法
      让成功路径至少白等 1 秒、失败路径把一个同步线程池工位占满 15 秒。
    - 停旧 watch 前先核验 PID 身份（`_verified_pid`）；核验不过就**不杀**，由新 watcher 自己去
      抢协调锁，抢不到就如实回 `coord_lock_held_by_other`（带 `previous_watch` 说明没杀成的原因）。
      "端点立刻返回 job id、由前端轮询"那一半要改 HTTP 面（`af_api.py`），不在这次的射程内。
    """
    _real_root = store_root if isinstance(store_root, (str, os.PathLike)) else None
    _gate_store = GraphStore(str(_real_root)) if _real_root else None
    # dry-live 不动设备（do 只记意图）——与 live_preflight 的 confirm 检查同口径，免码。
    if not dry_live:
        code_tag = _auth_code_check(_gate_store, auth_code, action="启动真机监听（start_watch）")
    else:
        code_tag = ""
    out = _start_watch_impl(ir, store_root=store_root, dry_live=dry_live)
    if code_tag:
        out["authorized_by"] = code_tag
        # AF-403 后半（裁定 Q4）：过闸凭据进审计流水。
        try:
            from .af_audit import AuditEvent as _AE, record_deploy as _rd
            _rd(
                _AE("WATCH_STARTED", at=datetime.now(timezone.utc),
                    message="live watch started (manual entry)",
                    data={"authorized_by": code_tag, "pid": out.get("pid")}),
                _gate_store,
            )
        except Exception as exc:
            logger.warning("WATCH_START_AUDIT_RECORD_FAILED 监听已起但记账失败：%s", exc)
    return out


def _restore_watch_after_restart(ir: dict, store_root: str) -> dict[str, Any]:
    """serve 进程重启的自动恢复（DCD 裁定 2026-10-30 §2.2 前半）。

    ★ **自动恢复特例：⛔ 扩权，仅限 serve 启动序列内调用。** 进程重启时无人可给码，
    加码＝把「进程死了有人补监听」换成「进程死了没人补」（假武装比免码更危险）。
    免码的正当性来自"无人值守"，与 dry-live 的免码（不动设备）是两种理由，⛔ 混写。
    函数层与公开 start_watch 分开，防参数漏传重开旁门。
    """
    return _start_watch_impl(ir, store_root=store_root, dry_live=False)


def _start_watch_impl(ir: dict, store_root: str | None = None, dry_live: bool = True) -> dict[str, Any]:
    """`start_watch` 的实现体（两条路径共用；闸在公开入口与自动恢复各自的包装层）。

    20261012 审计席确证的两格磁盘卫生（本批收）：
    - 每次启动建的 `watch-ir-*` 目录**先扫旧再建新**（`_reap_stale_watch_scratch`），`Popen` 抛错
      那一档就地收回本次那份。目录名仍必须每次不同——它是 sidecar 的身份锚点，见上面那段。
    - `watch.log` 在起子进程前过大小闸（`_gate_watch_log`）：超 `WATCH_LOG_MAX_BYTES` 轮转一档。
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
    # 先扫掉历次留下的 watch-ir-* 目录，再建本次的（sidecar 指着的那份不扫，它是身份不是垃圾）。
    _reap_stale_watch_scratch(root, previous_holder.get("graph"))
    # 写 IR 到一次性目录里的临时文件
    scratch = Path(tempfile.mkdtemp(prefix=WATCH_IR_PREFIX, dir=str(root)))
    tmp = scratch / WATCH_IR_FILENAME
    # 走原子助手：这份 IR 随后被独立进程读取，崩在半截会让部署侧读到半个图
    # （判据 E，审计 BUG-05）
    atomic_write_text(tmp, json.dumps(ir, ensure_ascii=False, indent=2))
    # P1-19：令牌从 credentials.json 读出，经环境变量传递（不传 --ha-token argv）
    ha_token = ""
    cred = root / "credentials.json"
    # AF58b：凭据文件存在却读不出来时，旧实现静默带空令牌起子进程，而探测只看 sidecar
    # 是否写出——"有令牌"与"无令牌"在返回里逐字相同。必须留痕，并把凭据状态回传。
    if cred.exists():
        try:
            ha_token = json.loads(cred.read_text(encoding="utf-8")).get("ha_token", "")
        except (OSError, ValueError) as exc:
            logger.warning(
                "START_WATCH_UNREADABLE_CREDENTIALS start_watch: 凭据文件存在但读不出 %s err=%r —— 将在无 HA 令牌下启动子进程",
                cred, exc,
            )
            cred_state = "unreadable"
        else:
            cred_state = "loaded" if ha_token else "empty"
            if not ha_token:
                logger.warning(
                    "START_WATCH_EMPTY_TOKEN start_watch: 凭据文件存在但 ha_token 为空 %s —— 将在无 HA 令牌下启动子进程",
                    cred,
                )
    else:
        cred_state = "absent"
    # 起 watch（不传 --ha-token，令牌走环境变量）
    cmd = [
        "forge", "watch", str(tmp),
        "--persist-dir", str(root),
        "--ha-url", os.environ.get("AUTOFORGE_HA_URL", ""),
    ]
    if dry_live:
        cmd.append("--dry-live")
    else:
        # 真机常驻监听必须 --confirm + --live-allow（白名单）。
        # 注意：本层在 dry_live=False 这一档**确实**传 --confirm/--live-allow（下一行），
        # 与早期"本层从不传"的旧注释相反——那条注释是陈旧的第二份真值（2026-10-17 审计核对）。
        cmd.append("--confirm")
        # 从 IR 提取所有实体作为白名单：含触发器（递归 group sources）、do 节点 params、
        # 以及 expect/if 等条件节点 params，避免真机档漏掉实体被白名单拦下。
        entities: set[str] = set()

        def _collect_trigger_entities(trig: dict) -> None:
            if not isinstance(trig, dict):
                return
            eid = trig.get("entity_id") or trig.get("entity")
            if eid:
                entities.add(eid)
            for src in trig.get("sources", []) or []:
                _collect_trigger_entities(src)

        # AF-723（PM 卡 20261102 追加①）：白名单提取同时认**容器与单条**两种 IR 形状
        # （与 l2_summary_of 的 AF-719 归一同族口径：判别只认容器键 `automations`，⛔ 不靠
        # nodes 键判）。手动 /api/watch/start 传单条 IR 时旧码循环零次 ⇒ entities 恒空 ⇒
        # --live-allow 整个不传 ⇒ 真机可写面被静默收窄。⛔ 空白名单语义不变（提取不到
        # 实体仍不传 --live-allow，live 面空白名单照旧被拒）——本修法只让单条形状能
        # 提出实体，不改放行语义。
        _autos = ir.get("automations")
        if not isinstance(_autos, list):
            _autos = [ir]  # 单条形状归一（AF-719 同族判别：单条 ⇒ [ir]）
        for auto in _autos:
            for node in auto.get("nodes", []):
                _collect_trigger_entities(node.get("trigger", {}) or {})
                params = node.get("params", {}) or {}
                eid = params.get("entity_id")
                if eid:
                    entities.add(eid)
        if entities:
            cmd.extend(["--live-allow", ",".join(sorted(entities))])
    # P1-19：构造子进程环境，注入 HA 令牌（新名 + 旧名双写，兼容新旧 CLI 读取）
    child_env = dict(os.environ)
    if ha_token:
        child_env["HA_LONG_LIVED_TOKEN"] = ha_token
        child_env["AUTOFORGE_HA_TOKEN"] = ha_token
    # 日志句柄在**父进程侧**必须关掉（审计 BUG-14）：`Popen` 已经把 fd 复制给子进程，父进程再
    # 持着一份只会让 Windows 上的 watch.log 无法轮转/删除，而 `Popen` 抛错的异常路径原来连
    # 引用计数回收都走不到。
    try:
        _gate_watch_log(root)
        with (root / "watch.log").open("ab") as logf:
            proc = subprocess.Popen(
                cmd,
                stdout=logf,
                stderr=subprocess.STDOUT,
                start_new_session=False,  # 巡检 #7：serve 死则 watch 死（之前 True 导致孤儿持锁）
                env=child_env,
            )
    except Exception as e:
        # 子进程确定没起来 ⇒ 本次这份 scratch 就地收回，不等下一次启动才扫。
        _drop_watch_scratch(scratch)
        return {"ok": False, "error": f"启动 watch 失败: {e}"}
    # PID 文件**推迟到探测成功后才写**（2026-10-17 敲打 P0 修复）：旧实现 Popen 后立即写，
    # 而子进程可能抢锁失败/启动即退——此时 pid_file 已被写成一个死进程的 PID，且 child_exited
    # 分支不回滚，导致后续 stop_watch 永远 pid_mismatch_with_sidecar → 孤儿锁死锁。
    # 现在只有在 sidecar 确认属于本次（真正注册成功）后才落 pid_file。
    pid_written = False
    # 等 sidecar 出现，同时做健康探测。
    # **sidecar 身份必须对得上本次这份 IR**：它是目录里唯一的一个文件，上一条 watch 的残留会被读成
    # "本次启动成功"（2026-10-09 现场实测：1.18s 就回 ok=true，带的是 9-29 另一条 watch 的 graph 路径）。
    import time
    info = root / "watch.lock.info"
    mine = str(tmp)
    # 档位如实命名，且**从本函数自己拼给 CLI 的旗子里读回来**（不另立第二套说法）。
    # `forge watch` 没有 `--live` 这个旗子：它的真机档是"不带 `--dry-live`"（af_cli.py:585
    # `live=not dry_live`），而缺 `--confirm` 时 CLI 直接拒启动（af_cli.py:565-566）。
    # 本层在 dry_live=False 这一档**会**传 `--confirm`/`--live-allow`（见上），故该档能真正
    # 常驻监听真机；dry_live=True 只带 `--dry-live`，不碰真机。
    tier = "dry_live" if "--dry-live" in cmd else "live_confirmed"
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
            # 注册成功后才写 PID 文件，且必须与 sidecar owner 的 PID 一致（从根上杜绝
            # pid_file ≠ sidecar 这档死锁）。
            owner_identity = _owner_pid_from_sidecar(str(data.get("owner", "")))
            sidecar_pid = owner_identity[1] if owner_identity else None
            pid_to_record = sidecar_pid if sidecar_pid is not None else proc.pid
            try:
                atomic_write_text(pid_file, str(pid_to_record))
                pid_written = True
            except OSError as exc:
                logger.warning(
                    "start_watch: 写 PID 文件失败（watcher 已注册但无法经 stop_watch 核验终止）"
                    " pid=%s path=%s err=%r", pid_to_record, pid_file, exc,
                )
            success_payload: dict[str, Any] = {
                "pid": proc.pid,
                "owner": data.get("owner", ""),
                "graph": data.get("graph", ""),
                "acquired_at": data.get("acquired_at", ""),
                "dry_live": dry_live,
                "tier": tier,
                # dry_live=True 只记意图不碰真机；dry_live=False 已带 --confirm + --live-allow，
                # 注册成功后常驻监听并可下发真机，故 real_device 据实回填。
                "real_device": not dry_live,
                "pid_file_written": pid_written,
                "credentials": cred_state,
            }
            if not pid_written:
                # AF58：watcher 虽已注册，但 PID 文件写不进去 ⇒ stop_watch 将拒绝核验终止，
                # 这是个"管理不了"的进程。不能裸 ok=True 掩盖（孤儿占锁、零日志）。
                return {
                    "ok": False,
                    "reason": "pid_file_write_failed",
                    "error": (
                        f"watcher 已启动并注册（pid={proc.pid}），但 PID 文件 {pid_file} 写入失败，"
                        "后续无法经 /api/watch/stop 核验终止；请按 sidecar owner 手动清理，"
                        "或修复 PID 文件目录权限后重启。"
                    ),
                    **success_payload,
                }
            return {"ok": True, **success_payload}
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


# ── v1.29 场景对象（SceneDef）落盘面 ＋ 执行面 ──────────────────────────────
# 卡名 AF-V129-G4-20261111；设计稿 docs/architecture/v129_scene_object_design.md §2/§3/§5。
# 三分边界：⛔ 本段不碰 `af_scene.py`（automation 组启停）与 `af_task_order.py`（任务单状态机），
# 只做「预设的批量设备动作清单」的落盘、一句话触发与按序执行。
#
# 落盘形状：一场景一文件 `.scenes/defs/<scene_id>.json`（内容 = `SceneDef.to_ir()` ＋ owner
# ＋ created_at）。为什么不是 `.orders/active.json` 那种单文件列表：场景定义是**用户手写资产**，
# 一文件一场景让「改错一个不影响别的」和 `git diff` 都可读。
# 删除 = 移到 `.scenes/deleted/`（⛔ 不物理删：误删无从恢复；同名加纳秒戳，与 answer_inbox 同纪律）。

SCENE_DEFS_DIR = (".scenes", "defs")
SCENE_DEFS_DELETED_DIR = (".scenes", "deleted")


def _scene_store_root(store: "GraphStore | None", *, what: str) -> Path:
    root = getattr(store, "root", None)
    if root is None:
        raise ServiceError(f"场景{what}需要 store 根——本调用未携带 store", status=403)
    return Path(root)


def _scene_defs_dir(store: "GraphStore | None") -> Path:
    return _scene_store_root(store, what="定义（落在 .scenes/defs/）").joinpath(*SCENE_DEFS_DIR)


def _scene_deleted_dir(store: "GraphStore | None") -> Path:
    return _scene_store_root(store, what="归档（落在 .scenes/deleted/）").joinpath(
        *SCENE_DEFS_DELETED_DIR)


def _scene_def_path(store: "GraphStore | None", scene_id: str) -> Path:
    return _scene_defs_dir(store) / f"{scene_id}.json"


def _scene_iso() -> str:
    """本地时区 ISO 时间戳（与 `af_scene_kit._iso` 同形，⛔ 另造第二种格式）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time()))


def _scene_read_defs(store: "GraphStore | None") -> list[dict[str, Any]]:
    """读全部场景定义（按 scene_id 排序）。

    坏文件**跳过并 warn**：一枚被手改坏的定义不该把整个列表/触发面拖成 500——
    但要留痕，否则「场景怎么没了」事后查不到。
    """
    directory = _scene_defs_dir(store)
    if not directory.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("SCENE_DEF_UNREADABLE 场景定义无法解析，已跳过：%s（%s）", path.name, exc)
            continue
        if isinstance(payload, Mapping) and payload.get("scene_id"):
            out.append(dict(payload))
    return sorted(out, key=lambda p: str(p.get("scene_id") or ""))


def _scene_owner_of(payload: Mapping[str, Any]) -> str:
    return str(payload.get("owner") or "")


def _scene_subject(*, caller: str, caller_is_owner: bool,
                   token_identity: bool | None) -> Subject:
    """身份单源（C-3）：⛔ 本段自造第二份归属规则，判定一律走 `decide`。"""
    sub = Subject.from_caller(caller, owner_face=caller_is_owner, token_identity=token_identity)
    if sub.token_state is TokenState.BROKEN:
        raise ServiceError(broken_identity_error(), status=403)
    return sub


def _scene_assert_visible(payload: Mapping[str, Any], *, sub: Subject) -> None:
    """读面归属判定（⑩ 两条腿）：非 owner 面读别人的场景 ⇒ 404 且与「不存在」逐字同形。

    ⛔ 不回「无权查看」：那等于承认存在（同 `_assert_archive_visible` 纪律）。
    """
    scene_id = str(payload.get("scene_id") or "")
    verdict = decide(lambda _n: _scene_owner_of(payload), scene_id, sub, write=False)
    if verdict is DenyKind.NOT_FOUND:
        raise ServiceError(visible_error(scene_id), status=404)


def _scene_assert_mutator(payload: Mapping[str, Any], *, sub: Subject, action: str) -> None:
    """写面归属判定：非 owner 面只能动自己名下的场景；无归属 ⇒ 仅 owner 面（fail-closed）。

    ⛔ 拒绝文案不点归属者的名（AF-509：那是批量采集全库归属者的通道）。
    """
    scene_id = str(payload.get("scene_id") or "")
    if decide(lambda _n: _scene_owner_of(payload), scene_id, sub, write=True) is DenyKind.FORBIDDEN:
        if _scene_owner_of(payload):
            raise ServiceError(mutator_error(scene_id, action), status=403)
        raise ServiceError(
            f"场景 {scene_id!r} 无归属，仅 owner 面可{action}（所有权隔离）", status=403)


def _scene_find(store: "GraphStore | None", scene_id: str) -> dict[str, Any]:
    """按 scene_id 取定义；不存在即 404（文案与「不属于你」同形）。"""
    sid = str(scene_id or "").strip()
    if not sid:
        raise ServiceError("场景 scene_id 不能为空", status=400)
    for payload in _scene_read_defs(store):
        if str(payload.get("scene_id") or "") == sid:
            return payload
    raise ServiceError(visible_error(sid), status=404)


def _scene_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """列表面条目：**不含 steps 全文**（列表要轻），步数单独给。"""
    return {
        "scene_id": str(payload.get("scene_id") or ""),
        "name": str(payload.get("name") or ""),
        "aliases": [str(a) for a in (payload.get("aliases") or [])],
        "version": payload.get("version", 1),
        "owner": _scene_owner_of(payload),
        "condition": payload.get("condition"),
        "step_count": len(payload.get("steps") or []),
        "created_via": str(payload.get("created_via") or ""),
        "created_at": str(payload.get("created_at") or ""),
    }


def scene_def_save(
    store: "GraphStore | None",
    scene_ir: Mapping[str, Any],
    *,
    caller: str = "",
    caller_is_owner: bool = False,
    token_identity: bool | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """落盘一份场景定义（构造期校验 fail-closed：查重／空 steps／未知键／L3 与禁入域拒绝）。

    `owner` 取**调用主体**（人那一侧身份，⛔ agent 不能自签）：场景是用户的资产，
    归属决定非 owner 面看不看得见、改不改得动。
    `replace=False` 时 scene_id 撞车即拒（409）：场景是**一句话触发**的入口，静默覆盖
    等于把用户原来的「回家模式」换成另一个，触发词还指着它——必须是显式动作。
    """
    sub = _scene_subject(caller=caller, caller_is_owner=caller_is_owner,
                         token_identity=token_identity)
    try:
        defn = SceneDef.from_ir(scene_ir, created_at=_scene_iso())
    except SceneDefError as exc:
        raise ServiceError(f"场景定义不合法：{exc}", status=400) from exc

    owner = str(caller or "")
    path = _scene_def_path(store, defn.scene_id)
    existing: dict[str, Any] | None = None
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = None
        existing = dict(loaded) if isinstance(loaded, Mapping) else None
    if existing is not None and not replace:
        raise ServiceError(
            f"场景 {defn.scene_id!r} 已存在（scene_id 构造期查重）——改定义请显式 replace=true",
            status=409,
        )
    if existing is not None:
        _scene_assert_mutator(existing, sub=sub, action="改定义")
    else:
        total = len(_scene_read_defs(store))
        if total >= SCENE_MAX_DEFS:
            raise ServiceError(
                f"场景定义数已达上限 {SCENE_MAX_DEFS} 条：⛔ 触发词会撞车（先删掉不用的）",
                status=400,
            )

    payload = dict(defn.to_ir())
    payload["owner"] = owner
    payload["created_at"] = _scene_iso()
    directory = _scene_defs_dir(store)
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    _grant_audit(
        store, "SCENE_DEF_SAVED",
        scene_id=defn.scene_id, name=defn.name, steps=len(defn.steps), owner=owner,
        replaced=existing is not None, subject=str(caller or ""),
        caller_is_owner=bool(caller_is_owner),
    )
    return payload


def scene_def_list(
    store: "GraphStore | None", *, caller: str = "", caller_is_owner: bool = False,
    token_identity: bool | None = None,
) -> dict[str, Any]:
    """列场景定义：owner 面全量；非 owner 面只看自己的，并报 `hidden_count`（⑩ 两腿）。"""
    sub = _scene_subject(caller=caller, caller_is_owner=caller_is_owner,
                         token_identity=token_identity)
    visible: list[dict[str, Any]] = []
    hidden = 0
    for payload in _scene_read_defs(store):
        scene_id = str(payload.get("scene_id") or "")
        if decide(lambda _n: _scene_owner_of(payload), scene_id, sub,
                  write=False) is DenyKind.NOT_FOUND:
            hidden += 1
            continue
        visible.append(_scene_summary(payload))
    result: dict[str, Any] = {"scenes": visible}
    if not sub.is_owner_face:
        # owner 面**不报** hidden_count：它没有藏起来的东西（⑩ 两条腿行为正相反）。
        result["hidden_count"] = hidden
    return result


def scene_def_get(
    store: "GraphStore | None", scene_id: str, *, caller: str = "",
    caller_is_owner: bool = False, token_identity: bool | None = None,
) -> dict[str, Any]:
    """取一份场景定义全文（含 steps）。"""
    sub = _scene_subject(caller=caller, caller_is_owner=caller_is_owner,
                         token_identity=token_identity)
    payload = _scene_find(store, scene_id)
    _scene_assert_visible(payload, sub=sub)
    return payload


def scene_def_delete(
    store: "GraphStore | None", scene_id: str, *, caller: str = "",
    caller_is_owner: bool = False, token_identity: bool | None = None,
) -> dict[str, Any]:
    """删场景定义 = 移入 `.scenes/deleted/`（⛔ 不物理删：误删无从恢复）。"""
    sub = _scene_subject(caller=caller, caller_is_owner=caller_is_owner,
                         token_identity=token_identity)
    payload = _scene_find(store, scene_id)
    _scene_assert_mutator(payload, sub=sub, action="删定义")
    src = _scene_def_path(store, str(payload["scene_id"]))
    dest_dir = _scene_deleted_dir(store)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{payload['scene_id']}--{time.time_ns()}.json"
    # 归档语义＝把**已经写完**的整份定义挪进 `.scenes/deleted/`：同文件系统重命名，
    # 源文件早已落盘完整、目标名带纳秒戳唯一 ⇒ 没有「固定 tmp 名被第二个写者截断」那个窗口，
    # 走 atomic_write_text 反而要多一次全量拷贝＋多一个半成品窗口。
    os.replace(src, dest)  # fixed-tmp: exempt(归档重命名，源已完整、目标名纳秒戳唯一，无固定 tmp 截断窗口)
    _grant_audit(
        store, "SCENE_DEF_DELETED", scene_id=str(payload["scene_id"]),
        name=str(payload.get("name") or ""), archived_to=str(dest),
        subject=str(caller or ""), caller_is_owner=bool(caller_is_owner),
    )
    return {"scene_id": str(payload["scene_id"]), "archived_to": str(dest), "deleted": True}


def scene_match(
    store: "GraphStore | None", phrase: str, *, caller: str = "",
    caller_is_owner: bool = False, token_identity: bool | None = None,
) -> dict[str, Any] | None:
    """一句话触发：按主词／别名**精确**匹配（⛔ 模糊猜：匹配不上即澄清）。"""
    text = str(phrase or "").strip()
    if not text:
        raise ServiceError("场景触发词为空：一句话触发需要一个主词（如「回家模式」）", status=400)
    sub = _scene_subject(caller=caller, caller_is_owner=caller_is_owner,
                         token_identity=token_identity)
    for payload in _scene_read_defs(store):
        names = [str(payload.get("name") or "")]
        names += [str(a) for a in (payload.get("aliases") or [])]
        if text not in [n for n in names if n]:
            continue
        _scene_assert_visible(payload, sub=sub)
        return payload
    return None


def _scene_live_executor(store: "GraphStore | None") -> Callable[[Any], Any]:
    """默认执行器：真机下发一步（与 `live_run` 共用同一套 live 配置／令牌／传输层）。

    ⛔ 不静默"假装成功"：live 未启用就 403——场景执行每一步都是真设备动作，
    没有 dry 语义（dry 走 `executor=` 注入假实现）。
    回传 `CallResult`（⛔ 不 `bool()` 截断：`error` 文本要进回执，AF-756）。
    """
    cfg = _live_config()
    if not cfg["enabled"]:
        raise ServiceError(
            "场景执行未启用真机下发：服务端需设置 AUTOFORGE_LIVE_ENABLED=1（或注入 executor）",
            status=403,
        )
    token = _ha_token(store.root if store is not None else None)
    ha_url = str(cfg["ha_url"])
    factory = LIVE_TRANSPORT_FACTORY
    transport = (factory(ha_url, token) if factory is not None
                 else HATransport(base_url=ha_url, token=token))
    adapter = HAAdapter(transport=transport, dry_run=False)

    def _dispatch(step: Any) -> Any:
        params = dict(getattr(step, "params", {}) or {})
        params.setdefault("entity_id", str(getattr(step, "target", "")))
        # AF-756：这里曾 `bool(...)` 一刀，把 `CallResult.error`（设备拒收／超时原文）
        # 截成 `False` ⇒ 回执只剩"失败了"、没有"为什么"。原样交回，由 runner 落 `error`。
        return adapter.call(str(getattr(step, "action", "")), params)

    return _dispatch


def scene_run(
    store: "GraphStore | None", *, scene_id: str = "", phrase: str = "", agent: str = "",
    caller: str = "", caller_is_owner: bool = False, token_identity: bool | None = None,
    grant_id: str = "", confirm: Callable[[Any], bool] | None = None,
    sleep: Callable[[float], None] | None = None,
    precondition: Callable[[Mapping[str, Any]], bool] | None = None,
    executor: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    """执行一次场景：一句话触发／显式 scene_id → 逐步独立过 grant → 汇总回执。

    判定规则（契约不变量，DCD 20261111 Q3 甲）：
    - 每步**独立**过 `GrantStore.check`（⛔ 整场一次性放行）；
    - `hard_denied` ⇒ 完成当前步、取消未执行步（已成步不回滚：设备动作不可逆）；
    - `needs_confirm` ⇒ 出票 → 人工确认 → 验票放行（⛔ 不升级 grant）；
    - `uses` 每次 run 计一次（`bump_use` 整盘重写，逐步计数＝账本抖动），在 kit 内。
    """
    sub = _scene_subject(caller=caller, caller_is_owner=caller_is_owner,
                         token_identity=token_identity)
    if scene_id:
        payload: dict[str, Any] | None = _scene_find(store, scene_id)
        _scene_assert_visible(payload, sub=sub)
    else:
        payload = scene_match(store, phrase, caller=caller,
                              caller_is_owner=caller_is_owner, token_identity=token_identity)
        if payload is None:
            raise ServiceError(
                f"没有匹配到场景：{str(phrase or '')!r}（匹配不上即澄清，⛔ 猜一个执行）", status=404)

    agent_text = str(agent or caller or "").strip()
    if not agent_text:
        raise ServiceError(
            "场景执行需要 agent（执行主体）：每步按该 agent 的执行授权放行", status=400)
    if not str(grant_id or "").strip():
        raise ServiceError(
            "场景执行需要执行授权（grant_id）：每步独立过 grant，⛔ 无授权空跑（DCD 20261111 Q3 甲）",
            status=403,
        )

    try:
        defn = SceneDef.from_ir(payload, created_at=str(payload.get("created_at") or ""))
    except SceneDefError as exc:
        raise ServiceError(f"场景定义不合法：{exc}", status=400) from exc

    # AF-790（元宝 SCN-01）：`scene_run` 经 `_scene_live_executor`（`HAAdapter(dry_run=False)`）
    # **真机下发**，与 `live_run` 同一档写入面 ⇒ 必须过同一道单写者租约。修前
    # `_single_writer_check` 全树只有 `live_run` 一处调用，于是 serve 持锁时 `af_live_run`
    # 503、`af_scene run` 照发——租约被整条场景面绕过。
    # 位置与 `live_run` 对齐（裁定 20261004 §一 1 A：「服务不可写」比「授权不足」更早看见）：
    # 排在 grant 解析**之前**——没租约就不该走到读授权账本那一步。
    _single_writer_check(store)

    gs = _grant_store(store)
    try:
        grant = gs.resolve(grant_id, agent=agent_text)
    except GrantError as exc:
        raise _grant_error(exc) from exc

    runner = SceneRunner(
        gs,
        executor=executor if executor is not None else _scene_live_executor(store),
        room_of=_grant_room_of(store),
        confirm=confirm,
        sleep=sleep,
    )
    try:
        receipt = runner.run(defn, grant=grant, agent=agent_text, precondition=precondition)
    except SceneConditionNotMet as exc:
        raise ServiceError(
            f"场景 {defn.scene_id!r} 前置条件未满足：本次不执行（⛔ 当它没有条件就开跑）",
            status=409,
        ) from exc
    except SceneDefError as exc:
        raise ServiceError(f"场景执行被拒：{exc}", status=400) from exc

    _grant_audit(
        store, "SCENE_RUN",
        run_id=str(receipt.get("run_id") or ""), scene_id=defn.scene_id, agent=agent_text,
        grant_id=str(grant.get("grant_id") or ""), code=str(receipt.get("code") or ""),
        steps=len(receipt.get("steps") or []), subject=str(caller or agent_text),
        caller_is_owner=bool(caller_is_owner),
    )
    return receipt
