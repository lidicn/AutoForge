"""Home Assistant 适配器（生产用）。

**G1 默认 `dry_run=True`**——只记录下发意图，不写真机。
真实下发需要同时满足：
    1. 构造时显式 `dry_run=False`
    2. 注入 `transport`（真正调用 HA REST/WebSocket 的可调用对象）

仿真场景不用这个适配器：`af_vhass.bridge.HassAdapter` 直接绑定到 `hass` 实例，
与它实现同一个 `Adapter` 协议。

`HATransport` 是**真机接线**（`forge run --live`）用的 REST 客户端：
- 下发：`POST {base_url}/api/services/{domain}/{service}`（Bearer 鉴权）
- 读状态：`GET {base_url}/api/states` + `GET /api/states/{entity_id}`

边界（KICKOFF §4.6）：传输层（连接/鉴权/套接超时）归适配器，语义层（重试/退避/降级）归 IR。
本模块**只做单次下发**，不含 retry/fallback。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable, Mapping

from ..af_state import Snapshot, StateProvider
from ..af_config import Config, get_config
from .base import AdapterError, CallResult, FaultQueue

__all__ = ["HAAdapter", "HATransport", "HAStateProvider"]

#: transport(action, params) -> CallResult
Transport = Callable[[str, Mapping[str, Any]], CallResult]

# R-39：不再硬编码内网 HA 地址；从环境变量 AUTOFORGE_HA_URL 读。
# 未配置时为空，构造 HATransport/HAStateProvider 会明确报错。
DEFAULT_HA_URL = (os.getenv("AUTOFORGE_HA_URL") or "").strip()

#: domain/service 合法字符（P0-11：防止路径注入）
_HA_DOMAIN_RE = re.compile(r"^[a-z0-9_]+$")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁用重定向（P0-11：防止 HA 令牌随 3xx 外泄到外部主机）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # 返回 None = 不跟随重定向


_no_redirect_opener = urllib.request.build_opener(_NoRedirectHandler)


class HATransport:
    """HA REST 客户端（真实下发）。实现 `Transport` 协议（可调用）。

    - `base_url`：HA 地址（缺省读环境变量 `AUTOFORGE_HA_URL`）
    - `token`：长期访问令牌（Bearer）
    - `timeout`：**传输层**套接超时（秒），保留它是为了单进程不永久挂死
    """

    def __init__(
        self,
        base_url: str = DEFAULT_HA_URL,
        token: str = "",
        timeout: float = 10.0,
        opener: Callable[..., Any] | None = None,
        cfg: "Config | None" = None,
    ):
        self.base_url = (base_url or DEFAULT_HA_URL).rstrip("/")
        if not self.base_url:
            raise RuntimeError(
                "HA_URL 未配置：请设置环境变量 AUTOFORGE_HA_URL 或显式传 base_url"
            )
        self._cfg = cfg
        self._revision = cfg.connection_revision if cfg is not None else -1
        # 凭据热重载（§2.9）：有 cfg 时 token 以 cfg 为准，call/get_state 比对代数刷新
        self.token = cfg.get_ha_token() if cfg is not None else token
        self.timeout = float(timeout)
        self._opener = opener or _no_redirect_opener.open

    def _refresh_if_stale(self) -> None:
        """凭据热重载（§2.9）：connection_revision 变化即丢弃旧 token，从 cfg 取最新。"""
        if self._cfg is not None and self._revision != self._cfg.connection_revision:
            self.token = self._cfg.get_ha_token()
            self._revision = self._cfg.connection_revision

    # ── Transport 协议 ────────────────────────────────────────────────
    def __call__(self, action: str, params: Mapping[str, Any]) -> CallResult:
        return self.call(action, params)

    def call(self, action: str, params: Mapping[str, Any]) -> CallResult:
        """单次下发：`domain.service` → `POST /api/services/{domain}/{service}`。"""
        self._refresh_if_stale()
        domain, _, service = action.partition(".")
        if not domain or not service:
            return CallResult.fail(f"非法动作（应为 domain.service）：{action!r}", action=action)
        # P0-11：domain/service 字符校验，防止路径注入
        if not _HA_DOMAIN_RE.match(domain) or not _HA_DOMAIN_RE.match(service):
            return CallResult.fail(f"非法 domain/service（含非法字符）：{action!r}", action=action)
        url = f"{self.base_url}/api/services/{domain}/{service}"
        payload = json.dumps(dict(params or {})).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                status = int(getattr(resp, "status", 200) or 200)
        except urllib.error.HTTPError as exc:  # 4xx/5xx（含 401 鉴权失败）
            detail = ""
            try:
                detail = exc.read().decode("utf-8")[:200]
            except Exception:  # pragma: no cover - 读取错误响应体失败
                detail = str(exc)
            return CallResult.fail(f"HA 返回 {exc.code}：{detail}", action=action, params=dict(params))
        except Exception as exc:  # 连接/套接超时等传输层异常
            return CallResult.fail(f"HA 下发失败（{action}）：{exc}", action=action, params=dict(params))
        if status >= 400:
            return CallResult.fail(f"HA 返回 {status}：{raw[:200]}", action=action, params=dict(params))
        try:
            data: Any = json.loads(raw) if raw else []
        except ValueError:
            data = {"raw": raw}
        return CallResult.ok({"action": action, "result": data})

    def get_state(self, entity_id: str) -> str | None:
        """读取单个实体状态；失败返回 None（不抛）。"""
        self._refresh_if_stale()
        url = f"{self.base_url}/api/states/{entity_id}"
        req = urllib.request.Request(
            url, method="GET", headers={"Authorization": f"Bearer {self.token}"}
        )
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        state = payload.get("state") if isinstance(payload, Mapping) else None
        return str(state) if state is not None else None

    def all_states(self) -> dict[str, tuple[str, dict[str, Any]]]:
        """拉取全量状态：`{entity_id: (state, attributes)}`；失败返回空 dict。"""
        self._refresh_if_stale()
        url = f"{self.base_url}/api/states"
        req = urllib.request.Request(
            url, method="GET", headers={"Authorization": f"Bearer {self.token}"}
        )
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return {}
        out: dict[str, tuple[str, dict[str, Any]]] = {}
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, Mapping) and "entity_id" in item:
                    out[str(item["entity_id"])] = (
                        str(item.get("state", "")),
                        dict(item.get("attributes") or {}),
                    )
        return out


class HAStateProvider:
    """HA 状态源（REST，生产用）。

    每次 `snapshot()` 拉取所需实体的**当前**状态，返回只读快照。
    拉取失败（网络/鉴权）时**不静默吞错**：缺失实体不进快照，
    后续求值会按 IR §14-9 走 `on_error` 软失效 / 漂移告警。
    """

    def __init__(
        self,
        transport: HATransport | None = None,
        *,
        base_url: str = DEFAULT_HA_URL,
        token: str = "",
        timeout: float = 10.0,
        cfg: "Config | None" = None,
    ):
        self._cfg = cfg
        self.transport = transport or HATransport(
            base_url=base_url, token=token, timeout=timeout, cfg=cfg
        )

    def snapshot(self, entity_ids: Iterable[str]) -> Snapshot:
        wanted = list(entity_ids)
        all_states = self.transport.all_states()
        values: dict[str, str] = {}
        attributes: dict[str, dict[str, Any]] = {}
        for entity_id in wanted:
            if entity_id in all_states:
                state, attrs = all_states[entity_id]
                values[entity_id] = state
                attributes[entity_id] = attrs
        return Snapshot(values=values, attributes=attributes)


class HAAdapter:
    name = "ha"

    def __init__(
        self,
        transport: Transport | None = None,
        dry_run: bool = True,
        on_dry_run: Callable[[str, Mapping[str, Any]], None] | None = None,
        undo_recorder: Callable[[str, Mapping[str, Any], dict[str, dict[str, Any]]], None] | None = None,
    ):
        self.transport = transport
        self.dry_run = dry_run
        self.on_dry_run = on_dry_run
        # F7：真实下发前的"动作前快照"捕获钩子（由 CLI/API 注入 UndoStore 落盘）。
        # 仅真实下发（dry_run=False）且 transport 支持 all_states 时生效。
        self.undo_recorder = undo_recorder
        self.intents: list[tuple[str, dict[str, Any]]] = []
        self.faults = FaultQueue()

    # ── 故障注入（G5，IR §9.5）：故障优先于 dry_run / transport ─────────
    def fail_next(self, error: str = "ha 注入的失败") -> None:
        self.faults.push("fail", error)

    def timeout_next(self, error: str = "ha 注入的传输层超时") -> None:
        self.faults.push("timeout", error)

    def drop_next(self, error: str = "ha 注入的消息丢包") -> None:
        self.faults.push("drop", error)

    def unavailable_next(self, error: str = "ha 注入的实体不可用") -> None:
        self.faults.push("unavailable", error)

    def call(self, action: str, params: Mapping[str, Any]) -> CallResult:
        fault = self.faults.pop()
        if fault is not None:
            kind, error = fault
            return CallResult.fail(error, fault=kind, action=action, params=dict(params))
        if self.dry_run:
            self.intents.append((action, dict(params)))
            logging.getLogger(__name__).debug("[HAAdapter.dry_run] %s %s", action, dict(params))
            if self.on_dry_run is not None:
                self.on_dry_run(action, params)
            return CallResult.ok({"dry_run": True, "action": action, "params": dict(params)})
        if self.transport is None:
            raise AdapterError("HAAdapter 未注入 transport，无法真实下发（G1 只支持 dry_run）")
        # F7：真实下发前抓取"动作前快照"交钩子落盘（fail-closed：捕获失败不影响下发）
        if self.undo_recorder is not None:
            try:
                pre = _capture_pre_snapshot(self.transport, params)
                if pre:
                    self.undo_recorder(action, params, pre)
            except Exception:  # 快照捕获异常绝不阻断真实下发
                import logging
                logging.getLogger("autoforge.adapter").warning(
                    "undo 快照捕获异常（已忽略，不下发）", exc_info=True
                )
        return self.transport(action, params)


def _capture_pre_snapshot(transport: Transport, params: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """从 transport 读取目标实体动作前状态 + 属性（fail-closed）。

    依赖 transport 提供 `all_states()`（HATransport 具备）；否则返回空（不记录）。
    """
    target = params.get("entity_id")
    entities = [target] if isinstance(target, str) else list(target or [])
    if not entities:
        return {}
    all_states = getattr(transport, "all_states", None)
    if not callable(all_states):
        return {}
    try:
        states = all_states()
    except Exception:
        return {}
    if not isinstance(states, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for e in entities:
        if e in states:
            st, attrs = states[e]
            out[str(e)] = {"state": st, "attributes": attrs if isinstance(attrs, dict) else {}}
    return out
