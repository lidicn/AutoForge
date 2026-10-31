"""AF-123（假绿三族 · 测试通道实体校验收口）回归钉，2026-10-31。

出处：QA 审计——幽灵实体 3 条意图在测试通道 `submit_batch` 拿到 3/3 passed、
100%。根因链两段：

1. `af_test._submit_one` 的 build 实体校验落在**测试区 store** 上，而设备目录
   挂在主 store 根——测试区是空目录 ⇒ `use_catalog` 拿到空目录 ⇒ known=None ⇒
   `entity_check=skipped` ⇒ 幽灵实体（`binary_sensor.ghost99`）检测不到；
2. 成功路径把 `build_warnings` 整个丢掉，`ENTITY_CHECK_DEGRADED` 发了也没人看得见。

修法三件套（`af_test._main_catalog_entity_ids` / `_submit_one` 成功路径 /
`graph_ref` 拼名）：build 白名单 = 主 store 设备目录全集，经
`apply(ref, known_entities=...)` 透传到 `svc.build`；空目录 fail-open（合法首建态）
但明细必须带 `entity_check=skipped` + `ENTITY_CHECK_DEGRADED`；`graph_ref` 用
`save_graph` 返回的 name+version 拼——返回键里根本没有 `graph_id`，旧实现恒空串。

变异自证（删掉 known_entities 透传 ⇒ 判据①必须红）由实现者在落地批次内手工
执行并附命令输出，不进本文件。
"""

from __future__ import annotations

from pathlib import Path

from autoforge.af_test import TestChannel


class _CatalogStub:
    """主 store 设备目录替身：AF-015 的 `resolve_best` + 本批新用的 `known_entity_ids`。"""

    def __init__(self, entities: set[str], name_map: dict[str, str] | None = None):
        self._entities = set(entities)
        self._name_map = dict(name_map or {})

    def resolve_best(self, name: str) -> str | None:
        return self._name_map.get(name)

    def known_entity_ids(self) -> set[str]:
        return set(self._entities)


# 判据②③共用的「主目录有货」基线：真实形状的 entity_id 全集。
_POPULATED = {"light.书房主灯", "binary_sensor.门磁", "climate.ac"}
_NAME_MAP = {"书房灯": "light.书房主灯"}


def _channel(tmp_path: Path, monkeypatch, catalog) -> TestChannel:
    """搭测试通道：目录替身挂在 `_resolve_catalog`（主 store 同源入口）。"""
    monkeypatch.setattr(TestChannel, "_resolve_catalog", lambda self: catalog)
    return TestChannel(str(tmp_path / "test"))


# ── 判据① 幽灵实体必须红，不许假绿 ──────────────────────────────────


def test_ghost_entities_rejected_not_false_green(tmp_path, monkeypatch):
    """主目录有货时，不存在的实体走 submit_batch ⇒ passed=False + ENTITY_NOT_FOUND。"""
    # 先现查：两个幽灵实体确实不在主目录里（不是等价变异自欺）
    for eid in ("binary_sensor.ghost99", "light.alsoghost"):
        assert eid not in _POPULATED, eid
    ch = _channel(tmp_path, monkeypatch, _CatalogStub(set(_POPULATED), _NAME_MAP))
    report = ch.submit_batch([
        {"name": "幽灵灯控", "when": {"entity": "binary_sensor.ghost99", "to": "on"},
         "do": {"action": "开灯", "target": "light.alsoghost"}},
    ])
    det = report["details"][0]
    assert det["passed"] is False, "幽灵实体 passed=True —— AF-123 假绿未收口"
    err = det.get("error") or {}
    codes = {e.get("code") for e in (err.get("build_errors") or [])}
    assert "ENTITY_NOT_FOUND" in codes, f"error 里没有具名码：{err}"
    assert det.get("stage") == "build", det.get("stage")


# ── 判据② 有货目录不误伤中文名意图 ──────────────────────────────────


def test_chinese_name_intent_passes_with_populated_catalog(tmp_path, monkeypatch):
    """主目录有货：中文名意图正常 pass（entity_check=provided，不是 skipped）。"""
    ch = _channel(tmp_path, monkeypatch, _CatalogStub(set(_POPULATED), _NAME_MAP))
    report = ch.submit_batch([
        {"name": "书房灯控", "when": {"entity": "书房灯", "to": "on"},
         "do": {"action": "开灯", "target": "书房灯"}},
    ])
    det = report["details"][0]
    assert det["passed"] is True, f"不误伤失败：{det.get('error')}"
    assert det.get("entity_check") == "provided", det.get("entity_check")


# ── 判据③ 空目录 fail-open 但必须说话 ────────────────────────────────


def test_empty_main_catalog_fail_open_but_speaks(tmp_path, monkeypatch):
    """主目录空：pass=True 合法（首建态），但明细必须带 skipped + ENTITY_CHECK_DEGRADED。"""
    ch = _channel(tmp_path, monkeypatch, _CatalogStub(set()))
    report = ch.submit_batch([
        # 显式 entity_id（不走名称解析）：空目录时名称解析本就该拒绝，别混进本判据；
        # 动作用 L1 的灯（climate 是 L2 要 requires_confirm，会因别的原因红）
        {"name": "显式实体灯控", "when": {"entity": "binary_sensor.m", "to": "off"},
         "do": {"action": "light.turn_on", "target": "light.x"}},
    ])
    det = report["details"][0]
    assert det["passed"] is True, f"空目录 fail-open 被误拦：{det.get('error')}"
    assert det.get("entity_check") == "skipped", det.get("entity_check")
    codes = {w.get("code") for w in (det.get("build_warnings") or [])}
    assert "ENTITY_CHECK_DEGRADED" in codes, det.get("build_warnings")
    assert det.get("entity_check_note"), "空目录必须说话：note 缺席等于静默 fail-open"


# ── 判据④ graph_ref 真回指 ──────────────────────────────────────────


def test_graph_ref_points_back_at_saved_graph(tmp_path, monkeypatch):
    """graph_ref 非空、含图名，且能在测试区 store 里 load 到那份归档。"""
    ch = _channel(tmp_path, monkeypatch, _CatalogStub(set(_POPULATED), _NAME_MAP))
    report = ch.submit_batch([
        {"name": "回指图", "when": {"entity": "书房灯", "to": "on"},
         "do": {"action": "开灯", "target": "书房灯"}},
    ])
    det = report["details"][0]
    assert det["passed"] is True, det.get("error")
    ref = det.get("graph_ref") or ""
    assert ref, "graph_ref 恒空串——save_graph 返回键里没有 graph_id（AF-123 附带）"
    assert "回指图" in ref, ref
    name, _, ver = ref.rpartition("@v")
    assert name == "回指图" and ver.isdigit(), ref
    rec = ch._get_store().load_record(name)
    assert rec and rec.get("graph"), f"graph_ref 回指的归档不存在：{ref}"
