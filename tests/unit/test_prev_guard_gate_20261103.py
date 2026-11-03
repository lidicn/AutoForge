# -*- coding: utf-8 -*-
"""`:prev` 同版本守卫判据（P0 修法 · AF卡_prev守卫修法_v120_P0_20261103）。

**本卡判据的核心要求：不依赖任何夹具可造的量**（v1.19 的教训）——旧判据比「镜像 Id／
版本标签」，而守卫在 `build` 之前调用、读到的永远是**两个旧标签**，同版本重部署时
结构上不可能相等 ⇒ 桩里喂个「相同 Id」就全绿、真机必挂。本版判据只认**部署标量**
`TARGET_SHA == FROM_SHA`（零外部命令、不依赖 docker／构建缓存）⇒ 桩与真机等价。

T1'（核心）同 sha ⇒ 跳过，log 含「同 sha 重部署」；**并断言该路径 docker 调用记录为 0**
     （⇒ 判据不依赖 docker，桩绿即真机绿）
T2'（回归）不同 sha（正常升级）⇒ 照旧 tag（⛔ 不许弄坏真升级）
T3' `:prev` 不存在（且非同 sha）⇒ 照旧 tag（首次部署不受影响）
T4' 版本标签相同 ⇒ 跳过（保留判据）；无 label ⇒ **不误跳**、照旧 tag
T5' `bash -n` RC=0（gates.sh／`check_release_line.py` 在提交面跑，见报告）
T5b'（形状腿）**守卫调用点必须在 `build` 之前**（真脚本现读行号；这条腿不依赖 docker，
     正是能抓到 v1.19 病灶的那条——守卫挪到 build 之后就拿不到旧镜像）
T6'（自证「桩无以致假」）**把 docker 桩改成任何 Id 值，T1'/T2' 结论不变**——
     证明结论只由 FROM_SHA／TARGET_SHA 决定。

被测函数 `tag_prev_guard()` 按名从 `scripts/host_update.sh` **抽取**（⛔ 测试里不复制
实现＝第二份漂移源；函数形状变了测试红而非假绿）。
⛔ 不真部署、⛔ 不碰线上镜像、⛔ 不 prune。
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "host_update.sh"

STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "${DOCKER_CALLLOG}"
tag=""
case "$*" in
  *"autoforge-api:prev"*) tag="prev";;
  *"autoforge-api:latest"*) tag="latest";;
esac
if [ "$1" = "tag" ]; then exit 0; fi
exists_var="SC_${tag^^}_EXISTS"
if [ "${!exists_var:-1}" != "1" ]; then exit 1; fi
case "$*" in
  *"{{.Id}}"*)
    v="SC_${tag^^}_ID"; printf '%s\n' "${!v:-}"; exit 0;;
  *'Config.Labels "version"'*)
    v="SC_${tag^^}_VER"; printf '%s\n' "${!v:-}"; exit 0;;
esac
exit 0
"""

TAG_CALL = "tag autoforge-api:latest autoforge-api:prev"


def _bash() -> str:
    """解析可用 bash。⚠️ Windows `which bash` 会命中 System32 的 WSL 入口（PATH/路径
    语义全不同＝本机登记过的假失败）⇒ git 同源推导 Git Bash 优先；CI=ubuntu 即真 bash。"""
    if os.name == "nt":
        git_exe = shutil.which("git")
        if git_exe:
            candidate = pathlib.Path(git_exe).resolve().parent.parent / "bin" / "bash.exe"
            if candidate.exists():
                return str(candidate)
        exe = shutil.which("bash")
        if exe and "system32" not in exe.lower():
            return exe
    return shutil.which("bash") or "bash"


def _extract_guard() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"^tag_prev_guard\(\) \{\n(.*?)^\}$", text, re.M | re.S)
    assert m, "scripts/host_update.sh 里抽不到 tag_prev_guard()——函数形状变了？"
    return m.group(0)


