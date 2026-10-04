"""原子写站点门禁必须**能变红**（铁律 #8），红的必须是"下一个写错的落盘点"。

起因：安全审计那份 zip 的 out_of_scope 14 个单元里盘出的写原子性族——`af_store._atomic_write`
在 P1-18 已经改成"随机 tmp 名 + fsync"，但这条纪律没扩散：AST 盘 src 全集实测 16 站里一半以上
仍是"固定名 `.tmp` + 裸 `write_text` + `os.replace`"。反例必须逐条覆盖四条判据：固定名站点未登记
（红）、走 mkstemp（绿）、就地豁免带理由（绿）/ 空理由（红且说的是理由为空）、射程塌了
（扫不到站点 / 解析失败 ⇒ exit 2，不许变成"没有发现"）。另外钉两条键的形状：同名方法分属两个类
必须是两条独立基线（否则修好一处等于冻结另一处），以及**本仓基线每条都必须带非空理由**。
"""
from __future__ import annotations

import contextlib
import io
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_atomic_write_sites.py"
BASELINE = ROOT / ".atomic-write-baseline.txt"

FIXED_NAME = '''
import os


def _save(path):
    tmp = path.with_suffix(".tmp")
    tmp.write_text("{}", encoding="utf-8")
    os.replace(tmp, path)
'''

MKSTEMP = '''
import os
import tempfile


def _save(path):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(fd)
    os.replace(tmp, path)
'''

TWO_CLASSES_SAME_METHOD = '''
import os


class Alpha:
    def save(self, path):
        tmp = f"{path}.tmp"
        open(tmp, "w").write("{}")
        os.replace(tmp, path)


class Beta:
    def save(self, path):
        tmp = f"{path}.tmp"
        open(tmp, "w").write("{}")
        os.replace(tmp, path)
'''


def _module():
    spec = importlib.util.spec_from_file_location("check_atomic_write_sites", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, target: pathlib.Path) -> tuple[int, str]:
    buf = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = mod.main(["check_atomic_write_sites.py", str(target)])
    return rc, buf.getvalue() + err.getvalue()


