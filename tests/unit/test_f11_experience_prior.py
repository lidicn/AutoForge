"""v2.4 / F11①：经验库接进实体解析（先验破同分）。

路线图原文写「接进 executor 实体解析/动作选择」，但源码核实：`af_executor` 既不解析实体
也不选择动作（只有漂移软失效 + adapter 键直查）。真正的实体解析在 `af_catalog.resolve`。
故本项落在真实接入点：经验库「被成功使用次数」作为**同级破同分**先验。

纪律：先验只在同一置信度 / 同可动作性 / 同集成优选 / 同离线状态 / 同相似度之后生效，
**不覆盖任何既有排序语义**；无经验文件或未观测 → 退化为 0，行为与接入前完全一致。
"""

import json

from autoforge.af_catalog import DeviceCatalog
from autoforge.af_experience import ExperienceStore, EXPERIENCE_PRIOR_WEIGHT_CAP
from autoforge.af_ir import load_graph

IR = {
    "ir_version": "0.2.1", "id": "e", "name": "e", "version": 1, "mode": "single",
    "nodes": [
        {"id": "a", "kind": "on", "trigger": {"type": "state", "entity_id": "binary_sensor.m", "to": "on"}},
        {"id": "d", "kind": "do", "adapter": "ha", "action": "light.turn_on", "params": {"entity_id": "light.x"}},
    ],
    "edges": [{"from": "a", "to": "d", "kind": "then"}],
}


