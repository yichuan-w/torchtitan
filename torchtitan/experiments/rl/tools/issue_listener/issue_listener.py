#!/usr/bin/env python3
"""Issue listener for the section "Requests between collaborators' agents in GitHub issues" of .claude/CLAUDE.md: one
Claude Code session per side, the hub, handles every issue of yichuan-w/torchtitan, each through a subagent of its own.
Ported from andylizf/simulator-kernel bench/tools/issue_listener.py.

watch          poll all open issues every 60 s for comment and issue-body versions from a collaborator account other
               than this one that are not acked, mention or not; print them in full and exit (a background task of the hub)
watch --init   mark everything that exists now as handled, so the first watch does not print the backlog
ack N ID       mark item ID of issue N (a comment id, or `body`) handled at the version watch printed; also
               `watch --ack N ID`
route list | get N | set N [--name S] [--id S] [--last ID] [--status S]
               the hub's issue -> subagent table
hub-set SID [CWD]
               record the hub's Claude session id, and the directory it is launched in, for agent-listen.sh
hub-check [--dry-run]
               one pass of agent-listen.sh's keeper: launch the hub when no process has its session open, restart it
               when it is frozen or stopped at a usage limit, and while it is idle with no `watch` running, poll once
               and deliver the new items with a request to restart `watch`
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Every process here (the hub's `watch`, the keeper's poll, a person's manual run) asks GitHub for the same
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


def state_dir():  # in the common git dir, shared by the checkout the keeper runs from and every worktree
    git_dir = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], check=True,
                             capture_output=True, text=True).stdout.strip()
    d = Path(git_dir) / "agent-listen-hub"
    d.mkdir(exist_ok=True)
    return d


def context():  # (repository, this gh account, collaborator accounts)
    repo = REPO
    me = gh_json(["user"], "3600s")["login"]
    return repo, me, {c["login"] for c in gh_pages(f"repos/{repo}/collaborators?per_page=100", "600s")}


# ---- Hub form: one watcher over the whole repository, acks, the routing table, and agent-listen.sh's hub check ----

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


def poll(d, record=True):
    """One poll: the items whose current version is not acked, each tagged against what was printed before, or None
    when the poll failed. With record, this poll's versions are recorded as printed."""
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
    if record:
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
    (d / "watch.pid").write_text(str(os.getpid()))
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


# ---- The keeper (agent-listen.sh): keeps the hub running, and delivers in its place while its `watch` is down ----
# It reads only what Claude Code itself keeps: the per-process session records in ~/.claude/sessions and the hub's
# transcript. Starting the hub and handing it a message are two commands, HUB_LAUNCH and HUB_DELIVER ({sid} is replaced
# by the hub's session id; each gets its text on stdin). Unset, the hub is an interactive `claude` in tmux session
# HUB_PANE and a message is pasted into it.
CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
HUB_PANE = "tt-hub-session"
HUB_FREEZE = int(os.environ.get("HUB_FREEZE_SECONDS", "600"))
HUB_ACT_GAP = 600  # at most one restart, and one delivery, per 10 minutes, so a hub that keeps failing is not hammered
REDELIVER_AFTER = 600  # an item delivered and still unacked this long after is delivered once more
FIRST_PROMPT = ("You are this side's hub (.claude/CLAUDE.md, \"Keeping a listener up\"). Before anything else, start "
                f"`python3 {LISTENER} watch` as a background task. Hand each item it prints to that issue's subagent "
                f"(`python3 {LISTENER} route list`), ack it with `issue_listener.py ack <issue> <id>`, and start "
                "`watch` again whenever it exits.")


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


def process_table():
    """{pid: (parent pid, command line)} of every process."""
    rows = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], check=True, capture_output=True, text=True).stdout
    table = {}
    for line in rows.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            table[int(parts[0])] = (int(parts[1]), parts[2])
    return table


def holders(sid, table=None):
    """Every live process that has the hub's session open (a session record) or was started to open it (`--resume
    <sid>` in its command line, as a wrapper that starts claude has). tmux's own processes are left out: a tmux server
    keeps the command line of the session that started it."""
    table = process_table() if table is None else table
    pids = {int(r["pid"]) for r in session_records(sid)}
    for pid, (_, command) in table.items():
        if (re.search(rf"--resume[ =]{re.escape(sid)}\b", command) and pid != os.getpid()
                and Path(command.split()[0]).name != "tmux"):
            pids.add(pid)
    return pids


