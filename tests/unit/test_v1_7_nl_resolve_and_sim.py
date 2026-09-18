"""v1.7.1：NL 实测（PROJECT-20260917-AF-NL）暴露问题的修复单测。

以「真实家庭目录 + 真实提示词」为蓝本的两类修复：

**A. `resolve` 解析鲁棒性**（报告 §7.2 P0-1 / P1-4）
1. 区域+功能组合查询：`"书房光照"` → 区域「书房」+「光照」；
2. 后缀剥离重试：`"油烟机灯光"` → `"油烟机"`；
3. 只读兜底升级：`"客厅电视"` 原样只命中只读 sensor → 用功能词「电视」重查出可控 media_player；
4. 可动作域优先：同置信度下 switch 排在同名只读 sensor 之前；
5. 同名多实体提示 / 只读域提示（note）；
6. **陷阱条不被误修**：`"次卧空调"`/`"阳台感应灯"` 仍必须 0 候选（否则设错条失效）。

**B. 仿真底座建模缺口**（报告 §7.2 P0-2 / P0-3）
7. `media_pause`/`media_play`/`media_stop` 不再记 `unmodeled`，状态真的翻成 paused/playing/idle；
8. `climate.set_hvac_mode` 按参数改状态；`set_fan_mode` 只改属性不改状态；
9. `toggle` 按当前状态翻转；
10. `seed` 支持属性（修 #3 的 `entity_drift`：climate 只有 state 没有 temperature）。
"""

from __future__ import annotations

import json

import pytest

from autoforge.af_catalog import DeviceCatalog
from autoforge.af_expect import evaluate_graph_expects
from autoforge.af_ir import load_graph
from autoforge.af_vhass import FakeHA, FakeHAAdapter, seed_from_graph

#: 真实家庭目录的**微缩复刻**：刻意保留 NL 实测里的三类坑
#: （同名只读统计量 / 名字里没有区域词的可控设备 / 后缀冗余的中文名）
STATES: dict[str, tuple[str, dict]] = {
    # #5/#7 客厅电视：只读统计量的名字含「客厅电视」，可控播放器反而不含「客厅」
    "sensor.ke_ting_tv_duration": (
        "8.8",
        {"friendly_name": "lidicn的电视 客厅电视昨日播放时长", "area": "客厅"},
    ),
    "media_player.tv_play_control": (
        "off",
        {"friendly_name": "lidicn的电视 播放控制", "area": "客厅"},
    ),
    # #1 书房光照：真实名是「书房人体传感器 光照度」，查询词「书房光照」不连续
    "sensor.study_illumination": ("2", {"friendly_name": "书房人体传感器 光照度", "area": "书房"}),
    "sensor.study_temperature": ("26", {"friendly_name": "书房人体传感器 温度", "area": "书房"}),
    # #8 油烟机灯光：真实名是「米家跨界吸油烟机S1 灯光」
    "light.hood_light": ("off", {"friendly_name": "米家跨界吸油烟机S1 灯光", "area": "厨房"}),
    # 可动作域优先：同名「书房吊灯」下，只读 sensor 与可控 switch 的相似度几乎相同
    "sensor.lamp_service_name": (
        "书房墙壁开关",
        {"friendly_name": "书房墙壁开关 书房吊灯 服务名称", "area": "书房"},
    ),
    "switch.lamp_switch": ("off", {"friendly_name": "书房墙壁开关 书房吊灯 开关", "area": "书房"}),
    # #3 同名双 climate（集成不同 → resolve 的选取依据必须被显式提示）
    "climate.ac_mcu": ("off", {"friendly_name": "主卧室空调", "area": "主卧室"}),
    "climate.ac_cn": ("off", {"friendly_name": "主卧室空调", "area": "主卧室"}),
}


