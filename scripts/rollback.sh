#!/bin/bash
# AutoForge 回滚：从 .quarantine-20260921 恢复备份的文件，restart api，health 验证。
# 用法：bash scripts/rollback.sh
# 注意：只恢复 src/autoforge/ 下有 .bak 的文件；不动 docker 镜像、不动数据库。

set -u
QDIR=/vol1/1000/docker/autoforge/.quarantine-20260921
SRC=/vol1/1000/docker/autoforge/src/autoforge

say() { echo "[$(date -u +%H:%M:%SZ)] $*"; }

[ -d "$QDIR" ] || { say "FAIL 备份目录不存在: $QDIR"; exit 1; }

say "=== 待恢复文件 ==="
ls -la "$QDIR"/*.bak 2>/dev/null || { say "FAIL 无 .bak 文件"; exit 1; }

echo
read -p "确认回滚？这会覆盖当前 src/autoforge/ 下对应文件（yes/NO): " ans
[ "$ans" = "yes" ] || { say "已取消"; exit 0; }

say "=== 恢复 ==="
for bak in "$QDIR"/*.bak; do
    f=$(basename "$bak" .bak)
    case "$f" in
        http.py) target="$SRC/af_adapters/http.py" ;;
        *)       target="$SRC/$f" ;;
    esac
    cp -p "$bak" "$target" && say "恢复 $target"
done

say "=== restart api ==="
docker restart autoforge-api >/dev/null && say "restart 已发"

sleep 10
code=$(docker exec autoforge-api python3 -c 'import urllib.request;print(urllib.request.urlopen("http://localhost:8787/api/health",timeout=5).status)' 2>/dev/null)
[ "$code" = "200" ] && say "PASS health=200" || say "FAIL health=$code"
