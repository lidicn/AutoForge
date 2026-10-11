#!/bin/bash
# AutoForge WebUI 自更新宿主侧执行脚本（参考 DCD 20261010 MA 裁定 Q1=B）
# crontab: * * * * * /vol1/1000/docker/autoforge/scripts/host_update.sh
#
# 流程：
#   1. 检测 /data/.update_request.json 标记文件
#   2. cd 到作者树，git fetch + reset --hard
#   3. 构建 UI（npm install + npm run build）
#   4. 同步 dist 到 docker 挂载目录
#   5. docker cp 后端代码到容器
#   6. 重启容器
#   7. 写结果到 /data/.update_result.json

set -uo pipefail

# 配置
REPO_DIR="/vol1/1000/AutoForge"
DATA_DIR="/vol1/1000/docker/autoforge-store"
DOCKER_DIR="/vol1/1000/docker/autoforge"
CONTAINER="autoforge-api"
MARKER="$DATA_DIR/.update_request.json"
RESULT="$DATA_DIR/.update_result.json"
LOG="$DATA_DIR/update.log"

# SSH 密钥（用于 git pull）
export GIT_SSH_COMMAND="ssh -i /home/lidicn/.ssh/id_ed25519_github -o StrictHostKeyChecking=no"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

write_result() {
    # $1=ok(true/false)  $2=error_message
    export _UR_OK="$1"
    export _UR_ERR="$2"
    export _UR_FROM="$FROM_SHA"
    export _UR_FROM_TAG="$FROM_TAG"
    export _UR_TO="$TO_SHA"
    export _UR_TO_TAG="$TO_TAG"
    export _UR_CHANGELOG="$CHANGELOG"
    export _UR_RESULT="$RESULT"
    python3 << 'PYEOF' >> "$LOG" 2>&1
import json, time, os
r = {
    "ok": os.environ.get("_UR_OK", "False") == "True",
    "from": os.environ.get("_UR_FROM", "unknown"),
    "from_tag": os.environ.get("_UR_FROM_TAG", "unknown"),
    "to": os.environ.get("_UR_TO", "unknown"),
    "to_tag": os.environ.get("_UR_TO_TAG", "unknown"),
    "error": os.environ.get("_UR_ERR", ""),
    "changelog": json.loads(os.environ.get("_UR_CHANGELOG", "[]")),
    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
}
with open(os.environ["_UR_RESULT"], "w") as f:
    json.dump(r, f, ensure_ascii=False, indent=2)
PYEOF
}

# 没有标记文件就退出
[ ! -f "$MARKER" ] && exit 0
log "=== 收到更新请求 ==="

# 读取目标分支
REF=$(python3 -c "import json; print(json.load(open('$MARKER')).get('ref','main'))" 2>/dev/null || echo "main")
log "目标 ref: $REF"

cd "$REPO_DIR" || { log "ERROR: 无法进入 $REPO_DIR"; exit 1; }

FROM_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
FROM_TAG=$(git describe --tags --always 2>/dev/null || echo "$FROM_SHA")
log "当前: $FROM_SHA ($FROM_TAG)"

# 备份
BACKUP="$DATA_DIR/update-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
tar czf "$BACKUP" -C "$REPO_DIR" --exclude='.git' --exclude='node_modules' --exclude='dist' src/ ui/ ui-user-mimo/ >> "$LOG" 2>&1
log "备份: $BACKUP"

TO_SHA=""
TO_TAG=""
CHANGELOG="[]"

# fetch
if ! git fetch --force origin "$REF" >> "$LOG" 2>&1; then
    log "ERROR: git fetch 失败"
    TO_SHA="$FROM_SHA"; TO_TAG="$FROM_TAG"
    write_result False "git fetch 失败，请检查网络或 SSH 密钥"
    rm -f "$MARKER"
    exit 1
fi
log "fetch 成功"

# reset to origin/$REF
if ! git reset --hard "origin/$REF" >> "$LOG" 2>&1; then
    log "ERROR: git reset 失败"
    TO_SHA="$FROM_SHA"; TO_TAG="$FROM_TAG"
    write_result False "git reset 失败，代码可能有冲突"
    rm -f "$MARKER"
    exit 1