@pytest.fixture(autouse=True)
def _unreachable_ha(monkeypatch):
    """所有用例都把 HA 指向必然拒绝连接的端口，确保不触真实 HA。"""
    monkeypatch.setenv("AUTOFORGE_HA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("AUTOFORGE_HA_TIMEOUT", "0.3")
    monkeypatch.delenv("AUTOFORGE_HA_TOKEN", raising=False)


@pytest.fixture
def catalog(tmp_path) -> DeviceCatalog:
    cat = DeviceCatalog(tmp_path, fetch_all=lambda: dict(STATES))
    assert cat.refresh()["ok"]
    return cat


def _ids(result: dict) -> list[str]:
    return [c["entity_id"] for c in result["candidates"]]


# ─────────────────────────────────────────────────────────────────────
# A. resolve 多级兜底
# ─────────────────────────────────────────────────────────────────────


def test_area_split_combines_room_and_function(catalog):
    """「书房光照」→ 区域「书房」+ 功能「光照」（原样查询无候选）。"""
    result = catalog.resolve("书房光照")
    assert result["match_stage"] == "area_split"
    assert "sensor.study_illumination" in _ids(result)
    # 派生阶段**不得**套用「可动作域优先」：问的是照度值，不能被可动作的设置项顶掉。
    assert _ids(result)[0] == "sensor.study_illumination"
    # 派生命中必须标注来源，且不谎称 high
    top = next(c for c in result["candidates"] if c["entity_id"] == "sensor.study_illumination")
    assert top["matched_by"].startswith("area_split::")
    assert top["confidence"] == "medium"
    assert "组合查询" in result["note"]


def test_suffix_stripped_retry(catalog):
    """「油烟机灯光」→ 剥离后缀后按「油烟机」命中灯光实体。"""
    result = catalog.resolve("油烟机灯光")
    assert result["match_stage"] == "suffix_stripped"
    assert _ids(result)[0] == "light.hood_light"
    assert result["candidates"][0]["matched_by"].startswith("suffix_stripped::")


def test_readonly_upgrade_finds_controllable(catalog):
    """「客厅电视」原样只命中只读 sensor → 升级为可控 media_player。"""
    result = catalog.resolve("客厅电视")
    assert result["match_stage"] == "readonly_upgrade"
    assert _ids(result) == ["media_player.tv_play_control"]
    assert "只读统计量" in result["note"]


def test_actionable_domain_outranks_readonly(catalog):
    """同置信度下可控设备（switch）必须排在只读统计量（sensor）之前。"""
    result = catalog.resolve("书房吊灯")
    assert _ids(result) == ["switch.lamp_switch", "sensor.lamp_service_name"]


def test_duplicate_name_hint(catalog):
    """同名多实体必须显式提示（原实现静默取首个候选）。"""
    result = catalog.resolve("主卧室空调")
    assert result["count"] == 2
    assert "同名实体" in result["note"]


def test_duplicate_hint_collapses_internal_whitespace(tmp_path):
    """HA 集成常见的**双空格**不能逃过同名检测。

    真实目录里两个主卧空调是「主卧室空调 空调」与「主卧室空调  空调」（差一个空格），
    肉眼与意图都是同名；`strip()` 只去首尾 → 分组失败 → Agent 静默取首个候选。
    """
    states = {
        "climate.ac_a": ("off", {"friendly_name": "主卧室空调 空调", "area": "主卧室"}),
        "climate.ac_b": ("off", {"friendly_name": "主卧室空调  空调", "area": "主卧室"}),
    }
    cat = DeviceCatalog(tmp_path, fetch_all=lambda: dict(states))
    assert cat.refresh()["ok"]

    result = cat.resolve("主卧室空调")

    assert result["count"] == 2
    assert "同名实体" in result["note"]


def test_readonly_hint_when_no_controllable(catalog):
    """命中全是只读域且找不到可控替代时，note 必须警告不能当 do 目标。"""
    result = catalog.resolve("光照度")
    assert result["count"] >= 1
    assert all(not c["services"] for c in result["candidates"])
    assert "只读域" in result["note"]


@pytest.mark.parametrize("trap", ["次卧空调", "阳台感应灯"])
def test_trap_queries_still_have_no_candidate(catalog, trap):
    """⚠️ 关键：设错条（次卧空调 / 阳台感应灯）必须仍然查不到，否则实测陷阱失效。"""
    result = catalog.resolve(trap)
    assert result["ok"] is True
    assert result["count"] == 0
    assert result["candidates"] == []


def test_direct_match_keeps_original_behavior(catalog):
    """原本能命中的查询不受兜底影响（stage 必须是 direct）。"""
    result = catalog.resolve("主卧室空调", area="主卧室")
    assert result["match_stage"] == "direct"
    assert result["count"] == 2


# ─────────────────────────────────────────────────────────────────────
# B. 仿真底座：服务建模 + seed 属性
# ─────────────────────────────────────────────────────────────────────


def test_media_pause_is_modeled_and_flips_state():
    """#7 的 media_pause 以前被记 unmodeled（IR 正确却无法验证），现已建模。"""
    states = FakeHA()
    adapter = FakeHAAdapter(states)
    states.set("media_player.tv", "playing")

    result = adapter.call("media_player.media_pause", {"entity_id": "media_player.tv"})

    assert result.success is True
    assert states.get("media_player.tv") == "paused"
    assert adapter.unmodeled == []


def test_media_play_and_stop_states():
    states = FakeHA()
    adapter = FakeHAAdapter(states)
    adapter.call("media_player.media_play", {"entity_id": "media_player.tv"})
    assert states.get("media_player.tv") == "playing"
    adapter.call("media_player.media_stop", {"entity_id": "media_player.tv"})
    assert states.get("media_player.tv") == "idle"
    assert adapter.unmodeled == []


def test_climate_hvac_mode_follows_param():
    states = FakeHA()
    adapter = FakeHAAdapter(states)
    states.set("climate.ac", "off")

    adapter.call("climate.set_hvac_mode", {"entity_id": "climate.ac", "hvac_mode": "cool"})

    assert states.get("climate.ac") == "cool"
    assert adapter.unmodeled == []


def test_climate_fan_mode_changes_attribute_only():
    states = FakeHA()
    adapter = FakeHAAdapter(states)
    states.set("climate.ac", "cool")

    adapter.call("climate.set_fan_mode", {"entity_id": "climate.ac", "fan_mode": "high"})

    assert states.get("climate.ac") == "cool"  # 状态不变
    assert states.attributes["climate.ac"]["fan_mode"] == "high"
    assert adapter.unmodeled == []


def test_toggle_flips_by_current_state():
    states = FakeHA()
    adapter = FakeHAAdapter(states)
    states.set("light.study", "on")

    adapter.call("light.toggle", {"entity_id": "light.study"})

    assert states.get("light.study") == "off"


def test_unmodeled_still_recorded():
    """诚实性不能被削弱：真未建模的服务仍必须记 unmodeled 且不伪造状态。"""
    states = FakeHA()
    adapter = FakeHAAdapter(states)

    adapter.call("vacuum.start", {"entity_id": "vacuum.x"})

    assert "vacuum.start" in adapter.unmodeled
    assert states.get("vacuum.x") is None


def test_seed_supports_state_and_attributes():
    """#3 根因：seed 原先只能给 state，climate 没有 temperature 属性 → entity_drift 误报。"""
    states = FakeHA()
    states.seed({"climate.ac": {"state": "cool", "attributes": {"temperature": 26}}})
    states.seed({"light.study": "off"})  # 旧写法仍必须可用

    snap = states.snapshot(["climate.ac", "light.study"])

    assert snap.values["climate.ac"] == "cool"
    assert snap.attributes["climate.ac"]["temperature"] == 26
    assert snap.values["light.study"] == "off"


def _demo_ir() -> dict:
    """读 climate 属性 + 写 climate 的最小 IR（复刻 #3 的 entity_drift 场景）。"""
    return {
        "ir_version": "0.2.1",
        "id": "demo",
        "name": "demo",
        "version": 1,
        "mode": "single",
        "snapshot": True,
        "nodes": [
            {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "climate.ac"}},
            {
                "id": "i1",
                "kind": "if",
                "expr": {
                    "op": "lt",
                    "left": {"var": "entity.climate.ac.temperature", "type": "numeric"},
                    "right": {"const": 16},
                },
            },
            {
                "id": "d",
                "kind": "do",
                "adapter": "ha",
                "action": "climate.set_temperature",
                "params": {"entity_id": "climate.ac", "temperature": 18},
            },
            {"id": "p", "kind": "pass"},
        ],
        "edges": [
            {"from": "o", "to": "i1", "kind": "then"},
            {"from": "i1", "to": "d", "kind": "then"},
            {"from": "i1", "to": "p", "kind": "no"},
            {"from": "d", "to": "p", "kind": "then"},
        ],
    }


