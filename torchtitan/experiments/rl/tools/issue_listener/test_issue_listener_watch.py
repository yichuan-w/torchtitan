"""Offline test of issue_listener.py: watch, ack and route state against fake gh responses, and the one-shot hub
launch against a fake tmux and a fake ~/.claude.

Run: python3 torchtitan/experiments/rl/tools/issue_listener/test_issue_listener_watch.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
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

    def gh_state(self):
        return Path(self.tmp.name)

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

    def test_stream_prints_each_item_once_per_run_as_one_line_with_its_text_file(self):
        self.gh.comment(3, 51, PEER, "two\nlines", at(58))
        polls = []

        def sleep(_):
            polls.append(1)
            if len(polls) == 2:
                self.gh.comment(4, 51, PEER, "later", at(1))
            if len(polls) == 3:
                raise KeyboardInterrupt

        def run():
            out = io.StringIO()
            saved, il.time.sleep = il.time.sleep, sleep
            try:
                with contextlib.redirect_stdout(out), self.assertRaises(KeyboardInterrupt):
                    il.watch_stream()
            finally:
                il.time.sleep = saved
            return out.getvalue().splitlines()

        lines = run()  # three polls: 3 once, not again on the second poll; 4 once it appears
        self.assertEqual([line.split()[:3] for line in lines], [["===", "#51", "3"], ["===", "#51", "4"]])
        path = Path(lines[0].split("full text: ")[1])
        self.assertEqual(path, self.gh_state() / "items" / "51-3.txt")
        self.assertIn("two\nlines", path.read_text())
        self.quiet(il.ack, "51", "4")
        polls.clear()
        lines = run()  # a re-armed stream hands back only what is still unacked
        self.assertEqual(len(lines), 1)
        self.assertIn("=== #51 3 yichuan-w", lines[0])
        self.assertIn("[again, first printed", lines[0])

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
# Stand-in for tmux: logs each call and answers has-session.
printf '%s\\n' "$*" >> "$FAKE_TMUX_LOG"
case "$1" in
  has-session) exit "${FAKE_HAS:-0}" ;;
esac
exit 0
"""

SID = "0123abcd-0000-4000-8000-000000000000"
HERE = Path(__file__).resolve().parent


class LaunchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state, self.home, bin_dir = root / "state", root / "claude", root / "bin"
        for p in (self.state, self.home / "sessions", bin_dir):
            p.mkdir(parents=True)
        (bin_dir / "tmux").write_text(FAKE_TMUX)
        (bin_dir / "tmux").chmod(0o755)
        self.log = root / "tmux.log"
        self.log.touch()
        self.env = {k: os.environ.get(k) for k in ("PATH", "FAKE_TMUX_LOG", "FAKE_HAS", "CLAUDE_CONFIG_DIR")}
        os.environ.update(PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_TMUX_LOG=str(self.log),
                          CLAUDE_CONFIG_DIR=str(self.home), FAKE_HAS="1")
        self.saved = il.state_dir, il.CLAUDE_HOME, il.holders
        il.state_dir, il.CLAUDE_HOME = (lambda: self.state), self.home
        self.held = set()
        il.holders = lambda sid: set(self.held)
        il.update_json(self.state / "hub.json", lambda h: h.update(session=SID, cwd="/repo"))

    def tearDown(self):
        il.state_dir, il.CLAUDE_HOME, il.holders = self.saved
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def launch(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = il.hub_launch()
        return code, out.getvalue()

    def test_no_process_launches_claude_once_with_the_watcher_prompt(self):
        code, out = self.launch()
        self.assertEqual(code, 0)
        calls = self.log.read_text().splitlines()
        new = next(c for c in calls if c.startswith("new-session"))
        settings = self.state / "hub-settings.json"
        self.assertIn(f"-s tt-hub-session -c /repo claude --dangerously-skip-permissions --settings {settings} "
                      f"--resume {SID}", new)
        self.assertIn('start the watcher: one background subagent (Agent tool, model "sonnet")', new)
        self.assertIn(f"`python3 {il.LISTENER} watcher-prompt`", new)
        self.assertIn("Do not run `watch` yourself", new)
        self.assertTrue(json.loads(settings.read_text())["skipDangerousModePermissionPrompt"])
        cfg = json.loads((self.home / ".claude.json").read_text())
        self.assertEqual(cfg["projects"]["/repo"], {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True})
        self.assertNotIn("hasCompletedOnboarding", cfg)  # user-wide: reported, never written
        self.assertIn("has not finished onboarding", out)
        self.assertFalse(any(c.startswith(("load-buffer", "paste-buffer", "send-keys")) for c in calls))

    def test_open_session_is_left_alone_and_reported(self):
        self.held = {4242}
        (self.home / "sessions" / f"{os.getpid()}.json").write_text(json.dumps(
            {"pid": os.getpid(), "sessionId": SID, "cwd": "/somewhere", "updatedAt": 1}))
        code, out = self.launch()
        self.assertEqual(code, 0)
        self.assertIn("is already open in pid 4242, cwd /somewhere; not launching", out)
        self.assertFalse(any(c.startswith(("new-session", "respawn-pane")) for c in self.log.read_text().splitlines()))

    def test_holders_see_a_wrapper_by_resume_or_session_id(self):
        il.holders = self.saved[2]
        for flag in ("--resume", "--session-id"):
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", flag, SID])
            try:
                self.assertIn(proc.pid, il.holders(SID))
            finally:
                proc.kill()
                proc.wait()

    def test_watcher_prompt_rearms_silently_and_sends_items_to_main(self):
        prompt = il.WATCHER_PROMPT
        self.assertIn(f"`python3 {il.LISTENER} watch --stream` running under the Monitor tool", prompt)
        self.assertIn("timeout_ms 1800000", prompt)
        self.assertIn("Arm it again, the same way and without telling anyone, only when", prompt)
        self.assertIn('(SendMessage to "main"), verbatim', prompt)
        self.assertIn("an item notification does not end it", prompt)
        self.assertIn("never have two monitors armed", prompt)

    def test_once_is_a_no_op_that_says_the_keeper_is_gone(self):
        r = subprocess.run(["bash", str(HERE / "agent-listen.sh"), "--once"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("the keeper was removed", r.stdout)
        self.assertEqual(self.log.read_text(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
