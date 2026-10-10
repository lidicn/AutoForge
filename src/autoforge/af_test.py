"""af_test — 测试通道：批量提交 + 自动 approve + 报告生成。

与正式环境完全隔离：/data/test/ vs /data/
不碰真机，只过 build + simulate。

`clear()` 是这条线上唯一的**不可逆删除面**（经 MCP 工具 `af_test_clear` 直达，scope `write`），
所以它删之前必过守卫、删之后必实测残留：

- 守卫的对照量是**这个进程真正在用的正式存储根**（`store.root`，由调用方递进来），不是本模块
  另抄的一份配置——`/data/test` 这种字面默认值当不了判据，`test_root` 指到哪算哪更不行；
- `shutil.rmtree(..., ignore_errors=True)` 已撤：那一档把"没删掉"抹平成 `cleared: True`，
  与仓内"没做成都不许报成 done"那一族同形（`af_adapters/inbox.py` 的投递失败、桥的缺凭据拒发）。
  现在残留是**数出来的**（`residual`），删除失败现场按 `onerror` 收进 `errors` 并如实带条数上限。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from .af_atomic import atomic_write_text
from .af_draft import draft_intent, DraftError
from .af_apply import apply
from .af_spec import graph_to_raw
from . import af_service
from .af_store import GraphStore

__all__ = ["TestChannel", "TestError", "TestGuardError", "assert_test_area_deletable"]

MAX_BATCH_SIZE = 500
#: `clear()` 的删除现场最多列几条——常驻服务里不能因为一次失败删除就攒出无界清单（BUG-01 同族）。
DELETE_ERRORS_MAX = 20

GUARD_ERR_FILESYSTEM_ROOT = "TEST_ROOT_IS_FILESYSTEM_ROOT"
GUARD_ERR_COVERS_PRODUCTION = "TEST_ROOT_COVERS_PRODUCTION"


class TestError(Exception):
    """测试通道错误。"""


class TestGuardError(TestError):
    """删除守卫拒判：测试区的形状不可信。带具名 `code` 给调用方如实回（不许塌成 `ok: True`）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _count_entries(root: Path) -> int:
    """`root` 之下的条目数（不含它自己）；目录不存在算 0。"""
    if not root.is_dir():
        return 0
    return sum(1 for _ in root.rglob("*"))


def _covers(target: Path, protected: Path) -> bool:
    """target 与 protected 同径，或 protected 落在 target 里面（删下去会连带销毁正式区）。"""
    if target == protected:
        return True
    try:
        protected.relative_to(target)
    except ValueError:
        return False
    return True


def assert_test_area_deletable(test_root: str | Path, protected_root: str | Path) -> None:
    """删除前的形状守卫：只拦"删下去必然出格"的两种目标，合法测试区照常放行。

    两枚码各自挡一种真实事故（都在临时树里复现过，见执行记录 §二之八十六）：

    - `TEST_ROOT_IS_FILESYSTEM_ROOT`：目标就是盘根（`/`、`C:\\`），不可能是测试区；
    - `TEST_ROOT_COVERS_PRODUCTION`：目标**等于**正式存储根，或是它的**祖先**——部署里正式根是
      `forge serve --store-root`（缺省 `DEFAULT_STORE_ROOT = ".forge"`，相对路径），测试区约定在它下面的
      `test/`，所以"祖先"这一档就是把手补在正式区头上把整条归档线删掉。对照量由调用方现递
      `store.root`（同一个进程真正在用的那一份），本模块不另抄一份配置，也不拿 `/data/test`
      这种字面默认值当判据。

    两侧都 `resolve()` 再比：正式根常写成相对路径（`DEFAULT_STORE_ROOT = ".forge"`），
    不 resolve 就会让"同一条路径的两种写法"看起来互不包含——那等于守卫形同虚设。
    """
    target = Path(test_root).resolve()
    protected = Path(protected_root).resolve()
    if target.parent == target:
        raise TestGuardError(
            GUARD_ERR_FILESYSTEM_ROOT,
            f"删除目标 {target} 就是文件系统根，不可能是测试区",
        )
    if _covers(target, protected):
        raise TestGuardError(
            GUARD_ERR_COVERS_PRODUCTION,
            f"删除目标 {target} 覆盖正式存储根 {protected}（同径或为其祖先），拒删",
        )


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
        # 走原子助手：报告是 WebUI 轮询读的，崩在半截会被读成「没有这份报告」
        # （判据 E，审计 BUG-05）
        atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2))

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

    def clear(self, *, protected_root: str | Path) -> dict[str, Any]:
        """清空测试区：删之前过守卫、删之后数残留，报的数就是盘上的数。

        `protected_root` 是**必填的关键字参数**（不是可选保险）：调用方必须把"这个进程真正在用的
        正式存储根"递进来，守卫才有对照量。少递一个就是 `TypeError`——宁可当场炸，也不让一次
        不可逆删除在"没人知道正式区在哪"的状态下发生。
        """
        assert_test_area_deletable(self.test_root, protected_root)
        target = self.test_root
        before = _count_entries(target)
        errors: list[str] = []

        def _collect(_func, path, exc_info) -> None:  # noqa: ANN001 - shutil.rmtree 的 onerror 形状
            errors.append(f"{path}: {exc_info[0].__name__}")

        if target.is_dir():
            shutil.rmtree(target, onerror=_collect)
        residual = _count_entries(target)   # 先量残留再重建目录，否则 `_ensure_dirs()` 会把三条空目录算成残留
        self._ensure_dirs()
        cleared = residual == 0
        out: dict[str, Any] = {
            "ok": cleared and not errors,
            "cleared": cleared,
            "test_root": str(target),
            "entries_before": before,
            "residual": residual,
        }
        if errors:
            out["errors"] = errors[:DELETE_ERRORS_MAX]
            out["errors_total"] = len(errors)
        return out


# 全局测试通道实例
_test_channel: TestChannel | None = None


def get_test_channel(test_root: str = "/data/test") -> TestChannel:
    """获取全局测试通道实例。"""
    global _test_channel
    if _test_channel is None:
        _test_channel = TestChannel(test_root)
    return _test_channel
