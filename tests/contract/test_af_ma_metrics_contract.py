"""ADM 联动契约测试 · AF→MA 的指标回灌通道（DCD v4.1 七问裁定 第一档①）。

本文件是 **跨仓接口的机器化契约**（消费者驱动）：它把 AutoForge（AF）作为调用方、
向 Memory-Agent（MA）回灌执行指标时所发出的线上 wire 形态冻结下来，进 CI 防漂移。

契约的"真相来源"是 `docs/MA_METRICS_CONTRACT.md`（v0.5.0）与生产者
`src/autoforge/af_metrics.py:Ingester`：
- 端点：`POST {MA_BASE}/api/metrics/ingest`（路径**精确**为 `/api/metrics/ingest`）。
- 鉴权：复用 butler 白名单，`Ingester` 以 `token`（BUTLER_TOKEN）作 `Bearer` 凭据发送。
- 单条 payload 字段见 §2；幂等键 `dedupe_key = {automation_id}:{window}`，
  `window = generated_at[:13]`（小时窗口 `YYYY-MM-DDTHH`）。
- 失败语义：2xx→成功；4xx/5xx/超时→落 `{buffer_dir}/metrics_buffer.jsonl` 并重试。
  这是 AF 侧契约的硬约束——MA 不可用绝不能让 AF 进程崩溃。

捕获方式：直接注入 `Ingester._post` 的 `http_post` 钩子，截获其实际发出的
`(url, metric, token)` 三元组。这正是 urllib 分支在 `_post` 内构造并发送的内容
（`ma_url.rstrip("/") + "/api/metrics/ingest"` + `Bearer {token}` + body），
故**离线可跑、不依赖 NAS 上的 MA 实例**，且无 socket/header 解析歧义。若 MA 改了
端点路径/鉴权，或 AF 改了 payload 形状而忘了同步契约文档，CI 会红——这正是 DCD
要求的"接口契约测试"杠杆。

注：本测试**不改动**任何跨仓接口，仅断言既有契约，属低风险加固，无需 DCD 评审。
（DB→AF 的 ASK 通道契约测试见同目录 `test_af_ask_contract.py`；本文件覆盖另一条
主通道 AF→MA，二者相互独立。）
"""

from __future__ import annotations

import urllib.error
from typing import Any

import pytest

from autoforge.af_metrics import Ingester

# ── 契约冻结点：与 MA_METRICS_CONTRACT.md §2 对齐的字段集 ──
REQUIRED_FIELDS = (
    "dedupe_key",
    "automation_id",
    "window",
    "generated_at",
    "runs",
    "success",
    "failed",
    "success_rate",
    "audit_distribution",
    "confidence",
    "last_event_at",
)
CONF_BANDS = ("auto", "shadow", "ask")
INGEST_PATH = "/api/metrics/ingest"


class _Calls:
    """捕获 Ingester 实际发出的 (url, metric, token)；可模拟成功/失败。"""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_with = fail_with

    def post(self, url: str, metric: dict[str, Any], token: str) -> None:
        self.calls.append({"url": url, "metric": metric, "token": token})
        if self.fail_with is not None:
            raise self.fail_with


def _snapshot() -> dict[str, Any]:
    """构造一份含「有样本」与「无样本」两种自动化的指标快照。"""
    return {
        "generated_at": "2026-09-15T10:00:00",
        "automations": {
            "study_day_light": {
                "runs": 12,
                "success": 11,
                "failed": 1,
                "success_rate": 0.9167,
                "audit_distribution": {"action_failed": 1, "event_emitted": 3},
                "confidence": {"value": 0.92, "band": "auto"},
                "last_event_at": "2026-09-15T09:55:00",
            },
            "no_sample_auto": {
                "runs": 0,
                "success": 0,
                "failed": 0,
                "success_rate": None,
                "audit_distribution": {},
                "confidence": {"value": None, "band": None},
                "last_event_at": None,
            },
        },
    }


