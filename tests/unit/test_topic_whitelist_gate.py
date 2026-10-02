"""主题白名单门禁的判别力（铁律 #8：新门禁必须实测能变红）。

依据：ADM 主题契约表 §五「代码里出现的 topic 必须在本表登记（未登记的判红）」、
§六「加主题 = 改本表」。反例落进 src 会真把线上 broker ACL 口径骗过去，所以这条
门禁本身必须先被证明会响。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_topic_whitelist.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_topic_whitelist", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(pkg_dir: Path, name: str, text: str) -> Path:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").touch()
    path = pkg_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def test_unregistered_topic_turns_red(tmp_path):
    mod = _load()
    pkg = _write(tmp_path, "bad.py", 'TOPIC = "af/automation/some_new_event"\n')
    findings = mod.check(pkg.parent)
    assert any("af/automation/some_new_event" in line for line in findings)
    assert mod.main(["check_topic_whitelist.py", str(pkg.parent)]) == 1


def test_registered_topic_stays_green(tmp_path):
    mod = _load()
    pkg = _write(tmp_path, "good.py", 'TOPIC = "af/automation/fired"\n')
    assert mod.check(pkg.parent) == []
    assert mod.main(["check_topic_whitelist.py", str(pkg.parent)]) == 0


def test_prefix_and_wildcard_forms_are_allowed(tmp_path):
    """订阅过滤器/域前缀判断不是可发布主题，不能把它们判红，否则门禁只会被绕过。"""
    mod = _load()
    pkg = _write(tmp_path, "filter.py", 'F = ("butler/inbox/#", "butler/inbox/", "af/")\n')
    assert mod.check(pkg.parent) == []


def test_unparseable_file_is_reported_not_skipped(tmp_path):
    """解析失败必须报红：静默跳过 = 门禁对语法坏的文件永久失明。"""
    mod = _load()
    pkg = _write(tmp_path, "broken.py", 'def f(:\n')
    findings = mod.check(pkg.parent)
    assert any("无法解析" in line for line in findings)


def test_src_is_clean():
    """真实 src 必须干净——本门禁不是摆设，落地即绿。"""
    mod = _load()
    assert mod.check(ROOT / "src" / "autoforge") == []
