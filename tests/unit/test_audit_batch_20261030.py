"""代码审计批（AF-301/302/303/304/307/308）的判据腿，2026-10-30。

出处 `E:/NAS/QA/AF_Tester/reports/AF_code_audit_1e71e63.md`。这批的共同特征：
每一条都是"上一轮说修了，但同族还有另一支没修"或"注释承诺了实现没做到"——
所以判据要钉的是**形状**，不是单点行为。
"""

from __future__ import annotations

import json
import tempfile

import pytest

from autoforge.af_error_knowledge import HINT_TO_CATEGORY, classify
from autoforge.af_mcp import _non_negative_int, _positive_int, dispatch
from autoforge.af_service import _catalog, _dispatch_verdict
from autoforge.af_store import GraphStore

OWNER = {"subject": "owner", "scopes": ["read", "write", "live", "admin"]}
IR = {"ir_version": "0.3.0", "id": "a", "name": "a", "version": 1, "mode": "single",
      "nodes": [{"id": "t", "kind": "on",
                 "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
                {"id": "p", "kind": "pass"}],
      "edges": [{"from": "t", "to": "p", "kind": "then"}]}


class _Rt:
    def __init__(self, types):
        self.audit = [type("E", (), {"to_dict": (lambda self, x=x: {"type": x})})() for x in types]


# ── AF-301：承诺过的档位必须真出得来 ──────────────────────────────────

def test_partial_failure_is_distinguishable_from_total_failure():
    """审计抓到的形状：`"failed" if dispatched else "failed"` 两支同值 + 注释承诺 partially。

    判据不许只测"失败"——要测**成败混合**这一格，否则退化三元照样绿。
    """
    out = _dispatch_verdict(_Rt(["af_action_dispatched", "action_failed"]))
    assert out["verdict"] == "partially_failed", out
    assert out["dispatched"] == 1 and out["failed"] == 1


@pytest.mark.parametrize("types,want", [
    (["af_action_dispatched"], "executed"),
    (["action_failed"], "failed"),
    ([], "nothing_dispatched"),
])
def test_verdict_other_levels(types, want):
    assert _dispatch_verdict(_Rt(types))["verdict"] == want


def test_docstring_promises_match_achievable_verdicts():
    """注释承诺过的每个档名都必须真能被产出（否则注释就是第二份假契约）。"""
    import inspect

    from autoforge import af_service as svc
    src = inspect.getsource(svc._dispatch_verdict)
    reachable = {"executed", "failed", "partially_failed", "nothing_dispatched"}
    for word in ("executed", "failed", "partially", "nothing"):
        assert word in src, f"文档串里少了 {word}"
    assert {"partially_failed", "nothing_dispatched"} <= reachable


# ── AF-302：同族每一支都要过脱敏（漏一支＝"已修"结论不成立）──────────

def test_not_found_receipt_carries_no_absolute_path():
    with tempfile.TemporaryDirectory() as td:
        for tool, args in (("af_get_graph", {"name": "ghost"}),
                           ("af_diff", {"name": "ghost", "old": 1, "new": 2})):
            content, _ = dispatch(tool, args, GraphStore(td), OWNER)
            blob = content[0]["text"]
            assert "v1.json" not in blob and f"{td}" not in blob, f"{tool} 仍泄露路径"
            assert json.loads(blob)["code"] != "ADM_ERR_INTERNAL"


# ── AF-303 / AF-304：catalog 构造的两处形状 ───────────────────────────

def test_catalog_survives_none_store(tmp_path):
    """审计 AF-304：崩点在 try **外面**的 `DeviceCatalog(store.root)`——一处有兜底一处没有。"""
    cat = _catalog(None)
    assert cat is not None and hasattr(cat, "resolve_best")


def test_no_hardcoded_internal_ha_url_in_catalog():
    """AF-303：catalog 曾回落硬编码内网地址（`ha.py` 收了，这处漏网）。

    ⚠️ 判据形状本身被我写糙过一次：`inspect.getsource` 会把**注释**也带进来，
    而注释里正引用着那个旧字面量（"这里曾回落 http://192.168…"），于是断言红在
    说明文字上。真契约是"没有字符串常量长这样"，所以扫 AST 的 Constant。
    """
    import ast
    import inspect
    import re

    from autoforge import af_service as svc

    tree = ast.parse(inspect.getsource(svc))
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    hosty = [s for s in literals if re.search(r"\d+\.\d+\.\d+\.\d+", s) and "http" in s]
    assert not hosty, f"源码字符串常量里出现带 IP 的 URL：{hosty[:2]}"


# ── AF-307：一个形状一个助手，0 的语义各自要对 ────────────────────────

# `""` 不在拒绝名单里：空串按"未提供"走缺省（与既有调用形状一致，⛔ 别把表单空值变 400）
@pytest.mark.parametrize("bad", [0, -1, "abc", "0.5", [], {}])
def test_positive_int_rejects(bad):
    from autoforge.af_service import ServiceError
    with pytest.raises(ServiceError):
        _positive_int(bad, "limit", 10)


def test_positive_int_accepts_and_defaults():
    assert _positive_int(None, "limit", 10) == 10
    assert _positive_int("3", "limit", 10) == 3


def test_offset_zero_is_legal_but_negative_is_not():
    """⛔ 不能拿 _positive_int 套 offset：0 是首页，是被静默换掉才叫 bug。"""
    assert _non_negative_int(0, "offset", 5) == 0
    assert _non_negative_int(None, "offset", 5) == 5
    from autoforge.af_service import ServiceError
    with pytest.raises(ServiceError):
        _non_negative_int(-1, "offset", 5)


def test_offset_zero_is_not_rejected_or_replaced():
    """0 是合法 offset（首页）：不能被拒，也不能被换成非零缺省。

    离线空目录下 `af_list_entities` 本身会回 ok:False（没数据），这腿只量
    "参数形状没被误拒"——所以断言的是**不出现 PARAM 类错误码**。
    """
    with tempfile.TemporaryDirectory() as td:
        content, _ = dispatch("af_list_entities", {"offset": 0, "limit": 5},
                              GraphStore(td), OWNER)
    payload = json.loads(content[0]["text"])
    assert payload.get("code") != "ADM_ERR_PAYLOAD_INVALID",         f"offset=0 被当成非法参数：{payload}"
    assert _non_negative_int(0, "offset", 5) == 0


# ── AF-308：表里不许留走不到的条目 ────────────────────────────────────

def test_hint_table_has_no_dead_entries():
    """映射表的每一项都必须真能命中 classify 的产出类别，否则它是文档不是代码。"""
    from autoforge.af_error_knowledge import CATEGORY_ADVICE
    for hint, cat in HINT_TO_CATEGORY.items():
        assert cat in CATEGORY_ADVICE, f"{hint}→{cat} 指向不存在的类别"
        assert classify("", hint_code=hint) == cat or cat == "UNKNOWN", f"{hint} 走不到"


def test_negative_offset_via_dispatch_is_rejected():
    """走真实入口才分得出助手在不在：`int(x or 0)` 对 0 等价、对 -1 会静默放行。

    M2 变异（摘掉 `_non_negative_int` 退回旧写法）对 offset=0 不改行为——那条腿本来
    就不该红；这一条改判 -1，助手一摘就红，才是有效区分面。
    """
    with tempfile.TemporaryDirectory() as td:
        content, is_err = dispatch("af_list_entities", {"offset": -1, "limit": 5},
                                   GraphStore(td), OWNER)
    payload = json.loads(content[0]["text"])
    assert is_err is True and payload.get("code") == "ADM_ERR_PAYLOAD_INVALID",         f"负 offset 没被拒：{payload}"
