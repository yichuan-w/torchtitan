# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Keep a Terminus terminal reachable without replacing its shell."""

from __future__ import annotations

import asyncio
import os
import shlex
import time
import uuid
from pathlib import PurePosixPath
from typing import Any

# What a sandbox looks like when the student has filled its memory: memory at
# the limit, the page cache reclaimed to nothing, both CPUs in reclaim, and no
# OOM kill because reclaim keeps "succeeding" by evicting the code pages every
# process needs next. In that state the harness's tmux probe never returns, the
# attempt ends as terminal_state_unavailable, and nothing says whose fault it
# was (compile-compcert, TB 2.1 step-40 supplement, sandbox 2014fb8f: opam sized
# its build at 47 jobs from the host's core count, on a 2-CPU, 4 GiB sandbox).
#
# The change below is a cgroup split. The control plane -- the Daytona daemon,
# every shell it starts for the harness, and the tmux server, which is the
# harness's multiplexer and not the student's work -- moves into its own
# cgroup with memory.min and a high cpu.weight, so its pages are not the ones
# reclaimed and its processes are scheduled ahead of a fork storm. The pane's
# shell, whose descendants are the student's processes, moves into a sibling
# cgroup, whose memory.pressure and memory.events are then the student's
# alone. The student's memory.max stays untouched: the budget is the same.
#
# Measured on Daytona 2026-09-14 with the same opam/Coq install
# (probe_sandbox_pressure.py --workload compcert): unsplit, the tmux probe went
# from 0.5 s to 17.7 s to not returning within 20 s once memory hit the limit;
# split, it stayed under 3.2 s, and the kernel's OOM killer ended the student's
# largest process instead of the sandbox livelocking -- the "Killed" the model
# would see from any OOM. pid 1 is the daemon, every process starts in
# /init.scope whose control files are owned by nobody and refuse writes, the
# root cgroup is empty with every controller delegated, and a sibling cgroup
# created from inside accepts both the moves and memory.min. Where that does
# not hold the setup fails closed: an event records it and the terminal runs
# as before.
_CGROUP_ROOT = "/sys/fs/cgroup"
_CONTROL_CGROUP = "control"
_STUDENT_CGROUP = "student"


class TerminalExited(RuntimeError):
    """The interactive shell exited normally, without a terminating signal."""


class TerminalUnavailable(RuntimeError):
    """The terminal was lost without evidence sufficient to score the attempt."""

    def __init__(self, reason: str, events: list[dict]) -> None:
        super().__init__(reason)
        self.failure_reason = reason
        self.terminal_events = list(events)


