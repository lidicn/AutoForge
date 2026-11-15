# -*- coding: utf-8 -*-
"""AF-801（原 AF137）判据 · 设备护栏 `area` 规则求值失败 fail-open → fail-closed（2026-11-15）。

**现场**（ADM-auditkit 第五期 AF137 ／ AF-QA-Code C6 §二 现读）：

```python
if kind == "area" and self.catalog is not None:
    area_of = getattr(self.catalog, "area_of", None)
    if callable(area_of):
        try:
            return str(area_of(entity_id)) == value
        except Exception:
            return False          # ⛔ 与「这条规则不适用于该实体」逐字同形
return False
```

后果：`match_tier` 见 `False` ⇒ 该规则视为未命中 ⇒ 返回 `None` ⇒ **默认放行**，
且**零留痕**。第二条腿同族：`catalog` 未注入／没有 `area_of` 时，`area` 规则**永远不命中**
（修前同样静默放行；`af_service.load_device_guard` 的 P0-7 只是让 serve 侧有 catalog，
CLI 侧仍是 `from_file(guard)` 无 catalog）。

**修法**（AF-PM 卡 2026-11-15 钉死）：求值不出来 ⇒ 具名 ERROR（`DEVICE_GUARD_AREA_UNRESOLVED`）
＋**按该规则最严档**处置（`tier=0`／`perm="-"`），⛔ 静默 `return False`、⛔ 默认放行。
级别取 ERROR 而不取 WARNING 的依据：裁定 20261011《十三问》§3 Q8.3「安全面降级按介入档就是
ERROR，WARNING 会让人沉默」，同族先例 `af_service.DEVICE_GUARD_CATALOG_INJECT_FAILED` 也是 ERROR。

**三腿**（群规⑧：每条都要有牙，⛔ 恒 0／恒红都不算）：

- **L1 解析失败不再与「不命中」同形**：`area_of` 抛异常 ⇒ `match_tier==0`／`match_perm=="-"`
  （修前是 `None`／`None`），且具名 ERROR 留痕带 `why=exception`。
- **L2 正常命中不回归**：area 命中（tier0／tier1+r）、area 不命中、`area_of` 正常返回 `None`
  （＝解析得出"没有区域"，仍属明确不命中）、entity/domain 规则、多规则取最严——全部照旧。
- **L3 负控（catalog 不可用行为显式）**：`catalog=None` 或没有 `area_of` ⇒ 同一 fail-closed 口径
  ＋`why=catalog_absent` 留痕；但**只对 area 规则生效**——entity/domain 规则在无 catalog 时照旧
  命中/不命中（证明这不是"恒拒绝"的覆盖洞）。
"""

from __future__ import annotations

import logging

from autoforge.af_ir import load_graph
from autoforge.af_scanner import DeviceGuardRegistry, GuardRule, StaticScanner

CODE = "DEVICE_GUARD_AREA_UNRESOLVED"

RAW_IR = {
    "ir_version": "0.2.1",
    "id": "af801",
    "name": "af801",
    "version": 1,
    "mode": "single",
    "nodes": [
        {"id": "o", "kind": "on", "trigger": {"type": "state", "entity_id": "light.study", "to": "on"}},
        {"id": "d", "kind": "do", "adapter": "ha", "action": "switch.turn_on",
         "params": {"entity_id": "switch.study_socket"}},
        {"id": "p", "kind": "pass"},
    ],
    "edges": [
        {"from": "o", "to": "d", "kind": "then"},
        {"from": "d", "to": "p", "kind": "then"},
    ],
}


class _Catalog:
    """catalog 桩：`area_map` 正常查表；`raises=True` 时 `area_of` 抛异常；`with_area_of=False` 时无该属性。"""

    def __init__(self, area_map: dict | None = None, *, raises: bool = False, with_area_of: bool = True) -> None:
        self.area_map = area_map or {}
        self.raises = raises
        if with_area_of:
            self.area_of = self._area_of  # type: ignore[assignment]

    def _area_of(self, entity_id: str):
        if self.raises:
            raise RuntimeError("catalog 不可用（桩）")
        return self.area_map.get(entity_id)


