#!/usr/bin/env python3
"""Independent, read-only failure recorder for a local training process.

Run in a separate systemd service so killing training does not kill its recorder.
No credentials or process environments are collected. SIGKILL sender attribution
requires kernel audit/eBPF; process polling cannot provide that information.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def process(pid):
    try:
        root = Path(f"/proc/{pid}")
        fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
        status = dict(line.split(":", 1) for line in (root / "status").read_text().splitlines() if ":" in line)
        result = {"pid": pid, "start_ticks": int(fields[19]), "state": fields[0],
                  "ppid": int(fields[1]), "uid": int(status["Uid"].split()[0]),
                  "name": status["Name"].strip(), "rss_kb": status.get("VmRSS", "").strip(),
                  "wchan": (root / "wchan").read_text().strip()}
        if fields[0] == "Z" and len(fields) > 49:
            result["wait_status"] = int(fields[49])
        return result
    except (OSError, ValueError, KeyError):
        return None


def command(args, timeout=10):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {"returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": str(e)}


class Tail:
    def __init__(self, path, from_end=False):
        self.path = Path(path)
        self.pos = self.path.stat().st_size if from_end and self.path.exists() else 0
        self.inode = None

    def read(self):
        try:
            st = self.path.stat()
            if (self.inode is not None and self.inode != st.st_ino) or st.st_size < self.pos:
                self.pos = 0
            self.inode = st.st_ino
            with self.path.open('rb') as f:
                f.seek(self.pos)
                data = f.read(4 * 1024 * 1024)
                self.pos = f.tell()
            return data.decode(errors='replace')
        except OSError:
            return ''


def summarize(run):
    evidence = []
    with (run / 'stdout.log').open(errors='replace') as f:
        for number, line in enumerate(f, 1):
            # Exclude sandbox/verifier errors: these do not establish host failure.
            host = line.startswith('[rank') or 'Unhandled monarch error' in line
            if ((host and any(x in line for x in ['timeout', 'taking the entire process down', 'Watchdog']))
                    or 'KILLED BY ' in line or 'failed: Killed(sig=' in line):
                evidence.append({'line': number, 'text': line.rstrip()})
    return {'updated_utc': now(), 'evidence': evidence[-80:],
            'sender_attribution': 'Not available from polling or exit status; needs kernel audit/eBPF.',
            'cores': [str(p) for p in run.glob('core*') if p.is_file()]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--pid', type=int)
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--summarize-only', action='store_true')
    args = parser.parse_args()
    out = args.run / 'forensics'
    out.mkdir(exist_ok=True)
    if args.summarize_only:
        (out / 'failure-summary.json').write_text(json.dumps(summarize(args.run), indent=2))
        return
    if not args.pid or args.interval <= 0:
        parser.error('--pid and a positive --interval are required')
    initial = process(args.pid)
    if initial is None:
        raise SystemExit('Controller already exited; use --summarize-only.')
    cg = Path('/sys/fs/cgroup') / next(line.split(':', 2)[2].lstrip('/')
        for line in Path(f'/proc/{args.pid}/cgroup').read_text().splitlines() if line.startswith('0:'))
    monarch = Tail(Path('/tmp') / os.environ.get('USER', 'unknown') / 'monarch_log.log', from_end=True)
    stdout = Tail(args.run / 'stdout.log')
    prior = {}
    trainer_pids = set()
    last_progress = time.monotonic()
    last_dump = 0
    dead_since = None
    tick = 0
    with (out / 'samples.jsonl').open('a', buffering=1) as samples, (out / 'events.jsonl').open('a', buffering=1) as events:
        def event(kind, **data):
            events.write(json.dumps({'time': now(), 'kind': kind, **data}) + '\n')
        event('recorder_started', controller=initial, cgroup=str(cg))
        while True:
            current = process(args.pid)
            alive = current is not None and current['start_ticks'] == initial['start_ticks'] and current['state'] != 'Z'
            if not alive and dead_since is None:
                dead_since = time.monotonic()
                event('controller_exited', observed=current)
            snapshot = {'time': now(), 'controller_alive': alive, 'cgroups': {}, 'processes': []}
            for directory in [cg, *cg.parents]:
                if directory == Path('/sys/fs/cgroup'):
                    break
                values = {}
                for name in ['memory.events', 'memory.current', 'memory.peak', 'memory.max', 'cpu.stat', 'memory.pressure']:
                    try:
                        values[name] = (directory / name).read_text().strip()
                    except OSError:
                        pass
                snapshot['cgroups'][str(directory)] = values
            try:
                pids = [int(p) for p in (cg / 'cgroup.procs').read_text().split()]
            except OSError:
                pids = []
            states = {p: v for p in pids if (v := process(p)) is not None}
            snapshot['processes'] = list(states.values())
            for pid, old in prior.items():
                if pid not in states or states[pid]['start_ticks'] != old['start_ticks']:
                    event('process_disappeared', last_observation=old)
            prior = states
            text = stdout.read()
            trainer_pids.update(int(p) for p in re.findall(r'forward_backward ENTER pid=(\d+)', text))
            if '[trainer]' in text or '[trainer_loop]' in text:
                last_progress = time.monotonic()
            if trainer_pids and alive and time.monotonic() - last_progress > 120 and time.monotonic() - last_dump > 300:
                last_dump = time.monotonic()
                for pid in trainer_pids & states.keys():
                    pyspy = shutil.which('py-spy') or str(Path.home() / '.local/bin/py-spy')
                    dump = command([pyspy, 'dump', '--pid', str(pid)])
                    (out / f'stalled-{int(time.time())}-{pid}.json').write_text(json.dumps(dump, indent=2))
                    # Python alone cannot distinguish a CUDA allocation wait
                    # from a collective wait inside the same extension call.
                    native = command([pyspy, 'dump', '--native', '--pid', str(pid)], timeout=15)
                    (out / f'stalled-native-{int(time.time())}-{pid}.json').write_text(json.dumps(native, indent=2))
                event('trainer_stall', seconds=time.monotonic() - last_progress)
            chunk = monarch.read()
            if chunk:
                with (out / 'monarch-shared-window.log').open('a') as f:
                    f.write(chunk)
            if tick % 2 == 0:
                snapshot['gpu'] = command(['nvidia-smi', '--query-gpu=index,uuid,utilization.gpu,memory.total,memory.used,memory.free', '--format=csv,noheader,nounits'])
                snapshot['gpu_processes'] = command(['nvidia-smi', '--query-compute-apps=gpu_uuid,pid,used_memory', '--format=csv,noheader,nounits'])
                rows = snapshot['gpu_processes'].get('stdout', '').splitlines()
                foreign_pids = {int(row.split(',')[1]) for row in rows if len(row.split(',')) == 3 and row.split(',')[1].strip().isdigit()}
                snapshot['gpu_process_identities'] = [v for p in foreign_pids if (v := process(p)) is not None]
            samples.write(json.dumps(snapshot) + '\n')
            if dead_since is not None and time.monotonic() - dead_since >= 20:
                break
            tick += 1
            time.sleep(args.interval)
        (out / 'kernel-at-exit.json').write_text(json.dumps(command(['dmesg', '--ctime']), indent=2))
        (out / 'failure-summary.json').write_text(json.dumps(summarize(args.run), indent=2))
        event('recorder_finished')


if __name__ == '__main__':
    main()
