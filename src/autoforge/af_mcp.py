"""AutoForge MCP server（零依赖，stdio 传输，兼容 MCP 2024-11-05）。

**为什么零依赖**：`mcp` 包在本机/NAS 双环境装不稳（外部网络受限），且 MCP 的 stdio
协议本质就是「逐行 JSON-RPC 2.0」，自实现最稳、双环境都能跑、测试可纯子进程驱动。
agent 端（Claude Desktop / CodeBuddy 等）只认 stdio 协议，不关心服务端用什么框架。

**架构**：server 进程内直调 `af_service`（与 REST 同源逻辑），不绕开任何安全闸；
并复用 v0.8.0 的 `TokenRegistry` 在**工具层**落实 scope 门——
写/live 工具需对应权限，让 agent 实测能真实踩到「只读令牌调写工具被拒」的场景。

启动：`forge mcp [--root .forge]`，由 agent 以 stdio 方式拉起：
    {"mcpServers": {"autoforge": {"command": "forge", "args": ["mcp"]}}}

协议：stdin 逐行读 JSON-RPC 请求，stdout 逐行回 JSON-RPC 响应（通知无 id 不回）。
"""

from __future__ import annotations

import json
import logging

# ADM B-07: MCP stdio single-line size limit (10MB), reject oversized to prevent OOM
MAX_STDIN_LINE = 10 * 1024 * 1024
import os
from pathlib import Path
import sys
import traceback
from typing import Any, Callable

from . import af_service as svc
from .af_auth import TokenExpired, TokenRegistry
from .af_store import DEFAULT_STORE_ROOT, GraphStore
from .af_telemetry import record_failure

__all__ = ["serve_mcp", "dispatch", "TOOLS", "PROTOCOL_VERSION"]

logger = logging.getLogger("autoforge.mcp")

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "autoforge-mcp"
SERVER_VERSION = svc.API_VERSION


def _text(s: str) -> dict[str, Any]:
    return {"type": "text", "text": s}


# ─────────────────────────────────────────────────────────────────────
# 鉴权：复用 v0.8.0 TokenRegistry，在工具层落实 scope 门
# ─────────────────────────────────────────────────────────────────────


#: R-40 fail-closed：鉴权已配却无法确定合法启动身份时，使用空 scope 身份
# （所有 write/live 工具走拒绝分支，read 仍公开），绝不再退回 None=全放行。
MCP_NOACCESS: dict[str, Any] = {"subject": "<no-access>", "scopes": []}


def _build_current(registry: TokenRegistry) -> dict[str, Any] | None:
    """当前启动主体：鉴权已配时必须由显式启动令牌 AUTOFORGE_MCP_TOKEN 确定。

    - 未配任何令牌（registry.enabled=False）→ None（原型模式全放行，向后兼容）。
    - 已配令牌 → 不再盲目取 subs[0]（两条令牌时第一条能用就放行整体 = fail-open）。
      启动令牌缺失 / 未知 / 已撤销 / 已过期 → 返回空 scope 的拒绝身份（A7）。
    """
    if not registry.enabled:
        return None
    token = (os.environ.get("AUTOFORGE_MCP_TOKEN") or "").strip()
    if not token:
        return dict(MCP_NOACCESS)
    try:
        info = registry.authenticate(token)
    except TokenExpired:
        return dict(MCP_NOACCESS)
    if info is None or info.invalid:
        return dict(MCP_NOACCESS)
    return {"subject": info.subject, "scopes": sorted(info.scopes)}


def _guard(scope: str | None, current: dict[str, Any] | None) -> None:
    """scope 门：None 表示公开；current 为 None 表示未启用鉴权（原型全放行）。"""
    if scope is None or current is None:
        return
    if scope not in current.get("scopes", []):
        raise ServiceError(
            f"拒绝：当前令牌缺少 '{scope}' 权限（当前 scopes：{current.get('scopes')}）。"
            f"请用含该 scope 的令牌启动 forge mcp（见 v0.8.0 多令牌主体模型）。"
        )