def _area_rule(area: str = "书房", *, tier: int = 0, perm: str = "") -> GuardRule:
    return GuardRule(match={"type": "area", "value": area}, tier=tier, perm=perm)


def _codes(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


# ── L1：解析失败不再与「不命中」同形 ─────────────────────────────────────
def test_l1_area_exception_fails_closed_not_silent_miss(caplog):
    reg = DeviceGuardRegistry([_area_rule()], catalog=_Catalog(raises=True))
    with caplog.at_level(logging.ERROR, logger="autoforge.af_scanner"):
        tier = reg.match_tier("light.study")
        perm = reg.match_perm("light.study")

    assert tier == 0, "解析失败必须按最严 tier-0（修前这里是 None＝默认放行）"
    assert perm == "-", "解析失败必须按最严 perm（⛔ 回落 rw）"
    msgs = _codes(caplog)
    assert any(CODE in m and "why=exception" in m for m in msgs), (
        f"必须有具名 ERROR 留痕：{msgs}"
    )


def test_l1_same_registry_still_allows_unrelated_entities(caplog):
    """牙的另一半：同一注册表里，area 求值正常且不命中的实体仍返回 None——⛔ 不是恒 0。"""
    reg = DeviceGuardRegistry([_area_rule("客厅")], catalog=_Catalog({"light.study": "书房"}))
    with caplog.at_level(logging.ERROR, logger="autoforge.af_scanner"):
        assert reg.match_tier("light.study") is None
    assert not [m for m in _codes(caplog) if CODE in m], "正常求值⛔ 不许留痕"


def test_l1_fail_closed_wins_over_milder_rules():
    """一条规则求值不出来 ⇒ 整条实体按最严，⛔ 被其他"较松"规则拉回去。"""
    reg = DeviceGuardRegistry(
        [
            GuardRule(match={"type": "entity", "value": "light.study"}, tier=1, perm="rw"),
            _area_rule("客厅", tier=1, perm="rw"),
            _area_rule("书房", tier=1, perm="rw"),
        ],
        catalog=_Catalog(raises=True),
    )
    assert reg.match_tier("light.study") == 0
    assert reg.match_perm("light.study") == "-"


# ── L2：正常命中不回归 ───────────────────────────────────────────────────
def test_l2_area_hit_tier0_and_tier1_unchanged():
    tier0 = DeviceGuardRegistry([_area_rule("书房", tier=0)], catalog=_Catalog({"light.study": "书房"}))
    assert tier0.match_tier("light.study") == 0
    assert tier0.match_perm("light.study") == "-"

    tier1 = DeviceGuardRegistry(
        [_area_rule("书房", tier=1, perm="r")], catalog=_Catalog({"light.study": "书房"})
    )
    assert tier1.match_tier("light.study") == 1
    assert tier1.match_perm("light.study") == "r"


def test_l2_area_miss_and_unknown_area_are_explicit_misses(caplog):
    """`area_of` 正常返回 `None`（目录里没这个实体／没区域标签）是**解析得出**的"没有区域"，
    属明确不命中（None），⛔ 不许被当成"解析失败"而误判成 tier-0（那会把护栏变成恒红）。"""
    reg = DeviceGuardRegistry([_area_rule("书房")], catalog=_Catalog({}))
    with caplog.at_level(logging.ERROR, logger="autoforge.af_scanner"):
        assert reg.match_tier("light.study") is None
        assert reg.match_perm("light.study") is None
    assert not [m for m in _codes(caplog) if CODE in m], "有值语义的 miss⛔ 不许留 ERROR"


def test_l2_entity_and_domain_rules_unchanged():
    reg = DeviceGuardRegistry(
        [
            GuardRule(match={"type": "entity", "value": "light.study"}, tier=0),
            GuardRule(match={"type": "domain", "value": "switch"}, tier=1, perm="rw"),
        ],
        catalog=_Catalog(raises=True),
    )
    # 没有 area 规则参与 ⇒ 完全不受 fail-closed 影响
    assert reg.match_tier("light.study") == 0
    assert reg.match_tier("switch.socket") == 1
    assert reg.match_perm("switch.socket") == "rw"
    assert reg.match_tier("light.other") is None
    assert reg.match_tier("switch.other") == 1


def test_l2_deny_all_registry_still_strictest():
    reg = DeviceGuardRegistry.deny_all_registry("规则文件读不出")
    assert reg.match_tier("light.anything") == 0
    assert reg.match_perm("light.anything") == "-"


# ── L3：负控——catalog 不可用行为显式（fail-closed，但只对 area 规则）──────
def test_l3_catalog_absent_is_explicit_fail_closed(caplog):
    reg = DeviceGuardRegistry([_area_rule()], catalog=None)
    with caplog.at_level(logging.ERROR, logger="autoforge.af_scanner"):
        assert reg.match_tier("light.study") == 0, "catalog 不可用 ⇒ area 规则不能静默不命中"
        assert reg.match_perm("light.study") == "-"
    msgs = _codes(caplog)
    assert any(CODE in m and "why=catalog_absent" in m for m in msgs), f"两条腿要可区分：{msgs}"


def test_l3_catalog_without_area_of_is_the_same_leg(caplog):
    reg = DeviceGuardRegistry([_area_rule()], catalog=_Catalog(with_area_of=False))
    with caplog.at_level(logging.ERROR, logger="autoforge.af_scanner"):
        assert reg.match_tier("light.study") == 0
    assert any("why=catalog_absent" in m for m in _codes(caplog))


def test_l3_no_catalog_does_not_block_entity_rules(caplog):
    """负控的收窄面：无 catalog 只影响 area 规则——entity 规则照旧（⛔ 不是"恒拒绝"）。"""
    reg = DeviceGuardRegistry(
        [
            GuardRule(match={"type": "entity", "value": "light.study"}, tier=1, perm="r"),
            _area_rule("书房"),
        ],
        catalog=None,
    )
    with caplog.at_level(logging.ERROR, logger="autoforge.af_scanner"):
        # 有 area 规则在⇒该实体仍按最严；没有 area 规则的实体完全不受影响
        assert reg.match_tier("light.study") == 0
        assert reg.match_tier("light.unrelated") == 0, "area 规则求值不出来对每个实体都成立（最严）"

    only_entity = DeviceGuardRegistry(
        [GuardRule(match={"type": "entity", "value": "light.study"}, tier=1, perm="r")],
        catalog=None,
    )
    assert only_entity.match_tier("light.study") == 1
    assert only_entity.match_perm("light.study") == "r"
    assert only_entity.match_tier("light.unrelated") is None


# ── 端到端：扫描器不再把「护栏求值不出来」当「没命中」（ENTITY_GUARD_TIER0 要亮）──
def test_end_to_end_scanner_reports_tier0_not_silent_pass():
    reg = DeviceGuardRegistry([_area_rule()], catalog=_Catalog(raises=True))
    scan = StaticScanner(graph=load_graph(RAW_IR), device_guard=reg).scan()
    tier0 = [d for d in scan.diagnostics if d.code == "ENTITY_GUARD_TIER0"]
    assert tier0, "护栏求值不出来必须让扫描器报 Tier-0 ERROR（修前这里是静默放行）"
    assert any("switch.study_socket" in d.message for d in tier0), (
        f"写目标实体要在 Tier-0 名单里：{[d.message for d in tier0]}"
    )
    assert scan.errors, "必须落成 ERROR 级，⛔ 只是提示"