def _run_guard(
    tmp_path: pathlib.Path, from_sha: str, target_sha: str, **sc: str
) -> tuple[list[str], str]:
    """在 docker 桩环境里跑抽取出的守卫函数，返回（docker 调用记录, 脚本 log）。"""
    stub_bin = tmp_path / "stubbin"
    stub_bin.mkdir(parents=True, exist_ok=True)
    stub = stub_bin / "docker"
    stub.write_text(STUB, encoding="utf-8", newline="\n")
    # ⚠️ CI=ubuntu：PATH 查命令要执行位，不给 chmod 会「command not found」⇒ 假红
    #（v1.19 的 CI 红点 1192827 修的就是这个；本版一次带对）。
    os.chmod(stub, 0o755)
    (tmp_path / "harness.sh").write_text(
        "log() { printf '%s\\n' \"$*\" >> \"$SCRIPT_LOG\"; }\n"
        f"FROM_SHA={from_sha!r}\nTARGET_SHA={target_sha!r}\n"
        + _extract_guard()
        + "\ntag_prev_guard\n",
        encoding="utf-8",
        newline="\n",
    )
    for name in ("docker_calls.log", "script_log.txt"):
        (tmp_path / name).write_text("", encoding="utf-8", newline="\n")
    env = os.environ.copy()
    # PATH 用**绝对**桩目录（相对 PATH 在部分 shell/cwd 组合下解析不可靠）。
    env["PATH"] = f"{stub_bin.resolve()}{os.pathsep}{env.get('PATH', '')}"
    env["DOCKER_CALLLOG"] = "docker_calls.log"
    env["SCRIPT_LOG"] = "script_log.txt"
    for key, value in sc.items():
        env[key] = value
    proc = subprocess.run(
        [_bash(), "harness.sh"], cwd=str(tmp_path), env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"守卫函数非零退出：{proc.stderr}"
    calls = (tmp_path / "docker_calls.log").read_text(encoding="utf-8").splitlines()
    log = (tmp_path / "script_log.txt").read_text(encoding="utf-8")
    return calls, log


SHA_A = "1192827abcdef0123456789abcdef0123456789a"
SHA_B = "abc0123abcdef0123456789abcdef0123456789b"


def test_t1_same_sha_skips_with_zero_docker_calls(tmp_path):
    """T1'：同 sha ⇒ 跳过＋log「同 sha 重部署」，且**docker 调用 0 次**（判据不依赖 docker）。"""
    calls, log = _run_guard(
        tmp_path, SHA_A, SHA_A,
        SC_LATEST_EXISTS="1", SC_PREV_EXISTS="1",
        SC_LATEST_ID="sha256:whatever", SC_PREV_ID="sha256:whatever",
    )
    assert calls == [], f"同 sha 路径上不许有任何 docker 调用，实得：{calls}"
    assert "同 sha 重部署" in log and "跳过" in log


def test_t2_different_sha_still_tags(tmp_path):
    """T2'：不同 sha（正常升级）⇒ 照旧 tag（⛔ 不许弄坏真升级）。"""
    calls, log = _run_guard(
        tmp_path, SHA_A, SHA_B,
        SC_LATEST_EXISTS="1", SC_PREV_EXISTS="1",
        SC_LATEST_ID="sha256:mmm", SC_PREV_ID="sha256:nnn",
    )
    assert any(c == TAG_CALL for c in calls), calls
    assert "旧镜像已打 tag" in log


def test_t3_prev_absent_still_tags(tmp_path):
    """T3'：`:prev` 不存在且非同 sha ⇒ 照旧 tag。"""
    calls, log = _run_guard(
        tmp_path, SHA_A, SHA_B, SC_LATEST_EXISTS="1", SC_PREV_EXISTS="0",
    )
    assert any(c == TAG_CALL for c in calls), calls
    assert "旧镜像已打 tag" in log


def test_t4_version_label_same_skips_but_absent_label_does_not(tmp_path):
    """T4'：版本标签相同 ⇒ 跳过；无 label ⇒ 不误跳（照旧 tag）。"""
    calls, log = _run_guard(
        tmp_path / "samever", SHA_A, SHA_B,
        SC_LATEST_EXISTS="1", SC_PREV_EXISTS="1",
        SC_LATEST_VER="1.19.0", SC_PREV_VER="1.19.0",
    )
    assert not any(c.startswith("tag ") for c in calls), calls
    assert "版本标签相同" in log

    calls2, log2 = _run_guard(
        tmp_path / "nolabel", SHA_A, SHA_B,
        SC_LATEST_EXISTS="1", SC_PREV_EXISTS="1",
        SC_LATEST_VER="", SC_PREV_VER="",
    )
    assert any(c == TAG_CALL for c in calls2), calls2
    assert "旧镜像已打 tag" in log2


def test_t5b_shape_guard_before_build():
    """T5b'（形状腿，不依赖 docker）：守卫调用点必须在 build 之前——v1.19 病灶的守卫腿。"""
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    guard_calls = [i for i, ln in enumerate(lines) if ln.strip() == "tag_prev_guard"]
    assert len(guard_calls) == 1, f"tag_prev_guard 调用点应恰 1 处，实得 {guard_calls}"
    guard_at = guard_calls[0]
    build_at = [
        i for i, ln in enumerate(lines)
        if "docker-compose.api.yml" in ln and "build" in ln
    ]
    assert build_at, "找不到 build 行（脚本形状变了？）"
    assert guard_at < build_at[0], (
        f"守卫调用点 (line {guard_at + 1}) 必须在 build (line {build_at[0] + 1}) 之前——"
        "挪到之后就拿不到旧镜像（P0 卡 §二.2）"
    )


@pytest.mark.parametrize(
    "ids",
    [
        ("sha256:same", "sha256:same"),
        ("sha256:aaa", "sha256:bbb"),
        ("", ""),
        ("sha256:zzz", ""),
    ],
)
def test_t6_conclusions_independent_of_stub_ids(tmp_path, ids):
    """T6'：把桩改成**任何 Id 值**，T1'/T2' 结论不变（结论只由 FROM_SHA/TARGET_SHA 决定）。"""
    latest_id, prev_id = ids
    calls_same, log_same = _run_guard(
        tmp_path / "same", SHA_A, SHA_A,
        SC_LATEST_EXISTS="1", SC_PREV_EXISTS="1",
        SC_LATEST_ID=latest_id, SC_PREV_ID=prev_id,
    )
    assert calls_same == [] and "同 sha 重部署" in log_same
    calls_up, log_up = _run_guard(
        tmp_path / "up", SHA_A, SHA_B,
        SC_LATEST_EXISTS="1", SC_PREV_EXISTS="1",
        SC_LATEST_ID=latest_id, SC_PREV_ID=prev_id,
    )
    assert any(c == TAG_CALL for c in calls_up) and "旧镜像已打 tag" in log_up


def test_t5_script_syntax_ok():
    """T5'：`bash -n scripts/host_update.sh` RC=0。"""
    proc = subprocess.run(
        [_bash(), "-n", str(SCRIPT)], capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
