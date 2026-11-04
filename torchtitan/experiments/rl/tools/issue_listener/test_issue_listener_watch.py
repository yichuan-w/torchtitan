"""Offline test of issue_listener.py: watch, ack and route state against fake gh responses, and the keeper's
launch, paste and redelivery against a fake tmux and a fake ~/.claude.

Run: python3 torchtitan/experiments/rl/tools/issue_listener/test_issue_listener_watch.py
"""
import contextlib
import io
import json
import os
import sys
import time
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import issue_listener as il  # noqa: E402

il.REPO = "o/r"  # the fake GitHub below serves this repository

ME, PEER, STRANGER = "andylizf", "yichuan-w", "someone-else"


def at(minutes_ago):  # watch's since window follows the clock, so the fake comments do too
    return il.stamp(datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))


class FakeGitHub:
    def __init__(self):
        self.issues = {}    # number -> issue
        self.comments = []  # repository-wide comment list

    def issue(self, n, author, body):
        self.issues[n] = {"number": n, "id": 1000 + n, "user": {"login": author}, "body": body,
                          "created_at": "2026-11-03T10:00:00Z", "updated_at": "2026-11-03T10:00:00Z",
                          "html_url": f"https://github.com/o/r/issues/{n}"}

    def comment(self, cid, n, author, body, at):
        self.comments.append({"id": cid, "issue_url": f"https://api.github.com/repos/o/r/issues/{n}",
                              "user": {"login": author}, "body": body, "created_at": at, "updated_at": at,
                              "html_url": f"https://github.com/o/r/issues/{n}#issuecomment-{cid}"})

    def edit(self, cid, body, at):
        c = next(c for c in self.comments if c["id"] == cid)
        c["body"], c["updated_at"] = body, at

    def gh_json(self, args, cache=None):
        if args == ["user"]:
            return {"login": ME}
        path = args[-1]
        if path.startswith("repos/o/r/collaborators"):
            return [[{"login": ME}, {"login": PEER}]]
        if path.startswith("repos/o/r/issues?state=open"):
            return [list(self.issues.values())]
        if path.startswith("repos/o/r/issues/comments?since="):
            since = path.split("since=")[1].split("&")[0]
            return [[c for c in self.comments if c["updated_at"] >= since]]  # GitHub filters on updated_at
        raise AssertionError(f"unexpected gh call {args}")


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gh = FakeGitHub()
        self.saved = il.gh_json, il.state_dir
        il.gh_json = self.gh.gh_json
        il.state_dir = lambda: Path(self.tmp.name)
        self.gh.issue(51, ME, "opened by us")

    def tearDown(self):
        il.gh_json, il.state_dir = self.saved
        self.tmp.cleanup()

    def run_watch(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = il.watch(once=True)
        return code, out.getvalue()

    def quiet(self, fn, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*args)

    def test_only_collaborators_other_than_us_and_no_mention_needed(self):
        self.gh.comment(1, 51, ME, "our own reply\n\nsession ee531e69", at(60))
        self.gh.comment(2, 51, STRANGER, "not a collaborator", at(59))
        self.gh.comment(3, 51, PEER, "no mention here, still a request", at(58))
        code, out = self.run_watch()
        self.assertEqual(code, 0)
        self.assertIn("=== #51 3 yichuan-w", out)
        self.assertIn("no mention here, still a request", out)
        self.assertNotIn("our own reply", out)
        self.assertNotIn("not a collaborator", out)
        self.assertNotIn("opened by us", out)  # our own issue body

    def test_unacked_prints_again_and_ack_stops_it(self):
        self.gh.comment(3, 51, PEER, "please run X", at(58))
        self.assertIn("=== #51 3 ", self.run_watch()[1])
        code, out = self.run_watch()  # a hub that died before acking gets it back
        self.assertEqual(code, 0)
        self.assertIn("again, first printed", out)
        self.quiet(il.ack, "51", "3")
        self.assertEqual(self.run_watch(), (1, ""))

    def test_edited_comment_is_reported_again_marked_edited(self):
        self.gh.comment(3, 51, PEER, "first text", at(58))
        self.run_watch()
        self.quiet(il.ack, "51", "c3")
        self.gh.edit(3, "second text", at(30))
        code, out = self.run_watch()
        self.assertEqual(code, 0)
        self.assertIn("[edited]", out)
        self.assertIn("second text", out)
        self.quiet(il.ack, "51", "3")
        self.assertEqual(self.run_watch()[0], 1)

    def test_ack_records_the_printed_version_not_a_later_edit(self):
        self.gh.comment(3, 51, PEER, "first text", at(58))
        self.run_watch()
        self.gh.edit(3, "edited before the ack", at(30))
        self.quiet(il.ack, "51", "3")  # acks the version that was printed
        code, out = self.run_watch()
        self.assertEqual(code, 0)
        self.assertIn("edited before the ack", out)

    def test_issue_body_from_collaborator_and_its_edit(self):
        self.gh.issue(60, PEER, "new issue body")
        self.assertIn("=== #60 body yichuan-w", self.run_watch()[1])
        self.quiet(il.ack, "60", "body")
        self.assertEqual(self.run_watch()[0], 1)
        self.gh.issues[60]["body"] = "new issue body, edited"
        self.assertIn("[edited]", self.run_watch()[1])

    def test_init_seeds_existing_items(self):
        self.gh.comment(3, 51, PEER, "old backlog", at(58))
        self.gh.issue(60, PEER, "old issue")
        self.quiet(il.watch_init)
        self.assertEqual(self.run_watch()[0], 1)
        self.gh.comment(4, 51, PEER, "after init", at(20))
        code, out = self.run_watch()
        self.assertIn("after init", out)
        self.assertNotIn("old backlog", out)

    def test_unacked_old_item_stays_in_the_since_window(self):
        printed = {"last_poll": "2026-11-09T12:00:00Z",
                   "items": {"51:3": {"version": "2026-11-03T11:02:00Z", "at": "2026-11-03T11:03:00Z"}}}
        self.assertEqual(il.watch_since(printed, {}), "2026-11-03T00:00:00Z")
        self.assertEqual(il.watch_since(printed, {"51:3": "2026-11-03T11:02:00Z"}), "2026-11-09T00:00:00Z")

    def test_route_set_get_list_and_ack_moves_last(self):
        self.quiet(il.route, ["set", "51", "--name", "issue-51", "--id", "a123", "--status", "running E181 ladder"])
        self.gh.comment(7, 51, PEER, "next step", at(58))
        self.run_watch()
        self.quiet(il.ack, "51", "7")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            il.route(["get", "51"])
            il.route(["list"])
        self.assertIn('"last": 7', out.getvalue())
        self.assertIn("#51\tissue-51\ta123\tlast=7", out.getvalue())



FAKE_TMUX = """#!/bin/sh
# Stand-in for tmux: logs each call (time, arguments, and stdin for load-buffer) and answers display and has-session.
case "$1" in
  load-buffer) printf '%s %s <<%s>>\\n' "$(python3 -c 'import time; print(time.time())')" "$*" "$(cat | tr "\\n" "|")" >> "$FAKE_TMUX_LOG" ;;
  *) printf '%s %s\\n' "$(python3 -c 'import time; print(time.time())')" "$*" >> "$FAKE_TMUX_LOG" ;;
esac
case "$1" in
  display) echo "${FAKE_PANE_DEAD:-0}" ;;
  has-session) exit "${FAKE_HAS:-0}" ;;
esac
exit 0
"""

SID = "0123abcd-0000-4000-8000-000000000000"


class KeeperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state, self.home, bin_dir = root / "state", root / "claude", root / "bin"
        for p in (self.state, self.home / "sessions", self.home / "projects" / "-repo", bin_dir):
            p.mkdir(parents=True)
        (bin_dir / "tmux").write_text(FAKE_TMUX)
        (bin_dir / "tmux").chmod(0o755)
        self.log = root / "tmux.log"
        self.log.touch()
        self.env = {k: os.environ.get(k) for k in ("PATH", "FAKE_TMUX_LOG", "FAKE_PANE_DEAD", "CLAUDE_CONFIG_DIR",
                                                    "HUB_LAUNCH", "HUB_DELIVER")}
        os.environ.update(PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_TMUX_LOG=str(self.log),
                          CLAUDE_CONFIG_DIR=str(self.home))
        for k in ("FAKE_PANE_DEAD", "HUB_LAUNCH", "HUB_DELIVER"):
            os.environ.pop(k, None)
        self.gh = FakeGitHub()
        self.gh.issue(51, ME, "opened by us")
        self.saved = il.gh_json, il.state_dir, il.CLAUDE_HOME, il.holders, il.kill, il.in_pane, il.instances
        il.in_pane = lambda pid: True
        il.instances = lambda sid: {pid: [pid] for pid in self.held}
        il.gh_json, il.state_dir, il.CLAUDE_HOME = self.gh.gh_json, (lambda: self.state), self.home
        self.held, self.killed = {os.getpid()}, []
        il.holders = lambda sid: set(self.held)
        il.kill = lambda pids: (self.killed.append(sorted(pids)), self.held.clear())
        il.update_json(self.state / "hub.json", lambda h: h.update(session=SID, cwd="/repo"))
        self.transcript = self.home / "projects" / "-repo" / f"{SID}.jsonl"
        self.transcript.write_text(json.dumps({"type": "assistant", "timestamp": at(1)}) + "\n")

    def tearDown(self):
        il.gh_json, il.state_dir, il.CLAUDE_HOME, il.holders, il.kill, il.in_pane, il.instances = self.saved
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def record(self, status):
        (self.home / "sessions" / f"{os.getpid()}.json").write_text(json.dumps(
            {"pid": os.getpid(), "sessionId": SID, "cwd": "/repo", "status": status, "updatedAt": 1}))

    def calls(self):
        return [line.split(" ", 1)[1] for line in self.log.read_text().splitlines()]

    def check(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            il.hub_check()
        return out.getvalue()

    def test_paste_is_bracketed_and_enter_waits(self):
        self.assertEqual(il.deliver(SID, "hello hub"), "pasted")
        lines = self.log.read_text().splitlines()
        self.assertTrue(lines[0].split(" ", 1)[1].startswith("display -p -t tt-hub-session #{pane_dead}"))
        self.assertIn("load-buffer -b tt-hub - <<hello hub>>", lines[1])
        self.assertIn("paste-buffer -p -d -b tt-hub -t tt-hub-session", lines[2])
        self.assertIn("send-keys -t tt-hub-session Enter", lines[3])
        self.assertGreaterEqual(float(lines[3].split()[0]) - float(lines[2].split()[0]), 0.35)

    def test_dead_pane_gets_no_paste(self):
        os.environ["FAKE_PANE_DEAD"] = "1"
        self.assertEqual(il.deliver(SID, "hello hub"), "dead")
        self.assertFalse(any(c.startswith(("load-buffer", "paste-buffer", "send-keys")) for c in self.calls()))

    def test_custom_deliver_gets_the_text_on_stdin(self):
        out = Path(self.tmp.name) / "delivered.txt"
        os.environ["HUB_DELIVER"] = f"cat > {out}; echo {{sid}} >> {out}"
        il.deliver(SID, "hello hub\n")
        self.assertEqual(out.read_text(), f"hello hub\n{SID}\n")

    def test_launch_refused_while_another_process_has_the_session(self):
        self.held = {4242}
        lines = []
        self.assertEqual(il.launch(self.state, SID, "/repo", lines.append, dry_run=False), 1)
        self.assertIn("not launching, session open in pid 4242", lines[0])
        self.assertEqual(self.calls(), [])

    def test_no_process_launches_claude_in_the_pane_with_the_first_prompt(self):
        self.held = set()
        os.environ["FAKE_HAS"] = "1"
        try:
            self.check()
        finally:
            os.environ.pop("FAKE_HAS")
        new = next(c for c in self.calls() if c.startswith("new-session"))
        settings = self.state / "hub-settings.json"
        self.assertIn(f"-s tt-hub-session -c /repo claude --dangerously-skip-permissions --settings {settings} "
                      f"--resume {SID}", new)
        self.assertIn("Before anything else, start `python3 torchtitan/experiments/rl/tools/issue_listener/issue_listener.py watch`", new)
        self.assertTrue(json.loads(settings.read_text())["skipDangerousModePermissionPrompt"])
        cfg = json.loads((self.home / ".claude.json").read_text())
        self.assertEqual(cfg["projects"]["/repo"], {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True})
        self.assertNotIn("hasCompletedOnboarding", cfg)  # user-wide: reported, never written
        self.assertFalse((self.home / "settings.json").exists())
        self.assertIn("has not finished onboarding", il.read_text(self.state / "hub.log"))

    def test_frozen_hub_is_stopped_and_relaunched(self):
        self.record("busy")
        old = time.time() - il.HUB_FREEZE - 60
        os.utime(self.transcript, (old, old))
        out = self.check()
        self.assertIn("frozen", out)
        self.assertEqual(self.killed, [[os.getpid()]])
        self.assertTrue(any(c.startswith("respawn-pane -k -t tt-hub-session") for c in self.calls()))

    def test_a_launcher_and_its_claude_are_one_instance_two_independent_are_two(self):
        table = {100: (1, f"python -m wrapper --resume {SID}"), 101: (100, "claude-real -p"),
                 200: (1, f"claude --resume {SID}")}
        saved = il.process_table, il.session_records, il.holders, il.instances
        try:
            il.holders, il.instances = self.saved[3], self.saved[6]
            il.process_table = lambda: {k: v for k, v in table.items() if k in self.keep}
            il.session_records = lambda sid: [{"pid": pid} for pid in (101, 200) if pid in self.keep]
            self.keep = {100, 101}
            self.assertEqual(il.instances(SID), {100: [100, 101]})
            self.keep = {100, 101, 200}
            self.assertEqual(il.instances(SID), {100: [100, 101], 200: [200]})
        finally:
            il.process_table, il.session_records, il.holders, il.instances = saved

    def test_two_independent_instances_are_reported_and_left_alone(self):
        il.instances = lambda sid: {100: [100, 101], 200: [200]}
        self.record("busy")
        old = time.time() - il.HUB_FREEZE - 60
        os.utime(self.transcript, (old, old))
        out = self.check()
        self.assertIn("DUPLICATE, its session is open in 2 independent instances: pid 100 (with 101); pid 200", out)
        self.assertEqual(self.killed, [])
        self.assertEqual(self.calls(), [])

    def test_frozen_hub_outside_the_pane_is_left_alone(self):
        il.in_pane = lambda pid: False
        self.record("busy")
        old = time.time() - il.HUB_FREEZE - 60
        os.utime(self.transcript, (old, old))
        self.assertIn("runs outside tmux tt-hub-session", self.check())
        self.assertEqual(self.killed, [])
        self.assertFalse(any(c.startswith(("respawn-pane", "new-session")) for c in self.calls()))

    def test_usage_limit_stop_is_relaunched(self):
        self.record("idle")
        with open(self.transcript, "a") as f:
            f.write(json.dumps({"type": "assistant", "timestamp": at(0), "isApiErrorMessage": True,
                                "error": "rate_limit", "message": {"content": [
                                    {"type": "text", "text": "You've hit your session limit"}]}}) + "\n")
        out = self.check()
        self.assertIn("stopped at a usage limit", out)
        self.assertEqual(self.killed, [[os.getpid()]])

    def test_idle_without_watch_delivers_then_redelivers_once_until_acked(self):
        self.record("idle")
        self.gh.comment(3, 51, PEER, "a request while watch is down", at(2))
        self.check()
        pasted = [c for c in self.calls() if c.startswith("load-buffer")]
        self.assertEqual(len(pasted), 1)
        self.assertIn("a request while watch is down", pasted[0])
        self.assertIn("Start `python3 torchtitan/experiments/rl/tools/issue_listener/issue_listener.py watch`", pasted[0])
        il.update_json(self.state / "hub-delivered.json",
                       lambda m: m["51:3"].__setitem__("at", time.time() - il.REDELIVER_AFTER - 1))
        il.update_json(self.state / "hub-act.json", lambda g: g.clear())
        self.check()
        again = [c for c in self.calls() if c.startswith("load-buffer")]
        self.assertEqual(len(again), 3)  # the redelivery, then this pass's restart-watch delivery
        self.assertIn("still not acked", again[1])
        il.update_json(self.state / "hub-act.json", lambda g: g.clear())
        self.check()
        self.assertFalse(any("still not acked" in c for c in self.calls()[len(self.calls()) - 4:]))
        with contextlib.redirect_stdout(io.StringIO()):
            il.ack("51", "3")
        il.update_json(self.state / "hub-act.json", lambda g: g.clear())
        self.check()
        self.assertEqual(il.read_json(self.state / "hub-delivered.json"), {})

if __name__ == "__main__":
    unittest.main(verbosity=2)
