#!/bin/bash
set -e

# 1. kill 旧 watch
kill 85 2>/dev/null || true
sleep 1
rm -f /data/watch.lock /data/watch.lock.info

# 2. 写合并 IR
python3 -c "
import json
ir = {
  'ir_version': '0.2.1',
  'automations': [
    {
      'id': 'auto_sync_on',
      'name': '书房射灯开则挂灯开',
      'mode': 'single',
      'nodes': [
        {
          'trigger': {'type': 'state', 'entity': 'switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1', 'to': 'on'},
          'do': {'action': 'turn_on', 'target': 'light.yeelink_cn_555003624_lamp22_s_2'},
          'on_fail': 'end'
        }
      ]
    },
    {
      'id': 'auto_sync_off',
      'name': '书房射灯关则挂灯关',
      'mode': 'single',
      'nodes': [
        {
          'trigger': {'type': 'state', 'entity': 'switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1', 'to': 'off'},
          'do': {'action': 'turn_off', 'target': 'light.yeelink_cn_555003624_lamp22_s_2'},
          'on_fail': 'end'
        }
      ]
    }
  ]
}
with open('/data/watch-ir-manual/deployed_ir.json', 'w') as f:
    json.dump(ir, f, ensure_ascii=False, indent=2)
print('IR written: 2 automations')
"

# 3. 起 watch（用 setsid 让它脱离 docker exec）
cd /data
setsid forge watch /data/watch-ir-manual/deployed_ir.json \
  --persist-dir /data \
  --ha-url http://192.168.2.200:8123 \
  --confirm \
  --live-allow switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1,light.yeelink_cn_555003624_lamp22_s_2 \
  > /data/watch.log 2>&1 < /dev/null &

sleep 3
echo "=== watch processes ==="
for f in /proc/[0-9]*/cmdline; do
  pid=$(echo "$f" | cut -d/ -f3)
  cmd=$(cat "$f" 2>/dev/null | tr '\0' ' ')
  case "$cmd" in *watch*) echo "PID $pid: $cmd";; esac
done
echo "=== log tail ==="
tail -5 /data/watch.log | grep -v inbox
