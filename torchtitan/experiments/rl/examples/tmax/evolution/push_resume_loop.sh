#!/bin/bash
# Resume-until-complete: the China->US link drops every few MB, and rsync's
# --append-verify makes each attempt continue where the last one died rather
# than restart. Bounded so a permanently dead link doesn't loop forever.
TARGET=70254080
for i in $(seq 1 40); do
  sz=$(ssh -o ConnectTimeout=30 flow-matic-andy 'stat -c %s /work/tianxia/tw-recover/tw_retry_small.tar 2>/dev/null || echo 0')
  echo "$(date -u +%H:%M:%SZ) attempt $i: remote has $((sz/1048576)) MB"
  [ "$sz" -ge "$TARGET" ] && { echo "COMPLETE"; break; }
  rsync -e "ssh -o ConnectTimeout=30 -o ServerAliveInterval=15" --partial --append-verify \
    --timeout=90 /tmp/tw_retry_small.tar results/retry_small.txt \
    flow-matic-andy:/work/tianxia/tw-recover/ 2>&1 | tail -1
done
