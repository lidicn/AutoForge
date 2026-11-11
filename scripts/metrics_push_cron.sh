#!/usr/bin/env bash
# AF→MA 指标回灌定时任务（MA 联动收尾 · 2026-10-06）
#
# 设计：
# - 令牌从宿主 /vol1/1000/docker/autoforge/.env 读取（BUTLER_TOKEN / MA_METRICS_URL），
#   不写进本脚本或 crontab 命令；**也不进 argv**——经 `docker exec --env-file`（0600 临时文件，
#   跑完即删）注入，见 AF-749。
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

# AF-749（2026-11-11）：令牌**不进 argv**。
# 旧写法有两处 argv 泄露面：① `docker exec -e "BUTLER_TOKEN=$TOKEN"` 出现在**宿主** ps 里；
# ② `... metrics push --token "$TOKEN"` 出现在**容器内** ps 里（台账 §1.190 抓到的就是它）。
# 现在改 `--env-file <0600 临时文件>`：两条 ps 里都只剩文件名；文件跑完即删（trap 兜底）。
ENVFILE=$(mktemp "${TMPDIR:-/tmp}/af_metrics_env.XXXXXX")
chmod 600 "$ENVFILE"
trap 'rm -f "$ENVFILE"' EXIT INT TERM
printf 'BUTLER_TOKEN=%s\nMA_METRICS_URL=%s\n' "$TOKEN" "$MA_URL" > "$ENVFILE"

pushed=0
for ir in "$EX_IR_HOST"/*.json; do
  [ -e "$ir" ] || continue
  name=$(basename "$ir")
  ir_ct="$EX_IR_CT/$name"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] push $ir_ct" >> "$LOG"
  # ⛔ 不要在这里加回 `--token` 或 `-e VAR=值`：那张 argv 会出现在 ps 里（AF-749）。
  if docker exec --env-file "$ENVFILE" \
      "$CT" python -m autoforge.af_cli metrics push "$ir_ct" \
      --ma-url "$MA_URL" --buffer-dir "$BUF_CT" >> "$LOG" 2>&1; then
    pushed=$((pushed + 1))
  fi
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] metrics_push_cron done (pushed=$pushed)" >> "$LOG"
