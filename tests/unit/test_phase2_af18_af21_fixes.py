"""第二期第十六轮（AF18）与第二十轮（AF21）的回归。

判定口径沿用本二期第二轮立下的那条：**「拒绝写入」和「丢数据」用磁盘内容分不出来**，
必须按异常类型判，并且 try 要包住**公开入口**（`InsightQueue.append()`／装饰后的 `executor._do`），
而不是内部助手——否则护栏装在调用方永远走不到的阶段。

AF21 只收影子档那半边：`_do` 的返回契约是「可用边集合；None = 实例已终止」，
ask 档 return None 缺的是"挂起"而不是"前进"，那半边要动 `resume`／`pending_confirm` 的
生命周期语义，不自决，递 DCD（见 `docs/可观测性清单.md` §七 与执行记录 §二之一百零二）。
"""
from pathlib import Path

import pytest

from autoforge.af_insight_queue import (
    InsightAlreadyDecided,
    InsightQueue,
    InsightRecord,
    _atomic_write,
)
from conftest import (
    FakeAutomation,
    FakeClock,
    FakeExecutor,
    FakeInstance,
    FakeNode,
    FakeStates,
    make_conf,
    make_recorder,
    make_shadow,
)

REPO = Path(__file__).resolve().parents[2]
SHADOW_SRC = REPO / "src" / "autoforge" / "af_shadow.py"
EXECUTOR_SRC = REPO / "src" / "autoforge" / "af_executor.py"


def _record(pid: str = "p1", received_at: float = 1000.0, conf: float = 0.5) -> InsightRecord:
    return InsightRecord(
        proposal_id=pid,
        hypothesis_id="h1",
        natural_language="把客厅灯打开",
        conf=conf,
        source="ma",
        received_at=received_at,
    )


# ---- AF18：已判定的提案不许静默变回待决 ------------------------------------ #


def test_repeat_append_after_decision_is_refused_and_verdict_holds(tmp_path):
    q = InsightQueue(tmp_path / "insight_proposals")
    q.append(_record())
    judged = q.move_to(_record(), status="rejected", decided_by="owner")
    assert q.list_pending() == []

    with pytest.raises(InsightAlreadyDecided):
        q.append(_record())  # 上游（MA）重复推同一条 proposal_id

    assert q.list_pending() == []  # 没有第二条 pending：不是"拒了但照样入队"
    assert q.get("p1").status == "rejected"
    assert judged.status == "rejected"


def test_refusal_leaves_no_pending_file_and_names_the_decided_one(tmp_path):
    root = tmp_path / "insight_proposals"
    q = InsightQueue(root)
    q.append(_record())
    q.move_to(_record(), status="rejected", decided_by="owner")
    pending_dir = root / "pending"
    before = sorted(p.name for p in pending_dir.glob("*.json"))
    with pytest.raises(InsightAlreadyDecided) as exc:
        q.append(_record())
    assert sorted(p.name for p in pending_dir.glob("*.json")) == before == []
    assert "decided" in str(exc.value)  # 报的是"去哪找那份判定"，不是空话


def test_crash_window_double_copy_reads_decided_first(tmp_path):
    """`move_to` 是「先写 decided、后删 pending」（顺序是对的）；中间断电留下同名双份时，
    旧口径按 pending 优先读到的是**判定前**的状态 ⇒ 判定结果静默回退。"""
    root = tmp_path / "insight_proposals"
    q = InsightQueue(root)
    record = _record()
    q.append(record)
    q.move_to(record, status="rejected", decided_by="owner")

    ghost = root / "pending" / f"{int(record.received_at * 1000):013d}-p1.json"
    _atomic_write(ghost, record.to_dict())  # 放回"还没删掉"的那一份

    assert q.get("p1").status == "rejected"
    assert len(q.list_pending()) == 1  # 幽灵不当作没发生：面板仍看得见


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_decided_ids_of_either_verdict_block_repend(tmp_path, status):
    q = InsightQueue(tmp_path / "insight_proposals")
    record = _record()
    q.append(record)
    q.move_to(record, status=status, decided_by="owner")
    with pytest.raises(InsightAlreadyDecided):
        q.append(_record())


