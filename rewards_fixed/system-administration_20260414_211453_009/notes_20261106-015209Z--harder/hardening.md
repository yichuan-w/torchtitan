# Hardening rationale

## Trace evidence and current strategy

All 12 attempts were rewarded. Their common path was to create the missing `myapp` account and runtime directories, inspect per-program stderr, change Redis from `daemonize yes` to `daemonize no`, repair nginx-owned paths, restart under Supervisor, and curl the health endpoint. For example, attempt 11 does those operations at turns 7-35 and succeeds at turn 37; attempt 12 does them at turns 8-38 and succeeds at turn 40. The task currently spares the student service authentication: every application uses an unauthenticated `redis://localhost:6379` URL, so repairing permissions and Redis foreground mode is sufficient.

The successful attempts earned their reward through the intended system-administration workflow; I found no edited grader, hard-coded final answer, or skipped public goal. One seed verifier check does require `/api/jobs` to return `pending_jobs`, although the original instruction states only the health endpoint acceptance condition. I will repair this hidden requirement by making Redis-backed API operation part of the public health goal and recording it in `run/repair.json`.

## Primary sources and pinned artifact

- Redis ACL documentation: https://redis.io/docs/latest/operate/oss_and_stack/management/security/acl/ — fetched 2026-11-05. It states that when the default user is `off`, new connections are not authenticated and must send `AUTH` or `HELLO` with authentication; it also documents `ACL SETUSER` rules.
- Redis AUTH documentation: https://redis.io/docs/latest/commands/auth/ — fetched 2026-11-05. It states that `AUTH [username] password` authenticates connections and that the two-argument form applies to the Redis 6+ ACL system.
- redis-py v5.0.8 connection implementation: https://raw.githubusercontent.com/redis/redis-py/v5.0.8/redis/connection.py — fetched 2026-11-05. Lines 323-364 show that redis-py obtains username/password credentials from the URL and sends both with `AUTH` (or `HELLO ... AUTH`).
- PyPI release metadata: https://pypi.org/pypi/redis/5.0.8/json — fetched 2026-11-05. It identifies the 5.0.8 sdist and SHA-256 `0c5b10d387568dfe0698c6fad6615750c24170e548ca2deac10c649d463e9870`.

The Dockerfile will pin `redis==5.0.8` from that release. The Redis server remains the seed image's Ubuntu 24.04 package (7.0.15); the upstream artifact introduced by this hardening is the pinned Python client used by both application services.

## Reproduction in this environment

I launched the image's Redis 7.0.15 with `user default off` and an enabled named ACL user, then ran:

```
redis-cli -p 6380 ping
redis-cli -p 6380 -a 's3cr@t#ops' ping
redis-cli -p 6380 --user appuser -a 's3cr@t#ops' ping
python3 -c 'import redis; r=redis.from_url("redis://appuser:s3cr@t#ops@localhost:6380"); print(r.ping())'
python3 -c 'import redis; r=redis.from_url("redis://appuser:s3cr%40t%23ops@localhost:6380"); print(r.ping())'
```

Observed output was respectively `NOAUTH Authentication required`, `WRONGPASS invalid username-password pair or user is disabled`, `PONG`, a redis-py connection failure to parsed host `t`, and `True`. Thus a plausible password-only repair is insufficient, while embedding the literal credentials without URI encoding silently changes URL parsing. The intended repair is discoverable by inspecting Redis ACL state/configuration, application errors, and URL parsing behavior, but no emitted error names percent-encoding as the fix.

## Proposed change and new decision

Redis will start with the default user disabled and a named application user whose password contains URI-reserved characters. The worker and API retain their currently broken unauthenticated environment URLs. After the familiar account, permissions, foreground-mode, and nginx repairs, the student must identify ACL authentication as the remaining failure, choose the named user rather than password-only authentication, and encode the credentials correctly in the Redis URLs supplied through Supervisor. This is one modest additional diagnosis/configuration step using the same log inspection and restart loop shown in every successful trace; it adds no unrelated deliverable and preserves the four-service and endpoint goal.

The environment contains only the failing configuration and the upstream client, not a patch, fixed configuration, or note naming the fix.

## Announced-fix trials

Both trials were run from fresh resets against the scratch verifier:

1. **Password-only authentication:** I changed both `REDIS_URL` values to `redis://:s3cr%40t%23ops@localhost:6379`, the direct password-only response to `NOAUTH`. Supervisor itself rejected the single `%` URI escapes as interpolation syntax (`unsupported format character 't'`). Grade: **0**. Separately, `redis-cli -a 's3cr@t#ops' ping` reached Redis and returned `WRONGPASS invalid username-password pair or user is disabled`, showing that password-only authentication remains wrong even after handling the Supervisor syntax.
2. **Restart/regenerate unchanged services:** from a clean image I applied the familiar user, directory, ownership and `daemonize no` repairs, then restarted all programs without changing their Redis URLs. Grade: **0**. The jobs check failed with `{'error': 'Authentication required.'}` and the worker remained FATAL/BACKOFF.

Neither trial passes, and neither message announces the complete named-user plus URI-encoding plus Supervisor-interpolation decision.

## Successful-attempt replays

I replayed the state-changing paths from two successful attempts, each after a fresh `./sandbox reset`, without starting anything beyond what those traces start:

- **Attempt 11 path:** created `myapp`, repaired ownership/runtime directories, switched Redis to foreground, and restarted the services as its turns 7-35 do. It now stops after the final restart with `worker FATAL`; `/health` still says healthy, but `/api/jobs` says `Authentication required.` Grade: **0**. Failed scratch check: Redis-backed jobs endpoint. (The authenticated Redis reachability check passes because Redis itself is running.)
- **Attempt 12 path:** started Supervisor, created `myapp`, repaired Redis/nginx paths, switched Redis to foreground and restarted as its turns 8-38 do. It likewise stops with `worker FATAL`; `/health` says healthy and `/api/jobs` says `Authentication required.` Grade: **0**. Failed scratch check: Redis-backed jobs endpoint.

Both prior paths therefore encounter the new condition rather than passing around it.

## Predicted next-group result

**6 of 12.** All previous attempts inspect service error logs and configuration, so they can discover ACL authentication, but none previously handled named Redis users, URI-reserved credentials, or Supervisor's doubled-percent interpolation in one repair. The healthy endpoint remains a misleading partial success until the explicitly requested Redis-backed endpoint is checked, making completion depend on that additional investigation rather than sheer command volume.
