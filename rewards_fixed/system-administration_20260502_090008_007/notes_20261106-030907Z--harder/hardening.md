# Hardening rationale

## Observed successful strategy

All 12 supplied attempts passed. They inspect the shipped Supervisor programs, add a one-shot database-initialization program, assign ascending priorities, start Supervisor, and probe the HTTP service. Most rely on priority (and sometimes unsupported `depends`/`depends_on` keys) as though it were a readiness dependency. For example, `traces/attempt-01.jsonl` turns 20-26 add the migration at priority 80, leave the application at priority 50, add `depends_on`, and start Supervisor; `traces/attempt-12.jsonl` turns 17-25 add priority tiers and unsupported `depends` keys. Their reward confirms that the current application tolerates this even though `instruction.md` says it crashes when its dependencies are not ready. The reference is one of the few workflows that already uses an active readiness wrapper.

No rewarded attempt violates the public requirement, edits grading material, or hard-codes a checked answer. The setup does have a mismatch: the instruction says the application crashes on startup before database/cache readiness, but `/opt/webapp/app.py` does not contact either service while starting. This will be repaired as part of the hardening and recorded in `run/repair.json`.

## Primary sources and pinned artifacts

- https://raw.githubusercontent.com/Supervisor/supervisor/4.2.5/docs/configuration.rst — “Lower priorities indicate programs that start first” and higher priorities start last; this describes ordering, not dependency readiness.
- https://raw.githubusercontent.com/Supervisor/supervisor/4.2.5/docs/subprocess.rst — a process moves from STARTING to RUNNING according to `startsecs`, while one that exits too quickly enters BACKOFF; these are process-lifetime states rather than an application health check.
- https://pypi.org/pypi/supervisor/4.2.5/json — PyPI identifies the 4.2.5 sdist URL and SHA-256 `34761bae1a23c58192281a5115fb07fbf22c9b0133c08166beffc70fed3ebc12`.
- https://raw.githubusercontent.com/redis/redis-py/v5.0.8/redis/connection.py — redis-py 5.0.8 sends `AUTH` when a username or password is supplied, and its connection URL syntax accepts a password.
- https://pypi.org/pypi/redis/5.0.8/json — PyPI identifies the pinned redis-py 5.0.8 sdist and SHA-256 `0c5b10d387568dfe0698c6fad6615750c24170e548ca2deac10c649d463e9870`.

The image pins `supervisor==4.2.5`, `redis==5.0.8`, and the other application Python dependencies so the reproduced behavior does not drift.

## Reproduction before editing

In the seed sandbox, `supervisord --version` reported `4.2.5`. I started a `slow` program at priority 10 whose process stayed alive immediately but created its readiness marker three seconds later, and a `follower` at priority 20 that required that marker. After one second, Supervisor reported `slow RUNNING` and `follower EXITED`; `test -e /tmp/svprobe/ready` returned 1. Thus the lower-priority process was considered started while its service was still unready, and the later-priority program ran too soon.

## Proposed change and new decision

The shipped Redis service will enforce authentication using its actual upstream configuration, and the pinned redis-py client used by the application and worker supports supplying that credential. A priority-only configuration can therefore bring processes up while leaving the stack semantically unhealthy. A solver must inspect the service configuration and propagate its existing credential through both managed programs, in addition to preserving the readiness/order workflow. This is one modest step beyond the attempts: they already inspect services, edit program environments, start Supervisor, and diagnose HTTP output, but generally stop at priority tiers.

The error path identifies Redis authentication as the unavailable dependency but does not prescribe where the credential belongs. The intended reference fix keeps its existing readiness wrapper and adds the discovered Redis credential to the wrapper probe and managed-program environments.

## Plausible first fixes

To be completed after the revised image is built: (1) the error/tool-output-suggested response, and (2) the common attempt response of priority plus unsupported dependency keys / reinstall-or-upgrade, each followed by its grade.

## Attempt replays and prediction

To be completed after the revised image is built. I will replay two successful trace command sequences from fresh resets, report where each stops and all failed checks, then predict the next solved count.

## Plausible first-fix results

After building the revised image, I tried the common attempt strategy on a fresh reset: assign priority 10 to PostgreSQL/Redis, priority 20 to the migration, priority 30 to the application/worker, priority 40 to Nginx, and start Supervisor. It produced an HTTP health response with `database: connected` but `cache: error: Authentication required.`; `./sandbox grade` returned reward 0.0. The failing retained checks were the worker becoming RUNNING and the cache health connection.

I also tried the natural package response of force-reinstalling the pinned `redis==5.0.8` client and restarting the application and worker. The same authentication error remained and `./sandbox grade` returned reward 0.0. Neither output prescribes where the credential belongs; the solver must inspect Redis configuration and propagate it through the managed programs.

## Successful-attempt replays

I replayed the commands from `traces/attempt-01.jsonl` from a fresh reset. That attempt first starts the seed configuration, then shuts Supervisor down and writes `depends_on` keys. Its replay stops at the second `supervisord` start because Nginx from the first daemon is still bound to port 80; the final state has no Supervisor control socket. `./sandbox grade` returned 0.0 and fails every service-management/accessibility check. Independently of that pre-existing replay fragility, its written application and worker environments omit the Redis credential.

I replayed `traces/attempt-12.jsonl` from another fresh reset. Supervisor accepts its unknown `depends` keys and all programs initially report STARTING, but `/api/data` returns `Authentication required`, the worker cannot pass its Redis startup probe, and `/health` reports the cache authentication error. `./sandbox grade` returned 0.0. The failed checks are the background worker RUNNING check and cache-connected health check; the service/process, database initialization, port-80, and database-connected checks pass after stabilization.

The revised mechanism directly intersects the dominant successful path while preserving the original orchestration goal. I expect **6 of 12** attempts to solve the next group: capable attempts already inspect application/configuration and test `/health` and `/api/data`, but those that only write priority tiers or invented dependency keys will fail.