class ServiceError(Exception):
    """工具调用被业务规则拒绝（映射到 MCP isError，而非协议错误）。"""


# ─────────────────────────────────────────────────────────────────────
# 工具实现（签名统一为 (store, args) -> dict）
# ─────────────────────────────────────────────────────────────────────


def _t_health(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.health()


def _t_build(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    # v1.1.0：未显式给 known_entities 时自动取设备目录全集（目录为空则静默退化为不校验）
    return svc.build(args["ir"], args.get("known_entities"), store=store, use_catalog=True)


def _t_compile(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    text = args.get("text") or args.get("spec") or args.get("prompt") or ""
    if not text.strip():
        return {
            "ok": False,
            "ir": None,
            "nl": "",
            "diagnostics": [],
            "error": {"code": "MISSING_ARG", "message": "af_compile_spec 需要 text 参数（AF-Spec 文本）"},
        }
    return svc.compile_text(text)


def _t_simulate(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    # v1.5.0：传 store → `_telemetry` 落遥测（token/断言结果）
    return svc.simulate(args["ir"], args.get("seed"), args.get("events"), store=store)


def _t_list_graphs(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.list_graphs(store)


def _t_get_graph(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.get_graph(store, args["name"], args.get("version"))


def _t_by_tag(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.graphs_by_tag(store, args["tag"])


def _t_conf(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.get_conf(store, args["name"])


def _t_set_tags(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.set_graph_tags(store, args["name"], args["tags"])


def _t_enable(store: GraphStore, args: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    return svc.enable_by_tag(
        store, args["tag"], bool(args["enabled"]), allow_bulk=bool(args.get("allow_bulk", False))
    )


def _t_export(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.export_store(store)


def _t_import(store: GraphStore, args: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    return svc.import_store(
        store,
        args["bundle"],
        args.get("strategy", "skip"),
        allow_bulk=bool(args.get("allow_bulk", False)),
    )


def _t_save(store: GraphStore, args: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    # v1.4.0：部署前写操作先入待批队列，人审后由服务层回放落盘。
    # MCP 面绝不注册 approve（agent 不能自批；批准只在 HTTP / CLI 服务层）。
    owner = (current or {}).get("subject", "") or ""
    payload = {
        "ir": args["ir"],
        "name": args["name"],
        "note": args.get("note", ""),
        "tags": args.get("tags"),
        "expect_version": args.get("expect_version"),
        "owner": owner,
        "allow_bulk": bool(args.get("allow_bulk", False)),
    }
    # P0-13：传 authenticated_subject，让服务层强制使用认证主体
    return svc.submit_pending(store, "af_save", payload, submitted_by=owner or "mcp", authenticated_subject=owner or None)


def _t_diff(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.diff(store, args["name"], int(args["old"]), int(args["new"]))


def _t_live(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.live_run(
        args["ir"], args["live_allow"], args.get("events"), bool(args.get("confirm", False))
    )


def _t_refresh_catalog(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.catalog_refresh(
        store,
        full=bool(args.get("full", True)),
        domain=args.get("domain", "") or "",
        area=args.get("area", "") or "",
    )


def _t_resolve_entity(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.catalog_resolve(
        store,
        args["name"],
        area=args.get("area", "") or "",
        domain=args.get("domain", "") or "",
        top_n=int(args.get("top_n", 8) or 8),
    )


def _t_remember_entity(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    """【写】v1.6.0：沉淀「设备名 → entity_id」精确映射（下次 `af_resolve_entity` 直中）。"""
    return svc.catalog_set_alias(store, args["name"], args["entity_id"])


def _t_list_entities(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.catalog_list(
        store,
        domain=args.get("domain", "") or "",
        area=args.get("area", "") or "",
        keyword=args.get("keyword", "") or "",
        limit=int(args.get("limit", 50) or 50),
        offset=int(args.get("offset", 0) or 0),
    )


def _t_get_entity_state(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.catalog_state(store, args["entity_id"])


def _t_draft(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    """意图 JSON → IR，返回 ref。"""
    from .af_draft import draft_intent, DraftError
    try:
        return draft_intent(args["intent"])
    except DraftError as e:
        return {"ok": False, "error": {"code": e.code, "message": str(e), "fix": e.fix}}


def _t_apply(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    """一次走完 校验→仿真→入队。"""
    from .af_apply import apply
    ref = args["ref"]
    stage = args.get("stage", "save")
    return apply(ref, stage=stage, store=store)


def _t_catalog(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.catalog_snapshot(store)


def _t_whoami(store: GraphStore, args: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "auth_enabled": current is not None,
        "subject": (current or {}).get("subject"),
        "scopes": (current or {}).get("scopes"),
        "note": "未启用鉴权（全放行）" if current is None else "已按令牌 scope 限制写/live 工具",
    }


def _t_experience(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    """v1.5.0：实体共现经验摘要（只在成功落盘后采集）。参数：limit(int 可选，默认 10)。"""
    return svc.get_experience(store, limit=int(args.get("limit", 10) or 10))


def _t_telemetry(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    """v1.5.0：token/结果遥测 + 错误类别分布。参数：days(int 可选，默认 30)。"""
    return svc.get_telemetry(store, days=int(args.get("days", 30) or 30))


#: 需要注入当前令牌主体（`current`）的工具：用于归档归属 / 所有权隔离（v1.3.0）
_TOOLS_WITH_CONTEXT = frozenset({_t_whoami, _t_save, _t_import, _t_enable})


# ─────────────────────────────────────────────────────────────────────
# 工具注册表：name / description（agent 看）/ inputSchema / 实现 / scope
# ─────────────────────────────────────────────────────────────────────


TOOLS: list[tuple[str, str, dict[str, Any], Callable, str | None]] = [
    (
        "af_draft",
        "【黄金路径第1步】传意图 JSON，自动生成 IR 并返回 ref。实体写中文名/别名，服务端自动解析。参数：intent(object 必填)。",
        {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "object",
                    "description": "意图 JSON，如 {name, mode, when, if, do}",
                }
            },
            "required": ["intent"],
        },
        _t_draft,
        None,
    ),
    (
        "af_apply",
        "【黄金路径第2步】用 ref 一次走完 校验→仿真→入队。参数：ref(str 必填)、stage(str 可选: check/simulate/save 默认 save)。",
        {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "af_draft 返回的 ref"},
                "stage": {"type": "string", "description": "check/simulate/save，默认 save"},
            },
            "required": ["ref"],
        },
        _t_apply,
        "write",
    ),
    (
        "af_health",
        "服务健康自检：返回版本、契约版本、里程碑、只读标记。无参数。",
        {"type": "object", "properties": {}},
        _t_health,
        None,
    ),
    (
        "af_refresh_catalog",
        "拉取 HA 全屋设备目录进本地缓存（**首次连上 HA 后、设备大幅增减后调一次**）。"
        "之后 af_resolve_entity / af_list_entities 只读缓存、毫秒返回，日常写 IR 无需重复调。"
        "参数：full(bool 可选，默认 true=全量替换；false=增量合并)、domain(str 可选)、area(str 可选)。"
        "返回 {ok,added,changed,removed,total,freshness}；失败返回 ok=false + error。",
        {
            "type": "object",
            "properties": {
                "full": {"type": "boolean", "description": "true=全量替换（默认）｜false=增量合并"},
                "domain": {"type": "string", "description": "可选：只刷新某域（light/switch/…）"},
                "area": {"type": "string", "description": "可选：只刷新某房间"},
            },
        },
        _t_refresh_catalog,
        None,
    ),
    (
        "af_resolve_entity",
        "【设备名→entity_id 唯一正路】把自然语言设备名（如「书房吊灯」「显示器挂灯」）解析成所有沾边候选 "
        "entity_id。**写 IR 之前必须先调用**，拿到真实 entity_id 后只许用返回里的 ID，禁止凭记忆编造。"
        "不过滤域：同一设备名可能对应 light/switch/cover 多个实体，全部返回由你按 domain+friendly_name 判断。"
        "每个候选返回 {entity_id, friendly_name, domain, area, state, possible_states, services, high_risk, "
        "matched_by, confidence}；possible_states 直接告诉你它能切到哪些状态（含 unavailable/unknown）。"
        "参数：name(str 必填)、area(str 可选，中文房间词，找不到自动放宽全局)、domain(str 可选)、top_n(int 可选，默认 8)。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "自然语言设备名，如「书房吊灯」"},
                "area": {"type": "string", "description": "房间（中文，可选，是提示不是硬约束）"},
                "domain": {"type": "string", "description": "可选：限定域（一般不要传，让它多返回候选）"},
                "top_n": {"type": "integer", "description": "最多返回几个候选，默认 8"},
            },
            "required": ["name"],
        },
        _t_resolve_entity,
        None,
    ),
    (
        "af_remember_entity",
        "【写】v1.6.0 决策智能：把「设备名 → entity_id」的**人工/agent 选择沉淀**为精确别名映射"
        "（写 `{root}/.catalog/aliases.json`），下次 `af_resolve_entity` 同名查询**直中**（high 置信），"
        "不再每轮重排/误选。**用在你从候选里选定之后**。参数：name(str 必填，用户说的设备名)、"
        "entity_id(str 必填，必须已在本机目录中)。需 write 权限。返回 {ok,alias,entity_id,total}。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "自然语言设备名（用户原话），如「书房电脑」"},
                "entity_id": {"type": "string", "description": "真实 entity_id（先用 af_resolve_entity 取）"},
            },
            "required": ["name", "entity_id"],
        },
        _t_remember_entity,
        "write",
    ),
    (
        "af_list_entities",
        "全屋实体目录·过滤浏览（读本地缓存，不触真实 HA）。与 af_resolve_entity 互补："
        "本工具用于「按条件浏览」，如「书房有哪些 light？」「全屋 cover 各在什么状态？」。"
        "**强制分页**（默认 50/页，上限 200），并透明回报 matched_count/returned/truncated/next_offset，"
        "杜绝静默截断。参数：domain/area/keyword(均可选)、limit(int 可选，默认 50)、offset(int 可选)。",
        {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "按域过滤（light/switch/cover/climate/…）"},
                "area": {"type": "string", "description": "按房间过滤（中文）"},
                "keyword": {"type": "string", "description": "模糊匹配 entity_id / 中文名 / 区域"},
                "limit": {"type": "integer", "description": "每页条数，默认 50，上限 200"},
                "offset": {"type": "integer", "description": "分页偏移，默认 0"},
            },
        },
        _t_list_entities,
        None,
    ),
    (
        "af_get_entity_state",
        "查某实体**当前**状态（省去为「查当前状态」专门搭一个节点）。先实时读 HA，"
        "失败自动回退目录缓存并标注 source=catalog_cache（可能非最新）。"
        "参数：entity_id(str 必填，先用 af_resolve_entity 取真实 ID，勿凭记忆编造)。"
        "返回 {ok, entity_id, source, state, domain, friendly_name, possible_states}。",
        {
            "type": "object",
            "properties": {"entity_id": {"type": "string", "description": "真实 entity_id，如 light.study_main"}},
            "required": ["entity_id"],
        },
        _t_get_entity_state,
        None,
    ),
    (
        "af_catalog",
        "设备目录摘要（**刻意不 dump 全量实体**，防数千实体撑爆上下文）：按域统计 + 区域列表 + freshness。"
        "需要明细请用 af_list_entities 按过滤分页取。无参数。",
        {"type": "object", "properties": {}},
        _t_catalog,
        None,
    ),
    (
        "af_build",
        "第一道闸：校验自动化 IR（JSON 或 {'automations':[...]}）。返回 ok/errors/warnings/diagnostics/nl（自然语言渲染）。"
        "agent 写完后必须先 build 过安全闸再 sim。参数：ir(dict 必填)，known_entities(list[str] 可选，实体白名单)。",
        {
            "type": "object",
            "properties": {
                "ir": {"type": "object", "description": "JSON IR（单条自动化 dict 或 {'automations':[...]}）"},
                "known_entities": {"type": "array", "items": {"type": "string"}, "description": "可选实体白名单"},
            },
            "required": ["ir"],
        },
        _t_build,
        None,
    ),
    (
        "af_compile_spec",
        "把 AutoForge 文本语法（AF-Spec）编译成 IR（等价 JSON，同一份真相）。参数：text(str 必填)。"
        "返回 ok/ir/nl/diagnostics；失败返回 ok=false + error。",
        {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "AF-Spec 文本"}},
            "required": ["text"],
        },
        _t_compile,
        None,
    ),
    (
        "af_simulate",
        "第二道闸：在内存 FakeHA 上回放自动化，验证逻辑。参数：ir(dict 必填)，"
        "seed(dict 可选：实体初始状态)，events(list[dict] 可选：[{'entity_id','state'} | {'advance_s':秒}])。",
        {
            "type": "object",
            "properties": {
                "ir": {"type": "object"},
                "seed": {"type": "object", "description": "实体初始状态 {entity_id: state}"},
                "events": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "事件序列：{'entity_id','state'} 推送状态并触发；{'advance_s':N} 推进虚拟时钟",
                },
            },
            "required": ["ir"],
        },
        _t_simulate,
        None,
    ),
    (
        "af_list_graphs",
        "列出已归档的自动化（最新版本、模式、自动化 id、标签）。无参数。",
        {"type": "object", "properties": {}},
        _t_list_graphs,
        None,
    ),
    (
        "af_get_graph",
        "获取某归档的指定版本图（默认最新）：含 ir/nl/diagnostics。参数：name(str 必填)，version(int 可选)。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "version": {"type": "integer", "description": "省略则取最新版本"},
            },
            "required": ["name"],
        },
        _t_get_graph,
        None,
    ),
    (
        "af_graphs_by_tag",
        "按标签筛选归档。参数：tag(str 必填)。",
        {"type": "object", "properties": {"tag": {"type": "string"}}, "required": ["tag"]},
        _t_by_tag,
        None,
    ),
    (
        "af_conf",
        "查看某归档（或 '_all'）的置信度分级（G4 MA）。参数：name(str 必填)。",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        _t_conf,
        None,
    ),
    (
        "af_set_tags",
        "【写】设置某归档的标签（覆盖式）。参数：name(str 必填)，tags(list[str] 必填)。需 write 权限。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "tags"],
        },
        _t_set_tags,
        "write",
    ),
    (
        "af_enable_by_tag",
        "【写】批量启停某标签下所有自动化（翻转 enabled 并保存新版本）。参数：tag(str 必填)，enabled(bool 必填)。需 write 权限。",
        {
            "type": "object",
            "properties": {"tag": {"type": "string"}, "enabled": {"type": "boolean"}},
            "required": ["tag", "enabled"],
        },
        _t_enable,
        "write",
    ),
    (
        "af_export_store",
        "导出整个 store 为可携带 bundle（含 tags + 校验和）。返回 bundle（可直接 import 回）。无参数。",
        {"type": "object", "properties": {}},
        _t_export,
        None,
    ),
    (
        "af_import_store",
        "【写】导入 bundle。参数：bundle(dict 必填)，strategy(str 可选 skip|overwrite|rename)。需 write 权限。"
        "返回 {imported,skipped,renamed,errors}。",
        {
            "type": "object",
            "properties": {
                "bundle": {"type": "object", "description": "af_export_store 返回的 bundle"},
                "strategy": {"type": "string", "enum": ["skip", "overwrite", "rename"]},
            },
            "required": ["bundle"],
        },
        _t_import,
        "write",
    ),
    (
        "af_save",
        "【写】把 IR 归档为新版本（**先过第一道闸**：未通过静态扫描则拒绝）。参数：name(str 必填)、"
        "ir(dict 必填)、note(str 可选，版本备注)、tags(list[str] 可选，覆盖式标签)、"
        "expect_version(int 可选，乐观锁，不匹配报冲突)。需 write 权限。返回 {ok,name,version,tags,warnings}。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "归档名（同名多版本自增）"},
                "ir": {"type": "object", "description": "JSON IR（单条自动化 dict 或 {'automations':[...]}）"},
                "note": {"type": "string", "description": "版本备注"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签（覆盖式）"},
                "expect_version": {"type": "integer", "description": "乐观锁：期望的当前最新版本"},
            },
            "required": ["name", "ir"],
        },
        _t_save,
        "write",
    ),
    (
        "af_diff",
        "版本 diff（G6）：对比同归档两版本，返回结构化差异与可读 render。参数：name(str)，old(int)，new(int) 必填。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "old": {"type": "integer"},
                "new": {"type": "integer"},
            },
            "required": ["name", "old", "new"],
        },
        _t_diff,
        None,
    ),
    (
        "af_live_run",
        "【live】真机下发（三重闸：服务端开关+令牌、confirm、白名单）。参数：ir(dict 必填)，"
        "live_allow(list[str] 必填：可写白名单)，events(list 可选)，confirm(bool 必填，必须显式 true)。需 live 权限。",
        {
            "type": "object",
            "properties": {
                "ir": {"type": "object"},
                "live_allow": {"type": "array", "items": {"type": "string"}, "description": "允许写入的实体白名单"},
                "events": {"type": "array", "items": {"type": "object"}},
                "confirm": {"type": "boolean", "description": "必须显式 true 才允许下发"},
            },
            "required": ["ir", "live_allow", "confirm"],
        },
        _t_live,
        "live",
    ),
    (
        "af_whoami",
        "自检当前 MCP 会话的鉴权身份（subject/scopes）。无参数。用于排查 scope 不足导致的写工具被拒。",
        {"type": "object", "properties": {}},
        _t_whoami,
        None,
    ),
    (
        "af_experience",
        "【v1.5.0 经验】实体共现经验摘要（**只在成功落盘后采集**，失败样本不污染）："
        "top 共现对（无向去序 `a|b`）、top 实体频次、IR 模式（kind/adapter/mode 计数）。"
        "参数：limit(int 可选，默认 10)。返回 {ok,observed,top_pairs,top_entities,patterns}。",
        {"type": "object", "properties": {"limit": {"type": "integer", "description": "各榜条数，默认 10"}}},
        _t_experience,
        None,
    ),
    (
        "af_telemetry",
        "【v1.5.0 经验】token/结果遥测（按字符/4 估算）+ 错误类别分布（四维：tool/category/ok/day）。"
        "参数：days(int 可选，默认 30)。返回 {ok,total,total_tokens,success_rate,by_tool,by_category,by_ok,by_day,knowledge}。",
        {"type": "object", "properties": {"days": {"type": "integer", "description": "统计窗口天数，默认 30"}}},
        _t_telemetry,
        None,
    ),
]


# ─────────────────────────────────────────────────────────────────────
# 分发（供测试直接调用，无需 stdio）
# ─────────────────────────────────────────────────────────────────────


def dispatch(
    name: str,
    args: dict[str, Any],
    store: GraphStore,
    current: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """执行一个工具，返回 (content, is_error)。is_error=True 时 content 为错误说明。"""
    tool = next((t for t in TOOLS if t[0] == name), None)
    if tool is None:
        return [_text(f"未知工具：{name!r}（可用：{', '.join(t[0] for t in TOOLS)}）")], True
    _name, _desc, _schema, fn, scope = tool
    try:
        _guard(scope, current)
        if fn in _TOOLS_WITH_CONTEXT:
            result = fn(store, args, current)
        else:
            result = fn(store, args)
        return [
            _text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        ], False
    except ServiceError as exc:
        return [_text(_annotate_failure(store, name, str(exc)))], True
    except Exception as exc:  # 业务异常（ServiceError/IRValidationError/KeyError 等）转 isError
        # R-57：完整 traceback 只落服务端日志，不回传 MCP 客户端（防文件路径/行号/堆栈外泄）
        logger.exception("MCP tool %s failed", name)
        msg = f"工具执行出错：{type(exc).__name__}: {exc}"
        return [_text(_annotate_failure(store, name, msg))], True


def _annotate_failure(store: GraphStore, tool: str, message: str) -> str:
    """v1.5.0：异常回执附**归因 + 建议**（并落知识库/遥测）；附属容错，绝不掩盖原错误。"""
    try:
        tel = record_failure(store.root, tool=tool, message=message)
    except Exception:  # 遥测失败不影响错误回执本身
        return message
    if not tel.get("category") or tel["category"] == "UNKNOWN":
        return message
    tail = f"\n↳ 归因：{tel['label']}｜建议：{tel['advice']}"
    if tel.get("history"):
        tail += f"｜历史同类 {len(tel['history'])} 条"
    return message + tail


# ─────────────────────────────────────────────────────────────────────
# stdio 协议循环（JSON-RPC 2.0）
# ─────────────────────────────────────────────────────────────────────


def _tool_def(tool: tuple) -> dict[str, Any]:
    name, desc, schema, _fn, _scope = tool
    return {"name": name, "description": desc, "inputSchema": schema}


def _respond(id_: Any, result: dict[str, Any]) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id_, "result": result}, ensure_ascii=False)


def _error(id_: Any, code: int, message: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}, ensure_ascii=False)


def serve_mcp(root: str = DEFAULT_STORE_ROOT) -> None:
    """MCP stdio 服务主循环。由 `forge mcp` 或 `python -m autoforge.af_mcp` 启动。"""
    store = GraphStore(root)
    # P0-12 修复：传 revoked_path，与 af_api/af_cli 一致
    registry = TokenRegistry(Path(root) / ".auth" / "revoked.json")
    current = _build_current(registry)
    if current:
        sys.stderr.write(f"[af_mcp] 鉴权启用：subject={current.get('subject')} scopes={current.get('scopes')}\n")
    else:
        sys.stderr.write("[af_mcp] 未启用鉴权（全放行，原型模式）\n")
    sys.stderr.flush()

    for raw in sys.stdin:
        # ADM B-07: reject oversized input lines to prevent OOM
        if len(raw) > MAX_STDIN_LINE:
            sys.stderr.write(f'[af_mcp] input line too large: {len(raw)} bytes > {MAX_STDIN_LINE}\n')
            sys.stderr.flush()
            continue
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        id_ = req.get("id")
        params = req.get("params") or {}

        if method == "initialize":
            out = _respond(
                id_,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )
        elif method == "tools/list":
            out = _respond(id_, {"tools": [_tool_def(t) for t in TOOLS]})
        elif method == "tools/call":
            # P0-12 修复：每次工具调用前重新加载 revoked 列表并重鉴权
            # （防止已吊销 token 在长运行 stdio 会话中继续有效）
            registry._load_revoked_file()
            current = _build_current(registry)
            content, is_error = dispatch(params.get("name", ""), params.get("arguments", {}) or {}, store, current)
            out = _respond(id_, {"content": content, "isError": is_error})
        elif method == "ping":
            out = _respond(id_, {})
        elif method is None or id_ is None:
            # 通知（如 notifications/initialized）→ 不回响应
            continue
        else:
            out = _error(id_, -32601, f"unknown method: {method}")
        sys.stdout.write(out + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    serve_mcp()
