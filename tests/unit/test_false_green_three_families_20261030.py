"""假绿三族收口判据（审计 AF-123/124/125，v1.14.22）。

出处 `E:/NAS/QA/AF_Tester/audit_mcp/BATCH_v11419_regression.md` §3：
- AF-124：`time.at="25:00"` build ok 照旧放行（16 题假绿）
- AF-125：`light.turn_onnn` 幽灵服务 build ok errors:[]（幽灵服务零拦截）
- AF-123：测试通道幽灵实体 40 问假绿——空目录静默退化 entity_check=skipped 且零诊断

三族同根：**build 只查实体、不查语义**。判据逐族红转绿 + 正常路径零误伤对照。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoforge.af_mcp import dispatch
from autoforge.af_store import GraphStore

QA = {"subject": "qa", "scopes": ["read", "write"]}


def _build(ir: dict, store: GraphStore) -> dict:
    content, _ = dispatch("af_build", {"ir": ir}, store, QA)
    return json.loads(content[0]["text"])


def _codes(body: dict, key: str = "errors") -> list[str]:
    return [e["code"] for e in body.get(key, [])]


def _ir_time(at) -> dict:
    return {"ir_version": "0.3.0", "id": "t1", "name": "t1", "version": 1, "mode": "single",
            "nodes": [{"id": "a", "kind": "on", "name": "t", "trigger": {"type": "time", "at": at}},
                      {"id": "p", "kind": "pass"}],
            "edges": [{"from": "a", "to": "p", "kind": "then"}]}


def _ir_do(action: str, entity: str = "binary_sensor.m", target: str = "light.m") -> dict:
    return {"ir_version": "0.3.0", "id": "g1", "name": "g1", "version": 1, "mode": "single",
            "nodes": [{"id": "a", "kind": "on", "name": "t",
                       "trigger": {"type": "state", "entity_id": entity, "to": "on"}},
                      {"id": "d", "kind": "do", "name": "d", "adapter": "ha",
                       "action": action, "params": {"entity_id": target}}],
            "edges": [{"from": "a", "to": "d", "kind": "then"}]}


def _seed_catalog(root: Path) -> None:
    """落一份含 binary_sensor.m / light.m 的目录缓存（.catalog/catalog.json）。"""
    cat = root / ".catalog"
    cat.mkdir(exist_ok=True)
    payload = {"version": 1, "freshness": "2026-10-30T00:00:00Z", "ha_url": "",
               "entities": {
                   "binary_sensor.m": {"entity_id": "binary_sensor.m", "state": "on",
                                       "friendly_name": "门", "domain": "binary_sensor",
                                       "attributes": {}},
                   "light.m": {"entity_id": "light.m", "state": "off",
                               "friendly_name": "灯", "domain": "light", "attributes": {}}}}
    (cat / "catalog.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ── AF-124：time.at 零校验 ─────────────────────────────────────────

@pytest.mark.parametrize("bad_at", ["25:00", "7:5", "19-30", "12:60", "23:59:99", "now"])
def test_invalid_time_at_is_rejected(tmp_path, bad_at):
    body = _build(_ir_time(bad_at), GraphStore(str(tmp_path)))
    assert body["ok"] is False, f"at={bad_at!r} 此前一路绿灯到归档（AF-124 假绿）"
    assert "TIME_AT_INVALID" in _codes(body)


@pytest.mark.parametrize("good_at", ["19:30", "07:05", "23:59:59", "00:00",
                                     "sunrise", "sunset", "sunrise+00:30", "sunset-01:00"])
def test_valid_time_at_still_passes(tmp_path, good_at):
    body = _build(_ir_time(good_at), GraphStore(str(tmp_path)))
    assert "TIME_AT_INVALID" not in _codes(body), f"at={good_at!r} 是合法时刻，不许误伤"


def test_missing_time_at_is_rejected(tmp_path):
    """time 触发没 at = 永远不触发。schema 先拦（at 必须是 string）；
    空串则轮到闸门——两道都得拒，不许有任何一条路放行。"""
    body = _build(_ir_time(None), GraphStore(str(tmp_path)))
    assert body["ok"] is False and "IR_SCHEMA_INVALID" in _codes(body)
    body2 = _build(_ir_time(""), GraphStore(str(tmp_path)))
    assert body2["ok"] is False and "TIME_AT_INVALID" in _codes(body2)


# ── AF-125：幽灵服务零拦截 ────────────────────────────────────────

def test_ghost_service_is_rejected_with_fix_hint(tmp_path):
    """turn_onnn：实体是真的、服务是幽灵——build/sim 双绿但真机 404。"""
    body = _build(_ir_do("light.turn_onnn"), GraphStore(str(tmp_path)))
    assert body["ok"] is False, "幽灵服务此前 errors:[] 照放行（AF-125）"
    assert "SERVICE_UNKNOWN" in _codes(body)


@pytest.mark.parametrize("action", [
    "light.turn_on", "light.turn_off", "switch.turn_on", "climate.set_temperature",
    "cover.open_cover", "lock.lock", "fan.set_percentage", "media_player.media_pause",
])
def test_registered_services_still_pass(tmp_path, action):
    """误伤面：八个高频域的注册服务必须照常过（服务表本身要全）。"""
    body = _build(_ir_do(action), GraphStore(str(tmp_path)))
    assert "SERVICE_UNKNOWN" not in _codes(body), f"{action} 是登记过的服务，不许误伤"


def test_set_state_and_toggle_escape_hatches_stay_open(tmp_path):
    """全域逃生口（fake.py 同口径）：set_state（状态提供者）/ toggle 不查域表。"""
    for action in ("light.set_state", "light.toggle"):
        body = _build(_ir_do(action), GraphStore(str(tmp_path)))
        assert "SERVICE_UNKNOWN" not in _codes(body)


def test_unregistered_domain_not_guessed(tmp_path):
    """未登记域不猜词汇（与 affordance_for 同口径）——不误伤冷门集成。"""
    body = _build(_ir_do("mystery_domain.whatever"), GraphStore(str(tmp_path)))
    assert "SERVICE_UNKNOWN" not in _codes(body)


# ── AF-123：空目录静默退化零诊断 ─────────────────────────────────

def test_empty_catalog_degradation_is_announced(tmp_path):
    """空目录 + 图里引用实体 ⇒ 必须有 ENTITY_CHECK_DEGRADED 告警（不许装作校验过）。"""
    ir = _ir_do("light.turn_on", entity="binary_sensor.ghost99", target="light.alsoghost")
    body = _build(ir, GraphStore(str(tmp_path)))
    assert "ENTITY_CHECK_DEGRADED" in _codes(body, "warnings"), \
        "空目录退化只有 entity_check=skipped 一个字样、零诊断——幽灵实体 40 问假绿的通道"


def test_populated_catalog_still_flags_ghost_entities(tmp_path):
    """非空目录下幽灵实体照旧 ENTITY_NOT_FOUND（既有能力回归位）。"""
    _seed_catalog(Path(tmp_path))
    ir = _ir_do("light.turn_on", entity="binary_sensor.ghost99", target="light.alsoghost")
    body = _build(ir, GraphStore(str(tmp_path)))
    assert body["ok"] is False
    assert _codes(body).count("ENTITY_NOT_FOUND") == 2


def test_populated_catalog_no_degradation_warning(tmp_path):
    _seed_catalog(Path(tmp_path))
    body = _build(_ir_do("light.turn_on"), GraphStore(str(tmp_path)))
    assert body["ok"] is True
    assert "ENTITY_CHECK_DEGRADED" not in _codes(body, "warnings")


def test_entity_check_field_reports_honestly(tmp_path):
    """回执的 entity_check 字段是诚实读数：空目录=skipped、非空=catalog。"""
    empty = _build(_ir_do("light.turn_on"), GraphStore(str(tmp_path)))
    assert empty.get("entity_check") == "skipped"
    _seed_catalog(Path(tmp_path))
    full = _build(_ir_do("light.turn_on"), GraphStore(str(tmp_path)))
    assert full.get("entity_check") == "catalog"


# ── 变异位对照：正常路径综合 ──────────────────────────────────────

def test_normal_ir_still_builds_green(tmp_path):
    _seed_catalog(Path(tmp_path))
    body = _build(_ir_do("light.turn_on"), GraphStore(str(tmp_path)))
    assert body["ok"] is True, f"正常 IR 被误伤：{_codes(body)}"


# ── AF-128：af_health 版本串滞后（同批捎带）────────────────────────

def test_api_version_has_single_source_of_truth():
    """af_health 报的版本（svc.API_VERSION）必须与包版本（__version__）同源。

    AF-128：API_VERSION 曾是第四处版本字面量，1.14.20/21 两版没人更 ⇒ health 报
    1.14.19、审计据版本锚差点误判"批次未部署"。发版 bump 少更一处 = 版本锚失真。
    """
    from autoforge import __version__
    from autoforge.af_service import API_VERSION
    assert API_VERSION == __version__, (
        f"af_health 版本串漂了：API_VERSION={API_VERSION} != __version__={__version__}"
        f"——发版三处 bump 之外还有这枚抄本（AF-128）"
    )
    import re
    pyproject = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    assert m and m.group(1) == __version__, "pyproject 与 __version__ 也得同值（三处同值的既有纪律）"
