"""MA 取数适配器 —— AF 作为 **MCP 客户端**向 memory-agent 取三路径读数（计划 §七 卡2，链主定档甲）。

计划卡片与 DCD 那张派工单把这一格叫 **`ma_query`**；AF 侧它不是一种节点 kind，而是本适配器
（`do` + `adapter: ma`）。grep `ma_query` 落到本文件就是这一层对应关系，⛔ 不是另立一族。

落形是 `do` + `adapter: ma` + `action: route_question | list_analysis_templates | export_insight |
run_analysis_template`，不是新增一枚节点 kind：AF 的 kind 词表是封闭 8 枚（`af_ir/models.py`
`NODE_KINDS`），未知 kind 在编译期就被 `ir.schema.json` 拒；卡1 的 `inbox_speak` 已经栽过同一格。
设计面（入参/出参绑定规则/失败出口）在 `docs/ADM联动执行计划-AF.md` §7.5，本文件是那一节的落码。

**返回键按 MA 现读，⛔ 不按派工单字面硬套**（定档甲＝AF 按既有形状读、MA 零代码）：
`export_insight` 的三块真身套了一层 ⇒ AF 读 `parsed["insight"]` 之下的 `insight`/`queries`/`implementation`；
`run_analysis_template` 返 `template`/`window`/`entities`/`summary_text`，与"三块"不同形。

**静默空值那一族由本文件挡**（链主 R3 定、DCD 追认的判据）：按顶层读三块**不会崩、只会读空**——
喂 `{"ok": True}`（缺 `insight`）与 `{"ok": True, "insight": {}}`（三块全空）两种形状都抛
`AdapterError`＋`ADM_ERR_PAYLOAD_INVALID`，⛔ 不许返回空 dict 让 `result_var` 静默绑成空值。
上游平铺不是本文件要防的那格（MA 侧契约腿 `tests/contract/test_ma_db_contract.py` 已反向锁住），
这里防的是 **AF 自己读空之后冒充成功**。

`dry_run=True` 是缺省档：只在进程内记一条意图，一个字节都不出网，⛔ 不伪造载荷值——
仿真面拿到的是"这次会调什么、带什么参数"，不是编造出来的读数。
"""

from __future__ import annotations

import json
import os
import socket
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from homesdk.adm.errors import (
    ADM_ERR_AUTH_REQUIRED,
    ADM_ERR_INTERNAL,
    ADM_ERR_PAYLOAD_INVALID,
    ADM_ERR_PEER_OFFLINE,
    ADM_ERR_UPSTREAM_TIMEOUT,
)

from .base import AdapterError, CallResult
from .http import guarded_open, host_of

__all__ = [
    "DEFAULT_MA_TRANSPORT_TIMEOUT",
    "MA_ACTIONS",
    "MAX_RESPONSE_BYTES",
    "MaAdapter",
]

#: 传输层套接超时（秒），与 `http.py` 同一口径：**传输层**超时留在适配器内部（不保留会单进程挂死），
#: 业务超时不归这里——IR 里用 `wait` + `on_timeout` 表达，`params` 里写 `timeout` 会被
#: `POLICY_PARAMS` 静态扫描判 `ADAPTER_POLICY_PARAM`。
DEFAULT_MA_TRANSPORT_TIMEOUT = 10.0

#: 单次响应正文上限（字节）。常驻服务里"对端返多大我吞多大"就是无界读，这里封顶并如实报错。
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

#: dry_run 意图环条数上限（与 `http.py`/`inbox.py` 同一纪律：只写不读的日志必须自己封顶）。
INTENTS_MAX = 200

MCP_PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "autoforge"

#: 端点与凭据的读取名。⛔ 不走 `credentials.json` 三段链——那条链的射程只有 HA/API 两枚键
#: （`af_secrets.py` 头部按安全审计 F-11 订正过），其余名字一律 secret 文件 > 环境变量。
MA_ENDPOINT_ENV = "AUTOFORGE_MA_MCP_URL"
MA_TOKEN_SECRET = "AUTOFORGE_MA_MCP_TOKEN"

