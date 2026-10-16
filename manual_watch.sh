#!/bin/bash
# 手动起合并 watch
set -e

# 清旧 lock
rm -f /data/watch.lock /data/watch.lock.info

# 构建合并 IR
python3 <<'PYEOF'
import json, os

SPOT = "switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1"
DESK = "light.yeelink_cn_555003624_lamp22_s_2"

ir = {
  "ir_version": "0.2.1",
  "automations": [
    {
      "id": "auto_sync_on",
      "name": "书房射灯开则挂灯开",
      "mode": "single",
      "nodes": [
        {
          "trigger": {"type": "state", "entity": SPOT, "to": "on"},
          "do": {"action": "turn_on", "target": DESK},
          "on_fail": "end"
        }
      ]
    },
    {
      "id": "auto_sync_off",
      "name": "书房射灯关则挂灯关",
      "mode": "single",
      "nodes": [
        {
          "trigger": {"type": "state", "entity": SPOT, "to": "off"},
          "do": {"action": "turn_off", "target": DESK},
          "on_fail": "end"
        }
      ]
    }
  ]
}

os.makedirs("/data/watch-ir-manual", exist_ok=True)
with open("/data/watch-ir-manual/deployed_ir.json", "w") as f:
    json.dump(ir, f, ensure_ascii=False, indent=2)
print("IR written")
PYEOF

# 起 watch
nohup forge watch /data/watch-ir-manual/deployed_ir.json \
  --persist-dir /data \
  --ha-url http://192.168.2.200:8123 \
  --confirm \
  --live-allow "$SPOT,$DESK" \
  > /data/watch.log 2>&1 &

echo "watch pid=$!"
sleep 3
tail -5 /data/watch.log | grep -v '^  ·'
