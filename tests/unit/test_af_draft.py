"""v2 M2 AF-Spec 封闭词表 forbid · af_draft 单测。

覆盖：意图顶层域封闭词表（未知域即拒 + 可机读 fix），以及合法意图 / ask / do_list
仍正常编译（回归）。
"""

from __future__ import annotations

import pytest

from autoforge.af_draft import DraftError, E_UNKNOWN_DOMAIN, draft_intent


def _when() -> dict:
    return {"type": "state", "entity": "前门", "to": "on"}


def test_legal_intent_with_do_compiles():
    res = draft_intent({"name": "开灯", "when": _when(), "do": {"action": "开灯", "target": "客厅灯"}})
    assert res["ok"] is True
    assert "ref" in res


def test_legal_intent_with_ask_only_compiles():
    res = draft_intent({"name": "询问", "when": _when(), "ask": {"prompt": "执行吗？"}})
    assert res["ok"] is True


def test_legal_intent_with_do_list_compiles():
    res = draft_intent(
        {
            "name": "多动作",
            "when": _when(),
            "do_list": [
                {"action": "开灯", "target": "客厅灯"},
                {"action": "关灯", "target": "卧室灯"},
            ],
        }
    )
    assert res["ok"] is True


def test_legal_intent_with_if_and_wait():
    res = draft_intent(
        {
            "name": "条件等待",
            "when": _when(),
            "if": {"lt": {"var": "照度", "const": 200}},
            "wait": "10s",
            "do": {"action": "开灯", "target": "客厅灯"},
        }
    )
    assert res["ok"] is True


def test_unknown_intent_domain_rejected():
    with pytest.raises(DraftError) as exc:
        draft_intent({"name": "x", "when": _when(), "do": {"action": "开灯"}, "frobnicate": 1})
    assert exc.value.code == E_UNKNOWN_DOMAIN
    assert "frobnicate" in exc.value.fix["unknown"]
    assert "frobnicate" not in exc.value.fix["allowed"]


def test_unknown_domain_fix_lists_allowed():
    with pytest.raises(DraftError) as exc:
        draft_intent({"when": _when(), "do": {"action": "开灯"}, "bogus": True})
    fix = exc.value.fix
    assert set(fix["allowed"]) >= {"name", "mode", "when", "if", "do", "do_list", "ask", "wait"}
    assert fix["unknown"] == ["bogus"]


def test_multiple_unknown_domains_all_reported():
    with pytest.raises(DraftError) as exc:
        draft_intent({"when": _when(), "do": {"action": "开灯"}, "x": 1, "y": 2})
    assert set(exc.value.fix["unknown"]) == {"x", "y"}


def test_missing_when_still_errors():
    # 既有错误码不被封闭词表改动影响
    with pytest.raises(DraftError) as exc:
        draft_intent({"do": {"action": "开灯"}})
    assert exc.value.code == "E_MISSING_WHEN"


def test_legal_field_if_compiles():
    res = draft_intent({"name": "带条件", "when": _when(), "if": {"lt": {"var": "照度", "const": 200}}, "do": {"action": "开灯"}})
    assert res["ok"] is True
