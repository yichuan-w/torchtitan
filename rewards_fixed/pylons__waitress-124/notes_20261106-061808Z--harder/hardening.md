# Hardening rationale

## Attempt evidence

All 12 rewarded attempts made the intended repair. Their shared strategy was to locate `start_response` in `waitress/task.py`, copy the nearby header-value defense, and add literal `\r`/`\n` checks for the status and header name. Attempts 01 and 12 also inspected issue-related commits before implementing the same narrow checks. None had to decide the complete syntax of either HTTP field.

No rewarded attempt edited grading machinery, hard-coded an answer, or skipped a stated requirement, so no seed repair is needed.

## Primary sources and reproduced behavior

- https://github.com/Pylons/waitress/issues/122 says status lines and header names have the same response-splitting exposure as header values and demonstrates Waitress 0.9.0b0 emitting the injected status and malformed header name.
- https://github.com/Pylons/waitress/commit/4deab9f23f9eb273971da247981aa85b7a16e946 has the message “Check header names and status for line feed/carriage return. Fixes #122” and implements literal CR/LF checks. This is the history-guided fix reached by the successful attempts.
- https://www.rfc-editor.org/rfc/rfc7230.txt defines `field-name = token`, `status-code = 3DIGIT`, and `reason-phrase = *( HTAB / SP / VCHAR / obs-text )`. Therefore CR/LF rejection is necessary but does not fully validate either field, and an empty reason phrase remains valid.
- https://peps.python.org/pep-3333/ says `status` has the form `"999 Message here"` and each response `header_name` must be a valid HTTP field-name.

The RFC fetched on 2026-11-05 has SHA-256 `c7fdc8bebdf1f8195f731592c47f5ea822b489436fd905b01b55ef531fca4120`; the image will pin that URL and digest. On the seed image, this reproduction:

```
cd /waitress && python - <<'PY'
from waitress.tests.test_task import TestWSGITask
for status, name in (("20 OK", "Bad Name"), ("200 OK\\r", "X-Test")):
    inst = TestWSGITask()._makeOne()
    def app(environ, start_response, status=status, name=name):
        start_response(status, [(name, "value")]); return [b""]
    inst.channel.server.application = app
    try: inst.execute(); print(repr(status), repr(name), "ACCEPTED")
    except Exception as exc: print(type(exc).__name__, str(exc))
PY
```

printed both cases as `ACCEPTED`. The malformed non-CR/LF syntax is silently emitted; no error announces a fix.

## Proposed change and new decision

Keep the issue’s goal, but require status strings and response header names to follow HTTP syntax rather than only blocking the two separators in its examples. The student must identify the governing grammar, implement distinct validation for status and field names, and preserve legal punctuation and the legal empty reason phrase. This is one modest protocol-level decision beyond the attempts’ demonstrated localized validation work.

The previous rewrite attempted this axis but its reference rejected `200 ` even though RFC 7230 permits an empty reason phrase. This rewrite corrects that implementation defect rather than adding another mechanism.

## Controls and replays

- From a fresh reset I applied the attempts’ literal CR/LF checks to both fields. `./sandbox grade` returned reward `0.0`: valid-name coverage passed, while invalid field-name syntax and invalid status syntax failed. This also exercises the behavior of upstream commit `4deab9f`.
- From another fresh reset I ran `pip install --upgrade waitress==2.1.2`, the newest release compatible with the image’s Python 3.7, and graded the checkout. `./sandbox grade` returned reward `0.0` with the same two failures because reinstalling a package does not repair the checked-out source.
- Attempt 01’s decisive path inspected issue-related history and added literal CR/LF checks. Replayed from a fresh reset, it stops at the new malformed-name and malformed-status cases and receives reward `0.0`; its valid-name case and all retained checks pass.
- Attempt 12 inspected commit `07e2157`, made the same two checks, and tested only the report’s examples. Its fresh replay receives reward `0.0` with the same two failed checks; the broad seed suite does not expose the additional syntax.

I expect **6 of 12** in the next group: copying the report or historical patch no longer suffices, while agents that inspect the pinned RFC can still finish with one localized edit and a short test loop.
