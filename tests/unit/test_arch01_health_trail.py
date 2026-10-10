"""第六轮审计 ARCH-01 落码的两半判据（§二之九十五）。

一半是**行为**：`/api/health` 里那段 tick 探测失败时既不崩、也不许咽得无声——具名码
`TICK_HEALTH_PROBE_SKIPPED` 必须进日志。这一族形状在仓里已有先例（`EXPERIENCE_OBSERVE_SKIPPED`
／`ENTITY_HEALTH_MAP_SKIPPED`），本批把最后一条裸 `except Exception: pass` 也收进同一口径。
另一半是**台账**：那条被删掉的基线指纹 `af_service.py#except-pass-broad#health` 今天必须
**真的不再命中**——删指纹的依据只能是"站点修好了"，不能是"台账少一行看起来更干净"。
所以除了"真源码扫不出违规"，还有一档**植桩反证**：把源码改回裸 `pass`，扫描器必须当场抓回来
（否则前一条腿是空集给的绿）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from homesdk.gates.config import GateConfig
from homesdk.gates.scan import scan_source

from autoforge import af_service as svc

ROOT = Path(__file__).resolve().parents[2]
REL = "src/autoforge/af_service.py"
SOURCE = (ROOT / REL).read_text(encoding="utf-8")
CFG = GateConfig.load_file(ROOT / ".gates.toml", ROOT / ".gates-baseline.txt")


def _bare_swallows(source: str) -> list[str]:
    return sorted(
        v.fingerprint
        for v in scan_source(REL, source, CFG)
        if v.rule == "except-pass-broad"
    )


class _Boom:
    def health(self):  # pragma: no cover - 永远不该被走到
        raise AssertionError("get_tick_supervisor 已经抛了，不该走到这里")


def _patch_probe(monkeypatch, *, supervisor=None, thread=None, side_effect=None):
    import autoforge.af_live as live

    def get_tick_supervisor():
        if side_effect is not None:
            raise side_effect
        return supervisor

    monkeypatch.setattr(live, "get_tick_supervisor", get_tick_supervisor)
    monkeypatch.setattr(live, "get_ticker_thread", lambda: thread)
    monkeypatch.setattr(live, "get_tick_exit_reason", lambda: "synthetic")


def test_probe_failure_still_answers_and_leaves_named_trail(monkeypatch, caplog):
    """探测抛穿 ⇒ /api/health 照答、`tick_health` 留 None，但日志里必须有具名码。"""
    _patch_probe(monkeypatch, supervisor=_Boom(), side_effect=RuntimeError("supervisor 起不来"))
    caplog.set_level(logging.DEBUG, logger="autoforge.service")
    out = svc.health()
    assert out["ok"] is True and out["tick_health"] is None
    msgs = [r.getMessage() for r in caplog.records]
    assert "TICK_HEALTH_PROBE_SKIPPED" in msgs, msgs


def test_probe_success_still_populates_tick_health(monkeypatch):
    """反方向：探测正常时不许被顺手吞掉——红了就等于把"静默"换成了"永远静默"。"""

    class _Snapshot:
        def snapshot(self, _clock):
            return {"phase": "running"}

    class _Sup:
        _clock = object()

        def health(self):
            return _Snapshot()

    class _Thread:
        def is_alive(self):
            return True

    _patch_probe(monkeypatch, supervisor=_Sup(), thread=_Thread())
    out = svc.health()
    assert out["tick_health"] == {"phase": "running"}
    assert out["ticker_alive"] is True and out["tick_exit_reason"] == "synthetic"


def test_af_service_has_no_bare_swallow_left():
    """真源码扫不出任何 `except-pass-broad`——ARCH-01 那条 error 级条目就是这么消掉的。"""
    assert _bare_swallows(SOURCE) == []


def test_the_scan_actually_catches_the_pre_fix_shape():
    """植桩反证：把站点改回修前的裸 `pass`，扫描器必须当场抓回来（前一条腿不是空集给的绿）。"""
    planted = SOURCE.replace(
        '        logger.debug("TICK_HEALTH_PROBE_SKIPPED", exc_info=True)',
        "        pass",
        1,
    )
    assert planted != SOURCE, "锚点没命中：留痕那一行的形状变了，本腿失去意义"
    assert _bare_swallows(planted) == [f"{REL}#except-pass-broad#health"]
