#!/bin/bash
rm -f /data/watch.lock /data/watch.lock.info
forge watch /data/watch-ir-manual/deployed_ir.json \
  --persist-dir /data \
  --ha-url http://192.168.2.200:8123 \
  --confirm \
  --live-allow switch.lumi_cn_lumi_158d000239c546_aq1_on_p_2_1,light.yeelink_cn_555003624_lamp22_s_2 \
  > /data/watch.log 2>&1
