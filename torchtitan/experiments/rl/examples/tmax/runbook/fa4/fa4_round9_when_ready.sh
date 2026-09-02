#!/bin/bash
# Wait out the login node's rate limiter, then start round 9.
#
# One connection attempt every 10 minutes, never a tight loop: della is
# pubkey+password 2FA and repeated probing risks locking the account, which
# would cost far more than this round is worth. Up to 6 hours, then give up
# and leave a note rather than keep knocking.
set -u
LOG=/Users/andyl/Projects/terminal-rl/logs/fa4_round9_wait.log
mkdir -p "$(dirname "$LOG")"
for i in $(seq 1 36); do
  if [ "$(ssh -o ConnectTimeout=25 -o BatchMode=yes della-tridao 'echo OK' 2>/dev/null)" = "OK" ]; then
    echo "$(date -u +%H:%M:%SZ) attempt $i: reachable, launching round 9" >>"$LOG"
    scp -o ConnectTimeout=30 -q "$(dirname "$0")/fa4_codex_brief_v9.md" \
      della-tridao:/scratch/gpfs/TRIDAO/al9080/fa4-fix/BRIEF9.md >>"$LOG" 2>&1
    ssh -o ConnectTimeout=30 della-tridao 'D=/scratch/gpfs/TRIDAO/al9080/fa4-fix;
      mv -f $D/codex8.log $D/codex-run8.log 2>/dev/null
      export PATH=~/.local/bin:$PATH
      cd $D && setsid nohup codex exec --dangerously-bypass-approvals-and-sandbox \
        --cd $D "$(cat BRIEF9.md)" > codex9.log 2>&1 < /dev/null &
      echo launched' >>"$LOG" 2>&1
    echo "$(date -u +%H:%M:%SZ) ROUND 9 LAUNCHED" >>"$LOG"
    exit 0
  fi
  echo "$(date -u +%H:%M:%SZ) attempt $i: still rate-limited, backing off 10min" >>"$LOG"
  sleep 600
done
echo "$(date -u +%H:%M:%SZ) GAVE UP after 6h — della never became reachable" >>"$LOG"
