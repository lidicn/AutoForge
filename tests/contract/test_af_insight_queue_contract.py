"""ADM 联动契约测试 · MA 洞察的**只落盘队列**（DCD 20261002 §三 ④A）。

裁定原文：「先持久化到独立队列，仍不可直接部署（新增只落盘的 ask 档，
approve 之后才进 `af_pending`）」。本文件把这条冻结成机器判据，重点是三件：

1. **重启不丢**：洞察落 `{store_root}/insight_proposals/pending/*.json`，换一个队列实例读得到；
2. **交接不等于部署**：approve 只在 `af_pending` 里生成一条待批操作，归档目录里
   不得出现任何已部署的图（`{name}/v{n}.json`）；
3. **不猜**：没有 IR / 一条提案塞多条自动化 / 重复判定，一律拒，且拒完记录还在 pending。

队列满时**拒绝入队并报错**（第 4 条），因为静默丢弃等价于"MA 从没投过这条"。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from autoforge import af_service
from autoforge.af_api import build_app
from autoforge.af_insight_queue import InsightQueue, InsightQueueFull
from autoforge.af_mqtt_bridge import INSIGHT_CONF_CAP, INSIGHTS_TOPIC, AfMqttBridge, make_durable_ask_sink

EXAMPLE_IR = Path(__file__).resolve().parents[2] / "examples" / "ir" / "case01_day_light.json"


class FakeClient:
    def __init__(self) -> None:
        self.published: list[dict] = []
        self.subscribed: list[str] = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
        return SimpleNamespace(rc=0)

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)
        return (0, [1])

    def will_set(self, *a, **k):
        return None


def _client(root: Path, *, readonly: bool = False) -> TestClient:
    app = build_app(store_root=str(root), examples_dir=None, readonly=readonly)
    return TestClient(app)


def _seed(root: Path, *, ir: dict | list | None, conf: float = 0.4, hid: str = "h-1") -> str:
    """用生产同一个工厂投一条洞察，返回 proposal_id。"""
    sink = make_durable_ask_sink(store_root=root)
    rec = sink.submit(hypothesis_id=hid, natural_language="天黑关廊灯", conf=conf, suggested_ir=ir)
    return rec.proposal_id


# ── 持久性 ──────────────────────────────────────────────────────────────
def test_insight_lands_on_disk_and_survives_a_new_process(tmp_path):
    pid = _seed(tmp_path, ir=json.loads(EXAMPLE_IR.read_text(encoding="utf-8")))

    files = list((tmp_path / "insight_proposals" / "pending").glob("*.json"))
    assert len(files) == 1, f"期望恰好一个落盘文件，实际 {files}"
    assert json.loads(files[0].read_text(encoding="utf-8"))["proposal_id"] == pid

    # "重启"= 换一个新的队列实例指向同一目录：读数必须还在
    again = InsightQueue(tmp_path / "insight_proposals")
    rows = again.list_pending()
    assert [r.proposal_id for r in rows] == [pid]
    assert rows[0].conf == pytest.approx(0.4) and rows[0].status == "pending"


def test_bridge_inbound_writes_the_durable_queue_with_capped_conf(tmp_path):
    sink = make_durable_ask_sink(store_root=tmp_path)
    bridge = AfMqttBridge(FakeClient(), proposal_sink=sink)
    result = bridge.handle_message(None, None, SimpleNamespace(
        topic=INSIGHTS_TOPIC,
        payload=json.dumps({"hypothesis_id": "h-live", "natural_language": "走廊灯半夜降亮", "conf": 0.99}).encode()))

    assert result["handled"] is True
    rows = InsightQueue(tmp_path / "insight_proposals").list_pending()
    assert len(rows) == 1
    # 高置信也不许换到可自动部署的待遇：封顶值必须落在 ask 带内
    assert rows[0].conf == pytest.approx(INSIGHT_CONF_CAP)
    assert rows[0].conf < 0.6, "封顶后仍须落在 ask 带（< SHADOW_LOW=0.60）"


def test_full_queue_refuses_instead_of_dropping(tmp_path):
    queue = InsightQueue(tmp_path / "insight_proposals", limit=1)
    from autoforge.af_insight_queue import InsightRecord

    first = InsightRecord("p1", "h1", "一条", 0.4, "ma", 1000.0)
    queue.append(first)
    with pytest.raises(InsightQueueFull):
        queue.append(InsightRecord("p2", "h2", "二条", 0.4, "ma", 1001.0))
    # 拒绝之后原记录必须还在（"满了"不等于"可以丢旧的"）
    assert [r.proposal_id for r in queue.list_pending()] == ["p1"]


# ── 交接：approve ≠ 部署 ────────────────────────────────────────────────
def test_approve_moves_into_pending_queue_without_deploying(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    ir = json.loads(EXAMPLE_IR.read_text(encoding="utf-8"))
    pid = _seed(tmp_path, ir={"automations": [ir]})
    client = _client(tmp_path)

    r = client.post("/api/insights/approve", json={"proposal_id": pid, "reviewer": "sp"})
    body = r.json()
    assert r.status_code == 200, body
    op_id = body["pending"]
    assert op_id

    # 待批队列里确实多了这一条
    from autoforge.af_pending import PendingStore

    ops = PendingStore(str(tmp_path)).list()
    assert [o["op_id"] for o in ops] == [op_id]
    assert ops[0]["tool"] == "af_save"
    # 铁律：这一步**没有**部署——归档目录不得出现任何 `{name}/v{n}.json`
    assert list(tmp_path.glob("*/v*.json")) == [], "approve 不该落盘部署"

    # 提案本身移出 pending、进 decided，且带判定人
    q = InsightQueue(tmp_path / "insight_proposals")
    assert q.list_pending() == []
    decided = q.list_decided()
    assert [(d.proposal_id, d.status, d.decided_by) for d in decided] == [(pid, "approved", "sp")]


def test_approve_twice_is_a_conflict_not_a_second_op(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    ir = json.loads(EXAMPLE_IR.read_text(encoding="utf-8"))
    pid = _seed(tmp_path, ir=ir)          # 单图（不带 automations 包装）也要能交接
    client = _client(tmp_path)

    assert client.post("/api/insights/approve", json={"proposal_id": pid}).status_code == 200
    r = client.post("/api/insights/approve", json={"proposal_id": pid})
    assert r.status_code == 409, r.text
    assert "不重复处理" in r.json()["detail"]


@pytest.mark.parametrize("ir,expect", [
    (None, "没有编译后的 IR"),
    ({"automations": [{"name": "a"}, {"name": "b"}]}, "一次 approve 只交接一条"),
])
def test_approve_refuses_to_invent_or_to_guess(tmp_path, monkeypatch, ir, expect):
    """反证用：把服务端"猜一条 IR / 猜要哪条"的分支删掉，这两条就会从 400 变 200。"""
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    pid = _seed(tmp_path, ir=ir)
    client = _client(tmp_path)

    r = client.post("/api/insights/approve", json={"proposal_id": pid})
    assert r.status_code == 400, r.text
    assert expect in r.json()["detail"]
    from autoforge.af_pending import PendingStore

    assert PendingStore(str(tmp_path)).list() == []
    # 被拒的提案仍在 pending：拒绝不等于消失
    assert [rec.proposal_id for rec in InsightQueue(tmp_path / "insight_proposals").list_pending()] == [pid]


def test_approve_of_unknown_proposal_is_404(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    client = _client(tmp_path)
    r = client.post("/api/insights/approve", json={"proposal_id": "nope"})
    assert r.status_code == 404, r.text
    assert "查不到" in r.json()["detail"]


def test_reject_marks_decided_and_creates_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    ir = json.loads(EXAMPLE_IR.read_text(encoding="utf-8"))
    pid = _seed(tmp_path, ir=ir)
    client = _client(tmp_path)

    assert client.post("/api/insights/reject", json={"proposal_id": pid, "reviewer": "sp"}).status_code == 200
    q = InsightQueue(tmp_path / "insight_proposals")
    assert q.list_pending() == []
    assert [d.status for d in q.list_decided()] == ["rejected"]
    from autoforge.af_pending import PendingStore

    assert PendingStore(str(tmp_path)).list() == []


# ── 只读实例（铁律 #6）：读得到，写不进 ──────────────────────────────────
def test_readonly_instance_lists_but_refuses_approve(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    ir = json.loads(EXAMPLE_IR.read_text(encoding="utf-8"))
    pid = _seed(tmp_path, ir=ir)
    client = _client(tmp_path, readonly=True)

    r = client.get("/api/insights/pending")
    assert r.status_code == 200
    assert [p["proposal_id"] for p in r.json()["proposals"]] == [pid]
    assert r.json()["queue"]["limit"] >= 1

    assert client.post("/api/insights/approve", json={"proposal_id": pid}).status_code == 503


def test_service_layer_has_no_deployer_for_the_queue():
    """队列模块不许持有任何部署把手（结构约束，不靠自觉）。"""
    import autoforge.af_insight_queue as q

    imports = [
        line.strip()
        for line in Path(q.__file__).read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(("from .", "from autoforge", "import autoforge"))
    ]
    assert imports, "本测试要判的是真实 import 列表，读不到就等于没判"
    for banned in ("af_apply", "af_pending", "af_service", "af_deploy", "af_executor"):
        assert not any(banned in line for line in imports), f"af_insight_queue 引了 {banned}：交接只能由 L2 显式做"
    assert not hasattr(q.InsightQueue, "approve") and not hasattr(q.InsightQueue, "deploy")


def test_undecodable_record_is_reported_not_skipped_silently(tmp_path):
    root = tmp_path / "insight_proposals"
    (root / "pending").mkdir(parents=True)
    (root / "pending" / "0000000000001-bad.json").write_text("{这不是 JSON", encoding="utf-8")

    q = InsightQueue(root)
    assert q.list_pending() == []
    assert len(q.unreadable) == 1 and "bad.json" in q.unreadable[0]
    assert q.stats()["unreadable"], "坏文件必须进 stats，否则'投过'看起来和'没投过'一样"


def test_af_service_exports_the_handoff():
    assert callable(af_service.approve_insight) and callable(af_service.reject_insight)
