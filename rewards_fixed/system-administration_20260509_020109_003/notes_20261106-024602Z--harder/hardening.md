# Hardening rationale

## Attempt evidence

All 12 measured attempts passed. Their common strategy was to diagnose the unsafe mode and malformed state header, replace `/etc/logrotate.d/myapp` with three straightforward stanzas using `copytruncate`, force one rotation, and add a daily cron script. For example, `traces/attempt-01.jsonl` turns 4–16 performs exactly that sequence, and `traces/attempt-12.jsonl` turns 4–19 does the same. Every attempt used `copytruncate`; none tested two eligible rotations under the archival naming policy. The current task therefore spares the student from checking whether a configuration that succeeds once can rotate a size-triggered log again before the date changes.

The successful attempts earned their rewards for the intended broad workflow; no attempt edited grading files or substituted a hard-coded report. There is, however, a seed-verifier hidden requirement recorded separately in `run/repair.json`: it requires `copytruncate`, although the public task permits any working logrotate method. The scratch verifier will check rotation semantics rather than that implementation choice.

## Primary sources and pinned artifact

- https://github.com/logrotate/logrotate/releases/tag/3.22.0 — the fetched release notes identify the real upstream `3.22.0` release and its changes.
- https://raw.githubusercontent.com/logrotate/logrotate/main/logrotate.8.in — the fetched upstream manual says that `dateext` archives logs with a date extension, that `dateformat` defaults to `-%Y%m%d`, and that datestamps must be lexically sortable because logrotate sorts rotated filenames for retention.
- https://github.com/logrotate/logrotate/releases/download/3.22.0/logrotate-3.22.0.tar.xz — the artifact to pin in the Dockerfile, SHA-256 `42b4080ee99c9fb6a7d12d8e787637d057a635194e25971997eebbe8d5e57618`.

## Reproduction

In the live sandbox, both Ubuntu's logrotate 3.21.0 and an upstream 3.22.0 binary built from the pinned tarball were given a size-triggered `copytruncate` stanza with `dateext` and `dateformat -%Y%m%d`. The first forced rotation succeeded. After refilling the live log, the second command exited 1 with:

```
error: destination /tmp/dateext-repro/app.log-20261106 already exists, skipping rotation
```

The 3.22.0 reproduction command was `/usr/local/sbin/logrotate-3.22.0 -f -s /tmp/dateext-state /tmp/dateext-repro.conf`, run twice with the log refilled between runs. This confirms the documented naming behavior is present in the pinned artifact and in this environment.

## Proposed change and new decision

The image will use pinned upstream logrotate 3.22.0 and ship an inherited date-based archive naming policy. The application will leave new data in `app.log` after an earlier same-day rotation, reproducing an operator-visible state in which another eligible rotation collides with the existing archive. The user goal remains restoring dependable rotation of the three logs; the added acceptance condition is that eligible logs can rotate repeatedly without overwriting or dropping an earlier archive.

Unlike the seed workflow, the student must inspect an actual failed rotation after the first repair, understand that a date-only destination is not unique enough for a size-triggered policy, and choose a compatible naming/rotation configuration. The collision message identifies the destination but does not dictate whether to disable inherited date naming, add a sufficiently fine sortable component, or use another valid archival method. This is one modest debug-and-retest step beyond the exact logrotate skills every attempt demonstrated.

## Shortcut trials

From a fresh revised image I tried the fix most directly suggested by the error: delete `/var/log/myapp/app.log-$(date +%Y%m%d)`, then run the otherwise repaired shipped configuration. `./sandbox grade` returned reward 0. The checker created two new eligible app logs; the second run failed at the repeated-rotation check with `destination ... already exists`, so deleting one archive only postpones the collision.

From another fresh image I tried the failed-step response common to every measured attempt: regenerate a simple three-stanza configuration using numeric archives and `copytruncate`, repair the state header, add the daily cron script, and force rotation. `./sandbox grade` returned reward 1. This is not an announced fix: the diagnostic only names the colliding destination, while the operator must decide to revise the inherited archive naming (numeric rotation is one valid choice) and verify repeated rotation.

## Successful-attempt replays

`traces/attempt-01.jsonl` and `traces/attempt-12.jsonl` use materially identical repair commands: both overwrite the shipped app stanza and thereby remove `dateext`, so both still pass the revised semantic checker when replayed from a fresh image. Their earlier paths do encounter a new stop before the overwrite: after fixing only mode and state, a real rotation now fails because the same-day destination exists. The later full-config replacement happens to resolve it, so no final check fails. The change nevertheless touches their path through a new diagnostic, while requiring students who preserve the existing stanza to identify and correct its interaction with repeated size-triggered rotation.

Expected next group: **7 of 12**. All measured attempts overwrite the full file and are likely to retain their accidental success, but the new requirement and collision should separate agents that preserve/reconcile existing policy from those that stop after the first successful forced rotation; the prediction is therefore only a modest reduction from 12 of 12 rather than half.
