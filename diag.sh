#!/bin/bash
echo "=== Processes ==="
for f in /proc/[0-9]*/cmdline; do
  pid=$(echo "$f" | cut -d/ -f3)
  cmd=$(cat "$f" 2>/dev/null | tr '\0' ' ')
  echo "PID $pid: $cmd"
done
echo ""
echo "=== Watch log (last 30, non-inbox) ==="
tail -50 /data/watch.log | grep -v inbox | tail -30
echo ""
echo "=== Lock files ==="
cat /data/watch.lock.info 2>&1
echo ""
ls -la /data/watch.lock 2>&1
