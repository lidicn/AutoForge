"""host_update.sh 三护栏（v1.13.0，2026-10-23 autoflow 方案 C 照搬）的形状钉。

三护栏：①ref 白名单（仅 master | vX.Y.Z tag——SHA 不收，fetch-by-SHA 依赖服务端
allowReachableSHA1InWant）；②活树 dirty 体检（已跟踪文件有未提交改动即拒绝——
checkout -f 会把 docker-compose 绑定等 NAS 特有手工配置整片冲掉）；③checkout -f
到 pinned SHA（禁 reset --hard——autoflow「升级按钮当格式化键」事故：永不
reset --hard / git clean -f）。

为什么是形状钉而非端到端：脚本硬编码 NAS 宿主路径（/vol1/1000/...），本机跑
bash 子进程会在读写真实路径上炸；AF 风格判据腿分离——真 bash 冒烟只做 `bash -n`
语法闸，护栏形状用文本/行级断言钉住。这个文件是宿主单点（d865f26 教训：
改完不体检就 push 等于部署了一份死脚本），形状漂移必须红灯。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "host_update.sh"
_TEXT = _SCRIPT.read_text(encoding="utf-8")


def test_bash_syntax_smoke():
    """真 bash 冒烟：脚本至少语法成立（Git Bash 环境可跑；无 bash 时跳过）。"""
    bash = shutil.which("bash")
    if bash is None:
        import pytest

        pytest.skip("no bash on PATH")
    proc = subprocess.run([bash, "-n", str(_SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_ref_whitelist_case_block_present():
    """护栏①：读 marker 后有 master | v* 的 case 白名单，非法 ref 走 write_result False。"""
    m = re.search(r"case \"\$REF\" in\n(.*?)\nesac", _TEXT, re.S)
    assert m, "找不到 $REF 的 case 白名单块"
    body = m.group(1)
    assert re.search(r"master\|v\[0-9\]\*\.\[0-9\]\*\.\[0-9\]\*", body), body
    assert 'write_result False "ref 非法' in body, body


def test_ref_read_defaults_to_master():
    """REF 读取失败必须回落 master（python3 解析崩不应让白名单吃进空串/垃圾值）。"""
    assert re.search(
        r"REF=\$\(python3 -c .*get\('ref','master'\).*\|\| echo \"master\"\)", _TEXT, re.S
    ), "REF 读取没有 master 兜底"


def test_dirty_tree_check_present():
    """护栏②：fetch 之后 checkout 之前有 git status --porcelain 体检，非空即拒。

    体检必须 `--untracked-files=no`：新版 git 的 `git status --porcelain --`
    仍把未跟踪文件计入，*.bak 遗留会被误判成「活树改动」⇒ 永久拒绝部署。
    """
    fetch_pos = _TEXT.index("git fetch --force github \"$REF\"")
    checkout_pos = _TEXT.index('git checkout -f "$TARGET_SHA"')
    window = _TEXT[fetch_pos:checkout_pos]
    assert "git status --porcelain --untracked-files=no" in window, "体检不在 fetch 之后 / 未用 --untracked-files=no（未跟踪 .bak 会被误拦）"
    assert 'write_result False "活树有未提交改动' in window, window


def test_checkout_f_pinned_sha_and_no_reset_hard():
    """护栏③：checkout 用 rev-parse 解析的 pinned SHA；全脚本零 reset --hard。"""
    assert 'TARGET_SHA=$(git rev-parse "github/$REF"' in _TEXT
    assert 'git checkout -f "$TARGET_SHA"' in _TEXT
    assert "reset --hard" not in _TEXT, "reset --hard 复活（autoflow 事故铁律）"
    # 三处回滚（py_compile 失败 / build 失败 / up -d 失败）也必须走 checkout -f
    assert _TEXT.count('git checkout -f "$FROM_SHA"') == 3, _TEXT.count('git checkout -f "$FROM_SHA"')


def test_checkout_f_comes_after_second_fetch():
    """pinned-SHA 解析与 checkout 必须都落在同 ref 的第二次 fetch 之后，且解析在 checkout 前。"""
    first_fetch = _TEXT.index("git fetch --force github \"$REF\"")
    second_fetch = _TEXT.index("git fetch --force github \"$REF\"", first_fetch + 1)
    revparse = _TEXT.index('TARGET_SHA=$(git rev-parse "github/$REF"')
    checkout = _TEXT.index('git checkout -f "$TARGET_SHA"')
    assert first_fetch < second_fetch < revparse < checkout, (
        "pinned-SHA 解析与第二次 fetch 的相对顺序漂移"
    )


def test_dirty_check_ignores_untracked_files(tmp_path):
    """护栏②口径（autoflow 同）：未跟踪 .bak 不算活树改动，不应触发拒绝。

    v1.13.0 实战缺陷：本 NAS 部署仓有 5 个 .bak 遗留，旧体检命令
    `git status --porcelain --` 在新版 git 仍把它们计入 ⇒ 永久拒绝部署。
    修法 `--untracked-files=no` 只数已跟踪改动（M/A/D/R）。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "src.py.bak").write_text("x = 0\n", encoding="utf-8")  # 未跟踪
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    # 脚本实际用的命令（--untracked-files=no）必须不再数到 .bak
    out = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no", "--"],
        capture_output=True, text=True,
    )
    assert out.stdout.strip() == "", "未跟踪 .bak 不应被体检命令数到"

    # 对照：旧命令 `git status --porcelain --` 在本机若是新 git 会把 .bak 数进来，
    # 证明修复必要；旧版 git 不数（对照跳过，不影响修复断言）。
    out2 = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--"],
        capture_output=True, text=True,
    )
    if "src.py.bak" in out2.stdout:
        # 新版 git 复现了本 NAS 实战根因：旧命令会误拦未跟踪 .bak
        assert "src.py.bak" not in out.stdout, "修复后必须不再数到"


def test_gate_scripts_watch_this_file():
    """发布线门盯着本文件的分支字面（test_release_line_gate 同款锚点），文件移位须同步改锚。"""
    gate = Path(__file__).resolve().parents[2] / "tests" / "unit" / "test_release_line_gate.py"
    if not gate.exists():
        import pytest

        pytest.skip("test_release_line_gate.py not present")
    assert "host_update.sh" in gate.read_text(encoding="utf-8")
