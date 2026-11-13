# -*- coding: utf-8 -*-
"""AF-778 判据 · `enable_by_tag` 过闸后必须 consume（2026-11-14，§1.216 裁定）。

**缺陷**：`enable_by_tag` 的启用腿只调 `_auth_code_check`（validate），**从不 consume**
⇒ 一枚短期码可以**无限次重放**：同一枚码反复启用不同标签/不同批次全都过，账本上的
`consumed` 记号永远不落下。而 MCP 部署面（`af_mcp._t_save`）是"过闸即 consume"，
两个面口径不同 ⇒ "码到底能烧几次"取决于走哪条路。

**修法**：`_auth_code_check` 通过后**立即** `acs.consume(auth_code)`，fail-closed
（`consume` 返回 False ⇒ 403 拒）。位置紧随 validate、save 循环之前，但在**爆炸半径检查
之后**——超额被拒的那次⛔ 不许扣额度。consume 按 kind 分型由 `AuthCodeStore` 自己决定
（短期码置 consumed、长期码 use_count+1），⛔ 这里不加"拒短期码"：DCD 双轨授权里短期码
本就是部署凭据。

**判据**：

- C1 短期码：部署成功后 `validate` 为 False，且**同码重放**再调一次 ⇒ 403 拒
- C2 长期码：部署后 `use_count` +1，且**仍可复用**（第二次仍成功、use_count 变 2）
- C3 超额被拒不烧码：`_check_blast` 触发 403 时，码的 `consumed`/`use_count` 一字未动
- C4 相邻通道不受影响：WebUI 待批 approve 通道不走 `enable_by_tag`（源码钉）；
  MCP 部署面（`_t_save`）自带的 consume 口径不变（行为钉）
- C5 CLI 面口径（⚠️ 现状纠正，见回执）：`forge store enable/disable` 走
  `svc_enable_disable`，**既无凭据闸也无 consume**（本地 CLI 形状）——
  本腿把现状钉住，防有人误以为那里也烧码
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

from autoforge.af_auth import AuthCodeStore
from autoforge.af_ir import Graph, load_automation
from autoforge.af_service import ServiceError, enable_by_tag
from autoforge.af_store import GraphStore

IR: dict[str, Any] = {
    "ir_version": "0.2.1", "id": "af778_ir", "name": "AF778 归档",
    "version": 1, "mode": "single",
    "nodes": [
        {"id": "t1", "kind": "on", "name": "人",
         "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "p1", "kind": "pass", "name": "结束"},
    ],
    "edges": [{"from": "t1", "to": "p1", "kind": "then"}],
}


def _env(tmp_path, *, tag: str = "night"):
    store = GraphStore(str(tmp_path))
    store.save(Graph([load_automation(IR)]), "af778_case", tags=[tag])
    acs = AuthCodeStore(tmp_path / ".auth" / "auth_codes.json")
    return store, acs


def _rec(acs: AuthCodeStore, code: str) -> dict[str, Any]:
    return next(r for r in acs.list(reveal=True) if r.get("code") == code)


# ── C1 短期码：一次性，重放被拒 ───────────────────────────────────────
def test_c1_short_code_is_consumed_and_replay_rejected(tmp_path):
    """判据：短期码部署成功后必须**立刻作废**；同码再启用一次必须被 403 拒。"""
    store, acs = _env(tmp_path)
    code = acs.create("short").code

    out = enable_by_tag(store, "night", True, allow_bulk=True,
                        caller="dsh", caller_is_owner=True, auth_code=code)
    assert out.get("ok") is True, "前置：带有效短期码的启用必须成功"

    assert acs.validate(code) is False, "部署成功后短期码必须已 consumed（⛔ 修前恒 True）"
    assert _rec(acs, code).get("consumed") is True, "账本上必须真的落下 consumed 记号"

    # 重放同一枚码：必须拒（修前这里会再成功一次，等于无限重放）
    with pytest.raises(ServiceError) as ei:
        enable_by_tag(store, "night", True, allow_bulk=True,
                      caller="dsh", caller_is_owner=True, auth_code=code)
    assert ei.value.status == 403, "重放必须 403 拒"


# ── C2 长期码：计次但可复用 ──────────────────────────────────────────
def test_c2_long_code_counts_use_and_stays_reusable(tmp_path):
    """判据：长期码部署后 `use_count` +1，且**仍可复用**（⛔ 不许把长期码做成一次性）。"""
    store, acs = _env(tmp_path)
    code = acs.create("long").code

    enable_by_tag(store, "night", True, allow_bulk=True,
                  caller="dsh", caller_is_owner=True, auth_code=code)
    assert _rec(acs, code).get("use_count") == 1, "长期码必须计一次 use_count"
    assert acs.validate(code) is True, "长期码不消耗（可撤销、不一次性）"

    enable_by_tag(store, "night", True, allow_bulk=True,
                  caller="dsh", caller_is_owner=True, auth_code=code)
    assert _rec(acs, code).get("use_count") == 2, "第二次复用必须继续计次"


# ── C3 超额被拒⛔ 不烧码 ──────────────────────────────────────────────
def test_c3_blast_rejection_does_not_burn_the_code(tmp_path, monkeypatch):
    """判据：爆炸半径拒（403）发生在 consume **之前** ⇒ 码一字未动。

    没有这条，「被拒还扣额度」这种形状就会混进来——用户被拒一次就得再生成一枚码。
    """
    store, acs = _env(tmp_path)
    code = acs.create("short").code

    import autoforge.af_service as svc

    # 造「影响面 2 > 上限 1」：同标签再加一条归档
    store.save(Graph([load_automation({**IR, "id": "af778_ir2"})]), "af778_case2", tags=["night"])
    monkeypatch.setattr(svc, "blast_radius_limit", lambda: 1)

    with pytest.raises(ServiceError) as ei:
        enable_by_tag(store, "night", True, allow_bulk=False,
                      caller="dsh", caller_is_owner=True, auth_code=code)
    assert ei.value.status == 400, "爆炸半径超额是 400（形状错误，不是鉴权错误）"

    rec = _rec(acs, code)
    assert rec.get("consumed") is False and rec.get("use_count") == 0, (
        "被拒的那次⛔ 不许扣额度（码必须完好，用户改小批次就能接着用）"
    )
    assert acs.validate(code) is True


# ── C4 相邻通道不受影响 ──────────────────────────────────────────────
def test_c4_other_channels_unaffected(tmp_path, monkeypatch):
    """判据：WebUI 待批 approve 通道不走 `enable_by_tag`；MCP 部署面的 consume 口径不变。"""
    from autoforge import af_api, af_mcp

    # ① approve 通道与 enable_by_tag 无调用关系（源码钉：改这里不许动那边）
    api_src = inspect.getsource(af_api)
    approve_handlers = [
        n for n in ast.walk(ast.parse(api_src.lstrip()))
        if isinstance(n, ast.FunctionDef) and "approve" in n.name
    ]
    assert approve_handlers, "前置：approve 处理器必须存在（否则钉错了地方）"
    for fn in approve_handlers:
        assert "enable_by_tag" not in ast.dump(fn), (
            "approve 通道⛔ 不许调 enable_by_tag（两条通道的 consume 归属必须分开）"
        )

    # ② MCP 部署面自带 consume，形状不变（过闸即烧一枚短期码）
    store, acs = _env(tmp_path)
    code = acs.create("short").code
    monkeypatch.setattr(af_mcp, "_MCP_AUTH_STORE", acs, raising=False)
    from autoforge.af_adapters.base import CallResult  # noqa: F401  （仅确保模块可导入）

    payload = {"name": "af778_mcp", "ir": {**IR, "id": "af778_mcp_ir"}, "auth_code": code}
    content, is_err = af_mcp.dispatch("af_save", payload, store,
                                      {"subject": "dsh", "scopes": ["read", "write"]})
    body = __import__("json").loads(content[0]["text"])
    assert is_err is False and body.get("ok") is True, "前置：MCP 部署必须成功"
    assert _rec(acs, code).get("consumed") is True, "MCP 面口径不变：部署即消耗短期码"


# ── C5 CLI 面：现状钉（无凭据闸、无 consume）──────────────────────────
def test_c5_cli_tag_path_has_no_auth_gate_by_design(tmp_path):
    """判据（⚠️ 现状纠正）：`forge store enable --tag` 走 `svc_enable_disable`，
    **既无 `_auth_code_check` 也无 consume**——本地 CLI 形状，不属凭据面。

    QA-Code R4 报"CLI tag 带 consume"，本卡现读代码**不成立**：全库 `.consume(` 只出现在
    af_api(approve)/af_mcp(部署)/af_service(live_run/grant)/af_premiere/af_auth(pair)。
    ⇒ 本腿钉住现状，防有人按错误印象给 CLI 补一道不该有的闸，也防有人以为那里已经烧码。
    """
    from autoforge.af_cli import svc_enable_disable

    src = inspect.getsource(svc_enable_disable)
    assert "_auth_code_check" not in src, "CLI tag 面现状无凭据闸（⛔ 改这条要另开卡）"
    assert ".consume(" not in src, "CLI tag 面现状不烧码（⛔ 不是本卡要修的面）"

    # 行为面同一件事：无码也能批量启用（本地 CLI 直连 store）
    store, _acs = _env(tmp_path)
    affected = svc_enable_disable(store, ["af778_case"], True)
    assert [a["name"] for a in affected] == ["af778_case"], "CLI 面照旧可用（无码）"
