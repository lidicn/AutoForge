"""watch 卡判据（PM 卡 20261102）：AF-723 白名单形状＋AF-725 审批留痕＋对账两向＋health 心跳。

纪律照卡面：断言按**符号/形状**钉（⛔ 不钉行号）；两向都有牙（漂了要红、没漂不红、
空白名单仍被拒）；⛔ 不为让门绿改判据。AF-723 负控按 PM 更正口径＝断「被拒」
（`ok is False` 且码属 {LIVE_WHITELIST_REQUIRED, LIVE_ENTITY_NOT_WHITELISTED} 之一），
⛔ 不钉死单一码名。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from autoforge import af_service as svc
from autoforge import af_live
from autoforge.af_ir import Graph, load_automation
from autoforge.af_scanner import ERROR, live_preflight

SWITCH = "switch.livingroom_a1"
TRIGGER = "binary_sensor.door_a1"


def _auto(aid: str = "a1", *, name: str = "空调跟随", entity: str = SWITCH,
          trig_entity: str = TRIGGER, with_nodes: bool = True) -> dict:
    nodes = (
        [{"id": "o1", "kind": "on", "name": "o",
          "trigger": {"type": "state", "entity_id": trig_entity, "to": "on"}},
         {"id": "d1", "kind": "do", "name": "d", "adapter": "ha",
          "action": "light.turn_on", "params": {"entity_id": entity}}]
        if with_nodes else
        # 零实体引用的合法形状：IR schema 要求 nodes 非空，pass 节点不带任何实体
        [{"id": "p1", "kind": "pass", "name": "p"}]
    )
    return {"ir_version": "0.3.0", "id": aid, "name": name, "version": 1, "mode": "single",
            "nodes": nodes, "edges": []}


# ── AF-723：白名单提取同时认容器与单条 ────────────────────────────────

def _capture_cmd(monkeypatch, root: Path) -> dict:
    """Popen 打桩：记 cmd 后抛错短路（测 cmd 拼装，⛔ 不起真子进程）。"""
    captured: dict = {}

    class _Boom:
        def __init__(self, *a, **k):
            captured["cmd"] = [str(x) for x in (a[0] if a else k.get("args", []))]
            raise RuntimeError("captured-by-test")

    import subprocess as _sp

    monkeypatch.setattr(_sp, "Popen", _Boom)
    return captured


def _live_allow(captured: dict) -> list[str] | None:
    cmd = captured.get("cmd", [])
    return cmd[cmd.index("--live-allow") + 1].split(",") if "--live-allow" in cmd else None


def test_af723_single_ir_shape_yields_live_allow(tmp_path, monkeypatch):
    """腿①：单条 IR（手动 /api/watch/start 的线上形状）⇒ --live-allow 含其实体，⛔ 不许缺。

    ⚠️ 夹具里 `getattr(rec, "code", None) or rec["code"]` 的取码写法是**照抄**
    `test_start_watch_identity.py:131-133` 的既有形状（AuthCodeStore.create 返回值形状
    历史上两种都出现过），⛔ 不是新发明——下任别顺手"改漂亮"。"""
    root = tmp_path / "store"
    root.mkdir()
    captured = _capture_cmd(monkeypatch, root)
    from autoforge.af_auth import AuthCodeStore
    rec = AuthCodeStore(root / ".auth" / "auth_codes.json").create("short", ttl_minutes=30)
    svc.start_watch(_auto(), store_root=str(root), dry_live=False,
                    auth_code=getattr(rec, "code", None) or rec["code"])
    allow = _live_allow(captured)
    assert allow is not None, f"单条形状必须提出白名单：{captured.get('cmd')}"
    assert set(allow) == {TRIGGER, SWITCH}, allow


def test_af723_container_shape_equals_single(tmp_path, monkeypatch):
    """腿②：容器形状与单条形状产出逐项相等（AF-719 同族归一的相等性钉）。"""
    a = _auto()
    single = {"ir": a}  # 记录用，防 Lint 清 unused
    root1 = tmp_path / "s1"
    root2 = tmp_path / "s2"
    root1.mkdir()
    root2.mkdir()
    from autoforge.af_auth import AuthCodeStore
    codes = []
    for r in (root1, root2):
        rec = AuthCodeStore(r / ".auth" / "auth_codes.json").create("short", ttl_minutes=30)
        codes.append(getattr(rec, "code", None) or rec["code"])
    cap_single = _capture_cmd(monkeypatch, root1)
    svc.start_watch(a, store_root=str(root1), dry_live=False, auth_code=codes[0])
    cap_container = _capture_cmd(monkeypatch, root2)
    svc.start_watch({"automations": [a]}, store_root=str(root2), dry_live=False,
                    auth_code=codes[1])
    assert _live_allow(cap_single) is not None
    assert sorted(_live_allow(cap_single)) == sorted(_live_allow(cap_container))
    assert single["ir"]["id"] == "a1"


def test_af723_negative_control_no_entities_still_refused(tmp_path, monkeypatch):
    """腿③（负控）：无实体的单条 IR ⇒ 不传 --live-allow（⛔ 不许改成空/全量放行），
    且真机预检必须拒（码属 {LIVE_WHITELIST_REQUIRED, LIVE_ENTITY_NOT_WHITELISTED} 之一——
    PM 更正：None 与空集走两个码，断「被拒」不钉码名）。"""
    root = tmp_path / "store"
    root.mkdir()
    captured = _capture_cmd(monkeypatch, root)
    from autoforge.af_auth import AuthCodeStore
    rec = AuthCodeStore(root / ".auth" / "auth_codes.json").create("short", ttl_minutes=30)
    bare = _auto(with_nodes=False)
    svc.start_watch(bare, store_root=str(root), dry_live=False,
                    auth_code=getattr(rec, "code", None) or rec["code"])
    assert "--live-allow" not in captured.get("cmd", []), "无实体不许伪造白名单"
    # 「空白名单必须被拒」的不变量落在**含实体**的图上（零实体图没有可写实体可拒，
    # 空集放行是正确语义——AF-Verify 三形状表的口径）。安全面用含实体图测两档：
    # known=None 与 known=set() 都必须 fail-closed 拒，码属二者之一（⛔ 不钉死单一码名）。
    full = Graph([load_automation(_auto())])
    for known in (None, set()):
        pre = live_preflight(full, known_entities=known, token="t", confirm=True, dry_live=False)
        assert pre.ok is False, f"known_entities={known!r} 必须 fail-closed 拒"
        codes = {d.code for d in pre.diagnostics if d.level == ERROR}
        assert codes & {"LIVE_WHITELIST_REQUIRED", "LIVE_ENTITY_NOT_WHITELISTED"}, codes
    # 不变量①（无条件，AF-Verify 四形状表）：`known_entities is None ⇒ 必拒`与图里有
    # 无实体无关——把零实体图也钉上，堵「拿零实体图绕 None 之拒」的口子。
    bare_graph = Graph([load_automation(bare)])
    pre = live_preflight(bare_graph, known_entities=None, token="t", confirm=True, dry_live=False)
    assert pre.ok is False, "零实体图 + None 仍必拒（不变量①无条件）"
    codes = {d.code for d in pre.diagnostics if d.level == ERROR}
    assert "LIVE_WHITELIST_REQUIRED" in codes, codes


# ── AF-725：approve 本体的独立审计留痕 ───────────────────────────────

def test_af725_approve_has_own_audit_record(tmp_path, monkeypatch):
    from autoforge.af_store import GraphStore

    root = tmp_path / "store"
    root.mkdir()
    store = GraphStore(str(root))
    # serve 侧 catalog 替身（submit_pending 实体闸）
    cat = root / ".catalog" / "catalog.json"
    cat.parent.mkdir(parents=True, exist_ok=True)
    cat.write_text(json.dumps({"version": 1, "freshness": "", "entities": {
        SWITCH: {"state": "off", "domain": "switch"},
        TRIGGER: {"state": "off", "domain": "binary_sensor"}}}, ensure_ascii=False),
        encoding="utf-8")
    # 隔离自动起 watch 的子进程面（返回失败不影响 approve 留痕判定点）
    monkeypatch.setattr(svc, "_start_watch_impl",
                        lambda **kw: {"ok": False, "error": "isolated-by-test"})
    res = svc.submit_pending(store, "af_save", {"ir": _auto(), "name": "n"}, submitted_by="agent-a")
    op_id = res["pending"]
    out = svc.approve_pending(store, op_id, reviewer="human-b")
    assert out.get("pending") == op_id
    lines = [json.loads(x) for x in
             (root / "deploy_audit.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    mine = [e for e in lines if e.get("type") == "approve_pending_applied"]
    assert len(mine) == 1, f"approve 本体必须恰有一条独立留痕：{[e.get('type') for e in lines]}"
    data = mine[0].get("data") or {}
    assert data.get("pending") == op_id and data.get("approved_by") == "human-b"
    assert data.get("name") == "n"
    # ★ 两件事分开读：approve 那条⛔ 不含 watch 结果字段
    assert "watch_ok" not in data and "pid" not in data, data
    # 派生事件仍在（watch 起没起是它的职责）
    assert any(e.get("type") == "APPROVE_WATCH_AUTO_STARTED" for e in lines), \
        [e.get("type") for e in lines]


# ── 对账：ir_deployment_sha 与 ir_reconcile_once（两向都有牙） ─────────

def test_sha_envelope_and_order_invariant():
    one = _auto("a1")
    two = _auto("a2", name="第二台", entity="switch.b", trig_entity="binary_sensor.b")
    base = svc.ir_deployment_sha(one)                     # 单条形状
    assert base == svc.ir_deployment_sha({"automations": [one]}), "容器键包裹必须同值"
    assert base == svc.ir_deployment_sha(
        {"version": 1, "automations": [one]}), "envelope 键不算漂移"
    both_a = svc.ir_deployment_sha({"automations": [one, two]})
    both_b = svc.ir_deployment_sha({"automations": [two, one]})
    assert both_a == both_b, "顺序不算漂移"
    assert base not in ("", both_a), "内容变了 sha 必须变"
    assert svc.ir_deployment_sha(None) == "" and svc.ir_deployment_sha({}) == ""
    assert svc.ir_deployment_sha({"automations": []}) == ""


def test_reconcile_drift_and_recovery_two_way(tmp_path):
    from autoforge.af_store import GraphStore

    root = tmp_path / "store"
    root.mkdir()
    store = GraphStore(str(root))
    store.save(Graph([load_automation(_auto())]), "n1", owner="agent-a")
    merged = svc._build_merged_ir(store)
    sha0 = svc.ir_deployment_sha(merged)

    # 首轮（prev=None）：以现状为启动基线 ⇒ ⛔ 启动即假报（漂移判据的相等性自证）
    rec1, e1, r1 = af_live.ir_reconcile_once(str(root), sha0, None)
    first = dict(rec1)
    first["startup_store_sha"] = first["store_sha"]
    first["drifted"] = False
    assert first["store_sha"] == sha0
    # 基线一致 ⇒ 不漂
    rec2, e2, r2 = af_live.ir_reconcile_once(str(root), sha0, first)
    assert rec2["drifted"] is False and e2 is False and r2 is False
    # store 多了一条归档 ⇒ 漂（进入漂移）
    store.save(Graph([load_automation(_auto("a2", name="第二台", entity="switch.b",
                                             trig_entity="binary_sensor.b"))]),
               "n2", owner="agent-a")
    rec3, e3, r3 = af_live.ir_reconcile_once(str(root), sha0, rec2)
    assert rec3["drifted"] is True and e3 is True, "漂了必须红（本卡的存在理由）"
    assert rec3["drift_since"], "进入漂移必须记时点"
    # store 回到基线（删掉 n2）⇒ 恢复
    import shutil
    shutil.rmtree(store._dir("n2"))
    rec4, e4, r4 = af_live.ir_reconcile_once(str(root), sha0, rec3)
    assert rec4["drifted"] is False and r4 is True and rec4["drift_since"] is None, \
        "恢复必须喊第二声且清 drift_since"


def test_health_watch_block_shapes(tmp_path):
    from autoforge.af_store import GraphStore

    # 无 sidecar ⇒ present False（⛔ 不报错、⛔ 不装成有）
    root = tmp_path / "store"
    root.mkdir()
    store = GraphStore(str(root))
    h = svc.health(store)
    assert h["watch"] == {"present": False} or h["watch"]["present"] is False
    assert svc.health(None)["watch"]["present"] is False
    # 有 sidecar ⇒ 字段透传
    (root / svc.WATCH_RECONCILE_FILE).write_text(json.dumps({
        "watch_ir_sha": "a" * 64, "startup_store_sha": "b" * 64, "store_sha": "c" * 64,
        "matches_store": False, "drifted": True, "drift_since": "2026-11-02T00:00:00+00:00",
        "checked_at": "2099-01-01T00:00:00+00:00", "interval_s": 600.0}), encoding="utf-8")
    w = svc.health(store)["watch"]
    assert w["present"] is True and w["drifted"] is True and w["stale"] is False
    assert w["store_sha"] == "c" * 64
    # 心跳过期 ⇒ stale 现形（watch 死了没人再写，⛔ 旧读数冒充新鲜）
    (root / svc.WATCH_RECONCILE_FILE).write_text(json.dumps({
        "watch_ir_sha": "a", "startup_store_sha": "b", "store_sha": "c",
        "matches_store": False, "drifted": True, "checked_at": "2020-01-01T00:00:00+00:00",
        "interval_s": 600.0}), encoding="utf-8")
    w2 = svc.health(store)["watch"]
    assert w2["stale"] is True and w2["alive"] is False
    # 坏 sidecar ⇒ 回落 present False（⛔ 500）
    (root / svc.WATCH_RECONCILE_FILE).write_text("{not-json", encoding="utf-8")
    assert svc.health(store)["watch"]["present"] is False


def test_reconcile_thread_first_cycle_writes_baseline(tmp_path, monkeypatch):
    """线程面：首轮以 store 现状为启动基线并**写出** sidecar（health 立刻可读），⛔ 首轮即报漂。"""
    from autoforge.af_store import GraphStore

    root = tmp_path / "store"
    root.mkdir()
    store = GraphStore(str(root))
    store.save(Graph([load_automation(_auto())]), "n1", owner="agent-a")
    sha0 = svc.ir_deployment_sha(svc._build_merged_ir(store))
    stop = threading.Event()
    # 用一次性循环：直接驱动循环体一轮（不真等 600s）——把间隔临时置 0，靠 stop 收
    monkeypatch.setattr(svc, "WATCH_RECONCILE_INTERVAL_S", 0.05)
    t = af_live.start_ir_reconcile(str(root), sha0, stop)
    deadline = threading.Event()
    import time as _t
    end = _t.monotonic() + 3.0
    rec_path = Path(root) / svc.WATCH_RECONCILE_FILE
    while _t.monotonic() < end and not rec_path.is_file():
        _t.sleep(0.05)
    stop.set()
    t.join(timeout=2.0)
    assert rec_path.is_file(), "对账线程必须落 sidecar（health 的数据源）"
    data = json.loads(rec_path.read_text(encoding="utf-8"))
    assert data["startup_store_sha"] == data["store_sha"], "首轮基线＝启动时现状"
    assert data["drifted"] is False, "⛔ 首轮不许报漂（拿自己当参照系）"
    assert data["watch_ir_sha"] == sha0
