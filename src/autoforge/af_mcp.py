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
from .af_auth import AuthCodeStore, PairCodeStore, TokenExpired, TokenRegistry
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

    - 未配任何令牌（registry.enabled=False）→ None，即"无身份"。`_guard` 对无身份的
      需鉴权工具**默认拒绝**，只有 `AUTOFORGE_MCP_ALLOW_NO_TOKEN=1` 才放行（裁定 20261004 §一 Q2=B）。
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


def allow_no_token() -> bool:
    """原型放行的**唯一**入口：显式 `AUTOFORGE_MCP_ALLOW_NO_TOKEN=1`。"""
    return (os.environ.get("AUTOFORGE_MCP_ALLOW_NO_TOKEN") or "").strip() == "1"


def _guard(scope: str | None, current: dict[str, Any] | None) -> None:
    """scope 门（裁定 20261004 §一 Q2=B：MCP 面默认拒绝）。

    - `scope is None` → 公开工具，不鉴权（只读/无副作用那一族照常可用）。
    - `current is None` → 没有任何令牌身份，**默认拒绝**；只有显式
      `AUTOFORGE_MCP_ALLOW_NO_TOKEN=1` 才退回原型全放行。此前这里是
      `current is None` 直接 return，等于"没配令牌 = 一切 scope 放行"，
      与 `/api/*` 的 fail-closed 是两面对不上。
    """
    if scope is None:
        return
    if current is None:
        if allow_no_token():
            return
        raise ServiceError(
            f"拒绝：MCP 面没有令牌身份 ⇒ 工具 '{scope}' 域默认拒绝（裁定 20261004 §一 Q2=B）。"
            "请配 `AUTOFORGE_TOKENS` 并用含该 scope 的 `AUTOFORGE_MCP_TOKEN` 启动；"
            "确需原型全放行请显式设 `AUTOFORGE_MCP_ALLOW_NO_TOKEN=1`。"
        )
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
    # 真机/Agent 面的 health 读数必须探自己服务的那个 store：不递就是 `store_ok: null`，
    # 等于告诉调用方"存储层我没看"（HTTP 面 af_api 早已递 store，两面对不上）。
    return svc.health(store)


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
    # v1.9.0 用户 WebUI：部署授权码路径——持高熵一次性消耗的有效码可走快速通道（人审替代路径）。
    # v2.5 安全加固（fp-authcode-bruteforce）：码须一次性 consume 才生效，无限重试验证已被堵死。
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
    auth_code = (args.get("auth_code") or "").strip()
    if auth_code:
        acs = _MCP_AUTH_STORE or AuthCodeStore(Path(store.root) / ".auth" / "auth_codes.json")
        if acs.validate(auth_code):
            # 路径 A：持有效授权码 → 先一次性 consume（防重放），直接部署
            acs.consume(auth_code)
            res = svc.submit_pending(
                store, "af_save", payload,
                submitted_by=owner or "mcp", authenticated_subject=owner or None,
            )
            op_id = res["pending"]
            applied = svc.approve_pending(store, op_id, reviewer=f"auth_code:{auth_code[:2]}" + "***")
            applied["deployed_via"] = "auth_code"
            return applied
        # 无效/已消耗/已锁定授权码：入待批队列（路径 B），并记录失败计数（10 次后锁定）
        acs.record_failure(auth_code)
        pending = svc.submit_pending(
            store, "af_save", payload,
            submitted_by=owner or "mcp", authenticated_subject=owner or None,
        )
        pending["warning"] = "auth_code 无效或已消耗，已转为待人工审批（连续 10 次错误该码将锁定 5 分钟）"
        return pending
    return svc.submit_pending(store, "af_save", payload, submitted_by=owner or "mcp", authenticated_subject=owner or None)


