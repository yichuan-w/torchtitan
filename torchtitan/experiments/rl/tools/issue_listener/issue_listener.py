#!/usr/bin/env python3
"""Issue listener: one Claude Code session per GitHub issue that @-mentions this gh account.

Generalized from andylizf/simulator-kernel bench/tools/issue_listener.py. Run from inside the checkout the sessions
work in; agent-listen.sh sets these and passes them into every session:
  LISTEN_REPO     owner/name of the GitHub repository whose issues are read (never derived from the checkout's remote)
  LISTEN_PREFIX   tmux/session prefix; state lives in <git dir>/agent-listen-<prefix>/
  LISTEN_ASKERS   comma list of accounts whose mentions count; empty means the repository's collaborators
  LISTEN_RULES    a sentence naming where the session's collaboration rules live (used in the hook-start text)
  LISTEN_ISSUE    set per issue session only; the hooks do nothing without it

pending [N]   print @-mentions of this gh account that no later reply from it answers (only issue N if given)
issues        print the issue numbers that have such a mention, one per line (agent-listen.sh starts a session each)
wait N        poll every 60 s until issue N has a mention not reported before; print it in full and exit
hook-start    SessionStart hook: in an issue session, record the session id and say how to work
hook-stop     Stop hook: in an issue session, keep the turn going while a mention is unanswered or `wait` is not running

A reply is a later comment in the same issue from this account that carries a `session <id>` line or does not mention
the account. Only askers (LISTEN_ASKERS, or the collaborators) count; this account's own agent comments (with a session line) never do.
An issue belongs to whichever of this account's agent sessions posted in it last (the issue body counts, so opening an
issue claims it). A mention in an issue another session holds is left to that session, here or on another machine, for
LISTEN_CLAIM_GRACE seconds (default 1800); after that the listener's session takes it, and by replying holds it.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

SESSION_LINE = re.compile(r"(^|\n)session ([0-9a-f]{8})\b")
LOOKBACK_DAYS = 14
SELF = Path(__file__).resolve()
_static = {}  # own login never changes within one process; the collaborator list is refreshed every 10 min


def env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"{name} is not set")
    return value


def gh(*args):
    return json.loads(subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout or "null")


def gh_pages(path):
    out = subprocess.run(["gh", "api", "--paginate", "--slurp", path], check=True, capture_output=True, text=True).stdout
    return [item for page in json.loads(out) for item in page]


def state_dir():
    git_dir = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], check=True, capture_output=True,
                             text=True).stdout.strip()
    d = Path(git_dir) / f"agent-listen-{env('LISTEN_PREFIX')}"
    d.mkdir(exist_ok=True)
    return d


def pending(issue=None):
    repo = env("LISTEN_REPO")
    askers = {a.strip() for a in os.environ.get("LISTEN_ASKERS", "").split(",") if a.strip()}
    if not askers:
        if time.time() - _static.get("collab_at", 0) > 600:
            _static["collab"] = {c["login"] for c in gh_pages(f"repos/{repo}/collaborators?per_page=100")}
            _static["collab_at"] = time.time()
        askers = _static["collab"]
    if "me" not in _static:
        _static["me"] = gh("api", "user")["login"]
    me = _static["me"]
    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if issue:
        comments = gh_pages(f"repos/{repo}/issues/{issue}/comments?since={since}&per_page=100")
        issues = [gh("api", f"repos/{repo}/issues/{issue}")]
    else:
        comments = gh_pages(f"repos/{repo}/issues/comments?since={since}&per_page=100")
        issues = gh_pages(f"repos/{repo}/issues?state=all&since={since}&per_page=100")
    items = [{"id": f"c{c['id']}", "issue": c["issue_url"].rsplit("/", 1)[1], "author": c["user"]["login"],
              "at": c["created_at"], "url": c["html_url"], "body": c["body"] or ""} for c in comments]
    items += [{"id": f"i{i['id']}", "issue": str(i["number"]), "author": i["user"]["login"], "at": i["created_at"],
               "url": i["html_url"], "body": i["body"] or ""} for i in issues if "pull_request" not in i]
    mention = re.compile(rf"@{re.escape(me)}\b", re.I)
    last_reply, holder = {}, {}
    for it in sorted(items, key=lambda it: it["at"]):
        line = SESSION_LINE.search(it["body"]) if it["author"] == me else None
        if it["author"] == me and (line or not mention.search(it["body"])):
            last_reply[it["issue"]] = it["at"]
        if line:
            holder[it["issue"]] = line.group(2)
    grace_end = (datetime.now(timezone.utc) - timedelta(seconds=int(os.environ.get("LISTEN_CLAIM_GRACE", "1800")))
                 ).strftime("%Y-%m-%dT%H:%M:%SZ")

    def held_elsewhere(n):  # another session of this account holds issue n; the listener's own session does not count
        own = state_dir() / f"issue-{n}.session"
        return n in holder and not (own.exists() and own.read_text().startswith(holder[n]))

    return sorted((it for it in items
                   if mention.search(it["body"]) and it["author"] in askers
                   and not (it["author"] == me and SESSION_LINE.search(it["body"]))
                   and it["at"] > last_reply.get(it["issue"], "")
                   and not (it["at"] > grace_end and held_elsewhere(it["issue"]))),
                  key=lambda it: it["at"])


def show(items, full=False):
    for it in items:
        body = it["body"].strip()
        text = body if full else (body.splitlines()[0][:200] if body else "")
        print(f"#{it['issue']} {it['author']} {it['at']} {it['url']}\n" + "\n".join("  " + l for l in text.splitlines()))


def wait(issue):
    d = state_dir()
    (d / f"wait-{issue}.pid").write_text(str(os.getpid()))
    seen_file = d / f"reported-{issue}.json"
    seen = set(json.loads(seen_file.read_text())) if seen_file.exists() else set()
    while True:
        try:
            new = [it for it in pending(issue) if it["id"] not in seen]
        except subprocess.CalledProcessError as e:  # a failed poll (network, rate limit) is retried next minute
            print(f"poll failed: {e.stderr.strip()[:200]}", file=sys.stderr)
            new = []
        if new:
            seen_file.write_text(json.dumps(sorted(seen | {it["id"] for it in new})))
            show(new, full=True)
            return
        time.sleep(60)


def wait_running(issue):
    pid_file = state_dir() / f"wait-{issue}.pid"
    if not pid_file.exists():
        return False
    try:
        os.kill(int(pid_file.read_text()), 0)
        return True
    except (ProcessLookupError, ValueError):
        return False


def hook_start():
    issue = os.environ.get("LISTEN_ISSUE")
    if not issue:
        return
    repo, rules = env("LISTEN_REPO"), env("LISTEN_RULES")
    sid = json.load(sys.stdin)["session_id"]
    (state_dir() / f"issue-{issue}.session").write_text(sid)
    print(f"You are this checkout's agent for issue #{issue} of the GitHub repository {repo}. Your session id is {sid}; "
          f"end every comment you post with the line `session {sid[:8]}`. {rules} Read the whole thread once "
          f"(`gh api repos/{repo}/issues/{issue}` for the issue body, `gh api --paginate "
          f"repos/{repo}/issues/{issue}/comments` for the comments) and download any image a comment links to with "
          f"`gh`. Post with `gh issue comment {issue} --repo {repo}`. Keep `python3 {SELF} wait {issue}` running as a "
          "background task; when it exits it prints each new mention in full, so you need not re-read the thread "
          "unless it has left your context. Handle the request, reply in the issue, then start `wait` again. "
          "Background tasks have a time limit, so `wait` can also end with nothing printed when that limit stops it; "
          "restart it then too. Restarting `wait` is always yours to do, whatever stopped it: this session exists to "
          "keep it running.")


def hook_stop():
    issue = os.environ.get("LISTEN_ISSUE")
    if not issue:
        return
    if json.load(sys.stdin).get("stop_hook_active"):  # already sent back once this turn; do not loop
        return
    reasons = []
    try:
        left = pending(issue)
    except subprocess.CalledProcessError:
        left = []
    if left:
        reasons.append("These mentions have no reply yet; handle each and reply in the issue:\n" +
                       "\n".join(f"{it['author']} {it['url']}" for it in left))
    if not wait_running(issue):
        reasons.append(f"`python3 {SELF} wait {issue}` is not running; start it as a background task.")
    if reasons:
        print(json.dumps({"decision": "block", "reason": "\n\n".join(reasons)}))


if __name__ == "__main__":
    cmd, arg = (sys.argv[1:] + ["", ""])[:2]
    if cmd == "pending":
        show(pending(arg or None))
    elif cmd == "issues":
        print("\n".join(sorted({it["issue"] for it in pending()}, key=int)))
    elif cmd == "wait" and arg:
        wait(arg)
    elif cmd == "hook-start":
        hook_start()
    elif cmd == "hook-stop":
        hook_stop()
    else:
        sys.exit(__doc__)
