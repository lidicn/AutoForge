"""af_test — 测试通道：批量提交 + 自动 approve + 报告生成。

与正式环境完全隔离：/data/test/ vs /data/
不碰真机，只过 build + simulate。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .af_draft import draft_intent, DraftError
from .af_apply import apply
from .af_spec import graph_to_raw
from . import af_service
from .af_store import GraphStore

__all__ = ["TestChannel", "TestError"]

MAX_BATCH_SIZE = 500


class TestError(Exception):
    """测试通道错误。"""


class TestChannel:
    """测试通道：批量提交测试自动化，自动 approve，生成报告。

    测试区完全隔离：/data/test/
    - pending/: 测试待批队列（自动 approve）
    - graphs/: 测试 GraphStore
    - reports/: 测试报告
    """

    def __init__(self, test_root: str = "/data/test"):
        self.test_root = Path(test_root)
        self.pending_dir = self.test_root / "pending"
        self.graphs_dir = self.test_root / "graphs"
        self.reports_dir = self.test_root / "reports"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for d in (self.pending_dir, self.graphs_dir, self.reports_dir):
            d.mkdir(parents=True, exist_ok=True)

    def _get_store(self) -> GraphStore:
        """获取测试区 GraphStore。"""
        return GraphStore(root=str(self.test_root))

    def submit_batch(
        self,
        intents: list[dict[str, Any]],
        batch_id: str | None = None,
    ) -> dict[str, Any]:
        """批量提交测试自动化。

        Args:
            intents: 意图 JSON 列表
            batch_id: 批次 ID（默认自动生成）

        Returns:
            测试报告摘要
        """
        if not intents:
            raise TestError("intents 不能为空")
        if len(intents) > MAX_BATCH_SIZE:
            raise TestError(f"单批次上限 {MAX_BATCH_SIZE} 条，当前 {len(intents)} 条")

        batch_id = batch_id or f"batch-{int(time.time())}"
        results: list[dict[str, Any]] = []

        for idx, intent in enumerate(intents):
            test_id = f"{batch_id}-{idx:03d}"
            result = self._submit_one(intent, test_id)
            results.append(result)

        report = self._gen_report(batch_id, results)
        self._save_report(batch_id, report)
        return report

    def _submit_one(self, intent: dict[str, Any], test_id: str) -> dict[str, Any]:
        """单条提交：draft → build → simulate → auto approve → save。"""
        record: dict[str, Any] = {
            "id": test_id,
            "intent": intent,
            "passed": False,
            "stage": None,
            "error": None,
            "graph_ref": None,
        }

        # 1. draft
        try:
            draft_result = draft_intent(intent)
        except DraftError as e:
            record["stage"] = "draft"
            record["error"] = {"code": e.code, "message": str(e)}
            return record
        except Exception as e:
            record["stage"] = "draft"
            record["error"] = {"code": "E_UNKNOWN", "message": str(e)}
            return record

        if not draft_result.get("ok"):
            record["stage"] = "draft"
            record["error"] = draft_result.get("error")
            return record

        ref = draft_result["ref"]
        record["ref"] = ref

        # 2. build + simulate（用测试区 store）
        store = self._get_store()
        try:
            apply_result = apply(ref, stage="simulate", store=store)
        except Exception as e:
            record["stage"] = "build_sim"
            record["error"] = {"code": "E_APPLY", "message": str(e)}
            return record

        if not apply_result.get("ok"):
            record["stage"] = apply_result.get("stage", "build_sim")
            build = apply_result.get("build", {})
            record["error"] = {
                "build_errors": build.get("errors", []),
                "build_warnings": build.get("warnings", []),
            }
            return record

        record["build_ok"] = True
        record["sim_ok"] = True

        # 3. 直接落盘测试区（不走 pending 队列，测试区自动 approve）
        try:
            from .af_draft import get_staged
            staged = get_staged(ref)
            graph = staged["graph"]
            ir_list = graph_to_raw(graph)
            ir_payload = {"automations": ir_list}

            save_result = af_service.save_graph(
                store,
                ir_payload,
                intent.get("name", test_id),
                note="af_test batch",
                tags=["test"],
            )
            record["graph_ref"] = save_result.get("graph_id", "")
        except Exception as e:
            record["stage"] = "save"
            record["error"] = {"code": "E_SAVE", "message": str(e)}
            return record

        record["passed"] = True
        record["stage"] = "done"
        return record

    def _gen_report(
        self, batch_id: str, results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """生成测试报告。"""
        total = len(results)
        passed = sum(1 for r in results if r["passed"])
        failed = total - passed

        # 失败原因分类
        fail_reasons: dict[str, int] = {}
        for r in results:
            if r["passed"]:
                continue
            stage = r.get("stage", "unknown")
            error = r.get("error", {})
            if isinstance(error, dict):
                code = error.get("code", stage)
            else:
                code = stage
            fail_reasons[code] = fail_reasons.get(code, 0) + 1

        return {
            "batch_id": batch_id,
            "total": total,
            "pass": passed,
            "fail": failed,
            "pass_rate": f"{passed/total*100:.1f}%" if total > 0 else "0%",
            "fail_reasons": fail_reasons,
            "details": results,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def _save_report(self, batch_id: str, report: dict[str, Any]) -> None:
        """保存测试报告。"""
        report_path = self.reports_dir / f"{batch_id}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    def get_report(self, batch_id: str) -> dict[str, Any]:
        """获取测试报告。"""
        report_path = self.reports_dir / f"{batch_id}.json"
        if not report_path.exists():
            raise TestError(f"报告不存在: {batch_id}")
        with open(report_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_reports(self) -> list[dict[str, Any]]:
        """列出所有测试报告（摘要）。"""
        reports = []
        for f in sorted(self.reports_dir.glob("*.json")):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    r = json.load(fp)
                reports.append({
                    "batch_id": r.get("batch_id", f.stem),
                    "total": r.get("total", 0),
                    "pass": r.get("pass", 0),
                    "fail": r.get("fail", 0),
                    "pass_rate": r.get("pass_rate", "0%"),
                    "created_at": r.get("created_at", ""),
                })
            except Exception:
                continue
        return reports

    def clear(self) -> dict[str, Any]:
        """清空测试区。"""
        shutil.rmtree(self.test_root, ignore_errors=True)
        self._ensure_dirs()
        return {"ok": True, "cleared": True, "test_root": str(self.test_root)}


# 全局测试通道实例
_test_channel: TestChannel | None = None


def get_test_channel(test_root: str = "/data/test") -> TestChannel:
    """获取全局测试通道实例。"""
    global _test_channel
    if _test_channel is None:
        _test_channel = TestChannel(test_root)
    return _test_channel