def instances(sid):
    """The holders grouped into independent instances, {top pid: [its holders]}: a holder whose ancestor is also a
    holder (the claude a wrapper started) belongs to that ancestor's instance. Two instances is the duplicate case."""
    table = process_table()
    held = holders(sid, table)
    groups = {}
    for pid in held:
        top, up = pid, table.get(pid, (0, ""))[0]
        while up in table and up > 1:
            if up in held:
                top = up
            up = table[up][0]
        groups.setdefault(top, []).append(pid)
    return {top: sorted(members) for top, members in groups.items()}


def describe(groups):
    return "; ".join(f"pid {top}" + (f" (with {', '.join(str(p) for p in members if p != top)})" if len(members) > 1
                                     else "") for top, members in sorted(groups.items()))


def transcript(sid):
    found = list((CLAUDE_HOME / "projects").glob(f"*/{sid}.jsonl"))
    return found[0] if found else None


def transcript_age(sid):
    """Seconds since the hub's transcript, or a transcript of one of its subagents, was last written."""
    files = list((CLAUDE_HOME / "projects").glob(f"*/{sid}.jsonl"))
    files += list((CLAUDE_HOME / "projects").glob(f"*/{sid}/subagents/*.jsonl"))
    return time.time() - max(f.stat().st_mtime for f in files) if files else None


def quota_stop(path, since):
    """True when the transcript's last assistant entry is Claude Code's usage-limit stop ("You've hit your … limit"),
    written after `since`: a synthetic entry with isApiErrorMessage and error rate_limit or billing_error."""
    if not path:
        return False
    with open(path, "rb") as f:
        f.seek(max(0, f.seek(0, os.SEEK_END) - 256 * 1024))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "assistant":
            continue
        at = datetime.fromisoformat(e.get("timestamp", "1970-01-01T00:00:00+00:00").replace("Z", "+00:00"))
        return bool(e.get("isApiErrorMessage") and e.get("error") in ("rate_limit", "billing_error")
                    and at.timestamp() > since)
    return False


def tmux(*args, **kw):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, **kw)


def pane_dead():
    r = tmux("display", "-p", "-t", HUB_PANE, "#{pane_dead}")
    return r.returncode != 0 or r.stdout.strip() == "1"


# Read from --settings ("flagSettings") as from user settings. baseRef head: a subagent's worktree starts from the
# hub checkout's commit; the default is the GitHub default branch, which here is not the canonical line.
HUB_SETTINGS = {"skipDangerousModePermissionPrompt": True, "worktree": {"baseRef": "head"}}