def test_seed_from_graph_carries_attributes_to_snapshot():
    """seed 的属性必须一路带到 Snapshot（否则 if 里读属性仍会漂移）。"""
    graph = load_graph(_demo_ir())
    states = seed_from_graph(graph, {"climate.ac": {"state": "cool", "attributes": {"temperature": 26}}})

    snap = states.snapshot(["climate.ac"])

    assert snap.values["climate.ac"] == "cool"
    assert snap.attributes["climate.ac"]["temperature"] == 26


# ─────────────────────────────────────────────────────────────────────
# C. 断言闭环：属性形态 expect + CLI 真的会翻状态
# ─────────────────────────────────────────────────────────────────────


def _demo_with_expect(expect: list[dict]) -> "object":
    raw = _demo_ir()
    raw["expect"] = expect
    return load_graph(raw)


def test_expect_attribute_form_passes():
    """属性形态：断言的是**属性**（temperature），不是粗粒度的 state。"""
    graph = _demo_with_expect(
        [{"entity_id": "climate.ac", "attribute": "temperature", "value": 18}]
    )
    states = seed_from_graph(
        graph, {"climate.ac": {"state": "cool", "attributes": {"temperature": 18}}}
    )

    report = evaluate_graph_expects(graph, states, [])

    assert report["declared"] == 1
    assert report["passed"] == 1 and report["failed"] == 0 and report["unverified"] == 0
    assert report["fully_verified"] is True