fi
TO_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
TO_TAG=$(git describe --tags --always 2>/dev/null || echo "$TO_SHA")
log "更新到: $TO_SHA ($TO_TAG)"

# changelog
CHANGELOG=$(git log --oneline "$FROM_SHA..$TO_SHA" --no-merges 2>/dev/null | head -20 | python3 -c "
import sys, json
commits = [l.strip() for l in sys.stdin if l.strip()]
print(json.dumps(commits, ensure_ascii=False))
" 2>/dev/null || echo "[]")

# py_compile 后端代码
if ! python3 -m py_compile src/autoforge/*.py >> "$LOG" 2>&1; then
    log "ERROR: py_compile 失败，回滚"
    git reset --hard "$FROM_SHA" >> "$LOG" 2>&1
    write_result False "语法检查失败，已自动回滚"
    rm -f "$MARKER"
    exit 1
fi
log "py_compile 通过"

# 检测 node/npm
HAS_NODE=0
if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    HAS_NODE=1
fi

# 构建 UI（开发者 webui）
if [ "$HAS_NODE" -eq 1 ]; then
    log "开始构建 ui（开发者 webui）..."
    cd "$REPO_DIR/ui" || { log "ERROR: 无法进入 ui 目录"; write_result False "无法进入 ui 目录"; rm -f "$MARKER"; exit 1; }
    if ! npm install >> "$LOG" 2>&1; then
        log "ERROR: ui npm install 失败"
        write_result False "ui npm install 失败"
        rm -f "$MARKER"
        exit 1
    fi
    if ! npm run build >> "$LOG" 2>&1; then
        log "ERROR: ui npm run build 失败"
        write_result False "ui npm run build 失败"
        rm -f "$MARKER"
        exit 1
    fi
    log "ui 构建成功"
else
    log "WARN: NAS 上未安装 node/npm，跳过 ui 构建（使用仓库中已有的 dist）"
fi

# 构建 UI（用户视角 mimo）
if [ "$HAS_NODE" -eq 1 ]; then
    log "开始构建 ui-user-mimo（用户视角）..."
    cd "$REPO_DIR/ui-user-mimo" || { log "ERROR: 无法进入 ui-user-mimo 目录"; write_result False "无法进入 ui-user-mimo 目录"; rm -f "$MARKER"; exit 1; }
    if ! npm install >> "$LOG" 2>&1; then
        log "ERROR: ui-user-mimo npm install 失败"
        write_result False "ui-user-mimo npm install 失败"
        rm -f "$MARKER"
        exit 1
    fi
    if ! npm run build >> "$LOG" 2>&1; then
        log "ERROR: ui-user-mimo npm run build 失败"
        write_result False "ui-user-mimo npm run build 失败"
        rm -f "$MARKER"
        exit 1
    fi
    log "ui-user-mimo 构建成功"
else
    log "WARN: NAS 上未安装 node/npm，跳过 ui-user-mimo 构建（使用仓库中已有的 dist）"
fi

# 同步 dist 到 docker 挂载目录
log "同步 dist 到 docker 挂载目录..."
rm -rf "$DOCKER_DIR/ui/dist"
cp -r "$REPO_DIR/ui/dist" "$DOCKER_DIR/ui/dist"
rm -rf "$DOCKER_DIR/ui-user-mimo/dist"
cp -r "$REPO_DIR/ui-user-mimo/dist" "$DOCKER_DIR/ui-user-mimo/dist"
log "dist 同步完成"

# docker cp 后端代码到容器
log "docker cp 后端代码到容器..."
docker cp "$REPO_DIR/src/autoforge/." "$CONTAINER:/app/src/autoforge/" >> "$LOG" 2>&1
if [ $? -ne 0 ]; then
    log "ERROR: docker cp 失败"
    write_result False "docker cp 后端代码失败"
    rm -f "$MARKER"
    exit 1
fi
log "docker cp 完成"

# 写结果
write_result True ""
rm -f "$MARKER"
log "=== 更新完成，重启容器 ==="

sleep 2
docker restart "$CONTAINER" >> "$LOG" 2>&1
log "容器已重启"
