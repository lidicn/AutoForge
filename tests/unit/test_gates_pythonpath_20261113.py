# -*- coding: utf-8 -*-
"""门禁自解析口径（AF-768，2026-11-13）。

## 缺陷现场

`gates.sh` 全文件只设 `PYTHON="${GATES_PYTHON:-python3}"`，**没有任何 `PYTHONPATH`
赋值**。于是门禁里那些**子 pytest** 进程（有界缓存门等）按 site-packages 解析
`autoforge`——开发机上装的是别处主检出或压根没装可编辑安装时，子进程导入陈旧代码、
塌成 `exit=2` 假红，与本树读数无关。门禁**必须自带**解析口径，⛔ 不许要求调用方
记得先 `export`。

## 三条判据（都靠"假解释器"截获真实环境，⛔ 不跑完整门禁——那要十几分钟）

  G1 正例：清空 `PYTHONPATH` 跑 `gates.sh`，子进程看到的第一个 `PYTHONPATH` 段
     必须**落在本树 `src/`**（⛔ 不是主检出、不是 site-packages）。
  G2 反例：预置一个指向不存在路径的 `PYTHONPATH`，跑完必须**仍以本树 `src/` 打头**
     ——证明是**前置拼接**而不是覆盖（覆盖会静默打断调用方自己挂的路径）。
  G3 互斥：同树并发跑两个 `gates.sh`，第二个必须拿到**明确的「已在跑」退出码 3**
     ——⛔ 不许静默等待，⛔ 更不许静默放过（放过＝互斥根本没装）。
     G3 新形状：holder 三行（`ppid` 调用方 pid／`started` 起跑时刻／`tree` 树路径）
     必须**逐行原样打出**——只报门禁自身 `$$` 等于没报（2026-11-13 那晚打出的
     `pid=2105` 早就死了，真正在跑的是它的子进程 30928/20252）。
     G3b 旧形状：只有 `pid` 的陈锁也要报出那个 pid，⛔ 不许因缺 holder 就报「未知」。

## 为什么判据用「假解释器」

真跑一次 `gates.sh` 要十几分钟、还依赖 homesdk 装没装。假解释器在第 44 行那句
`"$PYTHON" -c "import homesdk.gates"` 上当场退 2，让脚本**在导出环境之后、跑门禁之前**
就停下——判据量大的是「环境有没有传出去」，不是「门禁跑没跑完」。两条腿各归各的
测试文件（门禁跑不跑得完由 `gates.sh` 自己的实跑读数负责）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATES = REPO / "gates.sh"


def _find_bash() -> str | None:
    """挑一个**真能跑本仓**的 bash。

    ⛔ 别直接用 `shutil.which("bash")`：Windows 上它先命中 `C:\\WINDOWS\\system32\\bash.exe`
    ——那是 WSL 的启动器，喂 Windows 路径进去只会得到一句语焉不详的错，判据变成
    "在测 WSL 挂载"而不是"在测门禁"。优先 Git for Windows 的 bash。
    """
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    for cand in candidates:
        if not cand:
            continue
        lowered = cand.lower().replace("/", "\\")
        if lowered.endswith("system32\\bash.exe"):
            continue  # WSL 启动器
        return cand
    return None


BASH = _find_bash()

pytestmark = pytest.mark.skipif(
    BASH is None or not GATES.exists(),
    reason="本机没有可用的 git bash 或不在仓根（门禁脚本只在仓根有意义）",
)


def _bash_path(path: Path) -> str:
    """把 Windows 路径翻成 bash 眼里那个（`/e/NAS/...`）。

    ⛔ 别拿 `str(REPO)` 直接比：`gates.sh` 里 `REPO="$(cd … && pwd)"` 得到的是
    MSYS 形式，Python 的 `Path.resolve()` 得到的是 `E:\\NAS\\…`——两者的 `cksum`
    也不同，锁路径会对不上（假绿：以为测了互斥，其实测了两个不同的目录）。
    """
    return subprocess.check_output(
        [BASH, "-c", 'cd "$1" && pwd -P', "_", str(path)], text=True,
    ).strip()


def _lock_path(tmpdir: Path) -> Path:
    """复刻 `gates.sh` 的锁目录口径（`cksum` 的十进制键 + TMPDIR 前缀）。"""
    key = subprocess.check_output(
        [BASH, "-c", 'printf "%s" "$1" | cksum | awk \'{print $1}\'', "_", _bash_path(REPO)],
        text=True,
    ).strip()
    return tmpdir / f"af-gates-lock-{key}.d"


@pytest.fixture()
def fake_python(tmp_path: Path) -> tuple[Path, Path]:
    """造一枚假解释器：把**子进程真实看到的** `PYTHONPATH` 落盘，然后退 2。

    退 2 是有意的：`gates.sh` 的 `if ! "$PYTHON" -c "import homesdk.gates"` 会在
    第 44 行把它当成「homesdk 未安装」当场 `exit 2`，于是脚本**在导出环境之后、
    跑门禁之前**停下——判据要量的正是那段环境。
    """
    out = tmp_path / "seen_pythonpath.txt"
    stub = tmp_path / "fake_python.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s" "${PYTHONPATH-}" > "$GATES_STUB_OUT"\n'
        "exit 2\n",
        encoding="utf-8",
        newline="\n",
    )
    stub.chmod(0o755)
    return stub, out


def _run_gates(
    tmp_path: Path,
    stub: Path,
    out: Path,
    *,
    pythonpath: str | None,
    extra_env: dict[str, str] | None = None,
):
    """跑一次 `gates.sh`（假解释器，环境隔离），回 (rc, 看到的 PYTHONPATH, stdout+stderr)。"""
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir(exist_ok=True)
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("PYTHONPATH", "PYTHONPYCACHEPREFIX", "TMPDIR")
    }
    env.update({
        "GATES_PYTHON": str(stub),
        "GATES_STUB_OUT": str(out),
        "TMPDIR": str(tmpdir),
        # 别让门禁去清真实工作树的 __pycache__（假解释器根本用不到它）
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
    })
    if extra_env:
        env.update(extra_env)
    if pythonpath is not None:
        env["PYTHONPATH"] = pythonpath
    proc = subprocess.run(
        [BASH, str(GATES)], cwd=str(REPO), env=env,
        # Windows 上 bash 子进程的 stdout 可能混进 GBK 字节（门禁自己打印的中文），
        # 严格 utf-8 解码会在读线程里炸成 PytestUnhandledThreadExceptionWarning。
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    return proc.returncode, out.read_text(encoding="utf-8") if out.exists() else "", proc


# ── G1 正例：清空 PYTHONPATH，子进程仍落在本树 src ────────────────
def test_g1_gates_injects_own_src_when_pythonpath_is_empty(tmp_path, fake_python):
    stub, out = fake_python
    rc, seen, proc = _run_gates(tmp_path, stub, out, pythonpath="")
    assert rc == 2, f"假解释器应让脚本停在 homesdk 检查（exit 2），实得 {rc}\n{proc.stderr}"
    # ⛔ 别用 os.pathsep：Windows 上是 ';'，而这个值由 bash 产生、按 POSIX 用 ':'。
    head = seen.split(":")[0] if seen else ""
    assert head == _bash_path(REPO / "src"), (
        f"子进程解析到的第一段必须落在本树 src/，实得 {seen!r}——"
        f"门禁没自带解析口径＝子 pytest 会导入别处的 autoforge"
    )


# ── G2 反例：预置一个不存在的路径，本树仍打头（前置拼接≠覆盖）──────
def test_g2_gates_prepends_instead_of_overriding(tmp_path, fake_python):
    stub, out = fake_python
    sentinel = "/nonexistent/af768/sentinel"
    rc, seen, proc = _run_gates(tmp_path, stub, out, pythonpath=sentinel)
    assert rc == 2, f"假解释器应让脚本停在 homesdk 检查（exit 2），实得 {rc}\n{proc.stderr}"
    parts = seen.split(":") if seen else []
    assert parts, "PYTHONPATH 整段丢了——那是覆盖，不是拼接"
    assert parts[0] == _bash_path(REPO / "src"), (
        f"本树 src/ 必须**打头**（前置拼接），实得 {seen!r}"
    )
    assert parts[-1] == sentinel, (
        f"调用方预置的路径被覆盖掉了（实得 {seen!r}）——"
        f"整段覆盖会静默打断别人自己挂的解析路径"
    )


# ── G3 互斥：第二个必须拿到明确的「已在跑」退出码 ─────────────────
def test_g3_second_run_is_refused_with_explicit_code(tmp_path, fake_python):
    stub, out = fake_python
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir(exist_ok=True)
    lock = _lock_path(tmpdir)
    lock.mkdir(parents=True)
    # 新形状（AF-768 ④）：holder 三行，撞锁时**原样打出**
    (lock / "holder").write_text(
        "ppid=2105\n"
        "started=2026-11-13 01:22:25 +0800\n"
        "tree=/e/NAS/AutoForge/.claude/worktrees/core-stop-valve\n",
        encoding="utf-8",
    )
    (lock / "pid").write_text(str(os.getpid()), encoding="utf-8")

    try:
        rc, seen, proc = _run_gates(tmp_path, stub, out, pythonpath="")
    finally:
        shutil.rmtree(lock, ignore_errors=True)

    assert rc == 3, f"撞锁必须退 3（已在跑），实得 {rc}\n{proc.stderr}"
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "另一个门禁正在本树跑" in combined, f"必须显式报「已在跑」，实得：{combined!r}"
    # 三行现场必须逐行原样出现——「谁占的锁」要一眼能看见，⛔ 不许只报一个
    # 认不出人的 pid（2026-11-13 那晚 `pid=2105` 早就死了，真正在跑的是子进程）。
    for line in (
        "ppid=2105",
        "started=2026-11-13 01:22:25 +0800",
        "tree=/e/NAS/AutoForge/.claude/worktrees/core-stop-valve",
    ):
        assert line in combined, f"撞锁要原样打出持锁现场这一行 {line!r}，实得：{combined!r}"
    assert not seen, "撞锁时⛔ 不许真的去跑门禁（假解释器都不该被调用）"


# ── G3b 兼容：只有 `pid` 的旧形状陈锁，仍要报出那个 pid ────────────
def test_g3b_legacy_pid_only_lock_is_reported(tmp_path, fake_python):
    """AF-768 之前留下的陈锁只写了 `pid` 一个文件。

    ⛔ 别让新版脚本因为找不到 `holder` 就报「未知」——那种锁恰恰是最常见的
    （上次被 kill 在写 holder 之前），报不出 pid 就等于没有诊断信息。
    """
    stub, out = fake_python
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir(exist_ok=True)
    lock = _lock_path(tmpdir)
    lock.mkdir(parents=True)
    (lock / "pid").write_text(str(os.getpid()), encoding="utf-8")

    try:
        rc, seen, proc = _run_gates(tmp_path, stub, out, pythonpath="")
    finally:
        shutil.rmtree(lock, ignore_errors=True)

    assert rc == 3, f"旧形状陈锁也必须退 3，实得 {rc}\n{proc.stderr}"
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert str(os.getpid()) in combined, (
        f"旧形状陈锁要报出 pid，否则人无从判断是不是陈锁：{combined!r}"
    )
    assert not seen, "撞锁时⛔ 不许真的去跑门禁"


# ── G4 正常跑完要解锁（否则下一次永远撞锁）──────────────────────
def test_g4_lock_is_released_after_run(tmp_path, fake_python):
    stub, out = fake_python
    tmpdir = tmp_path / "tmpdir"
    rc, _seen, proc = _run_gates(tmp_path, stub, out, pythonpath="")
    assert rc == 2, f"假解释器应让脚本停在 homesdk 检查，实得 {rc}\n{proc.stderr}"
    assert not _lock_path(tmpdir).exists(), (
        "正常退出后锁还留着＝下一次永远撞锁（trap EXIT 没装好）"
    )


# ── G5 写入面：holder 四行必须是 ppid／started／tree／winpid ────────
def test_g5_lock_records_caller_identity(tmp_path):
    """⚠️ G3/G3b 只测**读**面（锁是测试自己造的），测不出「写面写错了」。

    2026-11-13 的变异自证当场暴露了这个洞：把 holder 写入整段摘掉，
    G1–G4 全绿——因为没人看过门禁**自己写出来的**锁长什么样。所以这里让
    假解释器在锁**还活着**的时候把 holder 原文捞出来（假解释器在取锁之后才被
    调用），逐行核对。

    ⚠️ 为什么不能断言 `ppid == os.getpid()`：Git for Windows 的 bash 从 **Windows
    进程**起跑时 `$PPID` 映射不到 Windows 侧，实测恒为 `1`（2026-11-13 现跑确认）。
    所以 `ppid` 那行只当"有没有父进程"看；**真正查人靠 `winpid`**——那是
    `Get-CimInstance Win32_Process -Filter "ProcessId=…"` 认得的号。
    """
    from datetime import datetime

    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir(exist_ok=True)
    lock = _lock_path(tmpdir)
    out = tmp_path / "seen_holder.txt"
    stub = tmp_path / "fake_python.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ -f "$GATES_STUB_LOCK/holder" ]; then\n'
        '  cat "$GATES_STUB_LOCK/holder" > "$GATES_STUB_OUT"\n'
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
        newline="\n",
    )
    stub.chmod(0o755)

    try:
        rc, seen, proc = _run_gates(
            tmp_path, stub, out, pythonpath="",
            extra_env={"GATES_STUB_LOCK": str(lock)},
        )
    finally:
        shutil.rmtree(lock, ignore_errors=True)

    assert rc == 2, f"假解释器应让脚本停在 homesdk 检查，实得 {rc}\n{proc.stderr}"
    lines = [line for line in seen.splitlines() if line]
    assert len(lines) == 4, (
        f"holder 必须恰好四行（ppid/started/tree/winpid），实得 {seen!r}"
    )

    # ① 调用方 pid：只能是数字（⛔ 别钉死具体值，见 docstring）
    key, _, value = lines[0].partition("=")
    assert key == "ppid" and value.isdigit(), (
        f"holder 第一行必须是 ppid=<数字>，实得 {lines[0]!r}"
    )
    # ② 起跑时刻：必须能被 %z 解析（带时区的本地时刻）
    assert lines[1].startswith("started="), f"第二行必须是 started=，实得 {lines[1]!r}"
    datetime.strptime(lines[1][len("started="):], "%Y-%m-%d %H:%M:%S %z")
    # ③ 树路径：MSYS 形式，与本树同源
    assert lines[2] == f"tree={_bash_path(REPO)}", (
        f"第三行必须是本树路径（MSYS 形式），实得 {lines[2]!r}"
    )
    # ④ Windows pid：查人用的那枚。拿不到时脚本写「未知」，这里要求是真数字。
    key4, _, winpid = lines[3].partition("=")
    assert key4 == "winpid" and winpid.isdigit(), (
        f"第四行必须是 winpid=<Windows pid>（`/proc/$$/winpid`），实得 {lines[3]!r}——"
        f"没有它，撞锁时人只看到一个 MSYS 内部 pid，查不到进程"
    )