#: 四条只读取数动作 ⇒ 允许出现在 IR `params` 里的键名（真源＝MA 侧 `tool_schema.TOOL_SPECS`
#: 与 `mcp_server.py` 的四个 `@mcp.tool`）。名单外的键**不透传**：那是"参数未声明却可用"那一族。
#: `entity_id` 是 AF 侧专用键，不转给 MA——它只用来把 `entities[]` 里那一枚实体的标量挑出来
#: （DSL 的 `resolve_var` 只对 Mapping 下钻，列表下标会读成 MISSING，所以选择在适配器内做一次）。
MA_ACTIONS: dict[str, tuple[str, ...]] = {
    "route_question": ("question", "days"),
    "list_analysis_templates": ("category",),
    "export_insight": ("template_id",),
    "run_analysis_template": ("template_id", "days", "start", "end", "include_timeline"),
}
AF_ONLY_PARAMS = frozenset({"entity_id"})
_REQUIRED_PARAMS = {"route_question": ("question",), "export_insight": ("template_id",),
                    "run_analysis_template": ("template_id",)}

#: 三条动作"成功载荷该长什么样"的键名单，逐枚现读 MA 入库面（`mcp_server.py` 的四个 `@mcp.tool`
#: 与 `templates.py:693-705`）。`route_question` 是 spec 驱动（全仓无 `def route_question`），
#: 计划体键名由 MA 的 `plan.to_dict()` 决定 ⇒ **AF 不手抄它的键名单**，只判"除 `ok`/`error` 之外至少有一格"。
_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "list_analysis_templates": ("templates",),
    "export_insight": ("insight", "queries", "implementation"),
    "run_analysis_template": ("template", "window", "entities", "summary_text"),
    "route_question": (),
}
#: "键都在但值全空"也算形状塌的那两条。⛔ 不把真实空答案误判成形状塌：`templates: []` 是
#: "确实没有模板"这个真值，而三块全空／四键全空是"读空之后冒充成功"。
_ALL_EMPTY_REFUSED = frozenset({"export_insight", "run_analysis_template"})
#: 三块在 `parsed["insight"]` 之下（MCP 包装 `{"ok": True, "insight": data}` 套的那一层）。
_NESTED_BLOCK = {"export_insight": "insight"}


