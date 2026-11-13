# -*- coding: utf-8 -*-
"""台账读数 pin 对账门判据（`scripts/check_pin_ledger_consistency.py`，2026-11-14 门禁增强批第③笔）。

**它治的病**（§1.226 三套不同步）：新增/移动一枚留痕站点要同步三套——台账自动段
（`--write` 重生成）、§三 手工登记（重钉锚点）、pytest 字面 pin（人改）。前两套有门看着，
第三套此前**只有 CI 全量 pytest 兜底**：v1.30.7 推上去才红（`sites` 259 vs 263、重码白名单
缺 `SCHED_PREMISE_EVAL_FAILED`）。本门在本地 `gates.sh` 把「清单自动段 ↔ `PINS`」逐项对撞。

**为什么不派生**（AF-PM 2026-11-14 裁定·方案 B）：pin 的价值是「有人在这个提交里认过这个数」；
派生会让它与 A 腿同源，任何一次 `--write` 把两边一起带平＝第二次「全绿但没人认过」。

**判据**：

- P1 真仓六读数逐项相等 ⇒ RC=0，且输出点名六项（不是空跑绿）
- P2 清单数字变而 `PINS` 不变 ⇒ RC=1 并点名该改哪个键；反方向（改 `PINS` 而清单不变）**同样**红
- P3 射程塌（PINS 缺失／缺键／写成派生表达式、清单标记缺失、计数行形状变了、2.2 表空）⇒ RC=2
- P4 重码集不一致 ⇒ RC=1 并逐枚点名是哪个码（不是只说「集合不等」）
- P5 计数行**自己不自洽**（无码总数 ≠ 分档求和）⇒ RC=1，且修法指向 `--write` 而不是改 pin

合成仓只放本门真正读的四段（两条计数行＋2.2 表＋标记），⛔ 不复制整份清单——
本门射程就是这四段，复制整份只会让判据跟着清单排版漂。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_pin_ledger_consistency.py"

TABLE_HEAD = "| 码 | 级别 | 站点 | 外层函数 |\n| --- | --- | --- | --- |\n"

ROWS_OK = (
    "| `SYN_ALPHA` | WARNING | `src/autoforge/syn.py:10` | `load` |\n"
    "| `SYN_ALPHA` | WARNING | `src/autoforge/syn.py:20` | `save` |\n"
    "| `SYN_BETA` | ERROR | `src/autoforge/syn.py:30` | `save` |\n"
)

PINS_OK = '''"""合成 pin 文件：本门只认模块级 `PINS` 字面量。"""

PINS: dict[str, object] = {
    "sites": 3,
    "files": 1,
    "levels": {"debug": 0, "info": 0, "warning": 2, "error": 1, "exception": 0, "critical": 0},
    "codes": 2,
    "coded": 3,
    "dup": {"SYN_ALPHA"},
    "uncoded": {"debug": 0, "info": 0, "warning": 0, "error": 0, "exception": 0, "critical": 0},
}
'''


def _ledger(*, sites: int = 3, warning: int = 2, error: int = 1, files: int = 1,
            codes: int = 2, coded: int = 3, uncoded_total: int = 0,
            uncoded_raw: str = "DEBUG=0 INFO=0 WARNING=0 ERROR=0 EXCEPTION=0 CRITICAL=0",
            rows: str = ROWS_OK, begin: bool = True, end: bool = True,
            total_line: bool | str = True, coded_line: bool | str = True) -> str:
    auto_head = "<!-- AUTO-BEGIN 由 scripts/check_observability.py --write 生成，勿手改 -->\n" if begin else ""
    auto_tail = "<!-- AUTO-END -->\n" if end else ""
    if total_line is True:
        total_line = (
            f"现读计数：{sites} 站点／{files} 个文件 · DEBUG=0 · INFO=0 · WARNING={warning} · "
            f"ERROR={error} · EXCEPTION=0 · CRITICAL=0 · 射程外被调对象形状 0 枚（口径见 §一 第 4 条）\n"
        )
    if coded_line is True:
        coded_line = (
            f"现读计数：具名码 {codes} 枚／{coded} 站点 · 无码站点 {uncoded_total} 枚（{uncoded_raw}）\n"
        )
    return (
        "# 可观测性清单（合成）\n\n## 二、现读面（自动段）\n\n"
        f"{auto_head}"
        "### 2.1 留痕站点分布（每文件 × 级别）\n\n| 文件 | WARNING | 合计 |\n| --- | --- | --- |\n"
        f"| `src/autoforge/syn.py` | 2 | 3 |\n\n{total_line}\n"
        "### 2.2 具名码站点现读（首参带全大写码的那批）\n\n"
        f"{TABLE_HEAD}{rows}\n{coded_line}"
        f"{auto_tail}"
    )


def _mk(tmp_path: Path, *, ledger: str | None = None, pins: str | None = None) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "docs" / "可观测性清单.md").write_text(
        ledger if ledger is not None else _ledger(), encoding="utf-8", newline="\n"
    )
    (root / "tests" / "unit" / "test_observability_gate.py").write_text(
        pins if pins is not None else PINS_OK, encoding="utf-8", newline="\n"
    )
    return root


def _run(root: Path, only: str | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), str(root)]
    if only:
        cmd += ["--only", only]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")


# ── P1 真仓：两个条目六／五读数全部相等 ⇒ RC=0，且真的比对了每一项 ──────
def test_p1_real_repo_is_green_and_says_what_it_compared():
    r = _run(REPO)
    assert r.returncode == 0, r.stdout + r.stderr
    for word in ("站点", "文件", "WARNING=", "具名码", "重码", "无码"):
        assert word in r.stdout, f"输出没点名 {word}——这条腿等于没真比对：{r.stdout}"
    assert "PINS" in r.stdout
    # 批 4：进程模型条目也在同一次运行里被比对（少了它，注册表退化也看不出来）
    assert "进程模型清单" in r.stdout and "PINS_PROCESS_MODEL" in r.stdout, r.stdout
    assert "生命周期" in r.stdout and "共享名" in r.stdout, r.stdout


def test_p1b_unknown_ledger_key_is_range_collapse():
    """`--only` 写错名字⛔ 不许静默跳过——那是「一个条目都没比对」冒充绿。"""
    r = _run(REPO, only="no-such-ledger")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "已注册" in r.stdout


# ── P2 两边不同步 ⇒ RC=1，且点名该改哪个键（两个方向都红） ─────────────
def test_p2a_ledger_moved_but_pins_did_not_is_red(tmp_path):
    root = _mk(tmp_path, ledger=_ledger(sites=4, warning=3))
    r = _run(root, only="observability")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "`sites`" in r.stdout and "4" in r.stdout and "3" in r.stdout, r.stdout
    assert "PINS" in r.stdout, "红因必须指向该改的那张表"


def test_p2b_pins_moved_but_ledger_did_not_is_red(tmp_path):
    root = _mk(tmp_path, pins=PINS_OK.replace('"sites": 3', '"sites": 4'))
    r = _run(root, only="observability")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "`sites`" in r.stdout and "4" in r.stdout, r.stdout


def test_p2c_only_one_level_drifts_is_red_and_names_the_level(tmp_path):
    root = _mk(tmp_path, pins=PINS_OK.replace('"warning": 2, "error": 1', '"warning": 3, "error": 1'))
    r = _run(root, only="observability")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "`levels`" in r.stdout and "WARNING" in r.stdout, r.stdout


def test_p2d_codes_and_coded_are_two_numbers_from_one_line(tmp_path):
    """`codes`（枚数）与 `coded`（站点数）来自同一条计数行——只改一格也要红。"""
    root = _mk(tmp_path, pins=PINS_OK.replace('"coded": 3', '"coded": 4'))
    r = _run(root, only="observability")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "`coded`" in r.stdout, r.stdout


# ── P3 射程塌 ⇒ RC=2（⛔ 不许用绿冒充） ────────────────────────────────
def test_p3_pins_missing_is_range_collapse(tmp_path):
    root = _mk(tmp_path, pins='"只有一句话，没有 PINS"\n')
    r = _run(root, only="observability")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "PINS" in r.stdout


def test_p3_pins_missing_key_is_range_collapse(tmp_path):
    root = _mk(tmp_path, pins=PINS_OK.replace('    "dup": {"SYN_ALPHA"},\n', ""))
    r = _run(root, only="observability")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "缺读数键" in r.stdout and "dup" in r.stdout


def test_p3_pins_derived_expression_is_range_collapse(tmp_path):
    """pin 表写成派生表达式 ⇒ 射程塌（本门只认字面量，⛔ 不执行代码）。

    这条不是形式主义：派生正是方案 A 的形状，AF-PM 已裁不采——门必须拒绝被那样糊过去。
    """
    root = _mk(tmp_path, pins='PINS = {k: v for k, v in [("sites", 3)]}\n')
    r = _run(root, only="observability")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "字面量" in r.stdout


def test_p3_pins_file_unparsable_is_range_collapse(tmp_path):
    """pin 表所在文件语法坏掉 ⇒ 也是射程塌（不是「红一条」）。"""
    root = _mk(tmp_path, pins="PINS = {k: 0 for k in ()\n")  # 故意没闭合
    r = _run(root, only="observability")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "解析不了" in r.stdout


def test_p3_auto_markers_missing_is_range_collapse(tmp_path):
    root = _mk(tmp_path, ledger=_ledger(begin=False))
    r = _run(root, only="observability")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "自动段标记" in r.stdout or "AUTO" in r.stdout


def test_p3_count_line_shape_changed_is_range_collapse(tmp_path):
    root = _mk(tmp_path, ledger=_ledger(total_line="现读计数：三个站点（旧排版）\n"))
    r = _run(root, only="observability")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "2.1" in r.stdout


def test_p3_empty_code_table_is_range_collapse(tmp_path):
    root = _mk(tmp_path, ledger=_ledger(rows="", coded=0, codes=0))
    r = _run(root, only="observability")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "2.2" in r.stdout


# ── P4 重码集：逐枚点名 ───────────────────────────────────────────────
def test_p4_dup_set_mismatch_names_the_code(tmp_path):
    root = _mk(tmp_path, pins=PINS_OK.replace('"dup": {"SYN_ALPHA"},', '"dup": set(),'))
    r = _run(root, only="observability")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "SYN_ALPHA" in r.stdout, f"重码不一致必须点名是哪个码：{r.stdout}"
    assert "重码集" in r.stdout


# ── P5 清单自己不自洽（无码总数 ≠ 分档求和）⇒ 指向 --write，不指向 pin ──
def test_p5_ledger_internal_inconsistency_points_at_write(tmp_path):
    root = _mk(tmp_path, ledger=_ledger(uncoded_total=5, uncoded_raw="DEBUG=1 INFO=0 WARNING=0 ERROR=0 EXCEPTION=0 CRITICAL=0"))
    r = _run(root, only="observability")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "不自洽" in r.stdout and "--write" in r.stdout, r.stdout


# ======================================================================================
# 进程模型侧（批 4）：同一套 P1–P5 形状，另一份清单／另一张 pin 表
# ======================================================================================
#: 清单自动段的标记文案与可观测性那份**不同**（生成器不同），所以夹具照抄原文案。
PM_BEGIN = "<!-- AUTO-BEGIN 由 scripts/check_process_model.py --write 生成，勿手改 -->"
PM_END = "<!-- AUTO-END -->"

PM_LEDGER_OK = (
    "# 进程模型清单（合成）\n\n## 二、现读自动段\n\n"
    f"{PM_BEGIN}\n"
    "### 2.1 生命周期站点（拉起子进程／发信号／装处理器／注册退出钩子）\n\n"
    "| kind | 站点 | 外层函数 | 关键参数 |\n| --- | --- | --- | --- |\n"
    "| os.kill | `src/autoforge/a.py:10` | `f` | signal=0 |\n\n"
    "现读计数：atexit.register=1 · os.kill=1 · subprocess.Popen=0\n\n"
    "### 2.2 同步原语站点（进程内 ＋ 跨进程）\n\n"
    "| 原语 | 站点 | 外层函数 | 绑定名 |\n| --- | --- | --- | --- |\n"
    "| FileLock | `src/autoforge/a.py:20` | `f` | `-` |\n"
    "| FileLock | `src/autoforge/b.py:30` | `g` | `-` |\n"
    "| threading.Lock | `src/autoforge/b.py:40` | `g` | `x` |\n\n"
    "现读计数：进程内 1 站点／1 个文件 · 跨进程 2 站点／2 个文件\n\n"
    "### 2.3 模块级共享名（被函数用 `global` 改写）\n\n"
    "| 名字 | 文件 | 改写函数 | global 语句所在函数行 | 函数体内有 `with` | 同模块进程内锁数 |\n"
    "| --- | --- | --- | --- | --- | --- |\n"
    "| `_A` | `src/autoforge/a.py` | `f` | 10 | 否 | 0 |\n"
    "| `_B` | `src/autoforge/b.py` | `g` | 20 | 是 | 1 |\n\n"
    "现读计数：2 枚名／2 个文件；其中函数体内没有任何 `with` 的 1 枚\n"
    f"{PM_END}\n"
)

PM_PINS_OK = '''"""合成 pin 文件：进程模型侧独立表名。"""

PINS_PROCESS_MODEL: dict[str, object] = {
    "lifecycle_counts": {"os.kill": 1, "atexit.register": 1},
    "primitives": {"inproc_sites": 1, "inproc_files": 1,
                   "crossproc_sites": 2, "crossproc_files": 2},
    "crossproc_by_file": {"src/autoforge/a.py": 1, "src/autoforge/b.py": 1},
    "shared": {"names": 2, "files": 2, "no_with": 1},
    "shared_locks": {"src/autoforge/a.py::_A": 0, "src/autoforge/b.py::_B": 1},
}
'''


def _mk_pm(tmp_path: Path, *, ledger: str | None = None, pins: str | None = None) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "docs" / "进程模型清单.md").write_text(
        ledger if ledger is not None else PM_LEDGER_OK, encoding="utf-8", newline="\n"
    )
    (root / "tests" / "unit" / "test_process_model_gate.py").write_text(
        pins if pins is not None else PM_PINS_OK, encoding="utf-8", newline="\n"
    )
    return root


# ── P6 真仓进程模型侧：五块逐格相等（已在 P1 一并断言），这里钉「块名一个不少」 ──
def test_p6_real_repo_process_model_entry_covers_all_five_blocks():
    """防空跑绿：输出必须点名五块，而「dup／无码 N/A」这件事要在读数说明里写明。"""
    r = _run(REPO, only="process_model")
    assert r.returncode == 0, r.stdout + r.stderr
    for word in ("生命周期", "进程内", "跨进程", "共享名", "逐名锁数"):
        assert word in r.stdout, f"进程模型侧没点名 {word}：{r.stdout}"
    assert "N/A" in r.stdout, "该清单没有 dup／无码格，说明书里要显式标 N/A 而不是含糊过去"


# ── P7 清单漂而 pin 没跟 ⇒ RC=1 点名块与子键 ───────────────────────────
def test_p7_pm_lifecycle_count_drift_names_kind(tmp_path):
    root = _mk_pm(tmp_path, ledger=PM_LEDGER_OK.replace("atexit.register=1", "atexit.register=2"))
    r = _run(root, only="process_model")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "lifecycle_counts" in r.stdout and "atexit.register" in r.stdout, r.stdout
    assert "PINS_PROCESS_MODEL" in r.stdout, "红因必须指向该改的那张表"


def test_p8_pm_primitive_drift_names_the_subkey(tmp_path):
    root = _mk_pm(tmp_path, pins=PM_PINS_OK.replace('"inproc_sites": 1', '"inproc_sites": 9'))
    r = _run(root, only="process_model")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "`primitives['inproc_sites']`" in r.stdout, r.stdout


def test_p9_pm_crossproc_by_file_drift_names_the_path(tmp_path):
    root = _mk_pm(tmp_path, pins=PM_PINS_OK.replace('"src/autoforge/a.py": 1', '"src/autoforge/a.py": 3'))
    r = _run(root, only="process_model")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "crossproc_by_file" in r.stdout and "src/autoforge/a.py" in r.stdout, r.stdout


def test_p10_pm_shared_locks_drift_names_file_and_name(tmp_path):
    root = _mk_pm(tmp_path, pins=PM_PINS_OK.replace('"src/autoforge/b.py::_B": 1', '"src/autoforge/b.py::_B": 2'))
    r = _run(root, only="process_model")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "shared_locks" in r.stdout and "src/autoforge/b.py::_B" in r.stdout, r.stdout


def test_p11_pm_shared_name_count_drift_names_the_block(tmp_path):
    root = _mk_pm(tmp_path, pins=PM_PINS_OK.replace('"names": 2', '"names": 3'))
    r = _run(root, only="process_model")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "`shared['names']`" in r.stdout, r.stdout


# ── P12 进程模型侧射程塌 ⇒ RC=2 ────────────────────────────────────────
def test_p12_pm_auto_markers_missing_is_range_collapse(tmp_path):
    root = _mk_pm(tmp_path, ledger=PM_LEDGER_OK.replace(PM_BEGIN, "<!-- 标记被改写了 -->"))
    r = _run(root, only="process_model")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "自动段标记" in r.stdout


def test_p12_pm_pins_missing_is_range_collapse(tmp_path):
    root = _mk_pm(tmp_path, pins='"没有表"\n')
    r = _run(root, only="process_model")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "PINS_PROCESS_MODEL" in r.stdout


def test_p12_pm_primitive_aggregate_mismatch_is_range_collapse(tmp_path):
    """2.2 表按族聚合与计数行不符 ⇒ **射程塌**（解析口径断了），⛔ 不许当 pin 漂报红。

    这条是这道门的自检器：分族规则（`threading.*`＝进程内）与清单不再一致时，
    报「红」会让人去改 pin——那是错的修法；正确动作是先对齐口径。
    """
    root = _mk_pm(tmp_path, ledger=PM_LEDGER_OK.replace("跨进程 2 站点／2 个文件", "跨进程 3 站点／2 个文件"))
    r = _run(root, only="process_model")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "不符" in r.stdout and "分族" in r.stdout, r.stdout


def test_p12_pm_shared_table_vs_count_line_mismatch_is_range_collapse(tmp_path):
    root = _mk_pm(tmp_path, ledger=PM_LEDGER_OK.replace("2 枚名／2 个文件", "3 枚名／2 个文件"))
    r = _run(root, only="process_model")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "2.3" in r.stdout