class TerminalLifecycle:
    def __init__(self, execute: Any) -> None:
        self.execute = execute
        self.directory = f"/var/tmp/terminus-{uuid.uuid4().hex}"
        self.prepared = False
        self.session: str | None = None
        self.server_pid = ""
        self.pane_pid = ""
        self.socket = ""
        self.starttime = ""
        self.pane_starttime = ""
        self.events: list[dict] = []
        self.isolated = False

    def _event(self, kind: str, **details: Any) -> None:
        self.events.append({"time": time.time(), "kind": kind, **details})

    def _unavailable(self, reason: str, **details: Any) -> TerminalUnavailable:
        self._event(reason, **details)
        return TerminalUnavailable(reason, self.events)

    async def prepare(self) -> None:
        result = await self.execute(f"mkdir -m 700 -p {shlex.quote(self.directory)}")
        if result.return_code != 0:
            raise self._unavailable("terminal_directory_creation_failed")
        self.prepared = True

    def _starttime_command(self) -> str:
        # The comm field in /proc/PID/stat can contain spaces and parentheses.
        return f"sed 's/.*) //' /proc/{self.server_pid}/stat | cut -d ' ' -f 20"

    async def bind(self, session: str) -> None:
        target = shlex.quote(session)
        result = await self.execute(
            f"tmux set-option -t {target} remain-on-exit on && "
            f"tmux display-message -p -t {target} '#{{pid}}|#{{pane_pid}}|#{{socket_path}}'"
        )
        fields = (result.stdout or "").strip().split("|")
        if result.return_code or len(fields) != 3:
            raise self._unavailable("terminal_identity_unavailable")
        server, pane, socket = fields
        if (
            not server.isdigit()
            or not pane.isdigit()
            or not socket.startswith(self.directory + "/")
        ):
            raise self._unavailable("terminal_identity_invalid")
        self.server_pid, self.pane_pid, self.socket = server, pane, socket
        result = await self.execute(self._starttime_command())
        self.starttime = (result.stdout or "").strip()
        if result.return_code or not self.starttime.isdigit():
            raise self._unavailable("terminal_starttime_unavailable")
        pane_stat = await self._pane_stat()
        if pane_stat and pane_stat[1] == self.server_pid:
            self.pane_starttime = pane_stat[19]
        self.session = session
        await self._ensure_live()

    async def isolate(self) -> None:
        """Set the student apart from the control plane.

        Runs once, after ``bind``. Never raises: every outcome is an event, and a
        terminal that could not be isolated keeps working as before.
        """
        if os.environ.get("TT_TERMINAL_ISOLATION", "1") != "1":
            self._event("isolation_disabled")
            return
        if not self.server_pid or not self.pane_pid:
            self._event("isolation_skipped", reason="no bound terminal")
            return
        memory_min = os.environ.get("TT_CONTROL_MEMORY_MIN", "128M")
        cpu_weight = os.environ.get("TT_CONTROL_CPU_WEIGHT", "10000")
        if not cpu_weight.isdigit() or not 1 <= int(cpu_weight) <= 10000:
            self._event(
                "isolation_skipped", reason=f"TT_CONTROL_CPU_WEIGHT={cpu_weight!r}"
            )
            return
        root, control, student = (
            shlex.quote(_CGROUP_ROOT),
            shlex.quote(f"{_CGROUP_ROOT}/{_CONTROL_CGROUP}"),
            shlex.quote(f"{_CGROUP_ROOT}/{_STUDENT_CGROUP}"),
        )
        setup = (
            f"test -w {root}/cgroup.procs || exit 3; "
            f"mkdir -p {control} {student} || exit 3; "
            # Every process the daemon started is in init.scope, including this
            # shell; a runtime without init.scope keeps them in the root.
            f"for p in $(cat {root}/init.scope/cgroup.procs {root}/cgroup.procs "
            "2>/dev/null); do "
            f"echo $p > {control}/cgroup.procs 2>/dev/null || :; done; "
            f"echo {shlex.quote(memory_min)} > {control}/memory.min || exit 4; "
            f"echo {cpu_weight} > {control}/cpu.weight || exit 4; "
            f"echo {self.pane_pid} > {student}/cgroup.procs || exit 5; "
            f"grep -q ':/{_CONTROL_CGROUP}$' /proc/{self.server_pid}/cgroup || exit 6; "
            f"grep -q ':/{_STUDENT_CGROUP}$' /proc/{self.pane_pid}/cgroup || exit 6; "
            f"printf 'isolated|%s|%s|%s' \"$(cat {control}/memory.min)\" "
            f'"$(cat {control}/cpu.weight)" "$(wc -l < {control}/cgroup.procs)"'
        )
        result = await self.execute(setup)
        fields = (result.stdout or "").strip().split("|")
        if result.return_code != 0 or fields[:1] != ["isolated"] or len(fields) != 4:
            self._event(
                "isolation_unsupported"
                if result.return_code == 3
                else "isolation_failed",
                return_code=result.return_code,
                stdout=(result.stdout or "")[-500:],
                stderr=(result.stderr or "")[-500:],
            )
            return
        self.isolated = True
        self._event(
            "isolated",
            control_memory_min=fields[1],
            control_cpu_weight=fields[2],
            control_procs=fields[3],
            server_pid=self.server_pid,
            pane_pid=self.pane_pid,
        )

    async def snapshot(self) -> None:
        """Record the cgroups' memory state; runs on the control plane's side."""
        root = _CGROUP_ROOT
        groups = [root]
        if self.isolated:
            groups += [f"{root}/{_STUDENT_CGROUP}", f"{root}/{_CONTROL_CGROUP}"]
        reads = "; ".join(
            f"printf '%s: ' {shlex.quote(g)}; for f in memory.current memory.events "
            "memory.pressure; do printf '%s=' $f; "
            f"tr '\\n' ' ' < {shlex.quote(g)}/$f 2>/dev/null; done; echo"
            for g in groups
        )
        try:
            result = await self.execute(reads)
        except Exception as exc:  # noqa: BLE001 - evidence, never the verdict
            self._event("pressure_snapshot_failed", error=type(exc).__name__)
            return
        self._event(
            "pressure_snapshot",
            return_code=result.return_code,
            stdout=(result.stdout or "")[-2000:],
        )

    async def _pane_stat(self) -> list[str]:
        result = await self.execute(f"cat /proc/{self.pane_pid}/stat")
        prefix, separator, tail = (result.stdout or "").rpartition(") ")
        fields = tail.split()
        if (
            result.return_code
            or not separator
            or not prefix.startswith(self.pane_pid + " (")
            or len(fields) < 50
            or not fields[19].isdigit()
        ):
            return []
        return fields

    async def _unreaped_exit(self) -> tuple[str, str]:
        # Older tmux can report a closed PTY without reaping its child. Linux
        # retains waitpid's status in field 52 until that zombie is reaped.
        fields = await self._pane_stat()
        if (
            not fields
            or fields[0] != "Z"
            or fields[1] != self.server_pid
            or not self.pane_starttime
            or fields[19] != self.pane_starttime
            or not fields[49].isdigit()
        ):
            return "", ""
        wait_status = int(fields[49])
        if not 0 <= wait_status <= 65535:
            return "", ""
        if os.WIFEXITED(wait_status):
            return str(os.WEXITSTATUS(wait_status)), ""
        if os.WIFSIGNALED(wait_status):
            return "", str(os.WTERMSIG(wait_status))
        return "", ""

    @staticmethod
    def _control(command: str) -> tuple[bool, bool]:
        """Recognize Harbor clients; compound commands are never replayed."""
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:
            return False, False
        if tokens[:1] == ["timeout"]:
            tokens = tokens[2:]
        control = (
            len(tokens) >= 2
            and tokens[0] == "tmux"
            and tokens[1]
            in {
                "has-session",
                "send-keys",
                "capture-pane",
                "wait",
                "wait-for",
                "load-buffer",
                "paste-buffer",
            }
        )
        simple = not any(
            token in {";", "&&", "||", "|", "&", "(", ")", "\n"} for token in tokens
        )
        return control, simple

    @staticmethod
    def _connection_missing(result: Any) -> bool:
        text = (result.stderr or "") + (result.stdout or "")
        return result.return_code != 0 and any(
            marker in text
            for marker in (
                "error connecting to ",
                "no server running on ",
                "failed to connect to server",
            )
        )

    async def _probe(self):
        return await self.execute(
            f"tmux display-message -p -t {shlex.quote(self.session or '')} "
            "'#{pid}|#{pane_pid}|#{pane_dead}|#{pane_dead_status}|#{pane_dead_signal}'"
        )

    async def _ensure_live(self) -> None:
        result = await self._probe()
        if self._connection_missing(result):
            # Compare the original server's start time before signalling it. A
            # missing socket does not authorize starting a replacement server.
            parent = shlex.quote(str(PurePosixPath(self.socket).parent))
            socket = shlex.quote(self.socket)
            repair = await self.execute(
                f'test "$({self._starttime_command()})" = {self.starttime} && '
                f"test ! -e {socket} && test ! -L {socket} && "
                f"mkdir -p {parent} && chmod 700 {parent} && kill -USR1 {self.server_pid}"
            )
            if repair.return_code != 0:
                await self.snapshot()
                raise self._unavailable("terminal_connection_lost")
            await asyncio.sleep(0.1)
            result = await self._probe()
            if result.return_code == 0:
                self._event("socket_restored", server_pid=self.server_pid)
        for attempt in range(4):
            if result.return_code != 0:
                await self.snapshot()
                raise self._unavailable(
                    "terminal_state_unavailable",
                    return_code=result.return_code,
                    stdout=(result.stdout or "")[-1000:],
                    stderr=(result.stderr or "")[-1000:],
                )
            fields = (result.stdout or "").strip().split("|")
            if len(fields) != 5:
                raise self._unavailable(
                    "terminal_identity_unavailable",
                    stdout=(result.stdout or "")[-1000:],
                    stderr=(result.stderr or "")[-1000:],
                )
            if fields[:2] != [self.server_pid, self.pane_pid]:
                raise self._unavailable(
                    "terminal_identity_changed",
                    expected=[self.server_pid, self.pane_pid],
                    observed=fields[:2],
                )
            _, _, dead, status, signal = fields
            if dead != "1" or status or signal or attempt == 3:
                break
            # Closing the PTY and reaping its child are separate events. Read
            # again before assigning an outcome to a pane without exit status.
            await asyncio.sleep(0.1)
            result = await self._probe()
        if dead == "1":
            source = "tmux"
            if not status and not signal:
                status, signal = await self._unreaped_exit()
                source = "proc" if status or signal else "unknown"
            self._event(
                "shell_exited",
                exit_status=status or None,
                signal=signal or None,
                source=source,
            )
            if not signal and status.isdigit():
                raise TerminalExited(f"shell exited with status {status}")
            # SIGKILL is also used by the OOM killer. Do not infer policy blame.
            raise self._unavailable(
                "terminal_signal_unknown" if signal else "terminal_exit_unknown"
            )
        if dead != "0":
            raise self._unavailable("terminal_state_invalid")

    async def run(self, command: str, execute: Any):
        control, simple = self._control(command)
        if self.session is None or not control:
            return await execute(command)
        await self._ensure_live()
        result = await execute(command)
        if simple and self._connection_missing(result):
            # A returned client connection error confirms that this one client
            # did not deliver its request. API exceptions never reach this retry.
            await self._ensure_live()
            return await execute(command)
        return result
