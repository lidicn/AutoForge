"""陈 pyc 不得改变判定（2026-11-11 PM 卡，发版前置红）。

**病根**：`git checkout` 会保留源文件 mtime+size，而 CPython 判「`.pyc` 还有效吗」
只看这两样 ⇒ 陈旧字节码被当有效、跑出来的是**旧断言**。AF-Verify 追的
`test_tampered_answer_is_not_consumed` 假红就是这个形状：traceback 报 308 行的旧
断言（`== 1`），盘上真身是 311-314 的新断言（`.rejected`），清掉 11 个
`__pycache__` 后两条面都绿。⛔ 它不是「偶发」——同一棵树 checkout 过就复现。

**修法两条都要**（`gates.sh` 门口）：
① 字节码缓存整个导到仓外临时目录（`PYTHONPYCACHEPREFIX`，源码树永不落 `.pyc`）；
② 清掉工作树里**已经躺在盘上**的陈 pyc——①只影响新写入，既有的 `__pycache__`
   仍按旧路径命中，所以光设环境变量治不了本（本文件第三条腿钉的正是这个）。

**为什么这组腿必须有牙**（⑧「恒 0 不是负控」）：只写「跑完是绿的」证明不了任何事
——绿可能是夹具没造出陈旧字节码。所以第一条腿先当负控：**不带门禁环境时旧版本必须
赢**；第二条腿再证明同一份陈旧字节码在门禁环境下不再影响判定。两条腿合起来才叫
「这个 guard 挡住了它」。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATES = REPO / "gates.sh"

#: 同长度——`st_size` 不变是这条陷阱的**必要条件**（CPython 的判据是
#: `(源 mtime, 源 size) == .pyc 头里存的那一对`）。换长度就自动失效，夹具也就失去意义。
OLD_SRC = 'VERSION = "old"\n'
NEW_SRC = 'VERSION = "new"\n'
IMPORT_CODE = "import demo_pyc_module as m; print(m.VERSION)"


def _env(root: Path, *, pycache_prefix: str | None) -> dict[str, str]:
    """子进程环境：去掉外层的 `PYTHONPYCACHEPREFIX`，免得 pytest 自己那套漏进来。"""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPYCACHEPREFIX",)}
    env["PYTHONPATH"] = str(root)
    if pycache_prefix is not None:
        env["PYTHONPYCACHEPREFIX"] = pycache_prefix
    return env


def _run(root: Path, *, pycache_prefix: str | None) -> str:
    proc = subprocess.run(
        [sys.executable, "-c", IMPORT_CODE],
        cwd=str(root), env=_env(root, pycache_prefix=pycache_prefix),
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"子进程失败：{proc.stderr[-800:]}"
    return proc.stdout.strip()


def _stage_stale_pyc(root: Path) -> None:
    """先 import 生成 `.pyc`，再改写源、并把 mtime+size 复原成编译时那一对。"""
    mod = root / "demo_pyc_module.py"
    mod.write_text(OLD_SRC, encoding="utf-8")
    assert _run(root, pycache_prefix=None) == "old", "夹具第一跑就应该是旧版本"
    pycs = list(root.rglob("*.pyc"))
    assert pycs, "第一次 import 没落 .pyc——夹具不成立（PYTHONDONTWRITEBYTECODE？）"
    stamp = mod.stat()
    mod.write_text(NEW_SRC, encoding="utf-8")
    assert mod.stat().st_size == stamp.st_size, "新旧源长度必须一致，否则陷阱自动失效"
    os.utime(mod, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))


def test_negative_control_stale_pyc_wins_without_guard(tmp_path):
    """负控：不带门禁环境时，陈旧 `.pyc` 真的会赢（打印旧版本）——陷阱是实的。"""
    _stage_stale_pyc(tmp_path)
    assert (tmp_path / "demo_pyc_module.py").read_text(encoding="utf-8") == NEW_SRC
    assert _run(tmp_path, pycache_prefix=None) == "old", (
        "负控没命中：这条 CPython 判据没复现，后面那条绿腿就不算证据")


def test_pycache_prefix_alone_already_heals_the_verdict(tmp_path):
    """实测更正（Python 3.13.2）：只设 `PYTHONPYCACHEPREFIX` 就**已经**让判定正确。

    理由（与直觉相反，所以钉成腿）：设了前缀之后 CPython 改从
    `<prefix>/<路径>.pyc` 找字节码，**`__pycache__` 那一份根本不再被查询**。
    于是「清 `__pycache__`」不是判定面的必要条件，只是工作树卫生——但它仍然要有：
    任何人绕过 `gates.sh` 裸跑 `python -m pytest` 时，躺在盘上的陈 pyc 会照旧撒谎。
    """
    _stage_stale_pyc(tmp_path)
    external = tmp_path / "_pycache_outside"
    external.mkdir()
    assert _run(tmp_path, pycache_prefix=str(external)) == "new", (
        "前缀居然盖不过既有 .pyc——CPython 版本变了，先核再改本判据")


def test_guard_env_plus_purge_sees_new_source(tmp_path):
    """正控：`gates.sh` 那套环境（仓外前缀 ＋ 清 `__pycache__`）之后，陈旧 pyc 不再影响判定。"""
    _stage_stale_pyc(tmp_path)
    for stale in tmp_path.rglob("__pycache__"):
        for f in stale.iterdir():
            f.unlink()
        stale.rmdir()
    external = tmp_path / "_pycache_outside"
    external.mkdir()
    assert _run(tmp_path, pycache_prefix=str(external)) == "new", (
        "清完盘还读到旧版本——说明还有别处在供陈字节码")


def test_gates_sh_wires_the_guard():
    """接线腿：`gates.sh` 必须**真**exports `PYTHONPYCACHEPREFIX` 且**真**清 `__pycache__`。

    ⛔ 只写在注释里不算：注释不会让任何解释器改变行为。
    """
    body = GATES.read_text(encoding="utf-8")
    code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
    assert "export PYTHONPYCACHEPREFIX" in code, "gates.sh 没 export 字节码缓存前缀"
    assert "mktemp -d" in code, "前缀应该导到仓外临时目录（mktemp -d）"
    assert "__pycache__" in code and "rm -rf" in code, "gates.sh 没清工作树里的陈 __pycache__"
    # 顺序：必须先于第一处 python 调用（否则第一次 import 已经吃了陈 pyc）。
    guard_at = code.index("export PYTHONPYCACHEPREFIX")
    first_py = min(
        (pos for pos in (code.find('"$PYTHON"'), code.find("$PYTHON ")) if pos != -1),
        default=-1,
    )
    assert first_py != -1, "gates.sh 里找不到 python 调用，判据要重写"
    assert guard_at < first_py, "guard 必须在第一处 python 调用之前"