class _UpstreamFailure(Exception):
    """取数没成功（通道/鉴权/对端层面），携带 `adm.errors` 的具名码。

    私有异常：它在本模块内就被映射成 `CallResult.fail`，⛔ 不冒到执行器——
    "MA 没答上"是可兜底的业务结果，走 IR 的 `on_error`；只有**读空冒充成功**才抛 `AdapterError`。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def action_of(action: str) -> str:
    """`ma.route_question` 与 `route_question` 两种写法都归一到后者（与 `inbox.kind_of` 同形）。"""
    return str(action or "").strip().lower().rsplit(".", 1)[-1]


def select_entity(parsed: Mapping[str, Any], entity_id: str) -> dict[str, Any] | None:
    """从 `entities[]` 里按 `entity_id` 选一枚实体，只取它的**标量**字段。

    DSL 取不到列表下标（`af_expect.py` 的 `resolve_var` 逐段只对 `Mapping` 生效），所以"把某个设备
    的数值挑出来"这一手必须在适配器内做**一次**，选中的实体以 `entity` 这个 Mapping 回到 `data` 顶层，
    绑法就还是 `vars.<result_var>.data.entity.<字段>`。⛔ 不是给 DSL 加下标语法（那是表达式层扩容，另走 DCD）。
    """
    wanted = str(entity_id or "").strip()
    if not wanted:
        return None
    for row in parsed.get("entities") or []:
        if not isinstance(row, Mapping):
            continue
        for key in ("entity_id", "logical_id", "id", "name"):
            if str(row.get(key) or "").strip() == wanted:
                return {k: v for k, v in row.items() if not isinstance(v, (list, tuple, dict, Mapping))}
    return None


def _decode_envelope(raw: bytes, content_type: str, msg_id: Any) -> dict[str, Any]:
    """把一次 POST 的响应体解成 JSON-RPC 信封；解不出一律 `_UpstreamFailure`（不静默当空答复）。

    MA 的 streamable-http 端点按 `Accept` 可能回 `application/json`，也可能回 SSE 分帧
    （`data:` 行）。两种都在这一处收口，⛔ 不在调用点各写一份解码。
    """
    text = raw.decode("utf-8", errors="replace")
    candidates: list[Any] = []
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    candidates.append(json.loads(chunk))
                except (ValueError, TypeError):
                    continue
    else:
        try:
            candidates.append(json.loads(text))
        except (ValueError, TypeError) as exc:
            raise _UpstreamFailure(
                ADM_ERR_PAYLOAD_INVALID, f"MA 响应不是合法 JSON：{type(exc).__name__}"
            ) from exc
    for item in candidates:
        if isinstance(item, dict) and (msg_id is None or item.get("id") == msg_id):
            return item
    if candidates and isinstance(candidates[-1], dict):
        return candidates[-1]
    raise _UpstreamFailure(ADM_ERR_PAYLOAD_INVALID, "MA 响应里没有 JSON-RPC 信封")


class MaAdapter:
    """单次调用、无重试、无降级、无语义层超时（协议见 `base.Adapter`）。

    会话握手（`initialize`）每个适配器实例只做一次；会话过期**不在这里重试**——纯执行层契约
    不允许适配器兜重试，重取数由 IR 用 `wait` + `on_error` 显式表达。
    """

    name = "ma"

    def __init__(
        self,
        endpoint: str = "",
        token: str = "",
        *,
        dry_run: bool = True,
        transport_timeout: float = DEFAULT_MA_TRANSPORT_TIMEOUT,
        allowed_hosts: tuple[str, ...] = (),
    ) -> None:
        self._endpoint = str(endpoint or "")
        self._token = str(token or "")
        self.dry_run = dry_run
        self.transport_timeout = float(transport_timeout)
        self._allowed_hosts = tuple(h for h in allowed_hosts if h)
        self.intents: list[dict[str, Any]] = []  # bounded-cache: exempt(dry_run 意图环形清单：唯一写入口 `call()` 按 INTENTS_MAX 裁头，读侧是仿真效果展开与测试；与 af_adapters/inbox.py 的 intents 同形，没有 TTL 腿——dry_run 记的就是"本会发什么"，不该自行过期)
        self._session_id: str | None = None
        self._rpc_id = 0

    # ── 调用面 ─────────────────────────────────────────────────────
    def call(self, action: str, params: Mapping[str, Any]) -> CallResult:
        name = action_of(action)
        allowed = MA_ACTIONS.get(name)
        if allowed is None:
            # 名单外的动作名是 AF 侧的写法错，不是 MA 的回答 ⇒ 抛出去（`AdapterError`），
            # 让它在编译/仿真面响亮，而不是静默返回一条"没数据"。
            raise AdapterError(
                f"{ADM_ERR_PAYLOAD_INVALID}: ma 适配器不认的动作 {action!r}"
                f"（已定义：{sorted(MA_ACTIONS)}）"
            )
        fields = dict(params or {})
        unknown = sorted(k for k in fields if k not in allowed and k not in AF_ONLY_PARAMS)
        if unknown:
            return CallResult.fail(
                f"ma.{name} 不接受这些参数：{unknown}（允许：{sorted(allowed)}）",
                code=ADM_ERR_PAYLOAD_INVALID,
                action=name,
            )
        missing = [k for k in _REQUIRED_PARAMS.get(name, ()) if not str(fields.get(k) or "").strip()]
        if missing:
            return CallResult.fail(
                f"ma.{name} 缺少必填参数：{missing}",
                code=ADM_ERR_PAYLOAD_INVALID,
                action=name,
            )
        arguments = {k: fields[k] for k in allowed if k in fields}

        if self.dry_run:
            intent = {"action": name, "arguments": dict(arguments)}
            if len(self.intents) >= INTENTS_MAX:
                del self.intents[0]
            self.intents.append(intent)
            return CallResult.ok({"dry_run": True, **intent})

        endpoint = self._resolve_endpoint()
        host = host_of(endpoint)
        if not host:
            return CallResult.fail(
                f"MA 端点未配置或非法（{MA_ENDPOINT_ENV}）：{endpoint!r}",
                code=ADM_ERR_AUTH_REQUIRED,
                action=name,
            )
        token = self._resolve_token()
        if not token:
            return CallResult.fail(
                f"未配置 MA 的 MCP 凭据（{MA_TOKEN_SECRET}），拒发",
                code=ADM_ERR_AUTH_REQUIRED,
                action=name,
            )

        try:
            parsed = self._tools_call(endpoint, host, token, name, arguments)
        except _UpstreamFailure as exc:
            return CallResult.fail(exc.message, code=exc.code, action=name)

        self._guard_shape(name, parsed)
        if parsed.get("ok") is False:
            # 对端明确答"没成"＝可兜底的业务结果（`on_error` 边接），⛔ 不是形状塌、也不冒充成功。
            # 目录里没有"上游给了个否答复"这一枚码（⛔ 不新造码），取最贴处置方向的那枚。
            return CallResult.fail(
                f"ma.{name} 被 MA 判为失败：{str(parsed.get('error') or '（MA 未给原因）')[:200]}",
                code=ADM_ERR_INTERNAL,
                action=name,
            )
        data: dict[str, Any] = {"action": name, "payload": parsed}
        for key in _REQUIRED_KEYS[name]:
            if key in parsed:
                data[key] = parsed[key]
        if name == "export_insight" and isinstance(parsed.get("insight"), Mapping):
            data["insight"] = parsed["insight"]
        entity = select_entity(parsed, str(fields.get("entity_id") or ""))
        if fields.get("entity_id"):
            if entity is None:
                return CallResult.fail(
                    f"ma.{name} 的 entities[] 里没有 entity_id={fields['entity_id']!r}",
                    code=ADM_ERR_PAYLOAD_INVALID,
                    action=name,
                )
            data["entity"] = entity
        return CallResult.ok(data)

    # ── 静默空值判据（链主 R3 / DCD 追认）──────────────────────────
    @staticmethod
    def _guard_shape(name: str, parsed: Mapping[str, Any]) -> None:
        """形状塌一律抛 `AdapterError`：不许让 `result_var` 静默绑成空值再冒充成功。"""
        if parsed.get("ok") is False:
            # 对端明确答"没成"——那是业务结果，由 `on_error` 兜，不在本判据射程。
            return
        block: Mapping[str, Any] = parsed
        nested = _NESTED_BLOCK.get(name)
        if nested is not None:
            inner = parsed.get(nested)
            if not isinstance(inner, Mapping):
                raise AdapterError(
                    f"{ADM_ERR_PAYLOAD_INVALID}: ma.{name} 的载荷缺 {nested!r} 或其不是对象"
                )
            block = inner
        required = _REQUIRED_KEYS[name]
        if not required:
            if not any(k not in ("ok", "error") for k in block):
                raise AdapterError(
                    f"{ADM_ERR_PAYLOAD_INVALID}: ma.{name} 除了 ok/error 之外读不到任何内容"
                )
            return
        # 判据取"至少一键在、且不是全空"，⛔ 不逐键硬要求：MA 的 export 面除三块外还带 `metadata`，
        # 键集会演进；逐键硬要会把"对端多给/少给一键"判成塌，而链主 R3 要挡的是**整块读空**。
        present = [k for k in required if k in block]
        if not present:
            raise AdapterError(
                f"{ADM_ERR_PAYLOAD_INVALID}: ma.{name} 的载荷里没有 {list(required)} 中的任何一键"
            )
        if name in _ALL_EMPTY_REFUSED and not any(block.get(k) for k in present):
            raise AdapterError(
                f"{ADM_ERR_PAYLOAD_INVALID}: ma.{name} 的载荷键齐而值全空（读空≠成功）"
            )

    # ── 传输 ────────────────────────────────────────────────────────
    def _resolve_endpoint(self) -> str:
        endpoint = self._endpoint or os.environ.get(MA_ENDPOINT_ENV, "")
        if not endpoint:
            return ""
        base = str(endpoint).rstrip("/")
        # MA 把 MCP 挂在 /mcp（`memory_agent/app.py::_mcp_dispatcher`）：只给 host:port 时补上那一段。
        parsed = urlparse(base)
        if parsed.scheme and parsed.hostname and parsed.path in ("", "/"):
            base = f"{base}/mcp"
        return base

    def _resolve_token(self) -> str:
        if self._token:
            return self._token
        from ..af_secrets import load_secret

        return str(load_secret(MA_TOKEN_SECRET, ""))

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _post(self, endpoint: str, host: str, token: str, body: Mapping[str, Any]) -> dict[str, Any]:
        req = Request(
            endpoint,
            data=json.dumps(dict(body)).encode("utf-8"),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        req.add_header("Authorization", f"Bearer {token}")
        if self._session_id:
            req.add_header("Mcp-Session-Id", self._session_id)
        hosts = self._allowed_hosts or (host,)
        try:
            with guarded_open(req, allowed_hosts=hosts, timeout=self.transport_timeout) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
                content_type = (resp.headers.get("content-type") or "").lower()
                sid = resp.headers.get("mcp-session-id") or self._session_id
        except HTTPError as exc:
            code = getattr(exc, "code", 0)
            if code in (401, 403):
                raise _UpstreamFailure(
                    ADM_ERR_AUTH_REQUIRED, f"MA 拒绝凭据（HTTP {code}）"
                ) from exc
            if code == 408 or code == 504:
                raise _UpstreamFailure(
                    ADM_ERR_UPSTREAM_TIMEOUT, f"MA 超时（HTTP {code}）"
                ) from exc
            if code >= 500:
                raise _UpstreamFailure(
                    ADM_ERR_PEER_OFFLINE, f"MA 侧故障（HTTP {code}）"
                ) from exc
            raise _UpstreamFailure(ADM_ERR_INTERNAL, f"MA 返回 HTTP {code}") from exc
        except socket.timeout as exc:
            raise _UpstreamFailure(
                ADM_ERR_UPSTREAM_TIMEOUT, "MA 未在传输层超时内应答"
            ) from exc
        except (URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise _UpstreamFailure(
                    ADM_ERR_UPSTREAM_TIMEOUT, "MA 未在传输层超时内应答"
                ) from exc
            raise _UpstreamFailure(
                ADM_ERR_PEER_OFFLINE, f"MA 不可达：{type(exc).__name__}: {exc}"
            ) from exc
        self._session_id = sid
        if len(raw) > MAX_RESPONSE_BYTES:
            raise _UpstreamFailure(
                ADM_ERR_PAYLOAD_INVALID,
                f"MA 响应超过上限 {MAX_RESPONSE_BYTES} 字节，拒收（不无界吞）",
            )
        return _decode_envelope(raw, content_type, body.get("id"))

    def _ensure_session(self, endpoint: str, host: str, token: str) -> None:
        if self._session_id is not None:
            return
        msg_id = self._next_id()
        envelope = self._post(
            endpoint,
            host,
            token,
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": CLIENT_NAME, "version": "1"},
                },
            },
        )
        if "error" in envelope:
            err = envelope["error"]
            raise _UpstreamFailure(
                ADM_ERR_INTERNAL,
                f"MA 拒绝握手：{str(err.get('message') if isinstance(err, Mapping) else err)[:200]}",
            )
        # 握手成功即视为会话可用；`notifications/initialized` 失败只说明对端不收这条通知，
        # 不影响随后的 tools/call，因此它**不参与**本次读数的成败判定，也不在这里重试。
        self._post(
            endpoint,
            host,
            token,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

    def _tools_call(
        self, endpoint: str, host: str, token: str, name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        self._ensure_session(endpoint, host, token)
        msg_id = self._next_id()
        envelope = self._post(
            endpoint,
            host,
            token,
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": dict(arguments)},
            },
        )
        if "error" in envelope:
            err = envelope["error"]
            raise _UpstreamFailure(
                ADM_ERR_INTERNAL,
                f"MA 工具调用出错：{str(err.get('message') if isinstance(err, Mapping) else err)[:200]}",
            )
        result = envelope.get("result")
        if not isinstance(result, Mapping):
            raise _UpstreamFailure(ADM_ERR_PAYLOAD_INVALID, "MA 响应里没有 result 对象")
        return _tool_payload(name, result)


def _tool_payload(name: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """把 MCP `tools/call` 的 result 取成 MA 的业务 dict（结构化内容优先，回落到 text 腿）。"""
    if result.get("isError") is True:
        text = next(
            (
                str(item.get("text") or "")
                for item in (result.get("content") or [])
                if isinstance(item, Mapping)
            ),
            "",
        )
        raise _UpstreamFailure(
            ADM_ERR_INTERNAL, f"ma.{name} 被 MA 判为出错：{text[:200]}"
        )
    structured = result.get("structuredContent")
    if isinstance(structured, Mapping):
        return dict(structured)
    for item in result.get("content") or []:
        if not isinstance(item, Mapping) or item.get("type") not in (None, "text"):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise _UpstreamFailure(
                ADM_ERR_PAYLOAD_INVALID, f"ma.{name} 的 text 载荷不是合法 JSON：{type(exc).__name__}"
            ) from exc
        if isinstance(parsed, Mapping):
            return dict(parsed)
        raise _UpstreamFailure(ADM_ERR_PAYLOAD_INVALID, f"ma.{name} 的 text 载荷不是对象")
    raise _UpstreamFailure(ADM_ERR_PAYLOAD_INVALID, f"ma.{name} 的 result 里没有可读载荷")
