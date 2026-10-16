#!/bin/bash
set -e

kill 85 2>/dev/null || true
sleep 1
rm -f /data/watch.lock /data/watch.lock.info

python3 << 'PYEOF'
import json

SPOT = "switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1"
DESK = "light.yeelink_cn_555003624_lamp22_s_2"

def make_auto(aid, name, to_state, action):
    return {
        "ir_version": "0.2.1",
        "id": aid,
        "name": name,
        "version": 1,
        "mode": "single",
        "enabled": True,
        "nodes": [
            {"id": "t1", "kind": "on", "name": "触发",
             "trigger": {"type": "state", "entity_id": SPOT, "to": to_state}},
            {"adapter": "ha", "action": action,
             "params": {"entity_id": DESK},
             "result_var": "r1", "id": "d2", "kind": "do", "name": "动作"},
            {"id": "p3", "kind": "pass", "name": "结束"}
        ],
        "edges": [
            {"from": "t1", "to": "d2", "kind": "then"},
            {"from": "d2", "to": "p3", "kind": "then"},
            {"from": "d2", "to": "p3", "kind": "on_error"}
        ]
    }

ir = {
    "version": 1,
    "automations": [
        make_auto("auto_sync_on", "书房射灯开则挂灯开", "on", "light.turn_on"),
        make_auto("auto_sync_off", "书房射灯关则挂灯关", "off", "light.turn_off"),
    ]
}

with open("/data/watch-ir-manual/deployed_ir.json", "w") as f:
    json.dump(ir, f, ensure_ascii=False, indent=2)
print("IR written: 2 automations")
PYEOF

setsid forge watch /data/watch-ir-manual/deployed_ir.json \
  --persist-dir /data \
  --ha-url http://192.168.2.200:8123 \
  --confirm \
  --live-allow switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1,light.yeelink_cn_555003624_lamp22_s_2 \
  > /data/watch.log 2>&1 < /dev/null &

sleep 4
echo "=== watch process ==="
for f in /proc/[0-9]*/cmdline; do
  pid=$(echo "$f" | cut -d/ -f3)
  cmd=$(cat "$f" 2>/dev/null | tr '\0' ' ')
  case "$cmd" in *"forge watch"*) echo "PID $pid: RUNNING";; esac
done
echo "=== log tail ==="
tail -10 /data/watch.log | grep -v inbox | grep -v '^  ·'
