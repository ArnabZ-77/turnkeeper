#!/bin/bash
# restart render-chunks.mjs whenever it stalls (no new file in out/chunks for 9 min) or exits non-zero before finishing
cd "$(dirname "$0")/.."
source scripts/env-x64.sh >/dev/null
while [ ! -f out/turnkeeper_v2.mp4 ]; do
  node scripts/render-chunks.mjs 300 2 > out/render_chunks_w.log 2>&1 &
  PID=$!
  LAST=$(date +%s)
  while kill -0 $PID 2>/dev/null; do
    sleep 30
    NEW=$(find out/chunks -newermt "-540 seconds" -type f 2>/dev/null | wc -l)
    AGE=$(( $(date +%s) - LAST ))
    if [ "$NEW" = "0" ] && [ $AGE -gt 840 ]; then
      echo "stall -> kill $(date +%T)"; powershell -NoProfile -Command "Stop-Process -Id $(cat /proc/$PID/winpid 2>/dev/null || echo 0) -Force -ErrorAction SilentlyContinue"
      kill $PID 2>/dev/null; break
    fi
    [ "$NEW" != "0" ] && LAST=$(date +%s)
  done
  powershell -NoProfile -Command "Get-Process chrome-headless-shell -ErrorAction SilentlyContinue | Stop-Process -Force"
  rm -f out/chunks/*.part.mp4
  sleep 3
done