def test_undecided_repeat_append_still_overwrites(tmp_path):
    """反例：还没判定时，二次推送照旧覆盖 pending——收紧只针对"已判定"。"""
    q = InsightQueue(tmp_path / "insight_proposals")
    q.append(_record())
    q.append(_record(conf=0.42))
    rows = q.list_pending()
    assert len(rows) == 1 and rows[0].conf == 0.42


def test_fresh_proposal_id_is_unaffected(tmp_path):
    q = InsightQueue(tmp_path / "insight_proposals")
    q.append(_record(pid="p1"))
    q.move_to(_record(pid="p1"), status="rejected", decided_by="owner")
    q.append(_record(pid="p2", received_at=2000.0))
    assert [r.proposal_id for r in q.list_pending()] == ["p2"]
    assert q.get("p2").status == "pending"


def test_unknown_proposal_id_still_returns_none(tmp_path):
    q = InsightQueue(tmp_path / "insight_proposals")
    assert q.get("nope") is None


# ---- AF21：影子档的返回值方向 ---------------------------------------------- #


def _band_setup(conf_value: float):
    clock = FakeClock()
    conf = make_conf({"a1": conf_value})
    states = FakeStates()
    rec = make_recorder(conf, clock)
    shadow = make_shadow(conf, clock, rec, states)
    inst = FakeInstance(automation=FakeAutomation(id="a1"))
    node = FakeNode(action="turn_on", entities=["light.study"], expected={"light.study": "on"})
    return clock, conf, states, rec, shadow, inst, node


def test_shadow_band_returns_then_edge_so_the_graph_keeps_advancing():
    *_, shadow, inst, node = _band_setup(0.75)
    executor = FakeExecutor()
    shadow.install(executor)
    assert executor._do(inst, node) == {"then"}
    assert executor.calls == []  # 一条上线字节都不发
    assert len(shadow.log.by_automation("a1")) == 1


def test_shadow_band_replays_every_do_node_not_only_the_first():
    """报告的确证面：多动作自动化原先只回放得到第一个 do，而 shadow_log 是转正证据。"""
    *_, shadow, inst, node = _band_setup(0.75)
    executor = FakeExecutor()
    shadow.install(executor)
    for _ in range(3):
        assert executor._do(inst, node) == {"then"}
    assert len(shadow.log.by_automation("a1")) == 3
    assert executor.calls == []


def test_auto_band_still_passes_through_untouched():
    *_, shadow, inst, node = _band_setup(0.95)
    executor = FakeExecutor()
    binding = shadow.install(executor)
    assert executor._do(inst, node) == "executed:turn_on"
    assert shadow.log.by_automation("a1") == []
    binding.restore()


def test_ask_band_still_returns_none_is_registered_not_fixed():
    """ask 档半边**未修**（要动 resume 生命周期，递 DCD）。这一条钉的是现状：
    提案开出来了、适配器没被调用、返回值仍是 None。修的那天这条会红，是预期的红。"""
    *_, shadow, inst, node = _band_setup(0.30)
    asks: list = []
    shadow.ask_handler = lambda **kw: asks.append(kw) or "ask-1"
    executor = FakeExecutor()
    shadow.install(executor)
    assert executor._do(inst, node) is None
    assert executor.calls == []
    assert asks and asks[0]["automation_id"] == "a1"


# ---- 返回契约的形状锚点（整行代码，不靠散文）-------------------------------- #


def test_return_contract_anchors_are_pinned():
    shadow_src = SHADOW_SRC.read_text(encoding="utf-8")
    executor_src = EXECUTOR_SRC.read_text(encoding="utf-8")
    # 影子档给的是"前进"边
    assert 'return {"then"}' in shadow_src
    # 驱动把 None 读成"已终止"——这两行同时在，AF21 的判据才成立
    assert "if kinds is None:" in executor_src
