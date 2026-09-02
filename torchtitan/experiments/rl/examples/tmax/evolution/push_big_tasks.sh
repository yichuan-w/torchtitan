#!/bin/bash
# Push the six oversized tasks one at a time, smallest first.
#
# The link to Berkeley drops every few MB, so each file gets repeated rsync
# attempts with --append-verify until its remote size matches the local one.
# One file at a time and smallest first, because a 43MB transfer that finishes
# is worth more than a 407MB one that never does — each completed file is a
# task that can be validated.
set -u
SRC=/Users/andyl/Projects/terminal-rl/tmp/big
DEST=flow-matic-andy:/work/tianxia/tw-recover/big
LOG=/Users/andyl/Projects/terminal-rl/logs/push_big.log
mkdir -p "$(dirname "$LOG")"

ssh -o ConnectTimeout=30 flow-matic-andy 'mkdir -p /work/tianxia/tw-recover/big'

for f in $(ls -S -r "$SRC"/*.tar); do
  name=$(basename "$f")
  want=$(/usr/bin/stat -f%z "$f")
  for i in $(seq 1 60); do
    have=$(ssh -o ConnectTimeout=30 flow-matic-andy \
      "stat -c %s /work/tianxia/tw-recover/big/$name 2>/dev/null || echo 0")
    printf '%s %s attempt %d: %s / %s bytes\n' \
      "$(date -u +%H:%M:%SZ)" "$name" "$i" "$have" "$want" >>"$LOG"
    [ "$have" = "$want" ] && { echo "$(date -u +%H:%M:%SZ) $name COMPLETE" >>"$LOG"; break; }
    rsync -e "ssh -o ConnectTimeout=30 -o ServerAliveInterval=15" \
      --partial --append-verify --timeout=90 "$f" "$DEST/" >>"$LOG" 2>&1
  done
done
echo "$(date -u +%H:%M:%SZ) ALL DONE" >>"$LOG"
