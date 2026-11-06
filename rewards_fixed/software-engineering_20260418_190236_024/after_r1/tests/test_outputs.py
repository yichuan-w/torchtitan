import subprocess
import sys


def test_supplied_workload_has_no_false_heartbeat_failures():
    failures = []
    for run_number in range(1, 4):
        try:
            result = subprocess.run(
                [sys.executable, "/app/worker_pool.py"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd="/app",
            )
        except subprocess.TimeoutExpired as error:
            failures.append(f"run {run_number} timed out: {error}")
            continue

        output = result.stdout + result.stderr
        print(f"=== Supplied workload run {run_number} ===")
        print(output)
        if result.returncode != 0 or "FAILURE" in output:
            failures.append(
                f"run {run_number} exited {result.returncode}; output:\n{output}"
            )

    assert not failures, "\n\n".join(failures)


def test_public_interface_healthy_hung_and_shutdown_behavior(tmp_path):
    exercise = tmp_path / "exercise_pool.py"
    exercise.write_text(
        '''import multiprocessing
import sys
import time

sys.path.insert(0, "/app")
from worker_pool import WorkerPool


def square(value):
    return value * value


def hang():
    time.sleep(30)


def identity(value):
    return value


def healthy_case():
    pool = WorkerPool(num_workers=2, heartbeat_timeout=0.4, max_restarts=1)
    pool.start()
    pool.submit(square, 7)
    submitted_result = pool.result_queue.get(timeout=3)[1]
    mapped_results = pool.map(square, [1, 2, 3, 4])
    false_restarts = pool.shutdown()
    if submitted_result != 49 or sorted(mapped_results) != [1, 4, 9, 16]:
        raise RuntimeError("public submit/map methods returned incorrect results")
    if false_restarts != 0:
        raise RuntimeError("healthy workers were incorrectly marked dead")


def hung_case():
    pool = WorkerPool(num_workers=1, heartbeat_timeout=0.2, max_restarts=1)
    pool.start()
    pool.submit(hang)
    time.sleep(0.8)
    if pool.map(identity, [73]) != [73]:
        raise RuntimeError("replacement worker did not process work")
    pool.shutdown()


if __name__ == "__main__":
    multiprocessing.set_start_method("fork", force=True)
    healthy_case()
    hung_case()
'''
    )
    try:
        result = subprocess.run(
            [sys.executable, str(exercise)],
            capture_output=True,
            text=True,
            timeout=15,
            cwd="/app",
        )
        failure = None if result.returncode == 0 else (
            f"public-interface exercise exited {result.returncode}:\n"
            f"{result.stdout}{result.stderr}"
        )
    except subprocess.TimeoutExpired as error:
        failure = f"public-interface exercise deadlocked or failed to detect a hung worker: {error}"

    assert failure is None, failure
