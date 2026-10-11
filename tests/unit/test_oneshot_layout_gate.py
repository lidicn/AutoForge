"""仓库卫生门禁（`scripts/check_oneshot_layout.py`）的判据腿。

收第六轮审计 §ARCH-08 的残余：`scripts/oneshot/` 约定＋对根目录一次性脚本的机械拒绝＋存量只减不增。
口径关键：判据读的是 **git 索引（入库面）**，不是磁盘——所以合成树腿一律显式传 `tracked=[…]`，
让"库里有／盘上没有"与反过来的两种形状各自可判；git 真读不出时门必须退 2（下面有两条 CLI 腿钉这一格）。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_oneshot_layout.py"
DOC_REL = "docs/仓库卫生清单.md"

BASE_TRACKED = [
    ".gitignore",
    DOC_REL,
    "README.md",
    "src/autoforge/__init__.py",
    "src/autoforge/af_api.py",
    "src/autoforge/af_service.py",
    "scripts/oneshot/README.md",
    "scripts/oneshot/export_ir.py",
]
BASE_GITIGNORE = """\
__pycache__/
*.pyc

# 一次性调试脚本（下划线前缀，用完即弃，不入库）
_*.py
_*.sh
_*.conf
!**/__init__.py
"""
LEGACY_NONE = "| 无 | — | — | — |\n"
MIGRATION_ONE = "| `export_ir.py` | `scripts/oneshot/export_ir.py` | 一次性导出脚本 |\n"


def _gate():
    spec = importlib.util.spec_from_file_location("check_oneshot_layout", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _doc(tmp_path: Path, gate, tracked: list[str], *, legacy_rows: str = LEGACY_NONE,
         migration_rows: str = MIGRATION_ONE, auto: list[str] | None = None,
         drop: tuple[str, ...] = ()) -> Path:
    """搭一棵带登记面的树。自动段用门自己的渲染器生成，测试里不手抄第二份格式。"""
    legacy_block = (f"## 二、存量登记（根目录入库 `.py`；只减不增，当前上限 {gate.LEGACY_CAP} 条）\n"
                    "| 文件 | 一句话身份 | 为什么还留在根 | 处置 |\n|---|---|---|---|\n" + legacy_rows)
    migration_block = ("## 四、迁移记录\n| 原根目录名 | 现入库路径 | 为什么搬 |\n|---|---|---|\n" + migration_rows)
    entries, _reds, _errs, _none = gate.parse_legacy(
        ["## 二、存量登记（略）"] + legacy_rows.splitlines())
    if auto is None:
        auto = gate.render_auto(gate.read_state(tmp_path, tracked), len(entries), gate.LEGACY_CAP)
    blocks = {
        "LEGACY": legacy_block,
        "IGNORE": "## 三、`.gitignore` 防线\n两枚锚点见门。\n",
        "MIGRATION": migration_block,
        "AUTO": gate.AUTO_BEGIN + "\n" + "\n".join(auto) + "\n" + gate.AUTO_END,
    }
    parts = ["# 仓库卫生清单\n"] + [blocks[k] for k in ("LEGACY", "IGNORE", "MIGRATION", "AUTO") if k not in drop]
    doc = tmp_path / DOC_REL
    doc.parent.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(p if p.endswith("\n") else p + "\n" for p in parts)
    doc.write_text(body, encoding="utf-8", newline="\n")
    (tmp_path / ".gitignore").write_text(BASE_GITIGNORE, encoding="utf-8", newline="\n")
    return tmp_path


def _rc(repo: Path, tracked: list[str]) -> tuple[int, list[str], dict[str, object]]:
    gate = _gate()
    red, errs, info = gate.check(repo, tracked=tracked)
    return (2 if errs else (1 if red else 0)), [*errs, *red], info


# ── 合成树：绿基线 ＋ 五条判据各自可红 ─────────────────────────────


def test_baseline_shape_is_green(tmp_path):
    """修好之后的形状必须判绿——否则这道门叫永久红，不叫门。"""
    gate = _gate()
    repo = _doc(tmp_path, gate, BASE_TRACKED)
    rc, findings, info = _rc(repo, BASE_TRACKED)
    assert rc == 0, f"干净形状仍被判红：{findings}"
    assert info["root_py"] == []
    assert info["oneshot"] == ["scripts/oneshot/README.md", "scripts/oneshot/export_ir.py"]


def test_new_root_script_cannot_be_made_green_by_registering(tmp_path):
    """根目录多一枚入库 `.py`：不登记 ⇒ 判据 B 红；往登记表补一行 ⇒ 红换到判据 C（上限 0）。两种都绿不了。

    这一格是本轮被自己的测试抓出来的：`LEGACY_CAP = 0` 之下，"登记表不是豁免通道"靠的是 C 那一格，
    ⛔ 不是 B——登记确实让 B 放过。断言按事实写成"换腿不换结论"，不按愿望写成"B 照样响"。
    """
    gate = _gate()
    tracked = [*BASE_TRACKED, "probe_once.py"]
    repo_unregistered = _doc(tmp_path, gate, BASE_TRACKED)
    rc1, f1, _ = _rc(repo_unregistered, tracked)
    assert rc1 == 1 and any(x.startswith("B：") and "probe_once.py" in x for x in f1), f1
    repo_registered = _doc(tmp_path / "reg", gate, tracked,
                           legacy_rows="| `probe_once.py` | 一次性探针 | 忘了搬 | 保留 |\n")
    rc2, f2, _ = _rc(repo_registered, tracked)
    assert rc2 == 1 and any(x.startswith("C：") and "上限" in x for x in f2), f2
    assert not any(x.startswith("B：") for x in f2), f"读数变了：登记面现在也拦不住 C，B 却还响 ⇒ {f2}"


def test_smuggled_underscore_script_is_rejected(tmp_path):
    """判据 B②：`git add -f` 穿过 `_*.py` 的那枚单下划线脚本 ⇒ 红——这正是 ignore 管不到的入库面。"""
    gate = _gate()
    tracked = [*BASE_TRACKED, "src/autoforge/_smuggled.py"]
    repo = _doc(tmp_path, gate, tracked)
    rc, findings, _info = _rc(repo, tracked)
    assert rc == 1, findings
    assert any(f.startswith("B：") and "_smuggled.py" in f and "git add -f" in f for f in findings), findings


def test_package_marker_is_not_an_underscore_hit(tmp_path):
    """反例（防过宽）：双下划线包标记 ⛔ 不在本门射程——把它拦下就是 §二之四十四 那格的反向事故。"""
    gate = _gate()
    tracked = [*BASE_TRACKED, "src/autoforge/af_closedloop/__init__.py"]
    repo = _doc(tmp_path, gate, tracked)
    rc, findings, _info = _rc(repo, tracked)
    assert rc == 0, f"包标记被判成一次性脚本：{findings}"


def test_stale_registration_is_red(tmp_path):
    """判据 C：登记了根目录脚本而索引里没有 ⇒ 过期登记红（棘轮的"减"要落到表上）。"""
    gate = _gate()
    repo = _doc(tmp_path, gate, BASE_TRACKED,
                legacy_rows="| `gone_away.py` | 早就删了的一次性件 | 历史 | 保留 |\n")
    rc, findings, _info = _rc(repo, BASE_TRACKED)
    assert rc == 1 and any(f.startswith("C：") and "过期登记" in f for f in findings), findings


def test_registration_row_without_a_reason_is_red(tmp_path):
    """判据 C：理由写成占位词＝没认领。⛔ 不是"表里有这行就放过"。"""
    gate = _gate()
    tracked = [*BASE_TRACKED, "probe_once.py"]
    repo = _doc(tmp_path, gate, BASE_TRACKED, legacy_rows="| `probe_once.py` | 探针 | 待补 | 保留 |\n")
    rc, findings, _info = _rc(repo, tracked)
    assert rc == 1 and any(f.startswith("C：") and "占位词" in f for f in findings), findings


@pytest.mark.parametrize("anchor", ["_*.py", "!**/__init__.py"])
def test_missing_gitignore_anchor_is_red(tmp_path, anchor):
    """判据 D：两枚 ignore 锚点各自失守都要红（少 `_*.py`＝防线没了；少例外＝包标记又被吃掉）。"""
    gate = _gate()
    repo = _doc(tmp_path, gate, BASE_TRACKED)
    text = (repo / ".gitignore").read_text(encoding="utf-8").replace(anchor + "\n", "")
    assert anchor not in text, "注入没生效就判 FAIL——先确认锚点还在不在"
    (repo / ".gitignore").write_text(text, encoding="utf-8", newline="\n")
    rc, findings, _info = _rc(repo, BASE_TRACKED)
    assert rc == 1 and any(f.startswith("D：") and anchor in f for f in findings), findings


def test_missing_convention_readme_is_red(tmp_path):
    """判据 E：约定目录的说明文件没入库 ⇒ "一次性脚本放哪"又变回口头约定。"""
    gate = _gate()
    tracked = [p for p in BASE_TRACKED if p != "scripts/oneshot/README.md"]
    repo = _doc(tmp_path, gate, BASE_TRACKED)
    rc, findings, _info = _rc(repo, tracked)
    assert rc == 1 and any(f.startswith("E：") and "README.md" in f for f in findings), findings


def test_migration_claim_without_the_move_is_red(tmp_path):
    """判据 E：登记说搬到了 `scripts/oneshot/…`，索引里却没有 ⇒ 登记比事实乐观。"""
    gate = _gate()
    tracked = [p for p in BASE_TRACKED if p != "scripts/oneshot/export_ir.py"]
    repo = _doc(tmp_path, gate, tracked)
    rc, findings, _info = _rc(repo, tracked)
    assert rc == 1 and any(f.startswith("E：") and "登记比事实乐观" in f for f in findings), findings


def test_copy_instead_of_move_is_red(tmp_path):
    """判据 E＋B：新旧两处都在库（复制而非移动）⇒ 残骸有两处，两条判据一起响。"""
    gate = _gate()
    tracked = [*BASE_TRACKED, "export_ir.py"]
    repo = _doc(tmp_path, gate, BASE_TRACKED)
    rc, findings, _info = _rc(repo, tracked)
    assert rc == 1, findings
    assert any(f.startswith("E：") and "复制而非移动" in f for f in findings), findings
    assert any(f.startswith("B：") and "export_ir.py" in f for f in findings), findings


def test_auto_section_drift_is_red(tmp_path):
    """判据 A：手改自动段（或加了文件不重生成）⇒ 逐行对撞红。"""
    gate = _gate()
    repo = _doc(tmp_path, gate, BASE_TRACKED)
    text = (repo / DOC_REL).read_text(encoding="utf-8").replace(
        "`scripts/oneshot/` 入库文件（2 个）", "`scripts/oneshot/` 入库文件（9 个）")
    assert "9 个" in text, "注入没生效就判 FAIL——先确认自动段那一行的写法"
    (repo / DOC_REL).write_text(text, encoding="utf-8", newline="\n")
    rc, findings, _info = _rc(repo, BASE_TRACKED)
    assert rc == 1 and any(f.startswith("A：") for f in findings), findings


def test_write_regenerates_the_auto_section(tmp_path):
    """`--write` 的闭环：漂完再写回去，判据 A 必须重新绿——否则登记面不可维护。"""
    gate = _gate()
    repo = _doc(tmp_path, gate, BASE_TRACKED)
    text = (repo / DOC_REL).read_text(encoding="utf-8").replace("（2 个）", "（7 个）")
    assert "（7 个）" in text, "注入没生效就判 FAIL"
    (repo / DOC_REL).write_text(text, encoding="utf-8", newline="\n")
    assert gate.check(repo, tracked=BASE_TRACKED)[0], "改掉自动段后门却不红：判据 A 是空门"
    assert gate.write_doc(repo, tracked=BASE_TRACKED) == 0
    assert gate.check(repo, tracked=BASE_TRACKED)[0] == [], "写完仍红：--write 与判据 A 不是同一口径"


# ── 射程塌：一律退 2，不许用"没发现"冒充"没问题" ──────────────────


@pytest.mark.parametrize("drop,needle", [("LEGACY", "存量登记"), ("MIGRATION", "迁移记录"), ("AUTO", "AUTO")])
def test_missing_section_is_scope_collapse(tmp_path, drop, needle):
    gate = _gate()
    repo = _doc(tmp_path, gate, BASE_TRACKED, drop=(drop,))
    rc, findings, _info = _rc(repo, BASE_TRACKED)
    assert rc == 2, f"{needle} 那节没了却 rc={rc}：对照物缺失被读成了干净"
    assert any(needle in f for f in findings), findings


def test_doc_absent_is_scope_collapse(tmp_path):
    gate = _gate()
    repo = _doc(tmp_path, gate, BASE_TRACKED)
    (repo / DOC_REL).unlink()
    rc, findings, _info = _rc(repo, BASE_TRACKED)
    assert rc == 2 and any(DOC_REL in f for f in findings), findings


def test_empty_auto_block_is_scope_collapse(tmp_path):
    """自动段被清空 ⇒ 判据 A 没有对撞对象，空集不给干净。"""
    gate = _gate()
    repo = _doc(tmp_path, gate, BASE_TRACKED, auto=[])
    rc, findings, _info = _rc(repo, BASE_TRACKED)
    assert rc == 2 and any("空段" in f for f in findings), findings


def test_index_with_no_python_is_scope_collapse(tmp_path):
    """索引读得出却 0 枚 `.py`＝路径口径断了（§二之四十四 同款假绿）。"""
    gate = _gate()
    repo = _doc(tmp_path, gate, BASE_TRACKED)
    rc, findings, _info = _rc(repo, [".gitignore", DOC_REL, "README.md"])
    assert rc == 2 and any("入库 `.py`" in f for f in findings), findings


def test_gitignore_absent_is_scope_collapse(tmp_path):
    gate = _gate()
    repo = _doc(tmp_path, gate, BASE_TRACKED)
    (repo / ".gitignore").unlink()
    rc, findings, _info = _rc(repo, BASE_TRACKED)
    assert rc == 2 and any(".gitignore" in f for f in findings), findings


# ── 真仓读数腿 ───────────────────────────────────────────────────


def test_real_repo_is_green_and_has_range():
    """真仓腿：门在 HEAD＋本批工作树上判绿，且射程不是空集。"""
    gate = _gate()
    tracked = gate.tracked_paths(ROOT)
    assert tracked, "读不出 git 索引：真仓腿没有口径"
    red, errs, info = gate.check(ROOT, tracked=tracked)
    assert errs == [], f"射程塌了：{errs}"
    assert red == [], f"真仓被判红：{red}"
    assert info["root_py"] == [], f"根目录又出现入库 .py：{info['root_py']}"
    assert len(info["oneshot"]) >= 5, f"`scripts/oneshot/` 只剩 {len(info['oneshot'])} 个：本批搬进来的四份没了"
    assert info["migration_rows"] == 4, f"迁移记录读到 {info['migration_rows']} 行，不是现读的 4 行"
    assert info["py_total"] > 100, f"索引内 .py 只有 {info['py_total']} 枚：口径断了"


def test_real_repo_rejects_a_smuggled_root_script():
    """反空集腿（真仓口径）：把修前形状种回去必须被扫出——根目录多一枚入库 `.py` 就得起红。"""
    gate = _gate()
    tracked = gate.tracked_paths(ROOT)
    red, errs, _info = gate.check(ROOT, tracked=[*tracked, "analyze_failures.py"])
    assert errs == []
    assert any(f.startswith("B：") and "analyze_failures.py" in f for f in red), red


def test_real_repo_rejects_a_missing_legacy_script():
    """反空集腿：迁移记录说搬走了、索引里却没有 ⇒ 判据 E 在真仓口径上起红。"""
    gate = _gate()
    tracked = [p for p in gate.tracked_paths(ROOT) if p != "scripts/oneshot/fix_ir.py"]
    red, _errs, _info = gate.check(ROOT, tracked=tracked)
    assert any(f.startswith("E：") and "fix_ir.py" in f for f in red), red


def test_gate_is_wired_into_gates_sh():
    """独立成档的门必须真在链上跑，否则它只是仓里一个没人执行的脚本。"""
    assert "check_oneshot_layout.py" in (ROOT / "gates.sh").read_text(encoding="utf-8"), \
        "门没接进 gates.sh：那它永远不会响"


def test_cli_real_repo_is_zero(tmp_path):
    """CLI 面正腿：真仓跑出 0（不是 import 出来 0）。"""
    run = subprocess.run([sys.executable, str(SCRIPT), str(ROOT)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "读数：入库面" in run.stdout, run.stdout


def test_cli_without_the_registration_face_is_two(tmp_path):
    """CLI 面射程腿：既没有登记面也读不出索引 ⇒ 退 2 并自证是哪一格塌了。"""
    run = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], capture_output=True, text=True)
    assert run.returncode == 2, f"rc={run.returncode}\n{run.stdout}\n{run.stderr}"
    assert "射程自证" in run.stdout, run.stdout


def test_cli_with_doc_but_no_git_is_two(tmp_path):
    """CLI 面关键一格：登记面在盘上、git 读不出 ⇒ 退 2 并点名「git 索引」，⛔ 不许改用磁盘读数。"""
    gate = _gate()
    repo = _doc(tmp_path, gate, BASE_TRACKED)
    inside = subprocess.run(["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
                            capture_output=True, text=True)
    if inside.returncode == 0:
        pytest.skip("这台机器的临时目录落在某个工作树内，无 git 那一档在这台机器上无从构造")
    run = subprocess.run([sys.executable, str(SCRIPT), str(repo)], capture_output=True, text=True)
    assert run.returncode == 2, f"rc={run.returncode}\n{run.stdout}\n{run.stderr}"
    assert "git 索引" in run.stdout, run.stdout