def preset_gates(d, cwd, say):
    """Answer the dialogs a fresh interactive claude would stop at, so a relaunch in a detached pane reaches its prompt.
    The --dangerously-skip-permissions confirmation is answered in a settings file of the hub's own, passed with
    --settings; the folder-trust question in the hub directory's own entry of ~/.claude.json, written only when it is
    missing, since every running claude rewrites that file. Nothing user-wide is written: an install that has never
    finished onboarding is reported, for one claude run by hand. Returns the settings file."""
    settings = d / "hub-settings.json"
    settings.write_text(json.dumps(HUB_SETTINGS, indent=2) + "\n")
    path = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~")).expanduser() / ".claude.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    if not data.get("hasCompletedOnboarding"):
        say("this Claude Code install has not finished onboarding; run `claude` once by hand, then the keeper's "
            "launches reach the prompt")
    entry = data.setdefault("projects", {}).setdefault(cwd, {})
    if not (entry.get("hasTrustDialogAccepted") and entry.get("hasCompletedProjectOnboarding")):
        entry.update(hasTrustDialogAccepted=True, hasCompletedProjectOnboarding=True)
        tmp = path.with_name(path.name + ".agent-listen")
        tmp.write_text(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    return settings


def launch(d, sid, cwd, say, dry_run):
    """Start the hub with FIRST_PROMPT, refusing while any process has its session open: two processes on one session
    answer everything twice. The CLI process is killed before a relaunch, so a holder here is another runner."""
    groups = instances(sid)
    if groups:
        say(f"hub {sid[:8]}: not launching, session open in {describe(groups)}; stop that first, then the keeper "
            f"launches it")
        return 1
    custom = os.environ.get("HUB_LAUNCH")
    if dry_run:
        say(f"hub {sid[:8]}: would launch " + (f"`{custom.format(sid=sid)}`" if custom else f"claude in tmux {HUB_PANE}"))
        return 0
    if custom:
        r = subprocess.run(custom.format(sid=sid), shell=True, input=FIRST_PROMPT, capture_output=True, text=True,
                           timeout=300)
        say(f"hub {sid[:8]}: HUB_LAUNCH exit {r.returncode}: {(r.stdout + r.stderr).strip()[-300:]}")
    else:
        settings = preset_gates(d, cwd, say)
        command = ["claude", "--dangerously-skip-permissions", "--settings", str(settings), "--resume", sid,
                   FIRST_PROMPT]
        if tmux("has-session", "-t", HUB_PANE).returncode == 0:
            r = tmux("respawn-pane", "-k", "-t", HUB_PANE, "-c", cwd, *command)
        else:
            r = tmux("new-session", "-d", "-s", HUB_PANE, "-c", cwd, *command)
            tmux("set-option", "-t", HUB_PANE, "remain-on-exit", "on")  # a dead pane stays, so pane_dead can see it
        say(f"hub {sid[:8]}: launched claude in tmux {HUB_PANE} (exit {r.returncode}) {r.stderr.strip()[:200]}")
    update_json(d / "hub.json", lambda h: h.__setitem__("launched", time.time()))
    return 0


def deliver(sid, text):
    """Hand `text` to the hub; returns what happened. HUB_DELIVER if set, otherwise a bracketed paste into HUB_PANE."""
    custom = os.environ.get("HUB_DELIVER")
    if custom:
        r = subprocess.run(custom.format(sid=sid), shell=True, input=text, capture_output=True, text=True, timeout=120)
        return f"HUB_DELIVER exit {r.returncode} {(r.stdout + r.stderr).strip()[-200:]}"
    if pane_dead():  # tmux takes keys into a dead pane without complaint
        return "dead"
    tmux("load-buffer", "-b", "tt-hub", "-", input=text)
    tmux("paste-buffer", "-p", "-d", "-b", "tt-hub", "-t", HUB_PANE)
    time.sleep(0.4)  # an Enter sent right behind the paste races it and is swallowed
    tmux("send-keys", "-t", HUB_PANE, "Enter")
    return "pasted"


def in_pane(pid):
    """True when pid runs inside HUB_PANE (the pane's process or one of its descendants)."""
    r = tmux("display", "-p", "-t", HUB_PANE, "#{pane_pid}")
    top = r.stdout.strip()
    pid = str(pid)
    while r.returncode == 0 and pid not in ("", "0", "1"):
        if pid == top:
            return True
        pid = subprocess.run(["ps", "-o", "ppid=", "-p", pid], capture_output=True, text=True).stdout.strip()
    return False


def kill(pids):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        for _ in range(30):
            if not any(alive(p) for p in pids):
                return
            time.sleep(1)


def gate(d, name, dry_run):
    """True when the action `name` last ran over HUB_ACT_GAP ago; a live run records this one."""
    path = d / "hub-act.json"
    last = read_json(path).get(name, 0)
    if time.time() - last < HUB_ACT_GAP:
        return False
    if not dry_run:
        update_json(path, lambda g: g.__setitem__(name, time.time()))
    return True


def delivery_text(items, again=False):
    head = (f"agent-listen: `watch` is not running. Start `python3 {LISTENER} watch` as a background "
            "task again" + (", then handle these items and ack each:" if items else "."))
    if again:
        head = "agent-listen: these items were delivered over 10 minutes ago and are still not acked:"
    return head + "\n\n" + "\n".join(item_text(it) for it in items)


def hub_check(dry_run=False):
    d = state_dir()
    say = lambda line: hub_log(d, line, dry_run)  # noqa: E731
    hub = read_json(d / "hub.json")
    sid = hub.get("session")
    if not sid:
        say(f"no hub recorded: run `python3 {LISTENER} hub-set <session id> [<cwd>]`")
        return 1
    records = session_records(sid)
    if records and records[0].get("cwd") and records[0]["cwd"] != hub.get("cwd") and not dry_run:
        update_json(d / "hub.json", lambda h: h.__setitem__("cwd", records[0]["cwd"]))
    cwd = (records[0].get("cwd") if records else None) or hub.get("cwd") or os.getcwd()
    groups = instances(sid)
    held = {pid for members in groups.values() for pid in members}
    if len(groups) > 1:
        say(f"hub {sid[:8]}: DUPLICATE, its session is open in {len(groups)} independent instances: {describe(groups)}. "
            "Both answer everything; stop all but one")
        return 1
    if not held:
        if gate(d, "launch", dry_run):
            return launch(d, sid, cwd, say, dry_run)
        say(f"hub {sid[:8]}: no process has its session open; relaunched under 10 min ago, waiting")
        return 0
    status = records[0].get("status") if records else None
    age = transcript_age(sid)
    why = None
    if quota_stop(transcript(sid), hub.get("launched", 0)):
        why = "stopped at a usage limit"
    elif status == "busy" and age is not None and age > HUB_FREEZE:
        why = f"frozen: busy, transcript last written {age / 60:.0f} min ago"
    if why:
        if not gate(d, "launch", dry_run):
            say(f"hub {sid[:8]} {why}; restarted under 10 min ago, waiting")
            return 0
        targets = {int(r["pid"]) for r in records} or held
        if not os.environ.get("HUB_LAUNCH") and not all(in_pane(pid) for pid in targets):
            # The default launcher would move the hub into tmux while its runner might start it again: two processes.
            say(f"hub {sid[:8]} {why}, but it runs outside tmux {HUB_PANE} (pid {sorted(targets)}), so it is left "
                "alone; stop that runner and the keeper launches it here")
            return 0
        say(f"hub {sid[:8]} {why}: " + ("would stop" if dry_run else "stopping") + f" pid {sorted(targets)} and relaunch")
        if not dry_run:
            kill(targets)
            for _ in range(30):  # a wrapper that started claude exits once claude does
                if not holders(sid):
                    break
                time.sleep(1)
        return launch(d, sid, cwd, say, dry_run) if not dry_run else 0
    delivered_file = d / "hub-delivered.json"
    handled = read_json(d / "watch-handled.json")
    delivered = read_json(delivered_file)
    unacked = {k: v for k, v in delivered.items() if handled.get(k) != v["version"]}
    if unacked != delivered and not dry_run:
        update_json(delivered_file, lambda m: [m.pop(k) for k in list(m) if k not in unacked])
    late = [v["item"] for k, v in unacked.items() if not v.get("again") and time.time() - v["at"] > REDELIVER_AFTER]
    if late:
        result = "would deliver" if dry_run else deliver(sid, delivery_text(late, again=True))
        say(f"hub {sid[:8]}: {len(late)} item(s) unacked 10 min after delivery, delivered once more: {result}")
        if not dry_run:
            update_json(delivered_file, lambda m: [m[key_of(it)].__setitem__("again", True) for it in late])
    watch_pid = read_text(d / "watch.pid")
    if status == "idle" and not (watch_pid.isdigit() and alive(int(watch_pid))):
        if not gate(d, "deliver", dry_run):
            return 0
        items = poll(d, record=not dry_run)
        if items is None:
            return 1
        items = [it for it in items if key_of(it) not in unacked]
        if dry_run:
            say(f"hub {sid[:8]} idle without watch: would deliver {len(items)} item(s) and ask it to restart watch")
            return 0
        result = deliver(sid, delivery_text(items))
        if result == "dead":
            say(f"hub {sid[:8]}: pane {HUB_PANE} is dead; relaunching instead of pasting")
            return launch(d, sid, cwd, say, dry_run)
        update_json(delivered_file, lambda m: m.update(
            {key_of(it): {"version": it["version"], "at": time.time(), "item": it} for it in items}))
        say(f"hub {sid[:8]} idle without watch: delivered {len(items)} item(s) and a restart of watch: {result}")
        return 0
    say(f"hub {sid[:8]} ok ({status})" + (f", transcript written {age:.0f} s ago" if dry_run and age is not None else ""))
    return 0


def read_text(path):
    return path.read_text().strip() if path.exists() else ""


def hub_log(d, line, dry_run):
    """Print the line; a live run also appends it to hub.log, skipping a repeat of the line before it."""
    if dry_run:
        print("DRY RUN " + line)
        return
    last = d / "hub-last.txt"
    if read_text(last) == line:
        return
    last.write_text(line)
    out = f"{datetime.now():%F %T} {line}"
    print(out)
    with open(d / "hub.log", "a") as f:
        f.write(out + "\n")


if __name__ == "__main__":
    cmd, arg = (sys.argv[1:] + ["", ""])[:2]
    if cmd == "watch" and arg == "--init":
        watch_init()
    elif cmd == "watch" and arg == "--ack" and len(sys.argv) == 5:
        ack(sys.argv[3], sys.argv[4])
    elif cmd == "watch" and not arg:
        sys.exit(watch())
    elif cmd == "ack" and len(sys.argv) == 4:
        ack(sys.argv[2], sys.argv[3])
    elif cmd == "route":
        route(sys.argv[2:])
    elif cmd == "hub-set" and arg:
        cwd = sys.argv[3] if len(sys.argv) > 3 else os.getcwd()
        update_json(state_dir() / "hub.json", lambda h: h.update(session=arg, cwd=cwd))
        print(f"hub session {arg}, launched in {cwd}; state in {state_dir()}")
    elif cmd == "hub-check":
        sys.exit(hub_check(dry_run=arg == "--dry-run"))
    else:
        sys.exit(__doc__)
