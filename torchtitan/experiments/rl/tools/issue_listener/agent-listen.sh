#!/usr/bin/env bash
# Launches this side's issue hub (.claude/CLAUDE.md, "Keeping a listener up") once. The hub is one Claude Code session
# that handles every issue through a subagent per issue. It listens by running
# `python3 torchtitan/experiments/rl/tools/issue_listener/issue_listener.py watch` itself as a background task, whose
# exit wakes it, and a Stop hook in .claude/settings.json keeps it from ending a turn without one. Nothing else keeps running: if the hub itself exits or stops at a usage limit,
# nothing restarts it; run this script again. Nothing is installed outside this repository: state lives under
# agent-listen-hub in the common git dir.
#
# Name the hub once: `python3 torchtitan/experiments/rl/tools/issue_listener/issue_listener.py hub-set <session id>
# [<directory it runs in>]`.
#
#   (no args)  if no process has the hub's session open, launch it once: interactive
#              `claude --dangerously-skip-permissions --settings <state dir>/hub-settings.json --resume <sid>` in tmux
#              session `tt-hub-session`, its first prompt telling it to start `watch`. Otherwise print where the
#              hub runs. The skip-permissions dialog is answered in hub-settings.json, the folder-trust question in the
#              hub directory's own entry of ~/.claude.json; an install that never finished onboarding needs one
#              `claude` run by hand.
#   --once     does nothing: it was one pass of the keeper loop (tmux session `tt-hub`), which is removed. Stop a loop
#              still calling it with: tmux kill-session -t =tt-hub
set -euo pipefail
self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
repo=$(git -C "$(dirname "$self")" rev-parse --show-toplevel)
cd "$repo"

case "${1:-}" in
  "") exec python3 torchtitan/experiments/rl/tools/issue_listener/issue_listener.py hub-launch ;;
  --once)
    echo "agent-listen.sh: the keeper was removed; the hub listens through its own background watch. This pass does" \
      "nothing. Stop this loop: tmux kill-session -t =tt-hub"
    exit 0 ;;
  *) sed -n '2,/^set -euo/p' "$self" | sed '$d'; exit 2 ;;
esac