def _t_diff(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.diff(store, args["name"], int(args["old"]), int(args["new"]))


# ── v1.9.0 用户 WebUI：配对（MCP 连接认证）──
# 模块级共享（serve_mcp 启动时注入），供配对工具访问运行时令牌注册表与配对码存储。
_MCP_REGISTRY: TokenRegistry | None = None
_MCP_PAIR_STORE: PairCodeStore | None = None
_MCP_AUTH_STORE: AuthCodeStore | None = None


def _pair_store(store: GraphStore) -> PairCodeStore:
    return _MCP_PAIR_STORE or PairCodeStore(Path(store.root) / ".auth" / "pair_codes.json")


def _t_request_pair(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    """agent 发起配对：后端生成 6 位单次短时效配对码，经 SSE 推送给用户弹窗。

    码只回显给用户（不返回给 agent），agent 等待用户口述码后调 `af_pair` 兑换令牌。
    """
    hint = (args.get("agent_name_hint") or "").strip() or "未知 agent"
    pc = _pair_store(store).create(hint)
    return {
        "ok": True,
        "expires_at": pc.expires_at,
        "message": "配对请求已发起，请在 ForgeSight 中输入显示的 6 位码完成配对",
    }


def _t_pair(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    """agent 用配对码兑换运行时签发的 Bearer 令牌（subject=agent_name）。"""
    code = (args.get("code") or "").strip()
    if not code:
        return {"ok": False, "error": "缺少 code 参数"}
    pc = _pair_store(store).consume(code)
    if pc is None:
        return {"ok": False, "error": "配对码无效、已使用或已过期"}
    agent_name = (args.get("agent_name") or "").strip() or pc.agent_name_hint or "agent"
    if _MCP_REGISTRY is None:
        return {"ok": False, "error": "服务未就绪（令牌注册表不可用）"}
    token = _MCP_REGISTRY.issue_for_agent(agent_name)
    return {
        "ok": True,
        "token": token,
        "subject": agent_name,
        "message": "配对成功：请将 token 作为 Bearer 调用 AutoForge API",
    }


def _t_live(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    # `store` 必须递进去：Tier-0 设备保护（`device_acl.json`）与实体健康视图都从 store 根目录读。
    # 漏掉它，Agent 这条路径就能写入人类点同一个按钮会被拦下的设备——闸门只装了 HTTP 一面。
    return svc.live_run(
        args["ir"], args["live_allow"], args.get("events"), bool(args.get("confirm", False)),
        store=store,
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
    """意图 JSON → IR，返回 ref。

    session_id（可选）：同一用户 compose 请求内多次调用传相同 id，
    用于统计多意图会话占比（v2.3 决策门，见 af_draft.ComposeMetrics）。
    不传则不采样（默认零影响）。
    """
    from .af_draft import draft_intent, DraftError
    try:
        return draft_intent(args["intent"], session_id=args.get("session_id"))
    except DraftError as e:
        return {"ok": False, "error": {"code": e.code, "message": str(e), "fix": e.fix}}


def _t_apply(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    """一次走完 校验→仿真→入队。"""
    from .af_apply import apply
    ref = args["ref"]
    stage = args.get("stage", "save")
    return apply(ref, stage=stage, store=store)


def _t_test_submit(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    """批量提交测试自动化：draft -> build -> simulate -> 自动 approve -> 落盘测试区。"""
    from .af_test import get_test_channel, TestError
    intents = args.get("intents", [])
    batch_id = args.get("batch_id")
    try:
        channel = get_test_channel()
        return channel.submit_batch(intents, batch_id=batch_id)
    except TestError as e:
        return {"ok": False, "error": str(e)}


def _t_test_report(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    """获取测试报告。参数：batch_id(str 必填)。"""
    from .af_test import get_test_channel, TestError
    batch_id = args["batch_id"]
    try:
        channel = get_test_channel()
        return channel.get_report(batch_id)
    except TestError as e:
        return {"ok": False, "error": str(e)}


def _t_test_clear(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    """清空测试区。无参数。"""
    from .af_test import get_test_channel
    channel = get_test_channel()
    return channel.clear()


def _t_catalog(store: GraphStore, args: dict[str, Any]) -> dict[str, Any]:
    return svc.catalog_snapshot(store)


def _t_whoami(store: GraphStore, args: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "auth_enabled": current is not None,
        "subject": (current or {}).get("subject"),
        "scopes": (current or {}).get("scopes"),
        "note": (
            "无令牌身份 ⇒ 需鉴权工具默认拒绝"
            if current is None and not allow_no_token()
            else "无令牌身份，但 AUTOFORGE_MCP_ALLOW_NO_TOKEN=1 ⇒ 原型全放行"
            if current is None
            else "已按令牌 scope 限制写/live 工具"
        ),
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
        "【黄金路径第1步】传意图 JSON，自动生成 IR 并返回 ref。实体写中文名/别名，服务端自动解析。参数：intent(object 必填)、session_id(str 可选)：同一用户 compose 请求内多次调用传相同 id，用于 v2.3 决策门统计多意图会话占比。",
        {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "object",
                    "description": "意图 JSON，如 {name, mode, when, if, do}",
                },
                "session_id": {
                    "type": "string",
                    "description": "同一用户 compose 请求内多次调用传相同 id（v2.3 决策门多意图占比统计）",
                },
            },
            "required": ["intent"],
        },
        _t_draft,
        None,
    ),
    (
        "af_apply",
        "【黄金路径第2步】用 ref 一次走完 校验→仿真→入队。参数：ref(str 必填)、stage(str 可选: check/simulate/dry_run/save，默认 save)。dry_run=只校验+仿真、不消费首演码、不入队（DB 侧「拟→验→批→部署」的「验」）。",
        {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "af_draft 返回的 ref"},
                "stage": {"type": "string", "description": "check/simulate/dry_run/save，默认 save"},
            },
            "required": ["ref"],
        },
        _t_apply,
        "write",
    ),
    (
        "af_test_submit",
        "【测试通道】批量提交测试自动化：draft → build → simulate → 自动 approve → 落盘测试区（/data/test/，与正式环境完全隔离）。参数：intents(array 必填，意图JSON列表)、batch_id(str 可选)。返回测试报告摘要。",
        {
            "type": "object",
            "properties": {
                "intents": {"type": "array", "description": "意图 JSON 列表，每个元素如 {name, mode, when, do}"},
                "batch_id": {"type": "string", "description": "批次 ID（可选，默认自动生成）"}
            },
            "required": ["intents"],
        },
        _t_test_submit,
        "write",
    ),
    (
        "af_test_report",
        "【测试通道】获取测试报告。参数：batch_id(str 必填)。返回完整测试报告（通过率、失败原因、每题详情）。",
        {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string", "description": "批次 ID"}
            },
            "required": ["batch_id"],
        },
        _t_test_report,
        None,
    ),
    (
        "af_test_clear",
        "【测试通道】清空测试区（/data/test/）。无参数。测试完调用此工具清理，不影响正式环境。",
        {"type": "object", "properties": {}},
        _t_test_clear,
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
        "af_request_pair",
        "【配对·第1步】agent 发起配对请求：后端生成 6 位单次短时效配对码，经 SSE 推送到"
        "用户 ForgeSight 弹窗。码只显示给用户，agent 不拿码，等用户口述后调 af_pair 兑换。"
        "参数：agent_name_hint(str 可选，agent 自报名称)。返回 {ok,expires_at}。",
        {
            "type": "object",
            "properties": {
                "agent_name_hint": {"type": "string", "description": "agent 自报名称（显示在用户弹窗）"}
            },
        },
        _t_request_pair,
        "write",
    ),
    (
        "af_pair",
        "【配对·第2步】用用户口述的 6 位配对码兑换运行时签发的 Bearer 令牌。"
        "参数：code(str 必填)、agent_name(str 可选，默认用请求时的 hint)。"
        "返回 {ok,token,subject}；token 作为 Bearer 调用 AutoForge API。",
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "用户在 ForgeSight 弹窗显示的 6 位配对码"},
                "agent_name": {"type": "string", "description": "agent 名称（可选）"},
            },
            "required": ["code"],
        },
        _t_pair,
        "write",
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
        "把 AutoForge 文本语法（AF-Spec）编译成 IR（等价 JSON，同一份真相）。参数：text(str，"
        "别名 spec/prompt，三者其一必填)。"
        "返回 ok/ir/nl/diagnostics；失败返回 ok=false + error。",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "AF-Spec 文本"},
                # 后两条是 `text` 的历史别名（`_t_compile` 按 text→spec→prompt 取第一个非空）。
                # 未声明却可读＝"看起来不可用、实际可用"，安全审计 fp-authcode-bruteforce 加重情节 3
                # 点名的正是这一族：既然实现吃它，就在 schema 里写清它是别名，而不是让调用方猜。
                "spec": {"type": "string", "description": "text 的别名"},
                "prompt": {"type": "string", "description": "text 的别名"},
            },
            "anyOf": [{"required": ["text"]}, {"required": ["spec"]}, {"required": ["prompt"]}],
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
        "【写】批量启停某标签下所有自动化（翻转 enabled 并保存新版本）。参数：tag(str 必填)，enabled(bool 必填)，"
        "allow_bulk(bool 可选，确认批量、绕过爆炸半径护栏)。需 write 权限。",
        {
            "type": "object",
            "properties": {
                "tag": {"type": "string"},
                "enabled": {"type": "boolean"},
                "allow_bulk": {
                    "type": "boolean",
                    "description": "true=确认这是一次批量操作，绕过爆炸半径护栏（默认 false）",
                },
            },
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
        "【写】导入 bundle。参数：bundle(dict 必填)，strategy(str 可选 skip|overwrite|rename)，"
        "allow_bulk(bool 可选，确认批量、绕过爆炸半径护栏)。需 write 权限。"
        "返回 {imported,skipped,renamed,errors}。",
        {
            "type": "object",
            "properties": {
                "bundle": {"type": "object", "description": "af_export_store 返回的 bundle"},
                "strategy": {"type": "string", "enum": ["skip", "overwrite", "rename"]},
                "allow_bulk": {
                    "type": "boolean",
                    "description": "true=确认这是一次批量导入，绕过爆炸半径护栏（默认 false）",
                },
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
        "expect_version(int 可选，乐观锁，不匹配报冲突)、auth_code(str 可选，部署授权码——8 位纯数字，"
        "一次性消耗，连续 10 次错误该码锁定 5 分钟)、allow_bulk(bool 可选，确认批量、绕过爆炸半径护栏)。"
        "需 write 权限。返回 {ok,name,version,tags,warnings}。",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "归档名（同名多版本自增）"},
                "ir": {"type": "object", "description": "JSON IR（单条自动化 dict 或 {'automations':[...]}）"},
                "note": {"type": "string", "description": "版本备注"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签（覆盖式）"},
                "expect_version": {"type": "integer", "description": "乐观锁：期望的当前最新版本"},
                "auth_code": {"type": "string", "pattern": "^[0-9]{8}$",
                              "description": "部署授权码（用户在 WebUI 生成）；8 位纯数字；一次性消耗，"
                                             "连续 10 次错误该码锁定 5 分钟"},
                "allow_bulk": {
                    "type": "boolean",
                    "description": "true=确认这是一次批量归档，绕过爆炸半径护栏（默认 false）",
                },
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


def _undeclared_args(schema: dict[str, Any], args: Any) -> list[str]:
    """返回 `args` 里**没有**出现在 `schema.properties` 的顶层键（已排序）。"""
    props = schema.get("properties")
    if not isinstance(props, dict) or not isinstance(args, dict):
        return []
    return sorted(str(k) for k in args if k not in props)


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
        unexpected = _undeclared_args(_schema, args)
        if unexpected:
            # 安全审计 fp-authcode-bruteforce 的**加重情节 3**（"schema 未声明却可用"）在 HEAD 上
            # 原样成立：`dispatch()` 只查工具名 + scope，arguments 从不与 inputSchema 对账，
            # 于是 `allow_bulk`（爆炸半径护栏绕过位）这类键在 `tools/list` 里看不见却能被调用。
            # 现在声明即契约：未声明的顶层键一律拒——`tools/list` 展示的参数集 = 实际接受参数集。
            declared = ", ".join(sorted((_schema.get("properties") or {}).keys())) or "（该工具无参数）"
            return [
                _text(
                    f"参数未声明，已拒绝：{', '.join(unexpected)}｜"
                    f"{_name} 声明的参数：{declared}"
                )
            ], True
        if fn in _TOOLS_WITH_CONTEXT:
            result = fn(store, args, current)
        else:
            result = fn(store, args)
        return [
            _text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        ], False
    except ServiceError as exc:
        return [_text(_annotate_failure(store, name, str(exc)))], True
    except svc.ServiceError as exc:
        # 服务层的拒绝**原样**回传：`_single_writer_check` 那条靠固定前缀
        # `READONLY_DEGRADED:` 让 DB 判别（裁定 20261004 §一 1 A），套上"工具执行出错："
        # 那层壳，前缀就不在文本开头了。
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
    registry = TokenRegistry(
        Path(root) / ".auth" / "revoked.json",
        issued_path=Path(root) / ".auth" / "issued_tokens.json",
    )
    current = _build_current(registry)
    # v1.9.0 用户 WebUI：配对工具需要运行时令牌注册表 + 配对码存储
    globals()["_MCP_REGISTRY"] = registry
    globals()["_MCP_PAIR_STORE"] = PairCodeStore(Path(root) / ".auth" / "pair_codes.json")
    globals()["_MCP_AUTH_STORE"] = AuthCodeStore(Path(root) / ".auth" / "auth_codes.json")
    if current:
        sys.stderr.write(f"[af_mcp] 鉴权启用：subject={current.get('subject')} scopes={current.get('scopes')}\n")
    elif allow_no_token():
        sys.stderr.write("[af_mcp] 未配令牌，且 AUTOFORGE_MCP_ALLOW_NO_TOKEN=1 ⇒ 需鉴权工具全放行（原型档，显式选择）\n")
    else:
        sys.stderr.write(
            "[af_mcp] 未配令牌 ⇒ 需鉴权工具（write/live 域）默认拒绝；"
            "公开工具照常。要原型全放行请显式设 AUTOFORGE_MCP_ALLOW_NO_TOKEN=1\n"
        )
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
