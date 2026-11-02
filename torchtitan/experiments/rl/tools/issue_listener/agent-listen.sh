#!/usr/bin/env bash
# One Claude Code session per GitHub issue that mentions this gh account, each in tmux session `<prefix>-<issue>`, in
# bypass mode so nothing waits on a permission prompt overnight. Generalized from andylizf/simulator-kernel
# bench/tools/agent-listen.sh. Run it from inside the checkout the sessions work in. LISTEN_REPO, LISTEN_PREFIX,
# LISTEN_ASKERS and LISTEN_RULES (see issue_listener.py) default to this repository's setup; the dispatcher and every
# session get them, and each session also gets LISTEN_ISSUE. The SessionStart and Stop hooks in .claude/settings.json
# act only in such a session. State lives under <git dir>/agent-listen-<prefix>; everything that runs is a tmux session.
#   (no args)  start the dispatcher, tmux session `<prefix>-dispatch`, which runs `--once` every minute; does nothing
#              if it is already running. Stop everything with: tmux kill-session -t <prefix>-dispatch, then each
#              <prefix>-<issue>.
#   --once     one pass: start a session for each issue with an unanswered mention (resuming that issue's earlier
#              session if there is one), and nudge an idle session whose `wait` poll is not running
set -euo pipefail
: "${LISTEN_REPO:=yichuan-w/torchtitan}" "${LISTEN_PREFIX:=tt}" "${LISTEN_ASKERS:=}"  # empty: the collaborators
if [ -z "${LISTEN_RULES:-}" ]; then
  LISTEN_RULES="the section \"Requests between collaborators' agents in GitHub issues\" in .claude/CLAUDE.md"
fi
export LISTEN_REPO LISTEN_PREFIX LISTEN_ASKERS LISTEN_RULES
here="$(cd "$(dirname "$0")" && pwd)"
self="$here/$(basename "$0")"
p=$LISTEN_PREFIX
repo=$(git rev-parse --show-toplevel)
state="$(git -C "$repo" rev-parse --absolute-git-dir)/agent-listen-$p"
mkdir -p "$state"
cd "$repo"
envs=(-e "LISTEN_REPO=$LISTEN_REPO" -e "LISTEN_PREFIX=$p" -e "LISTEN_ASKERS=$LISTEN_ASKERS" -e "LISTEN_RULES=$LISTEN_RULES")

if [ "${1:-}" != --once ]; then
  if tmux has-session -t "=$p-dispatch" 2>/dev/null; then
    echo "dispatcher already running: tmux attach -t $p-dispatch"
  else
    # bash -c: tmux runs a command through the user's own shell, which may not be bash (fish, for one)
    tmux new-session -d -s "$p-dispatch" -c "$repo" "${envs[@]}" bash -c "while true; do bash '$self' --once; sleep 60; done"
    echo "dispatcher started: tmux attach -t $p-dispatch"
  fi
  exit 0
fi

for n in $(python3 "$here/issue_listener.py" issues); do
  tmux has-session -t "=$p-$n" 2>/dev/null && continue
  if [ -s "$state/issue-$n.session" ]; then
    start="claude --dangerously-skip-permissions --resume $(cat "$state/issue-$n.session") 'You were restarted. Start the wait for issue #$n again and handle anything pending.'"
  else
    start="claude --dangerously-skip-permissions 'Take issue #$n of $LISTEN_REPO.'"
  fi
  tmux new-session -d -s "$p-$n" -c "$repo" "${envs[@]}" -e "LISTEN_ISSUE=$n" bash -c "$start"
  echo "$(date '+%F %T') started $p-$n"
done

now=$(date +%s)
for s in $(tmux list-sessions -F '#S' 2>/dev/null | grep -E "^$p-[0-9]+\$" || true); do
  n=${s#"$p"-}
  pid="$state/wait-$n.pid"
  [ -s "$pid" ] && kill -0 "$(cat "$pid")" 2>/dev/null && continue
  # Not polling is normal while the session is working on a request, or just after it started.
  tmux capture-pane -p -t "=$s:" | grep -qE '… \([0-9]+(m [0-9]+)?s' && continue  # Claude Code's busy spinner line
  [ $((now - $(tmux display -p -t "=$s:" '#{session_created}'))) -lt 180 ] && continue
  [ -f "$state/nudge-$n" ] && [ $((now - $(cat "$state/nudge-$n"))) -lt 600 ] && continue
  tmux send-keys -t "=$s:" -l "The wait for issue #$n is not running. Start it again and handle what pending prints."
  sleep 1  # Enter sent right behind the text is taken as a newline inside a paste, and the nudge is never submitted
  tmux send-keys -t "=$s:" Enter
  echo "$now" > "$state/nudge-$n"
  echo "$(date '+%F %T') nudged $s"
done