def _write_catalog(root) -> None:
    """写一份「两个完全同分候选」的目录：同名、同域、同集成、同在线状态。

    二者 `_score` 均为 `(0.0, friendly_name_exact, high)`，前 5 级排序键全等，
    因此最终次序只由「经验先验」或「entity_id 兜底」决定——正是本项要锁定的行为。
    """
    def meta(eid):
        return {
            "entity_id": eid, "friendly_name": "书房灯", "domain": "light",
            "state": "off", "platform": "demo", "integration": "demo",
            "integration_source": "local", "offline_now": False, "attributes": {},
        }

    cat_dir = root / ".catalog"
    cat_dir.mkdir(parents=True, exist_ok=True)
    (cat_dir / "catalog.json").write_text(
        json.dumps({
            "version": 1, "freshness": "",
            "entities": {"light.aaa": meta("light.aaa"), "light.bbb": meta("light.bbb")},
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_experience(root, counts: dict) -> None:
    (root / "experience.json").write_text(
        json.dumps({
            "observed": sum(counts.values()),
            "pairs": {},
            "entities": dict(counts),
            "patterns": {"kinds": {}, "adapters": {}, "modes": {}},
        }, ensure_ascii=False),
        encoding="utf-8",
    )


# ── 经验库定向读 API（F11① 的数据面）────────────────────────────────────
def test_entity_counts_and_single_count(tmp_path):
    store = ExperienceStore(tmp_path)
    g = load_graph(IR)
    store.observe(g, ok=True)
    store.observe(g, ok=True)
    assert store.entity_count("light.x") == 2
    assert store.entity_count("binary_sensor.m") == 2
    assert store.entity_count("light.never_used") == 0
    assert store.entity_counts() == {"binary_sensor.m": 2, "light.x": 2}


def test_co_occurrences_directional(tmp_path):
    """共现是无向去序存储，定向查询能还原「与谁一起用过」。"""
    store = ExperienceStore(tmp_path)
    store.observe(load_graph(IR), ok=True)
    assert store.co_occurrences("light.x") == [{"entity_id": "binary_sensor.m", "count": 1}]
    assert store.co_occurrences("binary_sensor.m") == [{"entity_id": "light.x", "count": 1}]
    assert store.co_occurrences("light.unknown") == []


# ── 经验→先验派生（F11②）────────────────────────────────────────────
def test_empirical_prior_zero_without_experience(tmp_path):
    """零经验 → (0.0, 0.0)，调用方据此不注入先验。"""
    store = ExperienceStore(tmp_path)
    assert store.empirical_prior([], []) == (0.0, 0.0)
    assert store.empirical_prior(["light.never", "switch.x"], ["ha", "xiaomi"]) == (0.0, 0.0)


def test_empirical_prior_rises_and_saturates(tmp_path):
    g = load_graph(IR)
    # 从 IR 字典提取实体/适配器（Graph 无 reads()/writes()，那在 Automation 上）
    entities: set = set()
    adapters: set = set()
    for node in IR["nodes"]:
        if node["kind"] == "on" and node.get("trigger"):
            entities.add(node["trigger"].get("entity_id"))
        if node["kind"] == "do":
            adapters.add(node.get("adapter"))
            eid = (node.get("params") or {}).get("entity_id")
            if eid:
                entities.add(eid)
    entities.discard(None)
    store = ExperienceStore(tmp_path)
    store.observe(g, ok=True)
    store.observe(g, ok=True)
    mean1, w1 = store.empirical_prior(entities, adapters)
    assert 0.0 < mean1 < 1.0
    assert w1 > 0
    # 更多使用 → mean 更接近 1、weight 更大
    for _ in range(30):
        store.observe(g, ok=True)
    mean2, w2 = store.empirical_prior(entities, adapters)
    assert mean2 > mean1
    assert w2 >= w1
    assert mean2 < 1.0  # 饱和仍严格 < 1
    # 权重封顶（防高频经验碾压真实时序证据）
    for _ in range(300):
        store.observe(g, ok=True)
    _, w3 = store.empirical_prior(entities, adapters)
    assert w3 <= EXPERIENCE_PRIOR_WEIGHT_CAP


# ── 先验接入 catalog 排序（F11① 的消费面）──────────────────────────────
def test_no_experience_file_falls_back_to_entity_id(tmp_path):
    """无经验文件：先验退化，次序仍由 entity_id 兜底（与接入前一致）。"""
    _write_catalog(tmp_path)
    res = DeviceCatalog(tmp_path).resolve("书房灯")
    assert [c["entity_id"] for c in res["candidates"]] == ["light.aaa", "light.bbb"]


def test_experience_prior_breaks_tie(tmp_path):
    """同分候选：历史上被成功使用次数多的实体排在前面。"""
    _write_catalog(tmp_path)
    _write_experience(tmp_path, {"light.aaa": 1, "light.bbb": 5})
    res = DeviceCatalog(tmp_path).resolve("书房灯")
    assert [c["entity_id"] for c in res["candidates"]] == ["light.bbb", "light.aaa"]


def test_experience_prior_reversed_counts(tmp_path):
    """反向计数 → 次序随之反转（证明是经验在起作用，而非 entity_id 偶然）。"""
    _write_catalog(tmp_path)
    _write_experience(tmp_path, {"light.aaa": 9, "light.bbb": 2})
    res = DeviceCatalog(tmp_path).resolve("书房灯")
    assert [c["entity_id"] for c in res["candidates"]] == ["light.aaa", "light.bbb"]


def test_experience_prior_does_not_override_confidence(tmp_path):
    """先验不得越过置信度：精确名（high）必须压过仅 entity_id 子串命中（low）的经验宠儿。"""
    cat_dir = tmp_path / ".catalog"
    cat_dir.mkdir(parents=True, exist_ok=True)

    def meta(eid, fname, domain="light"):
        return {
            "entity_id": eid, "friendly_name": fname, "domain": domain,
            "state": "off", "platform": "demo", "integration": "demo",
            "integration_source": "local", "offline_now": False, "attributes": {},
        }

    (cat_dir / "catalog.json").write_text(
        json.dumps({
            "version": 1, "freshness": "",
            "entities": {
                # 友好名不叫「书房灯」，只能靠 entity_id 子串命中 → low
                "light.书房灯_z": meta("light.书房灯_z", "顶灯"),
                "light.exact": meta("light.exact", "书房灯"),
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    # 给 low 候选灌极高的经验计数，企图靠先验翻盘
    _write_experience(tmp_path, {"light.书房灯_z": 999})
    res = DeviceCatalog(tmp_path).resolve("书房灯")
    top = res["candidates"][0]
    assert top["entity_id"] == "light.exact", "置信度必须优先于经验先验"
    assert top["confidence"] == "high"
