#!/usr/bin/env bash
# Keeps this side's GitHub issue traffic answered (.claude/CLAUDE.md, "Keeping a listener up"). One Claude Code
# session per side, the hub, handles every issue through a subagent per issue and keeps
# `python3 torchtitan/experiments/rl/tools/issue_listener/issue_listener.py watch` running as a background task,
# so the watch's exit wakes it. This script is the hub's keeper, ported from andylizf/simulator-kernel
# bench/tools/agent-listen.sh. Nothing is installed outside this repository: state lives under agent-listen-hub in the
# common git dir, and the keeper is tmux session `tt-hub`. Run the keeper from a worktree that belongs to this tooling
# alone: no subagent works in it and nothing switches its branch, because the keeper runs whatever version of this
# script that checkout holds.
#
# Name the hub once: `python3 torchtitan/experiments/rl/tools/issue_listener/issue_listener.py hub-set <session id>
# [<directory it runs in>]`. Each pass then reads Claude Code's own session records (~/.claude/sessions) and the hub's
# transcript:
#   - no process has the session open: launch the hub;
#   - its last assistant entry is a usage-limit stop, or it is busy with no transcript write for HUB_FREEZE_SECONDS
#     (600): stop its claude process and launch it again;
#   - idle with no `watch` running: poll once, as `watch` would, and deliver the new items with a request to restart
#     `watch`; an item still unacked 10 minutes after delivery is delivered once more.
# At most one launch and one delivery per 10 minutes. The hub is never launched while any process has its session
# open (a launcher and the claude it started count as one instance; two independent instances are reported as a
# duplicate and left alone), since two processes on one session answer everything twice; and with the default
# launcher it stops only a
# hub running inside `tt-hub-session`. Moving a hub from another runner is therefore: stop that runner, then let the
# keeper launch it.
#
# Launching and delivering are two commands, each given its text on stdin with {sid} replaced by the session id:
#   HUB_LAUNCH   default: interactive `claude --dangerously-skip-permissions --resume {sid}` in tmux session
#                `tt-hub-session`, its first prompt asking it to start `watch`. The skip-permissions dialog is
#                answered in hub-settings.json in the state directory, passed with --settings; the folder-trust
#                question in the hub directory's own entry of ~/.claude.json. Nothing user-wide is written: an
#                install that never finished onboarding needs one `claude` run by hand
#   HUB_DELIVER  default: check that pane is alive (a dead one is relaunched), then `tmux load-buffer`,
#                `paste-buffer -p`, 0.4 s, Enter
#
#   (no args)  start the keeper, `tt-hub`, which runs --once every minute; does nothing if it is already running.
#              Stop it with: tmux kill-session -t =tt-hub (the = keeps tmux from taking tt-hub-session, the hub,
#              by prefix when the keeper is not running). After a reboot, start it again by hand.
#   --once     one keeper pass, logged to hub.log in the state directory
#   --dry-run  one pass that only prints what it would do
set -euo pipefail
self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
repo=$(git -C "$(dirname "$self")" rev-parse --show-toplevel)
cd "$repo"

case "${1:-}" in
  "")
    if tmux has-session -t =tt-hub 2>/dev/null; then
      echo "keeper already running: tmux attach -t =tt-hub"
    else
      # -e: a session takes the tmux server's environment, not this shell's, so these are passed on explicitly.
      # bash -c: tmux runs a command through the user's own shell, which may not be bash (fish, for one).
      tmux new-session -d -s tt-hub -c "$repo" \
        ${HUB_FREEZE_SECONDS:+-e "HUB_FREEZE_SECONDS=$HUB_FREEZE_SECONDS"} \
        ${HUB_LAUNCH:+-e "HUB_LAUNCH=$HUB_LAUNCH"} ${HUB_DELIVER:+-e "HUB_DELIVER=$HUB_DELIVER"} \
        bash -c "while true; do bash '$self' --once; sleep 60; done"
      echo "keeper started: tmux attach -t =tt-hub"
    fi
    exit 0 ;;
  --once) python3 torchtitan/experiments/rl/tools/issue_listener/issue_listener.py hub-check || true; exit 0 ;;
  --dry-run) exec python3 torchtitan/experiments/rl/tools/issue_listener/issue_listener.py hub-check --dry-run ;;
  *) sed -n '2,/^set -euo/p' "$self" | sed '$d'; exit 2 ;;
esac
