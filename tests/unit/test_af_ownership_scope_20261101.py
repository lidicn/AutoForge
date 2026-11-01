"""C-3 归属判定单源判据（af-ownership-audit-model-v3-v3，PM 批准开工）。

本文件钉单源层（Subject/decide/scope_names/三文案）的判定表全貌；服务面红用例
（R1-R10）在服务面收口笔并入。每条服务面断言都必须能映射到这里的一条 verdict。

判定表（设计稿 §2）：
  BROKEN           → 读写皆拒（残缺身份，⛔ 不得降级为 NONE）
  NONE             → 豁免放行（⛔ 裁定 §四 1：不整体删除）
  PRESENT owner 面 → 放行
  PRESENT agent =caller → 放行
  PRESENT agent 归属="" → 读 404 同形 / 写 403 fail-closed
  PRESENT agent 他人    → 读 404 同形 / 写 403 不点名
"""

from __future__ import annotations

import pytest

from autoforge.af_ownership import (
    DenyKind,
    Subject,
    TokenState,
    broken_identity_error,
    decide,
    mutator_error,
    scope_names,
    visible_error,
)


@pytest.fixture()
def owner_of():
    owners = {"mine": "agent-a", "theirs": "agent-b", "orphan": ""}
    return lambda name: owners.get(name, "")


def sub(caller: str, token_identity):
    return Subject.from_current({"subject": caller} if caller else None, token_identity)


# ── BROKEN 档：读写同拒，⛔ 不得降级 ─────────────────────────────────

def test_broken_identity_denied_on_read_and_write(owner_of):
    s = sub("", True)  # 持令牌却缺 subject
    assert s.token_state is TokenState.BROKEN
    assert decide(owner_of, "mine", s, write=False) is DenyKind.BROKEN_IDENTITY
    assert decide(owner_of, "mine", s, write=True) is DenyKind.BROKEN_IDENTITY
    assert scope_names(owner_of, ["mine", "theirs"], s) == ([], 2), "BROKEN 列表面全隐"


# ── NONE 档：豁免保留（⛔ 裁定 §四 1） ───────────────────────────────

def test_anonymous_exemption_not_removed(owner_of):
    s = sub("", None)
    assert s.token_state is TokenState.NONE
    assert decide(owner_of, "theirs", s, write=True) is DenyKind.OK
    assert scope_names(owner_of, ["mine", "theirs"], s) == (["mine", "theirs"], 0)


def test_broken_never_degrades_to_none(owner_of):
    """C6：BROKEN 在任何面不得变 NONE——同一空 subject，flag 翻 True 即拒。"""
    assert sub("", None).token_state is TokenState.NONE
    assert sub("", True).token_state is TokenState.BROKEN
    assert sub("", False).token_state is TokenState.NONE


# ── PRESENT agent 档：读 404 同形 / 写 403 不点名 ────────────────────

def test_agent_read_hidden_write_forbidden(owner_of):
    s = sub("agent-a", True)
    assert decide(owner_of, "theirs", s, write=False) is DenyKind.NOT_FOUND
    assert decide(owner_of, "theirs", s, write=True) is DenyKind.FORBIDDEN
    assert decide(owner_of, "mine", s, write=True) is DenyKind.OK
    assert decide(owner_of, "mine", s, write=False) is DenyKind.OK


def test_ownerless_archive_is_owner_face_only(owner_of):
    """无归属归档：agent 双拒（读 404 同形/写 403），与 _assert_archive_visible 对 "" 一致。"""
    s = sub("agent-a", True)
    assert decide(owner_of, "orphan", s, write=False) is DenyKind.NOT_FOUND
    assert decide(owner_of, "orphan", s, write=True) is DenyKind.FORBIDDEN


# ── owner 面 ─────────────────────────────────────────────────────────

def test_owner_face_reads_and_writes_all(owner_of, monkeypatch):
    # _OWNER_SUBJECTS 内容未读（设计稿 §2 未读清单）：测试不假设名单，直接换 face 判据
    monkeypatch.setattr(Subject, "is_owner_face", property(lambda self: self.caller == "dev-face"))
    s = sub("dev-face", True)
    assert decide(owner_of, "theirs", s, write=True) is DenyKind.OK
    assert decide(owner_of, "theirs", s, write=False) is DenyKind.OK
    assert scope_names(owner_of, ["mine", "theirs"], s) == (["mine", "theirs"], 0)


# ── scope 计数合拢（C3） ─────────────────────────────────────────────

def test_scope_counts_reconcile(owner_of):
    s = sub("agent-a", True)
    visible, hidden = scope_names(owner_of, ["mine", "theirs", "orphan"], s)
    assert visible == ["mine"] and hidden == 2
    assert len(visible) + hidden == 3


def test_scope_empty_input(owner_of):
    s = sub("agent-a", True)
    assert scope_names(owner_of, [], s) == ([], 0)


# ── 三文案单源（C2/C8：同形、不点名） ────────────────────────────────

def test_visible_error_same_shape_as_not_found():
    """读拒串与「不存在」逐字同形（C2）——文案单源固定形状，⛔ 回「无权查看」。"""
    e = visible_error("foo")
    assert "没找到归档" in e and "foo" in e
    assert "无权" not in e and "不属于你" in e  # 设计稿草案形状：与不存在同形不带权利语
    # 与 af_service 不存在串的关系在服务面收口笔逐字对齐；此处钉形状锚。


def test_mutator_error_never_names_owner():
    """写拒串不点归属者名（AF-509/C8）：入参只有 name 与 action。"""
    e = mutator_error("theirs", "改标签")
    assert "theirs" in e and "agent-b" not in e
    assert "无权" in e


def test_broken_identity_error_literal():
    e = broken_identity_error()
    assert "残缺身份" in e and "subject" in e
