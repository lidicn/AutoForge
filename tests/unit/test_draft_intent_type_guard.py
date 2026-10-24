"""G5 判据腿：draft_intent 收到非 dict 时必须抛 E_INTENT_NOT_OBJECT。

背景（2026-10-24 实测复现）：`draft_intent('打开客厅灯')` 不抛"你传了字符串"，
而是把字符串逐字符迭代，每个汉字成"未知域"，抛 `E_UNKNOWN_DOMAIN`。
这把真实协议错误（该传 dict 却传了 str）伪装成"域名单错了"——agent 拿到的
`fix.allowed` 是字段表，会去改字段名，而真正的修法是包一层 dict。误导性归因。

本文件钉五条：str / list / None 三种非对象 → 新码；正常 dict 不回归；
真未知域仍走 E_UNKNOWN_DOMAIN（原有行为不动）。
"""

from __future__ import annotations

import pytest

from autoforge.af_draft import (
    E_INTENT_NOT_OBJECT,
    E_UNKNOWN_DOMAIN,
    DraftError,
    draft_intent,
)


def _when() -> dict:
    return {"type": "state", "entity": "前门", "to": "on"}


def test_str_intent_raises_not_object():
    with pytest.raises(DraftError) as ei:
        draft_intent("打开客厅灯")
    assert ei.value.code == E_INTENT_NOT_OBJECT
    assert "str" in ei.value.args[0]
    # 不要回灌字段名表——真正的修法是包 dict，不是改字段名
    assert ei.value.fix is not None
    assert ei.value.fix.get("expected") == "object"


def test_list_intent_raises_not_object():
    with pytest.raises(DraftError) as ei:
        draft_intent(["name", "开门"])
    assert ei.value.code == E_INTENT_NOT_OBJECT
    assert "list" in ei.value.args[0]


def test_none_intent_raises_not_object():
    with pytest.raises(DraftError) as ei:
        draft_intent(None)
    assert ei.value.code == E_INTENT_NOT_OBJECT
    assert "NoneType" in ei.value.args[0]


def test_dict_path_unaffected():
    """正常 dict 路径照旧走通（不回归）。"""
    res = draft_intent(
        {"name": "开灯", "when": _when(), "do": {"action": "开灯", "target": "客厅灯"}}
    )
    assert res["ok"] is True
    assert "ref" in res


def test_unknown_domain_still_unknown_domain():
    """真·未知域行为不动：合法 dict 但多一个未登记顶层键 → 仍 E_UNKNOWN_DOMAIN。"""
    with pytest.raises(DraftError) as ei:
        draft_intent({"name": "x", "when": _when(), "do": {"action": "开灯"}, "bogus": 1})
    assert ei.value.code == E_UNKNOWN_DOMAIN
    assert "bogus" in ei.value.fix["unknown"]
