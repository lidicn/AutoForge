"""第四轮审计 缺陷 2：六个域必须共用同一条恢复策略（反例锁）。

审计原话：「同一种情况（快照属性值是 HA 常见非法形态），三个域给出三种不同处理策略」。
本文件把修好之后的唯一策略钉死，任何域想改回"静默丢字段"或"整块放弃可读方向"都会红：

1. 状态读不出方向（缺失 / unknown / unavailable / 白名单外）→ 返回 None，**绝不写设备**；
2. 方向读得出、附加属性读不出 → 回放方向，并把这个字段列进 `RestoreCall.gaps`；
3. 读得出的字段一律原值回放， params 里不得出现任何快照里没有的值（不许设备默认值冒充）。
"""

from __future__ import annotations

import math

import pytest

from autoforge.af_adapters import CallResult
from autoforge.af_undo import DOMAIN_SETTER, RestoreCall, UndoStore, restore_call

#: HA 里"读不出"的常见形态：实体刚注册/掉线/被第三方集成写脏都会落到这几种
ILLEGIBLE = ["unknown", "unavailable", "abc", float("nan"), "12x", {}, []]

#: 每个域：状态可读的快照底座 + 它的附加属性字段名
DOMAIN_CASES = {
    "light": ({"state": "on", "attributes": {"brightness": 180, "color_temp": 370}},
              ("brightness", "color_temp")),
    "fan": ({"state": "on", "attributes": {"percentage": 40}}, ("percentage",)),
    "cover": ({"state": "open", "attributes": {"current_position": 35}}, ("current_position",)),
    "climate": ({"state": "heat", "attributes": {"temperature": 21.5, "hvac_mode": "heat"}},
                ("temperature",)),
    "switch": ({"state": "on", "attributes": {}}, ()),
    "lock": ({"state": "locked", "attributes": {}}, ()),
}


def _snap(base: dict, key: str, value) -> dict:
    attrs = dict(base["attributes"])
    attrs[key] = value
    return {"state": base["state"], "attributes": attrs}


#: 以"方向"为恢复目标的域：状态读不出就必须整体跳过。
#: cover/climate 的恢复目标是属性（位置 / 设定值），其中间态（opening、auto）不影响属性可回放。
DIRECTION_DOMAINS = ("light", "switch", "fan", "lock")


@pytest.mark.parametrize("domain", sorted(DOMAIN_CASES))
def test_状态读不出方向_任何域都不得下发写操作(domain):
    """修前的反面：unknown/unavailable 被当成"关"，状态未知的锁会收到 lock.unlock。"""
    base = DOMAIN_CASES[domain][0]
    for bad in ("unknown", "unavailable", None, ""):
        snap = {"state": bad, "attributes": dict(base["attributes"])}
        assert restore_call(f"{domain}.x", snap) is None, f"{domain} 对状态 {bad!r} 下发了动作"


@pytest.mark.parametrize("domain", DIRECTION_DOMAINS)
def test_白名单外的状态_不得当成开或关(domain):
    """jammed / locking / standby 含义不明，落到任何一边都是猜。"""
    base = DOMAIN_CASES[domain][0]
    for odd in ("jammed", "locking", "opening", "cleaning", "standby"):
        snap = {"state": odd, "attributes": dict(base["attributes"])}
        assert restore_call(f"{domain}.x", snap) is None, f"{domain} 对状态 {odd!r} 下发了动作"


@pytest.mark.parametrize("domain,extra_keys",
                         [(d, DOMAIN_CASES[d][1]) for d in sorted(DOMAIN_CASES) if DOMAIN_CASES[d][1]])
def test_附加属性读不出_必须进_gaps_不许静默(domain, extra_keys):
    """核心反例锁：字段存在却读不出 → gaps 必须点名它；字段不存在 → 不算 gap。"""
    base = DOMAIN_CASES[domain][0]
    for key in extra_keys:
        for bad in ILLEGIBLE:
            call = restore_call(f"{domain}.x", _snap(base, key, bad))
            if call is None:  # 该域以这个字段为唯一恢复目标（cover 位置）→ 跳过也是策略之一
                continue
            assert key in call.gaps, (
                f"{domain}.{key}={bad!r} 读不出却被报成完整恢复：{call}")


@pytest.mark.parametrize("domain,extra_keys",
                         [(d, DOMAIN_CASES[d][1]) for d in sorted(DOMAIN_CASES) if DOMAIN_CASES[d][1]])
