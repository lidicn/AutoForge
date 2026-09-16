"""v0.5.0 运行指标聚合 + 回灌 MA 客户端。

- `MetricsAggregator`：从 Runtime 聚合 **执行次数 / 成功率 / 审计分布 / 置信度变化**。
- `Ingester`：把指标推到 memory-agent（复用 butler 白名单 + Bearer 鉴权），
  具备 **幂等**（dedupe_key）、**指数退避**、**离线缓冲**（JSONL 落盘、恢复续传）。

契约（payload / 去重 / 鉴权）见 `docs/MA_METRICS_CONTRACT.md`。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .af_conf import decision_for

__all__ = ["MetricsAggregator", "Ingester", "DEFAULT_BUFFER_DIR"]

DEFAULT_BUFFER_DIR = ".af_metrics_buffer"


class MetricsAggregator:
    """从 Runtime 聚合运行指标，输出可 JSON 化的快照 dict。"""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    def snapshot(self) -> dict[str, Any]:
        exec_stats = self.runtime.exec_stats
        audit = self.runtime.audit
        conf = self.runtime.conf
        per_auto: dict[str, Any] = {}
        for auto in self.runtime.graph:
            aid = auto.id
            es = exec_stats.get(aid, {"runs": 0, "success": 0, "failed": 0})
            total = es["success"] + es["failed"]
            sr = round(es["success"] / total, 4) if total else None
            dist: dict[str, int] = {}
            last_at = None
            for ev in audit:
                if ev.automation_id != aid:
                    continue
                dist[ev.type] = dist.get(ev.type, 0) + 1
                if last_at is None or ev.at > last_at:
                    last_at = ev.at
            cval = conf.values.get(aid)
            band = decision_for(cval) if cval is not None else None
            per_auto[aid] = {
                "id": aid,
                "runs": es["runs"],
                "success": es["success"],
                "failed": es["failed"],
                "success_rate": sr,
                "audit_distribution": dist,
                "confidence": {"value": cval, "band": band},
                "last_event_at": last_at.isoformat() if last_at else None,
            }
        total_runs = sum(v["runs"] for v in exec_stats.values())
        total_success = sum(v["success"] for v in exec_stats.values())
        total_failed = sum(v["failed"] for v in exec_stats.values())
        overall = total_success + total_failed
        overall_sr = round(total_success / overall, 4) if overall else None
        overall_audit: dict[str, int] = {}
        for ev in audit:
            overall_audit[ev.type] = overall_audit.get(ev.type, 0) + 1
        return {
            "generated_at": self.runtime.clock.now().isoformat(),
            "overall": {
                "total_runs": total_runs,
                "total_success": total_success,
                "total_failed": total_failed,
                "overall_success_rate": overall_sr,
                "audit_distribution": overall_audit,
            },
            "automations": per_auto,
        }


class Ingester:
    """回灌客户端：幂等 + 指数退避 + 离线缓冲（JSONL）。

    推送一条指标失败时按 2^n 秒退避重试（封顶 30s，最多 `max_retries` 次），
    仍失败则写入 `{buffer_dir}/metrics_buffer.jsonl` 落盘；`flush_buffer()` 重发缓冲，
    成功即删、失败保留 → 断网不丢、恢复续传。
    """

    def __init__(
        self,
        buffer_dir: str | Path | None = DEFAULT_BUFFER_DIR,
        *,
        max_retries: int = 5,
        base_delay: float = 2.0,
    ):
        self.buffer_dir = Path(buffer_dir) if buffer_dir else None
        self.max_retries = max_retries
        self.base_delay = base_delay

    def _to_metrics(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        gen = snapshot.get("generated_at", "")
        # 按小时窗口去重：同一自动化在窗口内只计一次
        hour = gen[:13] if gen else "na"
        for aid, a in snapshot.get("automations", {}).items():
            out.append(
                {
                    "dedupe_key": f"{aid}:{hour}",
                    "automation_id": aid,
                    "window": hour,
                    "generated_at": gen,
                    "runs": a["runs"],
                    "success": a["success"],
                    "failed": a["failed"],
                    "success_rate": a["success_rate"],
                    "audit_distribution": a["audit_distribution"],
                    "confidence": a["confidence"],
                    "last_event_at": a["last_event_at"],
                }
            )
        return out

    def push(
        self,
        snapshot: dict[str, Any],
        *,
        ma_url: str,
        token: str,
        dry_run: bool = False,
        http_post: Callable[[str, dict[str, Any], str], None] | None = None,
    ) -> dict[str, Any]:
        """聚合快照并逐条推送；返回 {sent, buffered, dry, total}。"""
        metrics = self._to_metrics(snapshot)
        results: dict[str, Any] = {
            "sent": 0,
            "buffered": 0,
            "dry": dry_run,
            "total": len(metrics),
        }
        for m in metrics:
            if dry_run:
                results["buffered"] += 1
                continue
            try:
                self._post_with_retry(m, ma_url, token, http_post=http_post)
                results["sent"] += 1
            except Exception:  # noqa: BLE001 - 任何推送失败都落缓冲
                self._buffer(m)
                results["buffered"] += 1
        return results

    def _post_with_retry(
        self,
        metric: dict[str, Any],
        ma_url: str,
        token: str,
        http_post: Callable[[str, dict[str, Any], str], None] | None = None,
    ) -> None:
        delay = self.base_delay
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                self._post(metric, ma_url, token, http_post=http_post)
                return
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if attempt < self.max_retries - 1:
                    time.sleep(min(delay, 30.0))
                    delay *= 2
        raise last_err if last_err else RuntimeError("未知推送错误")

    def _post(
        self,
        metric: dict[str, Any],
        ma_url: str,
        token: str,
        http_post: Callable[[str, dict[str, Any], str], None] | None = None,
    ) -> None:
        if http_post is not None:
            http_post(ma_url.rstrip("/") + "/api/metrics/ingest", metric, token)
            return
        import urllib.request

        req = urllib.request.Request(
            ma_url.rstrip("/") + "/api/metrics/ingest",
            data=json.dumps(metric, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"MA ingest 返回 HTTP {resp.status}")

    def _buffer(self, metric: dict[str, Any]) -> None:
        if not self.buffer_dir:
            return
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        buf = self.buffer_dir / "metrics_buffer.jsonl"
        with buf.open("a", encoding="utf-8") as f:
            f.write(json.dumps(metric, ensure_ascii=False) + "\n")

    def flush_buffer(
        self,
        *,
        ma_url: str,
        token: str,
        http_post: Callable[[str, dict[str, Any], str], None] | None = None,
    ) -> dict[str, Any]:
        """重发缓冲中所有待发指标；成功即删、失败保留。"""
        if not self.buffer_dir:
            return {"flushed": 0, "remaining": 0}
        buf = self.buffer_dir / "metrics_buffer.jsonl"
        if not buf.exists():
            return {"flushed": 0, "remaining": 0}
        lines = [ln.strip() for ln in buf.read_text(encoding="utf-8").splitlines() if ln.strip()]
        remaining: list[str] = []
        flushed = 0
        for line in lines:
            metric = json.loads(line)
            try:
                self._post_with_retry(metric, ma_url, token, http_post=http_post)
                flushed += 1
            except Exception:  # noqa: BLE001
                remaining.append(line)
        if remaining:
            buf.write_text("\n".join(remaining) + "\n", encoding="utf-8")
        else:
            buf.unlink()
        return {"flushed": flushed, "remaining": len(remaining)}
