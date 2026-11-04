#!/usr/bin/env python3
"""Issue listener for the section "Requests between collaborators' agents in GitHub issues" of .claude/CLAUDE.md: one
Claude Code session per side, the hub, handles every issue of yichuan-w/torchtitan, each through a subagent of its own.
Ported from andylizf/simulator-kernel bench/tools/issue_listener.py.

watch          poll all open issues every 60 s for comment and issue-body versions from a collaborator account other
               than this one that are not acked, mention or not; print them in full and exit
watch --stream the same poll, never exiting: each item is printed once per run as one line naming a file that holds
               its full text, for the hub's watcher subagent to read through the Monitor tool
watch --init   mark everything that exists now as handled, so the first watch does not print the backlog
ack N ID       mark item ID of issue N (a comment id, or `body`) handled at the version watch printed; also
               `watch --ack N ID`
route list | get N | set N [--name S] [--id S] [--last ID] [--status S]
               the hub's issue -> subagent table
hub-set SID [CWD]
               record the hub's Claude session id, and the directory it is launched in, for agent-listen.sh
hub-launch     launch the hub once, in tmux session tt-hub-session, unless a process already has its session open
               (then print where); agent-listen.sh with no arguments runs this
watcher-prompt print the prompt the hub gives its watcher subagent
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Every process here (the watcher's `watch`, a person's manual run, the other worktrees) asks GitHub for the same
# URLs through gh's on-disk response cache, so the machine makes about one request per URL per POLL_CACHE however many
# run: polling per process once used up the 5000/h core limit.
POLL_CACHE = "60s"
# The repository is named, never taken from the checkout: gh resolves {owner}/{repo} from a remote, and a checkout
# can carry several (a fork's upstream, a host's own checkout).
REPO = "yichuan-w/torchtitan"
# Relative to the repository root, the hub's working directory.
LISTENER = "torchtitan/experiments/rl/tools/issue_listener/issue_listener.py"


def gh_json(args, cache=None):
    def run(*flags):
        return subprocess.run(["gh", "api", *flags, *args], check=True, capture_output=True, text=True).stdout or "null"
    if not cache:
        return json.loads(run())
    try:
        return json.loads(run("--cache", cache))
    except ValueError:  # JSONDecodeError and UnicodeDecodeError
        # Every listener process shares gh's cache, and a read can return invalid output (seen as extra data after a
        # single response, as two stray bytes in a joined page list, and as a UTF-8 sequence cut mid-character); the
        # same request without the cache parses.
        return json.loads(run())


def gh_pages(path, cache=POLL_CACHE):
    return [item for page in gh_json(["--paginate", "--slurp", path], cache) for item in page]


def state_dir():  # in the common git dir, shared by the hub's checkout and every worktree
    git_dir = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], check=True,
                             capture_output=True, text=True).stdout.strip()
    d = Path(git_dir) / "agent-listen-hub"
    d.mkdir(exist_ok=True)
    return d


def context():  # (repository, this gh account, collaborator accounts)
    repo = REPO
    me = gh_json(["user"], "3600s")["login"]
    return repo, me, {c["login"] for c in gh_pages(f"repos/{repo}/collaborators?per_page=100", "600s")}


# ---- One watch over the whole repository, acks, and the hub's routing table ----

def read_json(path):
    return json.loads(path.read_text()) if path.exists() else {}


def update_json(path, change):
    """change(data) edits the file's dict in place, under a lock: watch, ack and route run in different processes."""
    with open(path.with_name(path.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_json(path)
        result = change(data)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        os.replace(tmp, path)  # a crash mid-write leaves the old file, never half of one
        return result


def stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def watch_since(printed, handled):
    """The repository-wide comments call returns comments updated since this time (GitHub filters on updated_at, so
    edits of old comments come back too). It covers the last poll and every printed version not yet acked, so neither
    downtime nor an unacked item falls out of the window; a whole day keeps the URL, and gh's cache entry, steady."""
    now = datetime.now(timezone.utc)
    last = printed.get("last_poll") or stamp(now - timedelta(days=1))
    times = [stamp(datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - timedelta(hours=1))]
    times += [p["version"] for k, p in printed.get("items", {}).items()
              if not k.endswith(":body") and handled.get(k) != p["version"]]
    return min(times)[:10] + "T00:00:00Z"


def watch_items(since):
    """Every open issue's body and every comment in an open issue updated since `since`, from a collaborator account
    other than this one. Two list calls per poll (more only past 100 items a page), through the shared cache."""
    repo, me, collaborators = context()
    issues = [i for i in gh_pages(f"repos/{repo}/issues?state=open&per_page=100") if "pull_request" not in i]
    numbers = {str(i["number"]) for i in issues}
    asker = lambda login: login in collaborators and login != me  # noqa: E731
    items = [{"issue": str(i["number"]), "id": "body", "author": i["user"]["login"], "created": i["created_at"],
              "updated": i["updated_at"], "url": i["html_url"], "body": i["body"] or "",
              # an issue's updated_at moves with every comment and label, so a body's version is its text
              "version": "sha1:" + hashlib.sha1((i["body"] or "").encode()).hexdigest()[:12]}
             for i in issues if asker(i["user"]["login"])]
    for c in gh_pages(f"repos/{repo}/issues/comments?since={since}&per_page=100"):
        n = c["issue_url"].rsplit("/", 1)[1]
        if n in numbers and asker(c["user"]["login"]):
            items.append({"issue": n, "id": str(c["id"]), "author": c["user"]["login"], "created": c["created_at"],
                          "updated": c["updated_at"], "url": c["html_url"], "body": c["body"] or "",
                          "version": c["updated_at"]})
    return sorted(items, key=lambda it: (it["updated"], it["issue"]))


def key_of(it):
    return f"{it['issue']}:{it['id']}"


def poll(d):
    """One poll: the items whose current version is not acked, each tagged against what was printed before, or None
    when the poll failed. This poll's versions are recorded as printed."""
    printed_file, handled_file = d / "watch-printed.json", d / "watch-handled.json"
    started = stamp(datetime.now(timezone.utc))
    handled, printed = read_json(handled_file), read_json(printed_file)
    try:
        items = watch_items(watch_since(printed, handled))
    except subprocess.CalledProcessError as e:  # a failed poll (network, rate limit) is retried next time
        print(f"poll failed: {(e.stderr or '').strip()[:200]}", file=sys.stderr)
        return None
    new = [it for it in items if handled.get(key_of(it)) != it["version"]]
    for it in new:
        old = printed.get("items", {}).get(key_of(it))
        it["tags"] = []
        if (key_of(it) in handled or (old and old["version"] != it["version"])
                or (it["id"] != "body" and it["updated"] != it["created"])):
            it["tags"].append("edited")
        if old and old["version"] == it["version"]:
            it["tags"].append(f"again, first printed {old['at']}")

    def change(p):
        p["last_poll"] = started
        p.setdefault("items", {})
        for it in new:
            old = p["items"].get(key_of(it))
            if not old or old["version"] != it["version"]:
                p["items"][key_of(it)] = {"version": it["version"], "at": started}
    update_json(printed_file, change)
    return new


def item_text(it):
    return (f"=== #{it['issue']} {it['id']} {it['author']} created {it['created']} updated {it['updated']}"
            + (f" [{'; '.join(it['tags'])}]" if it["tags"] else "") + f"\n{it['url']}\n{it['body'].strip()}\n")


def watch(interval=60, once=False):
    """Print every item whose current version is not acked, then exit 0; with nothing to print, poll again every
    `interval` s. An item printed by an earlier run and never acked prints again at once: a hub that died before
    acking it gets it back. So the hub acks an item as soon as it has handed it to a subagent."""
    d = state_dir()
    while True:
        new = poll(d)
        if new:
            for it in new:
                print(item_text(it))
            print(f"Ack each once a subagent has it: python3 {LISTENER} ack <issue> <id>")
            return 0
        if once:
            return 1
        time.sleep(interval)


def watch_stream(interval=60):
    """The watcher's Monitor command: poll every `interval` s, never exit, and print each item as one line (Monitor makes
    each stdout line a notification, and cuts a long one short) the first time this run sees its current version: its
    header and the path of a file in the state directory holding its full text. An item still unacked therefore prints
    once per run, so every re-arm of the monitor hands the hub again what it has not acked."""
    d = state_dir()
    (d / "items").mkdir(exist_ok=True)
    sent = {}
    while True:
        for it in poll(d) or []:
            if sent.get(key_of(it)) != it["version"]:
                sent[key_of(it)] = it["version"]
                text = item_text(it)
                path = d / "items" / f"{it['issue']}-{it['id']}.txt"
                path.write_text(text)
                print(f"{text.splitlines()[0]} {it['url']} full text: {path}", flush=True)
        time.sleep(interval)


def watch_init():
    d = state_dir()
    started = stamp(datetime.now(timezone.utc))
    items = watch_items(watch_since({"last_poll": started}, {}))
    update_json(d / "watch-handled.json", lambda h: h.update({key_of(it): it["version"] for it in items}))
    update_json(d / "watch-printed.json", lambda p: p.update({"last_poll": started, "items": {}}))
    print(f"marked {len(items)} existing items handled; watch reports what changes after {started}")


def ack(issue, item):
    """Mark the version of an item that watch printed as handled; a later edit makes a new version, printed again."""
    item = item.lstrip("c") if item != "body" else item
    d = state_dir()
    k = f"{issue}:{item}"
    printed = read_json(d / "watch-printed.json").get("items", {}).get(k)
    if not printed:
        sys.exit(f"watch has not printed {k}; nothing to ack")
    update_json(d / "watch-handled.json", lambda h: h.__setitem__(k, printed["version"]))
    if item.isdigit() and str(issue) in read_json(d / "routes.json"):
        update_json(d / "routes.json", lambda r: r[str(issue)].update(
            last=max(int(item), int(r[str(issue)].get("last") or 0)), updated=stamp(datetime.now(timezone.utc))))
    print(f"acked {k} at {printed['version']}")


def route(argv):
    """The hub's table of which subagent has which issue: issue -> name, id, last handled comment id, status, updated."""
    p = argparse.ArgumentParser(prog="issue_listener.py route")
    sub = p.add_subparsers(dest="op", required=True)
    sub.add_parser("list")
    sub.add_parser("get").add_argument("issue")
    s = sub.add_parser("set")
    s.add_argument("issue")
    for field in ("name", "id", "last", "status"):
        s.add_argument(f"--{field}")
    a = p.parse_args(argv)
    path = state_dir() / "routes.json"
    if a.op == "set":
        fields = {f: getattr(a, f) for f in ("name", "id", "last", "status") if getattr(a, f) is not None}
        if "last" in fields:
            fields["last"] = int(fields["last"].lstrip("c"))
        fields["updated"] = stamp(datetime.now(timezone.utc))
        update_json(path, lambda r: r.setdefault(a.issue, {}).update(fields))
    table = read_json(path)
    if a.op == "get":
        if a.issue not in table:
            sys.exit(1)
        print(json.dumps(table[a.issue], indent=1))
    else:
        for n in sorted(table, key=int) if a.op == "list" else [a.issue]:
            r = table[n]
            print(f"#{n}\t{r.get('name', '-')}\t{r.get('id', '-')}\tlast={r.get('last', '-')}\t{r.get('updated', '-')}"
                  f"\t{r.get('status', '')}")


# ---- Launching the hub (agent-listen.sh with no arguments): once, never while a process has its session open ----
# Nothing here keeps running. The hub keeps itself listening through its watcher subagent's monitor; if the hub itself exits or
# stops at a usage limit, nothing restarts it until agent-listen.sh is run again.
CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
HUB_PANE = "tt-hub-session"
WATCHER_PROMPT = (
    "You are the hub's watcher (.claude/CLAUDE.md, \"Keeping a listener up\"). Your one job is to keep "
    f"`python3 {LISTENER} watch --stream` running under the Monitor tool and pass on what it prints. Load Monitor and "
    "SendMessage with ToolSearch (`select:Monitor,SendMessage`) if they are not loaded. Arm Monitor on that command, "
    "in the directory you start in, with timeout_ms 1800000, the longest it allows. Each line it prints is one item: "
    "send the lines of each notification, and of notifications that arrive together, to the main agent in one message "
    "(SendMessage to \"main\"), verbatim. The command never exits by itself: an item notification does not end it, "
    "so after forwarding items do not arm anything. Arm it again, the same way and without telling anyone, only when "
    "you get the monitor's expiry notice or a notice that its stream ended; never have two monitors armed. Do nothing "
    "else: no acks, no GitHub replies, no other commands, and never end a turn without the monitor armed.")
FIRST_PROMPT = (
    "You are this side's hub (.claude/CLAUDE.md, \"Keeping a listener up\"). Before anything else, start the "
    "watcher: one background subagent (Agent tool, model \"sonnet\") whose prompt is the output of "
    f"`python3 {LISTENER} watcher-prompt`, word for word. Do not run `watch` yourself. The watcher sends you each "
    "new or edited comment and issue body as a line that ends with the path of a file holding its full text; hand "
    "each to that issue's subagent "
    f"(`python3 {LISTENER} route list`), ack it with `issue_listener.py ack <issue> <id>` once the subagent has it "
    "(an unacked item comes back each time the watcher re-arms, every 30 minutes), and start a new watcher whenever "
    "you are told the watcher has finished.")


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, TypeError, ValueError):
        return False


def session_records(sid):
    """Claude Code's records of the live processes that have session `sid` open, newest first."""
    out = []
    for f in (CLAUDE_HOME / "sessions").glob("*.json"):
        try:
            r = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if r.get("sessionId") == sid and alive(r.get("pid")):
            out.append(r)
    return sorted(out, key=lambda r: r.get("updatedAt", 0), reverse=True)


def holders(sid):
    """Every live process that has the hub's session open (a session record) or was started to open it (`--resume
    <sid>` or `--session-id <sid>` in its command line, as a wrapper that starts claude has). tmux's own processes are
    left out: a tmux server keeps the command line of the session that started it."""
    rows = subprocess.run(["ps", "-axo", "pid=,command="], check=True, capture_output=True, text=True).stdout
    pids = {int(r["pid"]) for r in session_records(sid)}
    for line in rows.splitlines():
        parts = line.split(None, 1)
        if (len(parts) == 2 and re.search(rf"--(resume|session-id)[ =]{re.escape(sid)}\b", parts[1])
                and int(parts[0]) != os.getpid() and Path(parts[1].split()[0]).name != "tmux"):
            pids.add(int(parts[0]))
    return pids


def tmux(*args, **kw):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, **kw)


