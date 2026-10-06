#!/usr/bin/env bash
# AF→MA 指标回灌定时任务（MA 联动收尾 · 2026-10-06）
#
# 设计：
# - 令牌从宿主 /vol1/1000/docker/autoforge/.env 读取（BUTLER_TOKEN / MA_METRICS_URL），
#   不写进本脚本或 crontab 命令，避免明文泄露。
# - autoforge 包未装在宿主，故经 `docker exec autoforge-api` 在容器内调用 forge CLI；
#   examples/ir 已挂入容器 /app/examples/ir，IR 文件即 /app/examples/ir/*.json。
# - 回灌走 Ingester：幂等（dedupe_key）、指数退避、离线缓冲，MA 不可用不会让本任务失败。
# - 当前 NAS 跑只读层（AUTOFORGE_LIVE_ENABLED=0），无实时运行指标，故回灌示例自动化库
#   （examples/ir）以建立令牌使用与通道；将来有实时运行指标时可把 IR 源切到 store 图提取。
#
# crontab 示例（每日 03:17，错开 MA 备份 03:00）：
#   17 3 * * * /vol1/1000/docker/autoforge/scripts/metrics_push_cron.sh

set -uo pipefail
set -a
. /vol1/1000/docker/autoforge/.env
set +a

EX_IR_HOST=/vol1/1000/docker/autoforge/examples/ir
EX_IR_CT=/app/examples/ir
BUF_CT=/data/.af_metrics_buffer
LOG_DIR=/vol1/1000/docker/autoforge/.af_metrics_buffer
LOG="$LOG_DIR/cron.log"
MA_URL="${MA_METRICS_URL:-http://192.168.2.200:8086}"
TOKEN="${BUTLER_TOKEN:-}"
CT="autoforge-api"

mkdir -p "$LOG_DIR"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] metrics_push_cron start (ma_url=$MA_URL)" >> "$LOG"

if [ -z "$TOKEN" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] BUTLER_TOKEN 未设置，跳过" >> "$LOG"
  exit 1
fi

if ! docker inspect -f '{{.State.Running}}' "$CT" >/dev/null 2>&1; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 容器 $CT 未运行，跳过" >> "$LOG"
  exit 1
fi

pushed=0
for ir in "$EX_IR_HOST"/*.json; do
  [ -e "$ir" ] || continue
  name=$(basename "$ir")
  ir_ct="$EX_IR_CT/$name"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] push $ir_ct" >> "$LOG"
  if docker exec -e "BUTLER_TOKEN=$TOKEN" -e "MA_METRICS_URL=$MA_URL" \
      "$CT" python -m autoforge.af_cli metrics push "$ir_ct" \
      --ma-url "$MA_URL" --token "$TOKEN" --buffer-dir "$BUF_CT" >> "$LOG" 2>&1; then
    pushed=$((pushed + 1))
  fi
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] metrics_push_cron done (pushed=$pushed)" >> "$LOG"
