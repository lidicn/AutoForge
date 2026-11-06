# -*- coding: utf-8 -*-
"""运行期事实落盘判据（PM 卡 2026-11-04）：触发计数 ＋ 试演/canary 状态。

覆盖（编号带卡名）：
  D1 触发记账 + trigger_7d 计数（同一天累加）
  D2 group 触发器命中天然计入（不在匹配处另判——记在 _try_fire 单一出口）
  D3 **口径洞钉死（PM 批注①）**：trigger_7d 由按日计数器 + 窗口内累加，⛔ 不因日键封顶
     被裁而偏低；>7 天外的日键裁掉后窗口内计数不变（差分自证：造 30 天历史 vs 7 天内计数）
  D4 试演/canary 状态落盘（record_trial → snapshot.trial）
  D5 af_service 卡片从 runtime.json 现读三字段；缺文件/损坏 ⇒ fail-soft 回落 None/None/0
  D6 单一出口接线（源码钉）：scheduler._try_fire 有 runtime_facts 记账点；executor 有 trial 记录
  D7 记账失败不阻断主流程（写只读目录 ⇒ record 不抛）
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from autoforge import af_runtime_facts as RF
from autoforge.af_runtime_facts import RuntimeFacts


@pytest.fixture()
def root(tmp_path):
    return tmp_path


# ── D1 触发计数 ────────────────────────────────────────────────────
def test_d1_record_trigger_counts(root):
    rf = RuntimeFacts(root, "arch1")
    assert rf.snapshot()["trigger_7d"] == 0
    rf.record_trigger(automation_id="arch1", node_id="a1", entity_id="sensor.x", via="state")
    rf.record_trigger(automation_id="arch1", node_id="a1", entity_id="sensor.y", via="state")
    snap = rf.snapshot()
    assert snap["trigger_7d"] == 2, f"同日两次触发应计 2，实得 {snap['trigger_7d']}"
    assert snap["last_triggered"] is not None, "last_triggered 必须落值"


# ── D2 group 命中计入（via=group，记账单点不区分形状）────────────────
def test_d2_group_trigger_counted(root):
    rf = RuntimeFacts(root, "arch2")
    rf.record_trigger(automation_id="arch2", node_id="a1", entity_id="door", via="group")
    assert rf.snapshot()["trigger_7d"] == 1, "group 触发命中必须计入（记账在 _try_fire 单点）"


# ── D3 口径洞：封顶裁老日键不压低窗口内计数（PM 批注①）──────────────
def test_d3_trigger_7d_not_undercounted_by_pruning(root):
    """造 30 天历史（每天 1 条）+ 7 天内 3 条 ⇒ trigger_7d 必 = 3（窗口内），
    且日键总数被裁到 <=_MAX_DAYS（封顶生效），但**窗口内计数不受裁影响**。"""
    rf = RuntimeFacts(root, "arch3")
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    # 30 天前..1 天前，每天 1 条（都 >7 天外或窗口内交替）
    for i in range(30, 0, -1):
        rf.record_trigger(automation_id="arch3", node_id="a1", via="state",
                          at=now - timedelta(days=i))
    # 窗口内（今天）再加 3 条
    for _ in range(3):
        rf.record_trigger(automation_id="arch3", node_id="a1", via="state", at=now)
    data = json.loads((root / "arch3.runtime.json").read_text(encoding="utf-8"))
    days = data.get("trigger_days", {})
    assert len(days) <= RF._MAX_DAYS, f"日键应被封顶到 <= {RF._MAX_DAYS}，实得 {len(days)}"
    snap = rf.snapshot(now=now)
    # 窗口 = 今天往前 6 天，共 7 天。构造里 i=1..6 落在窗口内各 1 条 + 今天 3 条 = 9
    assert snap["trigger_7d"] == 9, (
        f"窗口内真实发生次数必须是 9（6 天各1 + 今天3），不许因封顶偏低；实得 {snap['trigger_7d']}"
    )


def test_d3b_window_excludes_old_days(root):
    rf = RuntimeFacts(root, "arch3b")
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    rf.record_trigger(automation_id="arch3b", node_id="a1", via="state",
                      at=now - timedelta(days=10))  # 窗口外
    rf.record_trigger(automation_id="arch3b", node_id="a1", via="state", at=now)  # 窗口内
    assert rf.snapshot(now=now)["trigger_7d"] == 1, "10 天前不计入 7 天窗口"


# ── D4 试演状态 ────────────────────────────────────────────────────
def test_d4_trial_state_recorded(root):
    rf = RuntimeFacts(root, "arch4")
    assert rf.snapshot()["trial"] is None
    rf.record_trial(state="observing", automation_id="arch4")
    t = rf.snapshot()["trial"]
    assert t and t["state"] == "observing" and t["automation_id"] == "arch4"
    rf.clear_trial()
    assert rf.snapshot()["trial"] is None, "clear_trial 后应清空"


# ── D5 af_service 卡片现读 + fail-soft ──────────────────────────────
def _mk_store(tmp_path):
    from autoforge.af_store import GraphStore
    from autoforge.af_ir import load_graph

    store = GraphStore(str(tmp_path / "store"))
    ir = {
        "ir_version": "0.2.1", "id": "a1arc", "name": "a1arc", "version": 1, "mode": "single",
        "nodes": [{"id": "p1", "kind": "pass", "name": "p"}], "edges": [],
    }
    store.save(load_graph(ir), "a1arc", note="", owner="x")
    return store


def test_d5_automation_card_reads_runtime_facts(tmp_path):
    from autoforge.af_service import _runtime_facts_fields

    store = _mk_store(tmp_path)
    # 无侧车 ⇒ 回落旧值（D10 第二面：`last_met`/`last_dispatched_at` 一并回落 None）
    fb = _runtime_facts_fields(store, "a1arc")
    assert fb == {"trial": None, "last_triggered": None, "trigger_7d": 0,
                  "last_met": None, "last_dispatched_at": None}
    # 写侧车 ⇒ 现读
    rf = RuntimeFacts(store.root, "a1arc")
    rf.record_trigger(automation_id="a1arc", node_id="a1", via="group")
    rf.record_trial(state="observing", automation_id="a1arc")
    got = _runtime_facts_fields(store, "a1arc")
    assert got["trigger_7d"] == 1 and got["trial"]["state"] == "observing"
    # D10 第二面：record_met 后详情带 last_met/last_dispatched_at
    rf.record_met(met=True)
    got2 = _runtime_facts_fields(store, "a1arc")
    assert got2["last_met"] is True and got2["last_dispatched_at"] is not None


def test_d5b_corrupt_sidecar_failsoft(tmp_path):
    from autoforge.af_service import _runtime_facts_fields

    store = _mk_store(tmp_path)
    (store.root / "a1arc.runtime.json").write_text("NOT JSON{{{", encoding="utf-8")
    got = _runtime_facts_fields(store, "a1arc")
    assert got == {"trial": None, "last_triggered": None, "trigger_7d": 0,
                   "last_met": None, "last_dispatched_at": None}, "损坏必须 fail-soft 回落"


# ── D6 单一出口接线（源码钉）────────────────────────────────────────
def test_d6_scheduler_has_single_exit_accounting():
    import inspect

    from autoforge.af_scheduler import Scheduler

    src = inspect.getsource(Scheduler._try_fire)
    assert "runtime_facts" in src or "_record_trigger_facts" in src, "_try_fire 必须含运行期事实记账点"
    # D10（2026-11-06）改版：`_try_fire` 现在有**两条**记账点——
    # ① 抑制路径（计数照记、不派发，D10 卡）② spawn 成功路径（单一出口，防重复计数）。
    # 记账实现收敛到 `Scheduler._record_trigger_facts` 一个方法（⛔ 不各写一份）。
    # 旧断言 `spawn < runtime_facts` 假设只有一条记账点且内联在 _try_fire，D10 加抑制腿后不再成立。
    # 新断言：**成功后仍有记账调用**（且在 spawn 之后）＋**抑制腿也照记**（计数是事实）。
    after_spawn = src.split("instances.spawn", 1)[1]
    assert "_record_trigger_facts" in after_spawn, "成功派发的记账必须在 spawn 之后（单出口）"
    assert "计数照记" in src, "D10：抑制腿必须照记触发计数（计数是事实、派发是决策）"


def test_d6b_executor_records_trial():
    import inspect

    from autoforge.af_executor import NodeExecutor

    src = inspect.getsource(NodeExecutor._do)
    assert "_record_trial" in src, "_do 的 canary 分支必须记试演状态"
    assert "observing" in src and "verified" in src, "试演状态要覆盖 observing/verified"


# ── D7 记账失败不抛（写只读目录）────────────────────────────────────
def test_d7_write_failure_does_not_raise(tmp_path):
    # 指向一个不可写的路径（把 root 设为已存在文件的子路径）
    f = tmp_path / "afile"
    f.write_text("x", encoding="utf-8")
    rf = RuntimeFacts(f, "arch7")  # root 是文件 ⇒ mkdir 会失败
    # 不抛即通过（内部吞成 warning）
    rf.record_trigger(automation_id="arch7", node_id="a1", via="state")
    rf.record_trial(state="observing", automation_id="arch7")
