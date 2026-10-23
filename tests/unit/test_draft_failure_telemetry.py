"""af_draft 失败归因 + _annotate_failure 历史摘要（v1.13.3，autoflow 经验闭环收尾）。

两个真实空档的收口：
① `_t_draft` 捕 DraftError 返回 fix 但此前零归因——同形意图反复递、反复拒，
   知识库/遥测一条痕迹都没有（record_failure 从没被调用）；
② `_annotate_failure` 只带历史条数不带内容——agent 拿到一个数字，还是得自己去查。

失败形状：
① _telemetry 缺席 ⇒ 失败样本不进知识库，af_telemetry 的错误类别分布永远看不见 draft 档；
② record_failure 自己炸（盘满/权限）会把业务回执拖成异常 ⇒ MCP isError——旁路容错
   必须真的容错；
③ 历史摘要行缺失/不带时间 ⇒ agent 看不到"这类失败最近发生过"。
"""
from __future__ import annotations

import json
from pathlib import Path

from autoforge.af_store import GraphStore
from autoforge.af_mcp import dispatch, _annotate_failure

_ALL = {"subject": "t", "scopes": ["read", "write", "live", "admin"]}


def _draft_fail(store: GraphStore, intent: dict) -> dict:
    content, is_error = dispatch("af_draft", {"intent": intent}, store, _ALL)
    assert is_error is False  # 业务失败（ok=False），不是协议错误
    return json.loads(content[0]["text"])


def test_draft_failure_carries_telemetry(tmp_path):
    """① DraftError 回执带 _telemetry（category/label/advice/ts 字段族）。"""
    store = GraphStore(tmp_path)
    body = _draft_fail(store, {"name": "x", "bogus_domain": 1})
    assert body["ok"] is False
    assert body["error"]["code"]  # 细粒度子码保留
    tel = body.get("_telemetry")
    assert isinstance(tel, dict), "_telemetry 缺席 ⇒ 失败样本不进知识库"
    for key in ("category", "label", "advice", "ts"):
        assert key in tel, (key, tel)


def test_draft_failure_history_closes_the_loop(tmp_path):
    """失败→落库→同类回读闭环：第二次同形失败的历史里应看到第一次。"""
    store = GraphStore(tmp_path)
    _draft_fail(store, {"name": "x", "bogus_domain": 1})
    body = _draft_fail(store, {"name": "y", "bogus_domain": 2})
    history = body["_telemetry"].get("history") or []
    assert len(history) >= 1, "第二次失败读不到第一次的历史 ⇒ 写→读闭环没接上"


def test_draft_telemetry_failure_does_not_break_the_receipt(tmp_path, monkeypatch):
    """② record_failure 自己炸不许把业务回执拖成异常——旁路容错必须真容错。"""
    import autoforge.af_mcp as mcp_mod

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(mcp_mod, "record_failure", _boom)
    store = GraphStore(tmp_path)
    body = _draft_fail(store, {"name": "x", "bogus_domain": 1})
    # 回执完好：ok=False + error 子码都在；_telemetry 可以缺席（旁路尽力而为）
    assert body["ok"] is False
    assert body["error"]["code"]
    assert "error" in body


def test_annotate_failure_appends_history_summary_line(tmp_path):
    """③ 历史同类不只报条数——≤3 条压成一行 `label@时间` 摘要。"""
    store = GraphStore(tmp_path)
    # 先制造同类历史（IR_SCHEMA 族）
    for _ in range(3):
        _annotate_failure(store, "af_build", "ir 校验失败: schema required property missing")
    out = _annotate_failure(store, "af_build", "ir 校验失败: another schema problem")
    assert "归因：" in out and "建议：" in out
    assert "历史同类 3 条" in out, out
    assert "@" in out  # 摘要行带时间戳
    # 摘要格：label@ts（ts 截到分钟位也行，必须能看见"何时发生过"）
    tail = out.rsplit("历史同类 3 条：", 1)[-1]
    assert tail.count("；") == 2, tail  # 3 条 = 两个分隔符


def test_annotate_failure_without_history_has_no_summary(tmp_path):
    """零历史时维持旧形状（归因+建议，不带历史尾巴）——不虚构空摘要。"""
    store = GraphStore(tmp_path)
    out = _annotate_failure(store, "af_build", "ir 校验失败: schema required property missing")
    assert "归因：" in out
    assert "历史同类" not in out
