"""v1.5.0 解析遥测 + 歧义消歧（C §3 P2）。

覆盖：五档 `exact/medium/low/ambiguous/none`、成功率漏斗落盘、歧义消歧提示。
"""

from __future__ import annotations

import pytest

from autoforge.af_catalog import DeviceCatalog


@pytest.fixture(autouse=True)
def _unreachable_ha(monkeypatch):
    """不触真实 HA、有界失败。"""
    monkeypatch.setenv("AUTOFORGE_HA_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("AUTOFORGE_HA_TIMEOUT", "0.3")
    monkeypatch.delenv("AUTOFORGE_HA_TOKEN", raising=False)


#: 同名设备故意跨域（书房吊灯 → light + switch），用于制造「歧义」候选
FAKE_STATES: dict[str, tuple[str, dict]] = {
    "light.study_lamp": ("off", {"friendly_name": "书房吊灯", "area": "书房"}),
    "switch.study_lamp_switch": ("on", {"friendly_name": "书房吊灯开关", "area": "书房"}),
    "light.living_main": ("on", {"friendly_name": "客厅主灯", "area": "客厅"}),
}


def _catalog(tmp_path) -> DeviceCatalog:
    cat = DeviceCatalog(tmp_path, fetch_all=lambda: dict(FAKE_STATES))
    assert cat.refresh()["ok"]
    return cat


@pytest.mark.integration
def test_bucket_exact_entity_id(tmp_path):
    cat = _catalog(tmp_path)
    res = cat.resolve("light.living_main")
    assert res["bucket"] == "exact"


@pytest.mark.integration
def test_bucket_exact_friendly_name(tmp_path):
    cat = _catalog(tmp_path)
    res = cat.resolve("客厅主灯")
    assert res["bucket"] == "exact"


@pytest.mark.integration
def test_bucket_none(tmp_path):
    cat = _catalog(tmp_path)
    res = cat.resolve("压根不存在的设备zzz")
    assert res["bucket"] == "none"


@pytest.mark.integration
def test_bucket_ambiguous_with_hint(tmp_path):
    """多个 medium 候选（无明确 top）→ ambiguous，且给出消歧提示。"""
    cat = _catalog(tmp_path)
    res = cat.resolve("吊灯")
    assert res["bucket"] == "ambiguous"
    dis = res.get("disambiguation") or {}
    assert dis.get("hint")
    assert len(dis.get("candidates", [])) >= 2


@pytest.mark.integration
def test_funnel_accumulates_and_persists(tmp_path):
    cat = _catalog(tmp_path)
    cat.resolve("light.living_main")  # exact
    cat.resolve("吊灯")                # ambiguous
    cat.resolve("zzz不存在")           # none
    metrics = cat.resolve_metrics()
    assert metrics["buckets"]["exact"] >= 1
    assert metrics["buckets"]["ambiguous"] >= 1
    assert metrics["buckets"]["none"] >= 1
    assert metrics["total"] >= 3
    assert metrics["success_rate"] is not None
    assert 0.0 <= metrics["success_rate"] <= 1.0
    assert (tmp_path / ".catalog" / "resolve_metrics.json").is_file()


@pytest.mark.integration
def test_service_exposes_resolve_metrics(tmp_path):
    from autoforge import af_service as svc
    from autoforge.af_store import GraphStore

    cat = _catalog(tmp_path)
    cat.resolve("客厅主灯")
    out = svc.catalog_resolve_metrics(GraphStore(str(tmp_path)))
    assert out["ok"] is True
    assert set(out["buckets"]) == {"exact", "medium", "low", "ambiguous", "none"}
    assert out["total"] >= 1
