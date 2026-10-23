#!/bin/bash
# AutoForge WebUI 自更新宿主侧执行脚本（参考 DCD 20261010 MA 裁定 Q1=B）
# crontab: * * * * * /vol1/1000/docker/autoforge/scripts/host_update.sh
#
# 流程（2026-10-17 DCD-20261017-001 §2.4 改为 compose build 重烘镜像）：
#   1. 检测 /data/.update_request.json 标记文件
#   2. cd 到作者树，git fetch + checkout -f 到 github/$REF
#   3. tar 备份 + py_compile 预检（失败回滚，不切流）
#   4. 旧镜像打 tag autoforge-api:prev（回滚用）
#   5. docker compose build 前后端一体镜像（前端已烘进镜像，不再宿主 npm/cp）
#   6. docker compose up -d；失败回滚 prev 镜像
#   7. 写结果到 /data/.update_result.json

set -uo pipefail

# 配置
REPO_DIR="/vol1/1000/docker/autoforge"
DATA_DIR="/vol1/1000/docker/autoforge-store"
DOCKER_DIR="/vol1/1000/docker/autoforge"
CONTAINER="autoforge-api"
MARKER="$DATA_DIR/.update_request.json"
RESULT="$DATA_DIR/.update_result.json"
LOG="$DATA_DIR/update.log"

# 全程持锁（2026-10-21）：cron 每分钟一跳，构建要 30+ 分钟，标记只在退出时删——没锁时重复实例会
# 堆叠（上次部署实测 5 例并发），失败例的回滚还会把 latest tag 打回旧镜像。非 0 退出码 = 已有
# 实例在跑，直接让位。
# 2026-10-22 修复：锁块原在 DATA_DIR 定义之前，`set -u` 下 `DATA_DIR: unbound variable`
# 秒退——dcc41f6 部署了自己之后的死脚本，自更新链路静默中断（本轮实测根因）。
LOCK="$DATA_DIR/.update.lock"
exec 9>"$LOCK"
flock -n 9 || exit 0

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
    export _UR_RESULT="$RESULT"
    python3 << 'PYEOF' >> "$LOG" 2>&1
import json, time, os, subprocess

# 直接从 git log 生成 changelog，避免 shell 变量转义问题
changelog = []
from_sha = os.environ.get("_UR_FROM", "unknown")
to_sha = os.environ.get("_UR_TO", "unknown")
if from_sha != "unknown" and to_sha != "unknown" and from_sha != to_sha:
    out = subprocess.run(
        ["git", "log", "--oneline", f"{from_sha}..{to_sha}", "--no-merges"],
        capture_output=True, text=True
    ).stdout.strip()
    changelog = [l.strip() for l in out.split("\n") if l.strip()][:20]

r = {
    "ok": os.environ.get("_UR_OK", "False") == "True",
    "from": from_sha,
    "from_tag": os.environ.get("_UR_FROM_TAG", "unknown"),
    "to": to_sha,
    "to_tag": os.environ.get("_UR_TO_TAG", "unknown"),
    "error": os.environ.get("_UR_ERR", ""),
    "changelog": changelog,
    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
}
with open(os.environ["_UR_RESULT"], "w") as f:
    json.dump(r, f, ensure_ascii=False, indent=2)
PYEOF
}

# 没有标记文件就退出
[ ! -f "$MARKER" ] && exit 0
log "=== 收到更新请求 ==="

# 读取目标 ref（默认 master）+ 白名单护栏（2026-10-23，autoflow 方案 C 照搬）：
# 只认 master 与 v* tag（^v[0-9]+\.[0-9]+\.[0-9]+$）。SHA 不进白名单——fetch-by-SHA
# 依赖服务端 allowReachableSHA1InWant 配置，回滚用 tag 承担。此前 ref 照单全收
# （docs/发布线清单.md §一.4「取值域=无」登记格随本批改为「master | v*tag」）。
REF=$(python3 -c "import json; print(json.load(open('$MARKER')).get('ref','master'))" 2>/dev/null || echo "master")
case "$REF" in
  master|v[0-9]*.[0-9]*.[0-9]*) ;;
  *) log "ERROR: ref 不在白名单: $REF"
     TO_SHA="unknown"; TO_TAG="unknown"
     write_result False "ref 非法（仅允许 master 或 vX.Y.Z tag）"
     rm -f "$MARKER"
     exit 1;;
esac
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
if ! git fetch --force github "$REF" >> "$LOG" 2>&1; then
    log "ERROR: git fetch 失败"
    TO_SHA="$FROM_SHA"; TO_TAG="$FROM_TAG"
    write_result False "git fetch 失败，请检查网络或 SSH 密钥"
    rm -f "$MARKER"
    exit 1
fi
log "fetch 成功"