def _src(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    root = tmp_path / "src"
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# ── 判据 A：固定名站点必须登记或走公共助手 ────────────────────────────

def test_control_site_goes_through_mkstemp_and_is_counted(tmp_path):
    """控制组自己数得出数：1 个文件、1 处站点、其中走 mkstemp 的 1 处。"""
    root = _src(tmp_path, {"af_ok.py": MKSTEMP})
    rc, out = _run(_module(), root)
    assert rc == 0, out
    assert "扫描 1 个文件" in out and "站点 1 处" in out and "公共助手 1 处" in out


def test_fixed_name_site_is_red_and_names_the_function(tmp_path):
    root = _src(tmp_path, {"af_bad.py": FIXED_NAME})
    rc, out = _run(_module(), root)
    assert rc == 1, out
    assert "af_bad.py" in out and "_save" in out and "固定名" in out


def test_baseline_frozen_site_is_green(tmp_path):
    mod = _module()
    root = _src(tmp_path, {"af_bad.py": FIXED_NAME})
    bl = tmp_path / "bl.txt"
    bl.write_text("af_bad.py::_save  # 单写者，代价是可重建\n", encoding="utf-8")
    mod.SRC = root
    mod.BASELINE = bl
    rc, out = _run(mod, root)
    assert rc == 0, out
    assert "基线冻结 1 站" in out


def test_stale_baseline_entry_is_reported(tmp_path):
    """基线里那条已经不命中（修好了）⇒ 绿行必须点名，否则"只减不增"没人看得见。"""
    mod = _module()
    root = _src(tmp_path, {"af_ok.py": MKSTEMP})
    bl = tmp_path / "bl.txt"
    bl.write_text("af_gone.py::_old  # 已修\n", encoding="utf-8")
    mod.SRC = root
    mod.BASELINE = bl
    rc, out = _run(mod, root)
    assert rc == 0, out
    assert "已不再命中" in out and "af_gone.py::_old" in out


def test_same_method_name_in_two_classes_are_two_entries(tmp_path):
    """键必须含类名：两个 `save` 共用一条基线 = 修好一处把另一处一起冻结。"""
    root = _src(tmp_path, {"af_two.py": TWO_CLASSES_SAME_METHOD})
    mod = _module()
    sites, broken, _ = mod.collect(root)
    assert not broken
    keys = {s["key"] for s in sites}
    assert keys == {"af_two.py::Alpha.save", "af_two.py::Beta.save"}
    bl = tmp_path / "bl.txt"
    bl.write_text("af_two.py::Alpha.save  # 理由\n", encoding="utf-8")
    mod.SRC = root
    mod.BASELINE = bl
    rc, out = _run(mod, root)
    assert rc == 1, out
    assert "Beta.save" in out and "Alpha.save" not in out


# ── 判据 B：豁免标记必须有理由 ────────────────────────────────────────

def test_exempt_with_reason_is_green(tmp_path):
    text = FIXED_NAME.replace(
        "    os.replace(tmp, path)",
        "    # fixed-tmp: exempt(整段在 alias.lock 内，第二个写者进不来)\n"
        "    os.replace(tmp, path)",
    )
    root = _src(tmp_path, {"af_ex.py": text})
    rc, out = _run(_module(), root)
    assert rc == 0, out
    assert "就地豁免 1 站" in out


def test_exempt_with_empty_reason_is_red(tmp_path):
    text = FIXED_NAME.replace(
        "    os.replace(tmp, path)",
        "    # fixed-tmp: exempt()\n    os.replace(tmp, path)",
    )
    root = _src(tmp_path, {"af_ex2.py": text})
    rc, out = _run(_module(), root)
    assert rc == 1, out
    assert "没写理由" in out


# ── 判据 C：射程读不成时不许报"干净" ──────────────────────────────────

def test_zero_replace_sites_is_range_failure_not_clean(tmp_path):
    root = _src(tmp_path, {"af_none.py": "def f():\n    return 1\n"})
    rc, out = _run(_module(), root)
    assert rc == 2, out
    assert "无从判定射程" in out


def test_unparseable_file_is_range_failure(tmp_path):
    root = _src(tmp_path, {"af_ok.py": MKSTEMP, "af_broken.py": "def f(:\n  pass\n"})
    rc, out = _run(_module(), root)
    assert rc == 2, out
    assert "解析失败" in out


def test_missing_directory_is_range_failure(tmp_path):
    rc, out = _run(_module(), tmp_path / "nope")
    assert rc == 2, out
    assert "目录不存在" in out


# ── 本仓真值：门在 HEAD 上必须绿，且基线每条都有理由 ──────────────────

def test_real_src_is_clean_and_measurable():
    mod = _module()
    rc, out = _run(mod, ROOT / "src")
    assert rc == 0, out
    import re

    m = re.search(r"站点 (\d+) 处", out)
    assert m and int(m.group(1)) >= 1, out


def test_every_baseline_entry_carries_a_nonempty_reason():
    assert BASELINE.is_file()
    entries = [
        ln.strip()
        for ln in BASELINE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert entries, "基线空了 ⇒ 要么真修完了（那这条测试该改），要么文件被挪走"
    for line in entries:
        key, _, reason = line.partition("#")
        assert key.strip().endswith(tuple("abcdefghijklmnopqrstuvwxyz_"))
        assert reason.strip(), f"{key.strip()} 没有理由"
        assert "::" in key, key


def test_baseline_keys_match_the_generator():
    """基线必须和 `--print-baseline` 对得上：手抄错一个键，那一站就悄悄脱离门禁。"""
    mod = _module()
    sites, _broken, _ = mod.collect(ROOT / "src")
    uncovered = {s["key"] for s in sites if not s["safe"] and not mod._has_exempt_mark(s)}
    listed = set(mod.load_baseline(BASELINE))
    assert uncovered == listed, f"生成器 {sorted(uncovered)} ≠ 基线 {sorted(listed)}"
