# Hardening rationale

## Observed strategy and reward audit

All 12 attempts inspected the CSV, wrote a short pandas/Python aggregation, localized the naive timestamps with the installed Cairo timezone, deduplicated `(timestamp, sensor_id)`, and wrote the requested CSV. Attempts 01, 03, 10, and 12 used pandas/`zoneinfo`; the others primarily used pytz. None checked which IANA timezone release the runtime was using. This is work the seed spares them because its system and Python timezone data agree and are current.

The successful rewards were not all semantically earned. Attempt 12, turn 8 explicitly treats the nonexistent April 26 00:00 hour as fixed EET (UTC+02) rather than shifting it forward, yet it received reward. The public instruction says those wall times do not exist but does not choose how to repair them, while the seed verifier silently expects pandas `nonexistent='shift_forward'` and checks only that the resulting transition-hour keys exist. This hidden convention and weak content check will be repaired by stating the ingestion policy and checking the actual transition aggregates; see `run/repair.json`.

## Primary sources and reproduced behavior

- https://raw.githubusercontent.com/eggert/tz/2023c/NEWS — I fetched tag `2023c` (commit `ed996bfdcd3bc9706a43de99d648896467d976e0`). Its 2023a release notes say: “Egypt now uses DST again, from April through October,” and “Starting in 2023, Egypt will observe DST from April's last Friday through October's last Thursday,” with transition times assumed at 00:00 and 24:00.
- https://raw.githubusercontent.com/python/cpython/v3.12.3/Doc/library/zoneinfo.rst — the Python 3.12.3 documentation says `ZoneInfo(key)` first searches `TZPATH`, that `PYTHONTZPATH` configures that search path, and that `reset_tzpath` can manipulate it at runtime.
- https://raw.githubusercontent.com/stub42/pytz/release_2022.7.1/src/README.rst — pytz 2022.7.1 documents that local wall times must use `localize()`, and that `is_dst=None` refuses to guess and raises for ambiguous or nonexistent times.
- https://pypi.org/pypi/pytz/2022.7.1/json — the pinned sdist is `pytz-2022.7.1.tar.gz`, SHA-256 `01a0681c4b9684a28304615eba55d1ab31ae00bf68ec157ec3708a8182dbbcd0`.
- https://data.iana.org/time-zones/releases/tzdata2022g.tar.gz — the stale deployment artifact pinned in the image, fetched with SHA-256 `4491db8281ae94a84d939e427bdd83dc389f26764d27d9a5c52d782c16764478`.
- https://data.iana.org/time-zones/releases/tzdata2023c.tar.gz — the first IANA release carrying the restored Egypt rule; fetched SHA-256 `3f510b5d1b4ae9bb38e485aa302a776b317fb3637bdb6404c4adf7b6cadd965c`.

Before editing, I reproduced the version difference in the seed sandbox. With installed pytz 2026.6, `Africa/Cairo.localize(datetime(2024,4,26,0,30), is_dst=None)` raised `NonExistentTimeError`, and 01:00 localized as `+03:00`, converting to `2024-04-25 22:00 UTC`. With `pytz==2022.7.1` installed into an isolated target, 00:30 and 01:00 both localized as `+02:00`; 01:00 converted to `2024-04-25 23:00 UTC`. The command was a Python loop over those two datetimes using `timezone('Africa/Cairo').localize(..., is_dst=None)` and `astimezone(UTC)`.

The revised image pins the old pytz sdist by version and installs the pinned IANA 2022g archive into an application timezone path selected by `PYTHONTZPATH`/`TZDIR`. The host distribution's current `/usr/share/zoneinfo` remains available. This models a real application configured with a stale private timezone database while making the primary source's behavior observable with `zoneinfo.TZPATH`, `zdump`, and conversion probes.

## New decision and expected difficulty

The student must now diagnose a silent disagreement between the configured application timezone data and the stated 2024 Cairo transition, then deliberately select current system timezone rules for the conversion. Merely knowing pandas aggregation and DST options is no longer enough. This is one modest operational step beyond the attempts: they already probe package versions and experiment with localization, but none verifies the data source behind the timezone implementation.

The planted condition does not announce a fix: stale rules produce plausible UTC timestamps and a plausible CSV. The current system database and the IANA release notes provide investigation clues; the intended fix is not shipped as a patch, backport, or note.

## Shortcut trials

From a fresh revised image I ran the ordinary pandas solution against the configured `Africa/Cairo` rules using `nonexistent='shift_forward'`, exactly as the instruction suggests for bad wall times. Grade was reward 0: 5 checks passed and 2 failed (`test_aggregate_values_sample` and `test_dst_transition_handling`); the stale rules yielded 20.765 instead of 17.445 for a retained sample and 24.575 instead of 24.7275 at the transition. There was no runtime error suggesting a repair.

I then upgraded/reinstalled both pandas and pytz with pip, reran the same script, and graded again. Grade remained reward 0 with the same 5 passes and 2 failures and the same wrong values. Updating Python packages does not override the stale application timezone path, so the fallback used across the traces does not announce the fix.

## Attempt replays and prediction

I replayed attempt 01's successful core path from a fresh reset: deduplicate, use pandas `tz_localize('Africa/Cairo', nonexistent='shift_forward')`, aggregate, and format the output. It now stops at grade reward 0 with 5 checks passing. The content sample check fails for post-transition UTC buckets and the transition check fails its aggregate values; all existence, schema, sensor, format, and unique-hour checks pass.

I replayed attempt 12's successful core path from another fresh reset: use `ZoneInfo('Africa/Cairo')`, force the nonexistent hour to fixed UTC+02, aggregate, and write CSV. It now stops at grade reward 0 with 4 checks passing. The UTC format check, retained content sample check, and transition handling check fail; existence, schema, sensors, and unique-hour checks pass.

The student already probes package versions and iterates after failed checks, so some runs should inspect `TZPATH` or `zdump` and find the current system rules. Most traces assume the named zone is authoritative and will stop at plausible output. Expected next group: **5 of 12**.