def test_expect_attribute_form_fails_on_wrong_value():
    """值不对必须判 fail——这才是「跑对了吗」的真答案。"""
    graph = _demo_with_expect(
        [{"entity_id": "climate.ac", "attribute": "temperature", "value": 18}]
    )
    states = seed_from_graph(
        graph, {"climate.ac": {"state": "cool", "attributes": {"temperature": 26}}}
    )

    report = evaluate_graph_expects(graph, states, [])

    assert report["failed"] == 1
    assert report["ok"] is False
    item = report["automations"]["demo"]["items"][0]
    assert item["status"] == "fail"
    assert item["kind"] == "entity_attribute"
    assert "18" in item["reason"]


def test_expect_attribute_unverified_is_not_pass():
    """属性没被播种 → unverified（不是 pass）：把「没验到」当「验过了」就是自欺。"""
    graph = _demo_with_expect(
        [{"entity_id": "climate.ac", "attribute": "temperature", "value": 18}]
    )
    states = seed_from_graph(graph)  # 不带属性

    report = evaluate_graph_expects(graph, states, [])

    assert report["unverified"] == 1 and report["failed"] == 0
    assert report["fully_verified"] is False


def test_expect_attribute_supports_comparison_ops():
    """属性形态支持 lt/gt 等比较（P6「设定温度低于 16 度」这类判据需要）。"""
    graph = _demo_with_expect(
        [{"entity_id": "climate.ac", "attribute": "temperature", "op": "lt", "value": 16}]
    )
    states = seed_from_graph(
        graph, {"climate.ac": {"state": "cool", "attributes": {"temperature": 15}}}
    )

    report = evaluate_graph_expects(graph, states, [])

    assert report["passed"] == 1 and report["fully_verified"] is True


def test_cli_fake_base_registers_fakeha_adapter(tmp_path):
    """CLI 的 fake 链路必须注册 FakeHAAdapter。

    原实现只用 `build_runtime` 默认的 `HAAdapter(dry_run=True)`——只记录下发**意图**、
    不翻转状态，于是 `do` 看着成功、实体原地不动，`expect` 永远 fail/unverified
    （NL 实测「跑完了但没人说跑对了」的机制性根因）。
    """
    from autoforge.af_cli import _make_runtime

    graph = load_graph(_demo_ir())
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(
        json.dumps({"climate.ac": {"state": "cool", "attributes": {"temperature": 15}}}),
        encoding="utf-8",
    )

    runtime = _make_runtime(graph, str(seed_file), "fake")
    adapter = runtime.adapters.get("ha")

    assert isinstance(adapter, FakeHAAdapter)
    assert adapter.states is runtime.states  # 绑到同一个 FakeHA，状态才会真的变

    adapter.call("climate.set_temperature", {"entity_id": "climate.ac", "temperature": 18})

    assert runtime.states.attributes["climate.ac"]["temperature"] == 18
    assert adapter.unmodeled == []
