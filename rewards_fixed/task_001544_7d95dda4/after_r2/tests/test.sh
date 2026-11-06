#!/bin/bash
set -e
mkdir -p /logs/verifier
rm -f /tmp/required_services_ready
api_process_running() {
    for pid in $(pgrep -f 'python.*api.py' 2>/dev/null); do
        cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
        cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)
        if [[ "$cmdline" == *"/app/services/api.py"* ]] \
           || { [[ "$cmdline" == *"api.py"* ]] && [[ "$cwd" == "/app/services" ]]; }; then
            return 0
        fi
    done
    return 1
}

cache_probe='ACGTACGTACGTACGTACGT'
redis-cli -n 0 del "$cache_probe" >/dev/null 2>&1 || true
redis-cli -n 1 del "$cache_probe" >/dev/null 2>&1 || true
if redis-cli -n 0 ping 2>/dev/null | grep -qx PONG \
   && api_process_running \
   && python3 -c "import json, urllib.request; request=urllib.request.Request('http://127.0.0.1:5000/score', data=json.dumps({'sequence':'$cache_probe'}).encode(), headers={'Content-Type':'application/json'}); assert urllib.request.urlopen(request, timeout=2).status == 200" 2>/dev/null \
   && redis-cli -n 0 exists "$cache_probe" 2>/dev/null | grep -qx 1; then
    touch /tmp/required_services_ready
fi
pkill -f '[p]ython.*api.py' 2>/dev/null || true

cat << 'TEST_EOF' > /tmp/test_final_state.py
# test_final_state.py
import os
import csv
import json
import random
import subprocess
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest

def parse_fasta(path):
    seqs = {}
    with open(path) as f:
        curr_id = None
        curr_seq = []
        for line in f:
            line = line.strip()
            if line.startswith(">"):
                if curr_id:
                    seqs[curr_id] = "".join(curr_seq)
                curr_id = line[1:]
                curr_seq = []
            else:
                curr_seq.append(line)
        if curr_id:
            seqs[curr_id] = "".join(curr_seq)
    return seqs

def get_bg(path):
    with open(path) as f:
        return "".join(l.strip() for l in f if not l.startswith(">"))

def revcomp(seq):
    comp = {'A': 'T', 'T': 'A', 'C': 'G', 'G': 'C'}
    return "".join(comp.get(c, c) for c in reversed(seq))

def get_score(seq, bg):
    max_score = 0
    for i in range(len(bg) - len(seq)):
        score = sum(2 if seq[j] == bg[i+j] else -1 for j in range(len(seq)))
        if score > max_score: 
            max_score = score
    return max_score

def original_inputs():
    rng = random.Random(42)
    bases = ['A', 'C', 'G', 'T']
    targets = {
        f"Target_{index + 1}": "".join(rng.choices(bases, k=100))
        for index in range(5)
    }
    bg = "".join(rng.choices(bases, k=5000))
    return targets, bg

def run_design_with_quota(bg):
    requests_seen = []
    sequences_seen = []

    class ScoreHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0'))
            data = json.loads(self.rfile.read(length))
            requests_seen.append(data)
            if len(requests_seen) > 12:
                self.send_response(429)
                self.end_headers()
                return
            if 'sequences' in data and isinstance(data['sequences'], list) and data['sequences']:
                sequences = data['sequences']
                payload = {'scores': [get_score(sequence, bg) for sequence in sequences]}
            elif isinstance(data.get('sequence'), str):
                sequences = [data['sequence']]
                payload = {'score': get_score(data['sequence'], bg)}
            else:
                self.send_response(400)
                self.end_headers()
                return
            sequences_seen.extend(sequences)
            encoded = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 5000), ScoreHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            ['python3', '/home/user/design.py'],
            cwd='/home/user', timeout=60, capture_output=True, text=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    return completed, requests_seen, sequences_seen

def test_optimized_primers():
    csv_path = "/home/user/optimized_primers.csv"
    design_path = "/home/user/design.py"
    targets_path = "/home/user/targets.fasta"
    bg_path = "/home/user/background.fasta"

    assert os.path.isfile(csv_path), f"Output file missing: {csv_path}"
    assert os.path.isfile(design_path), f"Design script missing: {design_path}"
    assert os.path.isfile('/tmp/required_services_ready'), "Redis database 0 and the Flask API on port 5000 must be running"
    assert os.path.isfile(targets_path), f"Targets file missing: {targets_path}"
    assert os.path.isfile(bg_path), f"Background file missing: {bg_path}"

    targets, bg = original_inputs()
    Path(targets_path).write_text("".join(f">{target_id}\n{target}\n" for target_id, target in targets.items()))
    Path(bg_path).write_text(f">Background\n{bg}\n")
    candidates = []
    for target in targets.values():
        candidates.extend(target[index:index + 20] for index in range(21))
        candidates.extend(revcomp(target[index:index + 20]) for index in range(60, 81))

    completed, requests_seen, sequences_seen = run_design_with_quota(bg)
    assert completed.returncode == 0, f"design.py failed: {completed.stderr or completed.stdout}"
    assert len(requests_seen) <= 12, f"design.py exceeded the 12-request API quota ({len(requests_seen)} requests)"
    assert set(sequences_seen) == set(candidates), "design.py did not score the full valid candidate set through the API"

    total_score = 0
    count = 0

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == ["Target_ID", "Forward_Primer", "Reverse_Primer"], "CSV must have exactly the three required columns"

        seen_targets = []
        for row in reader:
            tid = row['Target_ID']
            fwd = row['Forward_Primer']
            rev = row['Reverse_Primer']

            assert tid in targets, f"Unknown Target_ID in CSV: {tid}"
            target = targets[tid]

            assert len(fwd) == 20, f"Forward primer for {tid} is not 20bp"
            assert len(rev) == 20, f"Reverse primer for {tid} is not 20bp"

            assert fwd in target[:40], f"Forward primer for {tid} is not a valid substring of the first 40bp"
            assert revcomp(rev) in target[60:], f"Reverse primer for {tid} is not a valid reverse complement of the last 40bp"

            total_score += get_score(fwd, bg)
            total_score += get_score(rev, bg)
            count += 2
            seen_targets.append(tid)

    assert count == 10, f"Expected 10 primers (5 targets * 2), found {count}"
    assert sorted(seen_targets) == sorted(targets), "CSV must contain exactly one primer pair for each target"
    assert total_score <= 180, f"Total off-target score {total_score} exceeds threshold of 180"
TEST_EOF

if python3 -m pytest /tmp/test_final_state.py -x --tb=short 2>&1; then
    echo "1" > /logs/verifier/reward.txt
else
    echo "0" > /logs/verifier/reward.txt
fi
