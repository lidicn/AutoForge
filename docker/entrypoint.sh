#!/bin/bash
# entrypoint.sh：管 serve + watch 生命周期
# docker restart 时 trap SIGTERM，先杀 watch 再杀 serve，避免孤儿占锁

set -e

# 启动 serve（前台跑，但我们要 trap，所以后台跑再 wait）
# 2026-10-22：去掉 --examples——样例灌入只认显式传参，演示 IR 不再混进 /data 用户数据
forge serve --host 0.0.0.0 --port 8787 \
  --store-root /data \
  --ui-dir /ui \
  --ui-user-dir /mimo &

SERVE_PID=$!
echo "[entrypoint] serve pid=$SERVE_PID"

# trap：收到 SIGTERM/SIGINT 时，先杀 watch 子进程，再杀 serve
cleanup() {
  echo "[entrypoint] 收到信号，清理 watch..."
  pkill -f "forge watch" 2>/dev/null || true
  sleep 1
  kill $SERVE_PID 2>/dev/null || true
  wait $SERVE_PID 2>/dev/null
  echo "[entrypoint] 退出"
}
trap cleanup TERM INT

# 等 serve
wait $SERVE_PID