# 活树体检（2026-10-23，autoflow「升级按钮当格式化键」事故照搬）：已跟踪文件有未提交
# 改动即拒绝——checkout -f 会把 docker-compose 绑定等 NAS 特有手工配置整片冲掉。
# 未跟踪文件（data/、*.bak）不拦（autoflow 同口径）。
# ⚠️ 必须用 --untracked-files=no：新版 git 的 `git status --porcelain --` 仍把
# 未跟踪文件计入（-- 不再屏蔽未跟踪），本 NAS 有 5 个 .bak 遗留会被误判成
# 「活树改动」⇒ 永久拒绝部署。只数已跟踪改动（M/A/D/R）。
DIRTY=$(git status --porcelain --untracked-files=no -- 2>/dev/null | grep -c . || true)
if [ "$DIRTY" -gt 0 ] 2>/dev/null; then
    log "ERROR: 活树有未提交改动（已跟踪文件 $DIRTY 处），拒绝覆盖"
    TO_SHA="$FROM_SHA"; TO_TAG="$FROM_TAG"
    write_result False "活树有未提交改动（$DIRTY 处），拒绝覆盖——NAS 特有配置请先手工处置或提交"
    rm -f "$MARKER"
    exit 1
fi
log "活树体检通过（0 处已跟踪改动）"

# checkout 到 github/$REF 的 pinned SHA（2026-10-23：禁 checkout -f——autoflow self_update
# 的硬约束「永不 git checkout -f / git clean -f」；checkout -f 同效且不移动分支指针）。
if ! git fetch --force github "$REF" >> "$LOG" 2>&1; then
    log "ERROR: git fetch($REF) 失败"
    TO_SHA="$FROM_SHA"; TO_TAG="$FROM_TAG"
    write_result False "git fetch($REF) 失败，ref 可能不存在"
    rm -f "$MARKER"
    exit 1
fi
TARGET_SHA=$(git rev-parse "github/$REF" 2>/dev/null || echo "")
if [ -z "$TARGET_SHA" ]; then
    log "ERROR: 解析不出 github/$REF 的 SHA"
    TO_SHA="$FROM_SHA"; TO_TAG="$FROM_TAG"
    write_result False "ref 解析失败：github/$REF 不存在"
    rm -f "$MARKER"
    exit 1
fi
if ! git checkout -f "$TARGET_SHA" >> "$LOG" 2>&1; then
    log "ERROR: git checkout 失败"
    TO_SHA="$FROM_SHA"; TO_TAG="$FROM_TAG"
    write_result False "git checkout 失败，代码可能有冲突"
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
    git checkout -f "$FROM_SHA" >> "$LOG" 2>&1
    write_result False "语法检查失败，已自动回滚"
    rm -f "$MARKER"
    exit 1
fi
log "py_compile 通过"

# 旧镜像打 tag，便于一键回滚（DCD-20261017-001 §2.5②：回滚 = up -d prev）
if docker image inspect autoforge-api:latest >/dev/null 2>&1; then
    docker tag autoforge-api:latest autoforge-api:prev
    log "旧镜像已打 tag: autoforge-api:prev"
else
    log "WARN: 无 autoforge-api:latest，跳过 prev tag"
fi

# 从镜像构建前后端（DCD-20261017-001 §2.4：前端已烘进镜像，不再宿主 npm build / docker cp / dist 同步）
# build 失败 ⇒ 不切流：旧镜像仍在服务，latest tag 未被覆盖，回滚 git 保持代码一致。
log "开始 docker compose build 镜像（前后端一体）..."
if ! docker compose -f "$DOCKER_DIR/docker/docker-compose.api.yml" build >> "$LOG" 2>&1; then
    log "ERROR: docker compose build 失败，不切流（旧镜像继续服务），git 回滚到 $FROM_SHA"
    git checkout -f "$FROM_SHA" >> "$LOG" 2>&1
    write_result False "镜像构建失败，未切流，旧镜像继续服务"
    rm -f "$MARKER"
    exit 1
fi
log "镜像构建成功"

# up -d 起新容器；失败则回滚 prev 镜像
if ! docker compose -f "$DOCKER_DIR/docker/docker-compose.api.yml" up -d >> "$LOG" 2>&1; then
    log "ERROR: up -d 失败，回滚到 prev 镜像"
    docker tag autoforge-api:prev autoforge-api:latest >> "$LOG" 2>&1
    docker compose -f "$DOCKER_DIR/docker/docker-compose.api.yml" up -d >> "$LOG" 2>&1
    git checkout -f "$FROM_SHA" >> "$LOG" 2>&1
    write_result False "up -d 失败，已回滚到 prev 镜像"
    rm -f "$MARKER"
    exit 1
fi

# 写结果
write_result True ""
rm -f "$MARKER"
log "=== 更新完成（compose build + up -d）==="
