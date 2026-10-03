#!/usr/bin/env python3
"""读 GitHub Actions 的 run / job 结论——把"CI 读数"从一次性手工 curl 变成可复跑的命令。

为什么要它（不是偏好）：执行记录 §二之五 那批 CI 首读靠的是当场手搓 `curl` +
`~/.git-credentials` 里那条凭证，属于**一次性条件**：网页与 API 都不可达时，CI 又变成
读不到（假安心＝EXEMPT，铁律 #5）。本脚本把同一条只读链路固化下来，只做 GET，
不写任何仓外状态。

凭证来源（按顺序，取到即停）：
1. 环境变量 `GH_TOKEN` / `GITHUB_TOKEN`；
2. `git credential fill`（git 自己配的 helper 链，包括 store 文件与系统凭据管理器）。
**脚本不打印凭证，也不把它落盘**；失败时只打印"取不到凭证"。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
OWNER_REPO = "lidicn/AutoForge"


def _token() -> str:
    for var in ("GH_TOKEN", "GITHUB_TOKEN", "AF_GH_TOKEN"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if proc.returncode != 0:
        return ""
    for line in proc.stdout.splitlines():
        key, _, val = line.partition("=")
        if key.strip() == "password" and val.strip():
            return val.strip()
    return ""


def _get(path: str, token: str) -> object:
    req = urllib.request.Request(
        API + path,
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "autoforge-ci-reader"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """跟签名 URL 之前把 Authorization 摘掉：urllib 默认会在重定向时**继续带**自定义头，
    那等于把 token 发给日志存储的域名。所以先停在 302，取出 Location，再无凭证地取正文。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _get_log(job_id: str, token: str) -> str:
    """job 日志原文。

    为什么要有：步骤里 `echo` 出来的实测读数（例如 §二之七 的 npm fetch 主机计数）只存在于日志，
    job 的 conclusion 只告诉你"过了"，不告诉你"它看见了什么"。签名 URL 本身就是一次性凭证，
    所以这里**只回文本、由调用方 grep**，不打印 URL。
    """
    opener = urllib.request.build_opener(_NoRedirects)
    req = urllib.request.Request(
        f"{API}/repos/{OWNER_REPO}/actions/jobs/{job_id}/logs",
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "autoforge-ci-reader"},
    )
    try:
        resp = opener.open(req, timeout=60)
        # 没有 302：说明返回的直接就是 JSON 错误体
        raise RuntimeError(f"日志端点没有返回 302 签名地址，而是 {resp.status}")
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise
        location = exc.headers.get("Location")
        if not location:
            raise RuntimeError("302 响应里没有 Location") from exc
    signed = urllib.request.Request(location, headers={"User-Agent": "autoforge-ci-reader"})
    with urllib.request.urlopen(signed, timeout=180) as body:  # 无 Authorization
        return body.read().decode("utf-8", "replace")


def runs_lines(payload: dict) -> list[str]:
    """把 /actions/runs 的响应渲染成读数行（纯函数，便于离线测）。"""
    rows = payload.get("workflow_runs", [])
    lines = [f"total_count={payload.get('total_count')} listed={len(rows)}"]
    greens = [r for r in rows if r.get("conclusion") == "success"]
    lines.append(f"success runs: {[r.get('run_number') for r in greens]}")
    for run in rows:
        lines.append(
            f"run {run.get('run_number')} id={run.get('id')} "
            f"{str(run.get('head_sha'))[:7]} status={run.get('status')} "
            f"conclusion={run.get('conclusion')} event={run.get('event')}"
        )
    return lines


def jobs_lines(payload: dict) -> list[str]:
    lines = []
    for job in payload.get("jobs", []):
        lines.append(
            f"{job.get('name')}: {job.get('status')}/{job.get('conclusion')} "
            f"job_id={job.get('id')} failed_steps={[s.get('name') for s in job.get('steps', []) if s.get('conclusion') == 'failure']}"
        )
    return lines


def main(argv: list[str]) -> int:
    token = _token()
    if not token:
        print("取不到 github.com 凭证（GH_TOKEN/GITHUB_TOKEN 未设，git credential fill 无返回）", file=sys.stderr)
        return 2
    if len(argv) > 2 and argv[1] in ("jobs", "annotate"):
        jobs = _get(f"/repos/{OWNER_REPO}/actions/runs/{argv[2]}/jobs?per_page=100", token).get("jobs", [])
        if argv[1] == "jobs":
            print("\n".join(jobs_lines({"jobs": jobs})))
        else:
            for job in jobs:
                for step in [s for s in job.get("steps", []) if s.get("conclusion") == "failure"]:
                    print(f"[{job.get('name')}] 失败步骤：{step.get('name')}")
        return 0
    if len(argv) > 3 and argv[1] == "log":
        # 过滤是**必须**的：整份日志动辄上万行，且这一步之后别的步骤可能 echo 过环境。
        needle = argv[3]
        matched = [line for line in _get_log(argv[2], token).splitlines() if needle in line]
        print(f"匹配 {len(matched)} 行（关键词 {needle!r}）")
        for line in matched[:200]:
            print(line)
        return 0
    payload = _get(f"/repos/{OWNER_REPO}/actions/runs?per_page={os.environ.get('AF_CI_PAGES', '10')}", token)
    print("\n".join(runs_lines(payload)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
