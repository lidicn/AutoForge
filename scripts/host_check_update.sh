#!/bin/bash
# AutoForge 更新预检脚本
# crontab: */5 * * * * /vol1/1000/docker/autoforge/scripts/host_check_update.sh
#
# 流程：
#   1. cd 到作者树，git fetch origin master
#   2. 比较 local HEAD 与 origin/master
#   3. 写 .update_available.json 供容器内 API 读取

set -uo pipefail

REPO_DIR="/vol1/1000/docker/autoforge"
DATA_DIR="/vol1/1000/docker/autoforge-store"
PREVIEW="$DATA_DIR/.update_available.json"
LOG="$DATA_DIR/update_check.log"

export GIT_SSH_COMMAND="ssh -i /home/lidicn/.ssh/id_ed25519_github -o StrictHostKeyChecking=no"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

cd "$REPO_DIR" || { log "ERROR: 无法进入 $REPO_DIR"; exit 1; }

# fetch
if ! git fetch --force origin master >> "$LOG" 2>&1; then
    log "WARN: git fetch 失败"
    if [ ! -f "$PREVIEW" ]; then
        export _AU_PREVIEW="$PREVIEW"
        python3 << 'PYEOF'
import json, time, os
r = {
    "has_update": False,
    "local_commit": os.popen("git rev-parse HEAD").read().strip(),
    "local_tag": os.popen("git describe --tags --always").read().strip(),
    "remote_commit": "",
    "remote_tag": "",
    "changelog": [],
    "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "branch": "master",
    "error": "git fetch 失败",
}
with open(os.environ["_AU_PREVIEW"], "w") as f:
    json.dump(r, f, ensure_ascii=False, indent=2)
PYEOF
    fi
    exit 0
fi

# 用 Python 完成全部逻辑
export _AU_PREVIEW="$PREVIEW"
python3 << 'PYEOF'
import json, time, os, subprocess

def git(*args):
    return subprocess.run(["git"] + list(args), capture_output=True, text=True).stdout.strip()

local_sha = git("rev-parse", "HEAD")
local_tag = git("describe", "--tags", "--always")
remote_sha = git("rev-parse", "origin/master")
remote_tag = git("describe", "--tags", "--always", "origin/master")

has_update = local_sha != remote_sha and remote_sha != "unknown"

changelog = []
if has_update:
    log_out = subprocess.run(
        ["git", "log", "--oneline", f"{local_sha}..{remote_sha}", "--no-merges"],
        capture_output=True, text=True
    ).stdout.strip()
    changelog = [l.strip() for l in log_out.split("\n") if l.strip()][:20]

r = {
    "has_update": has_update,
    "local_commit": local_sha,
    "local_tag": local_tag,
    "remote_commit": remote_sha,
    "remote_tag": remote_tag,
    "changelog": changelog,
    "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "branch": "master",
    "error": "",
}
with open(os.environ["_AU_PREVIEW"], "w") as f:
    json.dump(r, f, ensure_ascii=False, indent=2)

print(f"local={local_sha[:12]} remote={remote_sha[:12]} has_update={has_update}")
PYEOF

log "检查完成: local=$(git rev-parse HEAD 2>/dev/null | cut -c1-12) remote=$(git rev-parse origin/master 2>/dev/null | cut -c1-12)"
