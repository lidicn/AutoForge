#!/bin/bash
# AutoForge 投产前检查（只读，不改任何东西）
# 用法：bash scripts/preflight.sh
# 退出码：0 = 全绿可投产；1 = 有红项

set -u
RED=0
say() { echo "[$(date -u +%H:%M:%SZ)] $*"; }
ok()  { say "PASS  $1"; }
bad() { say "FAIL  $1"; RED=1; }

say "=== 1. 容器状态 ==="
for c in autoforge-api autoforge-sync; do
    st=$(docker inspect --format '{{.State.Status}} restarts={{.RestartCount}}' "$c" 2>/dev/null) \
        && ok "$c: $st" || bad "$c: 不存在或未运行"
done

say "=== 2. 健康检查 ==="
code=$(docker exec autoforge-api python3 -c 'import urllib.request,sys; print(urllib.request.urlopen("http://localhost:8787/api/health",timeout=5).status)' 2>/dev/null) \
    && [ "$code" = "200" ] && ok "api /api/health=200" || bad "api /api/health=$code"

say "=== 3. homesdk 版本 ==="
ver=$(docker exec autoforge-api python3 -c 'import importlib.metadata as m;print(m.version("homesdk"))' 2>/dev/null) \
    && [ "$ver" = "0.1.1" ] && ok "homesdk=$ver" || bad "homesdk=$ver (期望 0.1.1)"

say "=== 4. 关键模块 import ==="
docker exec autoforge-api python3 -c '
from autoforge.af_pending import PendingStore, DEFAULT_TTL_S
from autoforge.af_adapters.http import _WhitelistRedirector
import autoforge.af_cli
print("TTL", DEFAULT_TTL_S)
' 2>/dev/null && ok "关键模块 import OK" || bad "关键模块 import 失败"

say "=== 5. 门禁（gates.sh）==="
if docker exec -w /app autoforge-api test -f gates.sh 2>/dev/null; then
    if docker exec -w /app autoforge-api bash gates.sh >/tmp/preflight_gates.txt 2>&1; then
        ok "gates.sh exit=0"
    else
        rc=$?
        bad "gates.sh exit=$rc（见 /tmp/preflight_gates.txt）"
    fi
else
    say "WARN gates.sh 不在容器可写层（已知态，归 AF2 烘镜像）"
fi

say "=== 6. 版本账 ==="
cd /vol1/1000/docker/autoforge 2>/dev/null && {
    head=$(git rev-parse --short HEAD 2>/dev/null)
    dirty=$(git status --porcelain | wc -l)
    say "部署树 HEAD=$head 未提交改动=$dirty"
}

echo
if [ "$RED" = "0" ]; then
    say "=== 全绿，可投产 ==="
    exit 0
else
    say "=== 有红项，先别投产 ==="
    exit 1
fi
