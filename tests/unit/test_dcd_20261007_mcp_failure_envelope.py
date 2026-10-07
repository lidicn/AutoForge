"""裁定 20261007 §二 戊A：MCP 面的**异常路径**也必须是机器可读的 JSON 信封。

改之前只有工具**主动拒**（返回 dict 里带 `code`）是结构化的，而 `dispatch()` 的四条
异常出口——未知工具、参数未声明、`_guard` 拒绝、未捕获异常——回的是散文
（`"未知工具：…"`、`"工具执行出错：KeyError: …"`）。`isError` 只有一个布尔，对端（DB）
分不出"我请求格式错了 / 我没权限 / AF 自己有病"这三档，正确的补救动作也就无从选。

本文件钉三件事：
1. **形状**：失败回执是 `{ok:false, code, message}` 一枚 JSON，`is_error=True`；
2. **码的档**：四类拒绝各落在契约 §7.2 的哪一枚（不手抄，从 `homesdk.adm.errors` import）；
3. **不泄漏**：traceback 只进服务端日志，不进 `message`（R-57）。

成功面另有一条对照腿：成功返回**不带** `code`，`is_error=False`——两档必须能被判出来，
否则"改信封"这件事本身没有可验收的差异。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from homesdk.adm.errors import (
    ADM_ERR_AUTH_REQUIRED,
    ADM_ERR_INTERNAL,
    ADM_ERR_PAYLOAD_INVALID,
)

from autoforge import af_mcp
from autoforge.af_mcp import TOOLS, dispatch
from autoforge.af_store import GraphStore

_READ = {"subject": "t-read", "scopes": ["read"]}
_WRITE = {"subject": "t-write", "scopes": sorted({"read", "write"})}


def _by_scope(scope: str | None) -> str:
    """按 scope 取一枚工具名：名单唯一真源是 `TOOLS`，这里不再抄第二份。"""
    return next(name for name, _d, _s, _f, s in TOOLS if s == scope)


def _payload(content: list[dict[str, Any]]) -> dict[str, Any]:
    assert len(content) == 1 and content[0]["type"] == "text", content
    text = content[0]["text"]
    loaded = json.loads(text)  # 不是 JSON 就红：这一条正是 DB 新口径的第一步
    assert isinstance(loaded, dict), text
    return loaded


@pytest.fixture(autouse=True)
def _no_proto_passthrough(monkeypatch):
    """原型全放行开关必须关着：`AUTOFORGE_MCP_ALLOW_NO_TOKEN=1` 会让"无身份"这一档失去意义。"""
    monkeypatch.delenv("AUTOFORGE_MCP_ALLOW_NO_TOKEN", raising=False)


def test_unknown_tool_reads_payload_invalid(tmp_path):
    content, is_err = dispatch("af_不存在", {}, GraphStore(tmp_path), _WRITE)
    assert is_err is True
    p = _payload(content)
    assert p["ok"] is False
    assert p["code"] == ADM_ERR_PAYLOAD_INVALID
    assert "af_不存在" in p["message"]
    # 老散文口径的残骸不许回来：那层"工具执行出错："壳一盖，机器就读不到档
    assert "工具执行出错" not in p["message"]


def test_undeclared_arg_reads_payload_invalid(tmp_path):
    """`tools/list` 没展示的参数不许能用（安全审计 fp-authcode-bruteforce 加重情节 3）。"""
    name = _by_scope(None)  # 公开面也要拦：未声明键与 scope 无关
    schema = next(s for n, _d, s, _f, _s in TOOLS if n == name)
    declared = set((schema.get("properties") or {}).keys())
    smuggled = "allow_bulk" if "allow_bulk" not in declared else "zz_not_declared"
    assert smuggled not in declared
    content, is_err = dispatch(name, {smuggled: True}, GraphStore(tmp_path), _WRITE)
    assert is_err is True
    p = _payload(content)
    assert p["code"] == ADM_ERR_PAYLOAD_INVALID
    assert smuggled in p["message"], p


def test_no_identity_reads_auth_required(tmp_path):
    name = _by_scope("write")
    content, is_err = dispatch(name, {}, GraphStore(tmp_path), None)
    assert is_err is True
    p = _payload(content)
    assert p["code"] == ADM_ERR_AUTH_REQUIRED
    assert "AUTOFORGE_MCP_TOKEN" in p["message"] or "AUTOFORGE_TOKENS" in p["message"], p


def test_missing_scope_reads_auth_required(tmp_path):
    name = _by_scope("write")
    content, is_err = dispatch(name, {}, GraphStore(tmp_path), _READ)
    assert is_err is True
    p = _payload(content)
    assert p["code"] == ADM_ERR_AUTH_REQUIRED
    assert "write" in p["message"], p


def test_uncaught_exception_reads_internal_without_traceback(tmp_path):
    """实现体抛出的非业务异常 ⇒ INTERNAL，且 traceback 不外传（R-57）。"""
    # af_experience 的 limit 走 `int(args["limit"])`：给它一枚不可转换的值就是天然异常腿。
    content, is_err = dispatch("af_experience", {"limit": "not-a-number"}, GraphStore(tmp_path), _READ)
    assert is_err is True
    p = _payload(content)
    assert p["code"] == ADM_ERR_INTERNAL
    assert "Traceback" not in p["message"] and "af_mcp.py" not in p["message"], p
    assert p["message"].startswith("工具执行出错："), p


def test_service_layer_rejection_reads_internal_and_keeps_its_prefix(tmp_path, monkeypatch):
    """`svc.ServiceError`（只读服务层 / 单写者租约那一族）落 INTERNAL，前缀留在 message 开头。

    与 `test_serve_lease_single_writer.py::test_mcp_face_text_starts_with_the_prefix`
    同一口径，那条走真锁，这条只验形状：前缀不许被信封壳挤走。
    """
    from autoforge import af_service as svc

    def _boom(*a, **k):
        raise svc.ServiceError("READONLY_DEGRADED: 探针造的那条拒绝")

    monkeypatch.setattr(svc, "get_experience", _boom)
    content, is_err = dispatch("af_experience", {}, GraphStore(tmp_path), _READ)
    assert is_err is True
    p = _payload(content)
    assert p["code"] == ADM_ERR_INTERNAL
    assert p["message"].startswith("READONLY_DEGRADED:"), p


def test_success_face_is_not_an_envelope(tmp_path):
    """CONTROL：成功返回不带 `code`、`is_error=False`——两档要能被判出来。"""
    content, is_err = dispatch("af_health", {}, GraphStore(tmp_path), None)
    assert is_err is False
    p = _payload(content)
    assert p["ok"] is True and "code" not in p, p


def test_every_failure_exit_has_a_real_adm_code(tmp_path, monkeypatch):
    """四条异常出口的码必须都出自 homesdk 的那一枚集合，不许出现自造码。

    契约 §7.2 的纪律是「凡是联动失败，必须带码」；带一枚对端词表里没有的码 = 换个姿势静默。
    """
    from homesdk.adm import errors as adm_errors

    known = {
        getattr(adm_errors, n)
        for n in dir(adm_errors)
        if n.startswith("ADM_ERR_") and isinstance(getattr(adm_errors, n), str)
    }
    store = GraphStore(tmp_path)
    probes = [
        ("af_不存在", {}, _WRITE),
        ("af_experience", {"zz_not_declared": 1}, _WRITE),
        (_by_scope("write"), {}, None),
        (_by_scope("live"), {}, _READ),
        ("af_experience", {"limit": "not-a-number"}, _READ),
    ]
    codes = []
    for name, args, current in probes:
        content, is_err = dispatch(name, args, store, current)
        assert is_err is True, (name, args)
        code = _payload(content)["code"]
        assert code in known, (name, code, sorted(known))
        codes.append(code)
    # 反空洞：这一批探针必须真的跨了至少两档，否则"码合法"可以是全同一条腿量出来的
    assert len(set(codes)) >= 2, codes
    assert {ADM_ERR_PAYLOAD_INVALID, ADM_ERR_AUTH_REQUIRED, ADM_ERR_INTERNAL} <= set(codes), codes


def test_envelope_helper_is_the_only_shape_writer():
    """形状只有一个写者：所有失败出口都经 `_failure_payload`，不散着手抄三键。"""
    import inspect

    src = inspect.getsource(af_mcp.dispatch)
    assert src.count("_failure_payload(") >= 4, src
    # 出口里的 json.dumps 只包 `_failure_payload(...)`/成功结果，不许再手拼 {ok/code/message}
    assert '"ok": False' not in src.replace("_failure_payload", ""), src
