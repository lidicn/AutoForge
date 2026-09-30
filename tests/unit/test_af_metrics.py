"""v0.5.0 运行指标聚合 + 回灌客户端（af_metrics）测试。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from autoforge.af_audit import AuditEvent
from autoforge.af_metrics import Ingester, MetricsAggregator

_EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "ir"


# ── mock runtime ────────────────────────────────────────────────────────

@dataclass
class _Auto:
    id: str


class _Conf:
    def __init__(self, values: dict):
        self.values = values


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 15, 10, 0, 0)


class _Audit:
    def __init__(self, events):
        self._events = events

    def __iter__(self):
        return iter(self._events)


class _Runtime:
    def __init__(self):
        self.exec_stats = {"a1": {"runs": 3, "success": 2, "failed": 1}}
        self.audit = _Audit(
            [
                AuditEvent(
                    type="action_failed",
                    at=datetime(2026, 9, 15, 9, 0, 0),
                    automation_id="a1",
                    message="x",
                )
            ]
        )
        self.conf = _Conf({"a1": 0.9, "a2": 0.5})
        self.graph = [_Auto("a1"), _Auto("a2")]
        self.clock = _Clock()


# ── MetricsAggregator ───────────────────────────────────────────────────

def test_aggregator_structure():
    snap = MetricsAggregator(_Runtime()).snapshot()
    assert "overall" in snap and "automations" in snap
    assert snap["generated_at"] == "2026-09-15T10:00:00"
    a1 = snap["automations"]["a1"]
    assert a1["runs"] == 3 and a1["success"] == 2 and a1["failed"] == 1
    assert abs(a1["success_rate"] - 2 / 3) < 1e-3
    assert a1["audit_distribution"] == {"action_failed": 1}
    assert a1["confidence"] == {"value": 0.9, "band": "auto"}
    a2 = snap["automations"]["a2"]
    assert a2["confidence"]["band"] == "ask"  # 0.5 < 0.60


def test_aggregator_overall():
    snap = MetricsAggregator(_Runtime()).snapshot()
    ov = snap["overall"]
    assert ov["total_runs"] == 3
    assert ov["total_success"] == 2
    assert ov["total_failed"] == 1
    assert ov["audit_distribution"] == {"action_failed": 1}


# ── Ingester：推送 / 退避 / 离线缓冲 ────────────────────────────────────

def _snapshot() -> dict:
    return {
        "generated_at": "2026-09-15T10:00:00",
        "overall": {},
        "automations": {
            "a1": {
                "id": "a1", "runs": 3, "success": 2, "failed": 1,
                "success_rate": 0.6667, "audit_distribution": {"action_failed": 1},
                "confidence": {"value": 0.9, "band": "auto"},
                "last_event_at": "2026-09-15T09:00:00",
            }
        },
    }


def test_ingester_to_metrics_dedupe_key():
    metrics = Ingester(max_retries=1)._to_metrics(_snapshot())
    assert metrics[0]["dedupe_key"] == "a1:2026-09-15T10"
    assert metrics[0]["window"] == "2026-09-15T10"


def test_ingester_push_success(tmp_path):
    calls = []

    def post(url, metric, token):
        calls.append((url, metric, token))

    ing = Ingester(buffer_dir=tmp_path, max_retries=1)
    res = ing.push(_snapshot(), ma_url="http://ma", token="t", http_post=post)
    assert res["sent"] == 1 and res["buffered"] == 0
    assert calls[0][2] == "t"
    assert calls[0][0] == "http://ma/api/metrics/ingest"


def test_ingester_dry_run(tmp_path):
    ing = Ingester(buffer_dir=tmp_path, max_retries=1)
    res = ing.push(_snapshot(), ma_url="http://ma", token="t", dry_run=True)
    assert res["dry"] is True and res["buffered"] == 1 and res["sent"] == 0


def test_ingester_http_failure_buffers(tmp_path):
    def boom(url, metric, token):
        raise RuntimeError("network down")

    ing = Ingester(buffer_dir=tmp_path, max_retries=1)
    res = ing.push(_snapshot(), ma_url="http://ma", token="t", http_post=boom)
    assert res["buffered"] == 1 and res["sent"] == 0
    buf = tmp_path / "metrics_buffer.jsonl"
    assert buf.exists()
    lines = buf.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["automation_id"] == "a1"


def test_ingester_flush_buffer_recovers(tmp_path):
    def boom(url, metric, token):
        raise RuntimeError("network down")

    ing = Ingester(buffer_dir=tmp_path, max_retries=1)
    ing.push(_snapshot(), ma_url="http://ma", token="t", http_post=boom)
    buf = tmp_path / "metrics_buffer.jsonl"
    assert buf.exists()

    received = []

    def ok(url, metric, token):
        received.append(metric)

    flush = ing.flush_buffer(ma_url="http://ma", token="t", http_post=ok)
    assert flush["flushed"] == 1
    assert flush["remaining"] == 0
    assert not buf.exists()
    assert received[0]["automation_id"] == "a1"


def test_ingester_idempotent_key_stable(tmp_path):
    """同一窗口的重复推送 key 一致（MA 端据此去重）。"""
    m1 = Ingester(max_retries=1)._to_metrics(_snapshot())[0]
    m2 = Ingester(max_retries=1)._to_metrics(_snapshot())[0]
    assert m1["dedupe_key"] == m2["dedupe_key"]


# ── CLI ─────────────────────────────────────────────────────────────────

def test_cli_metrics_show():
    from typer.testing import CliRunner

    from autoforge.af_cli import app

    case = _EXAMPLES / "case01_day_light.json"
    if not case.exists():
        pytest.skip("样例 case01.json 缺失")
    result = CliRunner().invoke(app, ["metrics", "show", str(case)])
    assert result.exit_code == 0, result.output
    assert "automations" in result.stdout


def test_cli_metrics_push_dry_run():
    from typer.testing import CliRunner

    from autoforge.af_cli import app

    case = _EXAMPLES / "case01_day_light.json"
    if not case.exists():
        pytest.skip("样例 case01.json 缺失")
    result = CliRunner().invoke(app, ["metrics", "push", str(case), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert '"dry": true' in result.stdout


# ── API ─────────────────────────────────────────────────────────────────

def test_api_metrics(monkeypatch):
    monkeypatch.setenv("AF_ALLOW_NOAUTH", "1")
    from fastapi.testclient import TestClient

    from autoforge.af_api import build_app

    app_ = build_app(store_root=str(Path(__file__).resolve().parent.parent / "tmp_metrics_store"))
    client = TestClient(app_)
    r = client.get("/api/metrics")
    assert r.status_code == 200
    body = r.json()
    assert "automations" in body and "overall" in body
    assert body.get("source") == "conf-store"
