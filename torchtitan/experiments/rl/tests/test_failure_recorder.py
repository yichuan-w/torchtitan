from pathlib import Path
import os

from torchtitan.experiments.rl.examples.tmax.runbook.record_failure import Tail, process, summarize


def test_process_identity_and_disappearance():
    info = process(os.getpid())
    assert info['pid'] == os.getpid()
    assert info['uid'] == os.getuid()
    assert info['start_ticks'] > 0
    assert process(2147483647) is None


def test_tail_handles_truncation_and_replacement(tmp_path):
    path = tmp_path / 'log'
    path.write_text('old')
    tail = Tail(path, from_end=True)
    assert tail.read() == ''
    with path.open('a') as f:
        f.write('new')
    assert tail.read() == 'new'
    path.write_text('x')
    assert tail.read() == 'x'
    other = tmp_path / 'other'
    other.write_text('replacement')
    other.replace(path)
    assert tail.read() == 'replacement'


def test_summary_keeps_host_failure_not_sandbox_errors(tmp_path):
    (tmp_path / 'stdout.log').write_text(
        '[actor=rollout_worker] verifier: Watchdog timeout\n'
        '[rank1]: Watchdog caught collective operation timeout: SeqNum=627\n'
        '  the process this actor was running on failed: Killed(sig=6, core)\n'
    )
    evidence = summarize(tmp_path)['evidence']
    assert [x['line'] for x in evidence] == [2, 3]
