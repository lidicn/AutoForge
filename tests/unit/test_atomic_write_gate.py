"""原子写站点门禁必须**能变红**（铁律 #8），红的必须是"下一个写错的落盘点"。

起因：安全审计那份 zip 的 out_of_scope 14 个单元里盘出的写原子性族——`af_store._atomic_write`
在 P1-18 已经改成"随机 tmp 名 + fsync"，但这条纪律没扩散：AST 盘 src 全集实测 16 站里一半以上
仍是"固定名 `.tmp` + 裸 `write_text` + `os.replace`"。反例必须逐条覆盖判据：固定名站点未登记
（红）、走 mkstemp（绿）、就地豁免带理由（绿）/ 空理由（红且说的是理由为空）、射程塌了
（扫不到站点 / 解析失败 ⇒ exit 2，不许变成"没有发现"）。另外钉两条键的形状：同名方法分属两个类
必须是两条独立基线（否则修好一处等于冻结另一处），以及**本仓基线每条都必须带非空理由**。

D 腿（授权面 `.auth/` 落盘必经原子助手，DCD 裁定 20261004 §二）的反例是另一族，它要证明的是
**政策性**而不是形状：① `af_auth.py` 内裸 `write_text` 红、② 常量可写模式的 `open()` 同样红、
读模式不算、③ 同一站点既登记进基线又贴豁免标记仍旧红（这一腿不接受这两个出口，接受就等于
把"撤销名单读回空 = 令牌复活"重新变成一个可以写理由绕过的选项）、④ 跨文件写向 `.auth` 路径红、
⑤ `af_auth.py` 被改名 ⇒ exit 2、⑥ 收紧的那一半必须守住：几百行装配函数里 `.auth` 只是被构造、
`write_text` 写的是无关 config ⇒ 不许假红（假红一多这条门会被当噪音绕开）、⑦ 助手被改名成
「各处自己手搓 mkstemp」⇒ exit 2（这一条是变形实测出来的洞，见 `test_renamed_helper_is_range_failure_not_green`）。
`SRC` 与 `AUTH_LEG_ROOT` 是两个开关：挪前者的测试若连带启动后者，临时树里没有本体文件，
A 腿的测试会全被 D 腿的 exit 2 带崩——这个耦合本身也是被测试钉住的。
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
    d = re.search(
        r"授权面腿射程函数 (\d+) 个、其中落盘 (\d+) 个（必经助手 (\d+) 个、自带 mkstemp (\d+) 个、裸写 0 个",
        out,
    )
    assert d, out
    # 反假绿：落盘读数必须拆得开——必经助手的 + 手搓 mkstemp 的正好等于落盘函数数，且助手锚点在。
    assert int(d.group(3)) + int(d.group(4)) == int(d.group(2)), out
    assert int(d.group(3)) >= 1, out


def test_real_auth_face_files_all_go_through_the_helper():
    """绿行的 D 腿读数得对上真值：`af_auth.py` 里每一个落盘函数都真的调用 `_atomic_write_text`。

    这条不是重复上一条——它把「助手被改名但腿还在按老名字数」这种失效单独钉住：数的是源码里的实际调用。
    """
    text = (ROOT / "src" / "autoforge" / "af_auth.py").read_text(encoding="utf-8")
    # 1 处 def + 5 处落盘调用；少了说明有落盘换回了裸写（腿会红，这条先替它把名字钉住）。
    assert text.count("_atomic_write_text(") >= 6, text.count("_atomic_write_text(")
    for bare in (".write_text(", ".write_bytes("):
        assert bare not in text, f"授权面里出现了裸写 {bare}"


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


# ── 判据 D：授权面（`.auth/`）落盘必经原子助手，且不吃基线不吃豁免 ──────

AUTH_HELPER = '''
import os
import tempfile
from pathlib import Path


def _atomic_write_text(path, text):
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp_path, str(path))


def save_revoked(self, path, data):
    _atomic_write_text(path, data)
    return True


def save_session(self, path, data):
    _atomic_write_text(path, data)
    return True
'''

#: 只换**第一处**助手调用：另一个落盘函数仍旧走助手，这一腿的锚点才不会被变形自己拆掉
#: （锚点一没就是 exit 2，会盖掉这条要看的「裸写判红」读数）。
AUTH_BARE = AUTH_HELPER.replace(
    "    _atomic_write_text(path, data)",
    '    path.write_text(data, encoding="utf-8")',
    1,
)

AUTH_BARE_OPEN = AUTH_HELPER.replace(
    "    _atomic_write_text(path, data)",
    '    with open(path, "w", encoding="utf-8") as fh:\n        fh.write(data)',
    1,
)

#: 读模式多加一个函数，而不是把唯一的助手调用换掉——换掉会让「必经助手」的锚点先消失，
#: 那条 exit 2 会把这条测试要看的读数盖掉。
AUTH_READER = AUTH_HELPER + '''

def load_revoked(self, path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()
'''

#: 装配函数那种形状：函数体里既有 `.auth` 的构造，也有与鉴权无关的整份裸写。
AUTH_UNRELATED_WRITE = '''
import os
from pathlib import Path


def build_app(root):
    auth_dir = Path(root) / ".auth"
    cfg = Path(root) / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    return auth_dir
'''

AUTH_VAR_PATH = '''
import os
from pathlib import Path


def revoke_all(root, blob):
    target = Path(root) / ".auth" / "revoked.json"
    target.write_text(blob, encoding="utf-8")
    return target
'''


def _auth_mod(tmp_path, files: dict[str, str]):
    """把 `files` 摆成 src 形状，并把 D 腿的射程边界指过来。

    D 腿有自己的开关（`AUTH_LEG_ROOT`）：挪 A 腿的 `SRC` 是为了测基线读取，不该顺手把 D 腿
    启动起来——临时树里没有 `af_auth.py` 本体，那样只会得到 exit 2。
    """
    mod = _module()
    root = _src(tmp_path, files)
    mod.AUTH_LEG_ROOT = root.resolve()
    return mod, root


def test_auth_leg_green_line_carries_real_numbers(tmp_path):
    mod, root = _auth_mod(tmp_path, {"af_auth.py": AUTH_HELPER})
    rc, out = _run(mod, root)
    assert rc == 0, out
    assert "授权面腿射程函数 3 个、其中落盘 3 个（必经助手 2 个、自带 mkstemp 1 个、裸写 0 个" in out, out


def test_renamed_helper_is_range_failure_not_green(tmp_path):
    """助手改名、各处改成自己手搓 mkstemp ⇒ exit 2，不许继续报绿。

    这一条是 M4 变形实测出来的洞：把 `mkstemp` 和「叫得出名字的助手」记成同一种读数时，
    改名后的树仍旧是绿的——绿行声称「必经那个被评审过的助手」，但那个助手已经不存在了。
    """
    text = AUTH_HELPER.replace("_atomic_write_text", "_write_auth_blob")
    mod, root = _auth_mod(tmp_path, {"af_auth.py": text})
    rc, out = _run(mod, root)
    assert rc == 2, out
    assert "叫得出名字的原子助手" in out, out


def test_bare_write_inside_auth_face_is_red(tmp_path):
    mod, root = _auth_mod(tmp_path, {"af_auth.py": AUTH_BARE})
    rc, out = _run(mod, root)
    assert rc == 1, out
    assert "[授权面落盘]" in out and "save_revoked" in out, out


def test_auth_leg_red_survives_both_baseline_and_exempt_mark(tmp_path):
    """这一腿的政策性反例：把站点登记进基线、再就地加豁免标记，红必须还是红。

    A 腿允许「有理由的固定名」，D 腿不允许——授权面读回空 = 已撤销的令牌复活，不给理由的余地。
    """
    text = AUTH_BARE.replace(
        "    return True",
        "    # fixed-tmp: exempt(单写者)\n    return True",
    )
    mod, root = _auth_mod(tmp_path, {"af_auth.py": text})
    bl = tmp_path / "bl.txt"
    bl.write_text("af_auth.py::save_revoked  # 有理由也不收\n", encoding="utf-8")
    # 把 A 腿的两个开关也摆成「这一站已经登记且已豁免」的样子，D 腿仍旧要红。
    mod.SRC = root
    mod.BASELINE = bl
    rc, out = _run(mod, root)
    assert rc == 1, out
    assert "[授权面落盘]" in out, out


def test_open_with_constant_writable_mode_counts_as_bare_write(tmp_path):
    mod, root = _auth_mod(tmp_path, {"af_auth.py": AUTH_BARE_OPEN})
    rc, out = _run(mod, root)
    assert rc == 1, out
    assert "[授权面落盘]" in out and "open" in out, out


def test_open_for_reading_is_not_counted(tmp_path):
    mod, root = _auth_mod(tmp_path, {"af_auth.py": AUTH_READER})
    rc, out = _run(mod, root)
    assert rc == 0, out
    assert "裸写 0 个" in out, out


def test_cross_file_bare_write_to_auth_path_is_red(tmp_path):
    """D 腿买的是「下一个授权面落盘点开在 `af_auth` 之外」——路径表达式里看得见 `.auth` 就抓。"""
    mod, root = _auth_mod(
        tmp_path, {"af_auth.py": AUTH_HELPER, "af_store.py": AUTH_VAR_PATH}
    )
    rc, out = _run(mod, root)
    assert rc == 1, out
    assert "af_store.py" in out and "revoke_all" in out, out


def test_assembler_with_unrelated_bare_write_stays_green(tmp_path):
    """收紧的那一半：`build_app` 里 `.auth` 只是被构造出来，`write_text` 写的是 config ⇒ 不许红。

    按「函数体含 `.auth` 字面量」判会把这种几百行装配函数的所有裸写一起打红，
    假红一多这条门就会被当成噪音绕开。
    """
    mod, root = _auth_mod(
        tmp_path,
        {
            "af_auth.py": AUTH_HELPER,
            "af_api.py": AUTH_UNRELATED_WRITE,
            "af_ok.py": MKSTEMP,
        },
    )
    rc, out = _run(mod, root)
    assert rc == 0, out
    assert "build_app" not in out, out


def test_missing_auth_face_file_is_range_failure(tmp_path):
    """`af_auth.py` 被改名或挪包 ⇒ D 腿无从判定射程，必须 exit 2 而不是「干净」。"""
    mod, root = _auth_mod(tmp_path, {"af_authorization.py": AUTH_HELPER, "af_ok.py": MKSTEMP})
    rc, out = _run(mod, root)
    assert rc == 2, out
    assert "af_auth.py" in out, out


def test_auth_leg_does_not_run_for_a_foreign_target(tmp_path):
    """扫别的目录时不掺入 D 腿读数——那里没有 `af_auth.py` 本体，掺进来只会把 A 腿打成假红。"""
    mod = _module()
    root = _src(tmp_path, {"af_ok.py": MKSTEMP})
    rc, out = _run(mod, root)
    assert rc == 0, out
    assert "授权面腿" not in out, out