def _bodies(calls: _Calls) -> dict[str, dict[str, Any]]:
    return {c["metric"]["automation_id"]: c["metric"] for c in calls.calls}


def test_wire_contract_path_auth_and_schema(tmp_path):
    """端点路径 / 鉴权令牌 / 必填字段 全部符合契约文档。"""
    cap = _Calls()
    res = Ingester(buffer_dir=tmp_path).push(_snapshot(), ma_url="http://ma.invalid", token="butler-tok-123", http_post=cap.post)
    assert res["sent"] == 2 and res["buffered"] == 0
    assert len(cap.calls) == 2
    for c in cap.calls:
        assert c["url"].endswith(INGEST_PATH), f"端点路径须以 {INGEST_PATH} 结尾，实际 {c['url']}"
        assert c["token"] == "butler-tok-123"
        body = c["metric"]
        for k in REQUIRED_FIELDS:
            assert k in body, f"payload 缺契约字段 {k}"


def test_dedupe_key_and_window_format(tmp_path):
    """dedupe_key = {automation_id}:{window}，window = generated_at[:13]。"""
    cap = _Calls()
    Ingester(buffer_dir=tmp_path).push(_snapshot(), ma_url="http://ma", token="t", http_post=cap.post)
    bodies = _bodies(cap)
    a = bodies["study_day_light"]
    assert a["window"] == "2026-09-15T10"
    assert a["dedupe_key"] == "study_day_light:2026-09-15T10"
    assert a["generated_at"] == "2026-09-15T10:00:00"


def test_nullable_fields_and_band_enum(tmp_path):
    """success_rate 可空、last_event_at 可空、confidence.band 受限于枚举。"""
    cap = _Calls()
    Ingester(buffer_dir=tmp_path).push(_snapshot(), ma_url="http://ma", token="t", http_post=cap.post)
    bodies = _bodies(cap)
    ns = bodies["no_sample_auto"]
    assert ns["success_rate"] is None
    assert ns["last_event_at"] is None
    assert ns["confidence"] == {"value": None, "band": None}
    au = bodies["study_day_light"]
    assert isinstance(au["success_rate"], float)
    assert au["confidence"]["band"] in CONF_BANDS


def test_5xx_buffers_and_persists(tmp_path):
    """MA 返回 5xx → 落离线缓冲、不抛异常、不计入 sent。"""
    buf = tmp_path / "metrics_buffer.jsonl"
    cap = _Calls(fail_with=RuntimeError("MA ingest 返回 HTTP 500"))
    res = Ingester(buffer_dir=tmp_path, max_retries=1, base_delay=0.01).push(
        _snapshot(), ma_url="http://ma", token="t", http_post=cap.post
    )
    assert res["sent"] == 0 and res["buffered"] == 2
    assert buf.exists()
    lines = [l for l in buf.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2


def test_timeout_buffers(tmp_path):
    """推送超时（网络不可达）→ 同样落缓冲、不抛异常。"""
    buf = tmp_path / "metrics_buffer.jsonl"
    cap = _Calls(fail_with=urllib.error.URLError("timed out"))
    res = Ingester(buffer_dir=tmp_path, max_retries=1, base_delay=0.01).push(
        _snapshot(), ma_url="http://unreachable.invalid", token="t", http_post=cap.post
    )
    assert res["sent"] == 0 and res["buffered"] == 2
    assert buf.exists()


def test_dry_run_no_network(tmp_path):
    """dry_run 不触网、只计 buffered 计数。"""
    cap = _Calls()
    res = Ingester(buffer_dir=tmp_path).push(
        _snapshot(), ma_url="http://nope.invalid", token="t", dry_run=True, http_post=cap.post
    )
    assert res["dry"] is True and res["sent"] == 0 and res["total"] == 2
    assert cap.calls == []  # dry_run 不应发出任何请求
