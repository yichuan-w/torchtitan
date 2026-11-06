# Hardening rationale

## Attempt evidence

All 12 attempts earned reward, but their common strategy was narrowly tailored to the simulated delay: inspect `inventory/views.py`, turn the pricing methods into coroutines, replace `time.sleep(0.5)` with `await asyncio.sleep(0.5)`, and run `load_test.py`. Attempts 01, 02, 03, 04, 06, 10, and 11 use that direct strategy. Attempts 05, 07, 08, 09, and 12 add a lock, cache, or shared task, but still replace the pricing operation with an `asyncio.sleep` and hard-coded rates. The task currently spares the student from adapting a genuinely synchronous third-party client while preserving the data returned by that client.

The rewarded runs also expose a seed verifier defect. Attempt 01's turn 14 reports 12 successes and 5 errors, and attempt 12's turn 18 reports 11 successes and 41 errors, yet both receive reward because `load_test.py` exits successfully whenever timeouts are low. The repaired contract requires requests to complete successfully, not merely avoid the timeout counter.

## Primary sources and pinned artifact

- https://raw.githubusercontent.com/psf/requests/v2.32.3/docs/user/advanced.rst — the Requests 2.32.3 documentation says: “Requests does not provide any kind of non-blocking IO” and “these calls will still block.”
- https://raw.githubusercontent.com/django/asgiref/3.7.2/README.rst — the asgiref 3.7.2 documentation says `SyncToAsync` runs synchronous functions in a threadpool, that synchronous calls are thread-sensitive by default, and that `thread_sensitive=False` may be used for code that does not rely on thread-bound state.
- https://pypi.org/pypi/requests/2.32.3/json — fetched metadata identifies the pinned sdist `requests-2.32.3.tar.gz` with SHA-256 `55365417734eb18255590a9ff9eb97e9e1da868d4ccd6402399eaf68af20a760`.

The environment will pin `requests==2.32.3`; the existing `asgiref==3.7.2` pin is retained.

## Reproduction

In the booted task container, after installing the pinned Requests release, I ran eight concurrent coroutines that each called `requests.get()` against a local threaded HTTP server whose response takes 0.5 seconds. The observed output was:

```
requests=2.32.3 elapsed=4.03s
```

I also measured the documented asgiref distinction with eight 0.5-second synchronous calls:

```
default_thread_sensitive=4.00s
thread_sensitive_false=0.50s
```

Thus the upstream behavior is present in this environment: calling Requests directly blocks the event loop, while blindly using default `sync_to_async` serializes independent calls on its shared thread.

## Proposed change

Replace the simulated pricing delay with a local pricing HTTP service and the pinned Requests client. The inventory API must retain the pricing values supplied by that service while remaining responsive and returning successful responses under concurrent load. A direct conversion from `time.sleep` to `asyncio.sleep` no longer fixes the blocking operation, and replacing the operation with hard-coded rates no longer preserves correct API behavior. The student must identify the synchronous client boundary and offload that independent I/O without applying the same concurrency setting to the thread-bound Django ORM work.

This is one modest step beyond the demonstrated skill: the attempts already recognize an event-loop stall and propagate `await` through the call chain; they now must use the real library's documented sync/async bridge and distinguish non-thread-bound HTTP I/O from database access.

The output does not announce the exact fix. It still identifies event-loop blocking through timing and timeout behavior, while source inspection exposes the client and package version.

## Candidate-fix trials and replays

To be completed after the revised image and checker are in place. The two required first-fix grades and two successful-attempt replays will be recorded here before final validation.

### Results

- **Fix suggested by the client symptom: increase the Requests timeout.** On a fresh reset I changed only `timeout=4` to `timeout=30` and ran `./sandbox grade`. Grade: **0.0**. The inventory event loop remained blocked and the checker timed out waiting for the server process to stop.
- **First fix used by the successful attempts: replace the blocking operation with `asyncio.sleep` and hard-coded rates.** On fresh resets I replayed this strategy from attempts 01 and 02, including making the pricing methods async and awaiting them. Each `./sandbox grade` returned **0.0**. Requests completed quickly, but the pricing-content assertion failed because the response no longer used the pricing service's 1.17 and 0.93 rates.

The intended reference fix offloads the pinned Requests call with `sync_to_async(..., thread_sensitive=False)` while leaving ORM wrappers thread-sensitive. `./sandbox oracle --fresh` returned reward **1.0**; its load-test output was 15 successes, 0 timeouts, 0 errors.

### Successful-attempt replays

- **Attempt 01:** replayed its decisive strategy on a fresh reset: replace the pricing operation with `await asyncio.sleep(0.5)`, return hard-coded rates, propagate async/await, then grade. It now stops at the pricing semantic check. Failed checks: product responses do not use the pricing service's current multiplier and currency rate. Grade **0.0**.
- **Attempt 02:** replayed the same direct async conversion from its turn 7 on a second fresh reset, then grade. It now stops at the same pricing semantic check. Failed checks: product responses do not use the pricing service's current multiplier and currency rate. Grade **0.0**.

Both prior paths are touched. Based on all 12 attempts immediately recognizing the event-loop issue but none preserving a real synchronous client's output, I expect **6 of 12** in the next same-sized group.
