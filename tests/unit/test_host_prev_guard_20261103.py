# -*- coding: utf-8 -*-
"""host_update.sh 的 `:prev` 同版本守卫判据（AF 卡 `AF卡_host_update_prev守卫_v119_20261103.md`）。

⛔ 验法＝**docker 桩**（卡 §三）：把 `docker` 换成 PATH 里**记录调用**的 shell 桩，
断言「桩收到/没收到 `docker tag autoforge-api:latest autoforge-api:prev`」——
⛔ 不真部署、⛔ 不碰线上镜像、⛔ 不 prune（v1.18.0 那次拿生产回滚网做实验的教训）。
被测函数 `tag_prev_guard()` **按名从 scripts/host_update.sh 抽取**（⛔ 不在测试里
复制逻辑＝第二份漂移源；函数形状变了这里会红，不会静默假绿）。

T1 `:latest`/`:prev` **Id 相同** ⇒ ⛔ 不执行 `docker tag`，log 含「跳过」
T2 Id 不同（正常升级路径）⇒ 照旧 tag（⛔ 不许把正常回滚网也弄没了）
T3 `:prev` 不存在 ⇒ 照旧 tag（首次部署路径不受影响）
T4 版本标签相同但 Id 不同 ⇒ 跳过，且 log 说明是「版本标签相同」这一路
T0 附加：`:latest` 不存在 ⇒ WARN 照旧（原 else 路径不回归）
T5 `bash -n scripts/host_update.sh` RC=0
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess

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
    """解析可用的 bash。⚠️ Windows 上 `which bash` 会命中 **System32\bash.exe（WSL
    入口）**——它的 PATH 分隔符/路径语义全不同，本机实测拿它跑桩＝假失败（登记过的
    坑）。解析顺序：git 同源推导的 bash.exe（Git for Windows）→ 非 System32 的 PATH
    bash → 兜底 PATH 值（CI=ubuntu 时即真 bash）。"""
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
    """按函数名从脚本抽取（⛔ 复制逻辑＝第二份漂移源；抽不到＝形状变了，红）。"""
    text = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"^tag_prev_guard\(\) \{\n(.*?)^\}$", text, re.M | re.S)
    assert m, "scripts/host_update.sh 里抽不到 tag_prev_guard()——函数形状变了？"
    return m.group(0)


def _run_guard(tmp_path: pathlib.Path, **sc: str) -> tuple[list[str], str]:
    """在桩环境里跑抽取出的守卫函数，返回（docker 调用记录, 脚本 log）。"""
    stub_bin = tmp_path / "stubbin"
    stub_bin.mkdir(exist_ok=True)
    (stub_bin / "docker").write_text(STUB, encoding="utf-8", newline="\n")
    (tmp_path / "harness.sh").write_text(
        "log() { printf '%s\\n' \"$*\" >> \"$SCRIPT_LOG\"; }\n"
        + _extract_guard()
        + "\ntag_prev_guard\n",
        encoding="utf-8",
        newline="\n",
    )
    for name in ("docker_calls.log", "script_log.txt"):
        (tmp_path / name).write_text("", encoding="utf-8", newline="\n")
    env = os.environ.copy()
    env["PATH"] = f"stubbin{os.pathsep}{env.get('PATH', '')}"
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


def test_t1_same_id_skips_overwrite(tmp_path):
    """T1：Id 相同 ⇒ 不执行 docker tag，:prev 保持原值，log 含「跳过」。"""
    calls, log = _run_guard(
        tmp_path, SC_LATEST_EXISTS="1", SC_PREV_EXISTS="1",
        SC_LATEST_ID="sha256:aaa", SC_PREV_ID="sha256:aaa",
    )
    assert not any(c.startswith("tag ") for c in calls), calls
    assert "跳过" in log and "Image Id 相同" in log


def test_t2_different_id_still_tags(tmp_path):
    """T2：Id 不同（正常升级路径）⇒ 照旧 tag（回滚网不因守卫消失）。"""
    calls, log = _run_guard(
        tmp_path, SC_LATEST_EXISTS="1", SC_PREV_EXISTS="1",
        SC_LATEST_ID="sha256:bbb", SC_PREV_ID="sha256:aaa",
    )
    assert any(c == TAG_CALL for c in calls), calls
    assert "旧镜像已打 tag" in log


def test_t3_prev_absent_still_tags(tmp_path):
    """T3：:prev 不存在 ⇒ 照旧 tag（首次部署路径不受影响）。"""
    calls, log = _run_guard(
        tmp_path, SC_LATEST_EXISTS="1", SC_PREV_EXISTS="0",
        SC_LATEST_ID="sha256:bbb",
    )
    assert any(c == TAG_CALL for c in calls), calls
    assert "旧镜像已打 tag" in log


def test_t4_same_version_label_skips(tmp_path):
    """T4：版本标签相同但 Id 不同 ⇒ 跳过，log 说明是「版本标签相同」这一路。"""
    calls, log = _run_guard(
        tmp_path, SC_LATEST_EXISTS="1", SC_PREV_EXISTS="1",
        SC_LATEST_ID="sha256:bbb", SC_PREV_ID="sha256:aaa",
        SC_LATEST_VER="1.18.0", SC_PREV_VER="1.18.0",
    )
    assert not any(c.startswith("tag ") for c in calls), calls
    assert "跳过" in log and "版本标签相同" in log
    assert "Image Id 相同" not in log, "T4 必须走「版本标签」这一路，不是 Id 路"


def test_t0_latest_absent_warns(tmp_path):
    """T0（原路径回归）：:latest 不存在 ⇒ WARN，不 tag。"""
    calls, log = _run_guard(tmp_path, SC_LATEST_EXISTS="0")
    assert not any(c.startswith("tag ") for c in calls), calls
    assert "WARN" in log


def test_t5_script_syntax_ok():
    """T5：bash -n RC=0（语法门；gates.sh 与发布线门在提交面跑）。"""
    proc = subprocess.run(
        [_bash(), "-n", str(SCRIPT)], capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
