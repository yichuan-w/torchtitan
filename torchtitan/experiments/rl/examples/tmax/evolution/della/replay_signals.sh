#!/bin/bash
# Feed a dev workdir with signals a real run already consumed, so a change to
# the loop can be tried on the same tasks the production loop saw.
#
#   usage: replay_signals.sh <from-evolution-root> <dev-workdir> [n] [direction]
#     from-evolution-root   e.g. .../workdirs/wd-20260903b/evolution
#     n                     how many of the newest matching signals (default 3)
#     direction             harder (k/k, default) or easier (0/k)
#
# A signal already in the dev workdir's signals/ or consumed/ is skipped, so
# repeating the call moves on to older ones.
set -euo pipefail
FROM=${1:?evolution root of the run to replay from}
W=${2:?dev workdir}
N=${3:-3}
DIR=${4:-harder}
DEST=$W/evolution/signals
mkdir -p "$DEST" "$W/evolution/consumed"
copied=0
for f in $(ls -t "$FROM"/consumed/*.json 2>/dev/null); do
  b=$(basename "$f")
  [ -e "$DEST/$b" ] || [ -e "$W/evolution/consumed/$b" ] && continue
  python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("direction")==sys.argv[2] else 1)' "$f" "$DIR" || continue
  cp -p "$f" "$DEST/$b"
  echo "replayed $b"
  copied=$((copied+1))
  [ "$copied" -ge "$N" ] && break
done
echo "copied $copied signal(s) into $DEST"
