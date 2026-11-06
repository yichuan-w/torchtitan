We have a distributed task processing system in `/app/worker_pool.py` that uses multiprocessing for parallel job execution. Workers periodically send heartbeats to signal they're alive, and a supervisor monitors these heartbeats to detect and restart hung workers.

The problem is that the heartbeat detection is unreliable - workers get incorrectly marked as dead even though they're running fine. This happens intermittently but frequently enough to cause production issues. When a worker is incorrectly marked as dead, it gets terminated and restarted, causing lost work and reduced throughput.

The heartbeat mechanism uses `multiprocessing.Event` - each worker has an event that it pulses (set then clear) as a heartbeat, and the supervisor waits on these events with a timeout. According to the Python documentation, `Event.wait(timeout)` should return `True` if the event was set at any point during the wait, but something is going wrong.

Investigate why the heartbeat detection is failing, identify the root cause, and fix the `worker_pool.py` implementation so that:
1. Heartbeat detection correctly identifies alive workers (no false positives)
2. Workers that are genuinely hung (not sending heartbeats for more than the timeout) are still detected
3. The fix doesn't introduce deadlocks or other synchronization issues

You can test your fix by running `python worker_pool.py` which will execute a test workload and report any false heartbeat failures.

Keep the `WorkerPool` public interface intact, but the heartbeat's internal representation and private attribute names are up to you.