# Read from --settings ("flagSettings") as from user settings. baseRef head: a subagent's worktree starts from the
# hub checkout's commit; the default is the GitHub default branch, which here is not the canonical line.
HUB_SETTINGS = {"skipDangerousModePermissionPrompt": True, "worktree": {"baseRef": "head"}}


def preset_gates(d, cwd, say):
    """Answer the dialogs a fresh interactive claude would stop at, so a launch in a detached pane reaches its prompt.
    The --dangerously-skip-permissions confirmation is answered in a settings file of the hub's own, passed with
    --settings; the folder-trust question in the hub directory's own entry of ~/.claude.json, written only when it is
    missing, since every running claude rewrites that file. Nothing user-wide is written: an install that has never
    finished onboarding is reported, for one claude run by hand. Returns the settings file."""
    settings = d / "hub-settings.json"
    settings.write_text(json.dumps(HUB_SETTINGS, indent=2) + "\n")
    path = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~")).expanduser() / ".claude.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    if not data.get("hasCompletedOnboarding"):
        say("this Claude Code install has not finished onboarding; run `claude` once by hand, then launch the hub")
    entry = data.setdefault("projects", {}).setdefault(cwd, {})
    if not (entry.get("hasTrustDialogAccepted") and entry.get("hasCompletedProjectOnboarding")):
        entry.update(hasTrustDialogAccepted=True, hasCompletedProjectOnboarding=True)
        tmp = path.with_name(path.name + ".agent-listen")
        tmp.write_text(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    return settings


def hub_launch():
    """Start the hub with FIRST_PROMPT in tmux session HUB_PANE, unless a process already has its session open (two
    processes on one session answer everything twice); then say where it runs."""
    d = state_dir()
    hub = read_json(d / "hub.json")
    sid = hub.get("session")
    if not sid:
        print(f"no hub recorded: run `python3 {LISTENER} hub-set <session id> [<cwd>]`")
        return 1
    held = holders(sid)
    if held:
        records = session_records(sid)
        where = f", cwd {records[0]['cwd']}" if records and records[0].get("cwd") else ""
        print(f"hub {sid[:8]} is already open in pid {', '.join(str(p) for p in sorted(held))}{where}; not launching"
              + (f" (tmux session {HUB_PANE} exists: tmux attach -t ={HUB_PANE})"
                 if tmux("has-session", "-t", f"={HUB_PANE}").returncode == 0 else ""))
        return 0
    cwd = hub.get("cwd") or os.getcwd()
    settings = preset_gates(d, cwd, print)
    command = ["claude", "--dangerously-skip-permissions", "--settings", str(settings), "--resume", sid, FIRST_PROMPT]
    if tmux("has-session", "-t", f"={HUB_PANE}").returncode == 0:
        r = tmux("respawn-pane", "-k", "-t", f"={HUB_PANE}", "-c", cwd, *command)
    else:
        r = tmux("new-session", "-d", "-s", HUB_PANE, "-c", cwd, *command)
        tmux("set-option", "-t", f"={HUB_PANE}", "remain-on-exit", "on")  # a hub that exits leaves its last screen
    print(f"hub {sid[:8]}: launched claude in tmux {HUB_PANE} (exit {r.returncode}) {r.stderr.strip()[:200]}"
          f"\nattach: tmux attach -t ={HUB_PANE}")
    return r.returncode


if __name__ == "__main__":
    cmd, arg = (sys.argv[1:] + ["", ""])[:2]
    if cmd == "watch" and arg == "--init":
        watch_init()
    elif cmd == "watch" and arg == "--ack" and len(sys.argv) == 5:
        ack(sys.argv[3], sys.argv[4])
    elif cmd == "watch" and not arg:
        sys.exit(watch())
    elif cmd == "watch" and arg == "--stream":
        watch_stream()
    elif cmd == "ack" and len(sys.argv) == 4:
        ack(sys.argv[2], sys.argv[3])
    elif cmd == "route":
        route(sys.argv[2:])
    elif cmd == "hub-set" and arg:
        cwd = sys.argv[3] if len(sys.argv) > 3 else os.getcwd()
        update_json(state_dir() / "hub.json", lambda h: h.update(session=arg, cwd=cwd))
        print(f"hub session {arg}, launched in {cwd}; state in {state_dir()}")
    elif cmd == "hub-launch":
        sys.exit(hub_launch())
    elif cmd == "watcher-prompt":
        print(WATCHER_PROMPT)
    else:
        sys.exit(__doc__)