def test_字段缺失不算丢失(domain, extra_keys):
    base = DOMAIN_CASES[domain][0]
    attrs = {k: v for k, v in base["attributes"].items() if k not in extra_keys}
    call = restore_call(f"{domain}.x", {"state": base["state"], "attributes": attrs})
    if call is not None:
        assert call.gaps == (), f"快照里本就没有这些字段，不该报部分恢复：{call}"


@pytest.mark.parametrize("domain", sorted(DOMAIN_CASES))
def test_params_只含快照里读得出的原值(domain):
    """不许用 0/设备默认值冒充：params 里每个数值都必须能在快照里找到出处。"""
    base = DOMAIN_CASES[domain][0]
    call = restore_call(f"{domain}.x", base)
    assert call is not None and call.gaps == ()
    for k, v in call.params.items():
        if k == "entity_id" or not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        src_key = "current_position" if k == "position" else k
        src = base["attributes"].get(src_key)
        assert src is not None and math.isclose(float(v), float(src)), f"{k}={v} 无出处"


def test_light_亮度读不出_仍回方向但标记部分恢复():
    call = restore_call("light.x", {"state": "on", "attributes": {"brightness": "unknown"}})
    assert call == RestoreCall("light.turn_on", {"entity_id": "light.x"}, ("brightness",))
    # 灯该亮还是要亮：方向正确，只是亮度未回放
    assert "brightness" not in call.params


def test_climate_温度读不出_与_light_同策略():
    """修前这里是 return None（整块放弃），与 light 的静默丢字段正好相反。"""
    call = restore_call("climate.x", {
        "state": "heat", "attributes": {"temperature": "unknown", "hvac_mode": "heat"}})
    assert call == RestoreCall(
        "climate.set_hvac_mode", {"entity_id": "climate.x", "hvac_mode": "heat"},
        ("temperature",))


def test_cover_位置读不出_不许下发_open_cover():
    """HA 的 cover state=open 覆盖 1%–99%，"全开"不是恢复。"""
    for bad in ("unknown", "abc", None):
        assert restore_call("cover.x",
                            {"state": "open", "attributes": {"current_position": bad}}) is None


def test_revert_把部分恢复如实上报():
    class _Ad:
        def __init__(self):
            self.calls = []

        def call(self, action, params):
            self.calls.append((action, dict(params)))
            return CallResult.ok({"action": action})

    import tempfile
    store = UndoStore(tempfile.mkdtemp())
    store.record("dep-1", {
        "light.a": {"state": "on", "attributes": {"brightness": "unknown"}},
        "switch.b": {"state": "off", "attributes": {}},
        "cover.c": {"state": "open", "attributes": {"current_position": "unknown"}},
    })
    res = _Ad()
    out = store.revert("dep-1", res, confirm=True)

    assert out["ok"] is True                      # 没有调用失败
    assert out["restored"] == ["light.a", "switch.b"]
    assert out["skipped"] == ["cover.c"]
    assert [p["entity_id"] for p in out["partial"]] == ["light.a"]
    assert out["partial"][0]["not_restored"] == ["brightness"]
    assert out["fully_restored"] is False          # 关键：不许谎报"全部恢复"
    assert ("light.turn_on", {"entity_id": "light.a"}) in res.calls

    clean = UndoStore(tempfile.mkdtemp())
    clean.record("dep-2", {"switch.b": {"state": "off", "attributes": {}}})
    assert clean.revert("dep-2", _Ad(), confirm=True)["fully_restored"] is True


def test_六个域都在同一张表里_不许有域偷偷另写策略():
    """DOMAIN_SETTER 是唯一入口：新增域必须一并纳入上面的策略扫描。"""
    assert set(DOMAIN_SETTER) == {"light", "switch", "fan", "cover", "climate", "lock"}


def test_映射函数抛异常_仍然按_fail_closed_跳过(monkeypatch, caplog):
    """防御分支：setter 内部炸掉时不得下发半成品恢复。"""
    def _boom(snapshot, entity_id):
        raise RuntimeError("属性读取炸了")

    monkeypatch.setitem(DOMAIN_SETTER, "light", _boom)
    with caplog.at_level("ERROR"):
        assert restore_call("light.x", {"state": "on", "attributes": {"brightness": 1}}) is None
    assert "fail-closed" in caplog.text
