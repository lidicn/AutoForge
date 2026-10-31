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

from homesdk.adm.errors import ADM_ERR_INTERNAL, ADM_ERR_PAYLOAD_INVALID

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
    """测试通道错误。

    带具名 `code`（裁定 20261006 §二.2「凡是联动失败，必须带码」）：`af_test_submit`／`af_test_report`
    这两站过去只回 `{"ok": False, "error": str(e)}`，对端拿到一句自由文本，分不出"入参不合法"与
    "内部没做成"——⛔ 新造码，缺省吃 homesdk 目录里的兜底档 `ADM_ERR_INTERNAL`；入参校验那一族
    就地给 `ADM_ERR_PAYLOAD_INVALID`，守卫子类（`TestGuardError`）继续带自己的两枚具名码。
    """

    def __init__(self, message: str, code: str = ADM_ERR_INTERNAL) -> None:
        super().__init__(message)
        self.code = code


class TestGuardError(TestError):
    """删除守卫拒判：测试区的形状不可信。带具名 `code` 给调用方如实回（不许塌成 `ok: True`）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}", code=code)


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

    def _resolve_catalog(self):
        """名称解析用的设备目录——来自**主 store**，不是测试区那一份。

        体验者 AF-015（2026-10-29）：同一个中文设备名，正式 `af_draft` 能解析、
        测试通道解析不出来。根因是 `draft_intent(intent)` 在这里**根本没传 catalog**，
        而 `svc._catalog(store)` 的目录缓存与 HA 凭据都挂在主 store 根（`/data`）上；
        测试区 `/data/test` 是空目录 ⇒ 零候选 ⇒ 全部落成 `?占位符`。
        v1.14.12 之前这些占位符能静默过闸（AF-013 橡皮图章），现在会被
        `UNRESOLVED_ENTITY` 响亮拒绝——拒绝是对的，但**测试通道本来就该能解析名字**。

        只读解析：测试区的隔离性体现在归档/队列/报告，不体现在设备目录（那是全局事实）。
        构造失败退回 None——此时中文名退化成 `?占位符`，由 build 闸拒绝，⛔ 不静默。
        """
        from . import af_service as svc
        try:
            return svc._catalog(GraphStore(root=str(Path(self.test_root).parent)))
        except Exception:  # 目录拿不到（HA 未配/凭据缺失）时不阻断测试通道
            return None

    def _main_catalog_entity_ids(self) -> set[str] | None:
        """build 实体校验的白名单——主 store 设备目录全集（AF-123 假绿收口）。

        与 `_resolve_catalog` 同源（AF-015）：设备目录是全局事实，挂在主 store 根上。
        测试区的 store 是空目录，旧实现让 build 的 use_catalog 在测试根上拿空目录 ⇒
        `entity_check=skipped` ⇒ 幽灵实体（如 `binary_sensor.ghost99`）一路绿——
        审计 AF-123 的 40 连绿就是这么来的。这里把主目录全集递给 `apply` 透传到
        `svc.build(known_entities=...)`。

        目录为空 / 构造失败 ⇒ None：空目录是合法首建态，fail-open 不拦截，但**必须
        说话**——build 回执带 `entity_check=skipped` + `ENTITY_CHECK_DEGRADED` 警告，
        报告明细如实转述（见 `_submit_one` 成功路径），不许装作校验过。
        """
        catalog = self._resolve_catalog()
        if catalog is None:
            return None
        try:
            ids = catalog.known_entity_ids()
        except Exception:  # noqa: BLE001 - 目录读不出等同空目录：fail-open 但要说话
            return None
        return ids or None

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
            raise TestError("intents 不能为空", ADM_ERR_PAYLOAD_INVALID)
        if len(intents) > MAX_BATCH_SIZE:
            raise TestError(f"单批次上限 {MAX_BATCH_SIZE} 条，当前 {len(intents)} 条", ADM_ERR_PAYLOAD_INVALID)

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

        # 1. draft（名称解析走主 store 目录，见 `_resolve_catalog`）
        try:
            draft_result = draft_intent(intent, catalog=self._resolve_catalog())
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
        # AF-123（假绿三族，v1.14.22）：实体存在性校验用**主 store 设备目录**
        # （`_main_catalog_entity_ids`），不是测试区 store 的空目录——否则
        # entity_check=skipped，幽灵实体检测不到。
        store = self._get_store()
        known = self._main_catalog_entity_ids()
        try:
            apply_result = apply(ref, stage="simulate", store=store, known_entities=known)
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
                # apply 层面的失败（如 `simulate_noop`：合成过触发仍零实例）不带 build 明细，
                # 旧实现只抄 build ⇒ 测试通道报"失败"却说不出为什么，等于又一个橡皮图章的反面。
                "apply_error": apply_result.get("error"),
                "reason": apply_result.get("reason"),
                "sim_coverage": apply_result.get("sim_coverage"),
            }
            return record

        record["build_ok"] = True
        record["sim_ok"] = True
        # AF-123：成功记录也要带 build 的 entity_check 与 warnings——旧实现只在
        # 失败路径抄 build 明细，成功路径把 ENTITY_CHECK_DEGRADED 整个丢了，
        # 「pass=True 但存在性校验根本没跑」在报告里读不出来。
        build = apply_result.get("build", {})
        record["entity_check"] = build.get("entity_check")
        record["build_warnings"] = build.get("warnings", [])
        if known is None:
            record["entity_check_note"] = (
                "主设备目录为空/不可用：实体存在性校验 skipped（fail-open 合法首建态），"
                "幽灵实体检测不到——这条 passed 不构成存在性背书，先 af_catalog 刷新目录"
            )

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
            # AF-123（附带）：save_graph 的返回键里**没有 graph_id**（实测：
            # blast_radius/name/note/ok/owner/tags/version/warnings），旧实现
            # `get("graph_id", "")` 恒为空串——报告里的 graph_ref 从来没指过任何图。
            # 用 name+version 拼出可回指的引用；不动 save_graph 契约。
            record["graph_ref"] = (
                f"{save_result.get('name', '')}@v{save_result.get('version', '')}"
            )
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
