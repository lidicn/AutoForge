"""ADM 联动契约测试 · DB→AF 的 MCP 调用链（执行计划 第 4 步①③）。

DB（doubao-butler）用 MCP 走的是 **「拟 → 验 → 批 → 部署」**。本文件把这条链里
跨仓会漂移的两件事钉住：

1. `af_apply` 的 `stage` 取值面：`dry_run` 必须**零写入**（不入待批队列、不烧首演码），
   而**未知 stage 一律拒绝**——过去打错一个字母会一路落到 `save`，等于"笔误即部署"。
2. 验完再批的时序证据：`dry_run` 之后队列必须还是空的，`save` 之后必须恰好多一条。

ask+answer 那条通道（`/api/asks/*`、`AUTOFORGE_INBOX_KEY` 生效前提）在同一目录下
`tests/contract/test_af_ask_contract.py`，本文件不重复断言。
"""
from __future__ import annotations

import pytest

from autoforge.af_apply import APPLY_STAGES, apply
from autoforge.af_mcp import _t_apply, _t_draft
from autoforge.af_pending import PendingStore
from autoforge.af_store import GraphStore

INTENT = {
    "name": "开门亮灯",
    "when": {"type": "state", "entity": "前门", "to": "on"},
    "do": {"action": "开灯", "target": "客厅灯"},
}


@pytest.fixture()
def store(tmp_path):
    return GraphStore(tmp_path)


def _ref(store):
    res = _t_draft(store, {"intent": INTENT})
    assert res.get("ok") is not False, f"draft 失败：{res}"
    return res["ref"]


def test_dry_run_enqueues_nothing(store):
    """第 4 步① 的验收：DB 调一次 dry_run，不实际部署。"""
    ref = _ref(store)
    ps = PendingStore(store.root)
    assert ps.list() == []

    res = _t_apply(store, {"ref": ref, "stage": "dry_run"})
    assert res["stage"] == "dry_run"
    assert res["dry_run"] is True
    assert res["pending_ref"] is None
    # 正面证据：验过的东西真跑 save 就会入队
    assert res["ok"] is True and res["would_enqueue"] is True
    assert ps.list() == [], "dry_run 不许往待批队列写任何东西"


def test_unknown_stage_is_refused_not_defaulted_to_save(store):
    """`stage="dry-run"`（连字符笔误）必须被拒，不许退化成部署。"""
    ref = _ref(store)
    res = apply(ref, stage="dry-run", store=store)
    assert res["ok"] is False
    assert res["reason"] == "unknown_stage"
    assert res["stage"] == "args"
    assert set(res["allowed_stages"]) == set(APPLY_STAGES)


def test_apply_stage_surface_matches_tool_schema():
    """MCP 工具描述里写的 stage 取值必须等于 `APPLY_STAGES`——文档与代码不许两套口径。"""
    from autoforge.af_mcp import TOOLS

    entry = next(t for t in TOOLS if t[0] == "af_apply")
    described = entry[1] + str(entry[2]["properties"]["stage"]["description"])
    for stage in APPLY_STAGES:
        assert stage in described, f"工具面没写 {stage}，DB 侧就调不到它"


def test_save_after_dry_run_lands_exactly_one_pending(store):
    """「验 → 批」时序：dry_run 不占位，随后的 save 才在队列里留下恰好一条。"""
    ref = _ref(store)
    ps = PendingStore(store.root)

    assert apply(ref, stage="dry_run", store=store)["pending_ref"] is None
    assert ps.list() == []
    res = apply(ref, stage="save", store=store)
    assert res["stage"] == "save"
    # 入队这件事以队列为准，不以返回字面量为准
    assert len(ps.list()) == 1, f"save 后队列应有 1 条，实得 {ps.list()}"
    assert res["save"]["ok"] is True


def test_premiere_code_is_not_consumed_by_dry_run(store, monkeypatch):
    """dry_run 不许烧一次性首演码：调用后 `af_premiere.consume` 必须一次都没发生。"""
    from autoforge import af_premiere

    ref = _ref(store)
    calls: list[tuple[str, str]] = []

    def _spy(code, diff_sha):
        calls.append((code, diff_sha))
        raise AssertionError("dry_run 不该走到首演码消费")

    monkeypatch.setattr(af_premiere, "consume", _spy)
    res = apply(ref, stage="dry_run", store=store, premiere_code="AF-DRYRUN-0001")
    assert calls == []
    assert res["stage"] == "dry_run"
