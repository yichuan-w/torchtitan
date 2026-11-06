# Hardening rationale

## Attempt evidence and current strategy

All 12 attempts earned reward 1. Their shared strategy was to inspect `raw_data.csv`, split each physical input line at commas (using `strchr`, `strtok`, or an equivalent loop), validate the resulting email with `<regex.h>`, aggregate in memory, write `clean_data.csv`, and bulk-import it with the SQLite CLI. Examples are `traces/attempt-01.jsonl` turns 1-10, `traces/attempt-07.jsonl` turns 5-18, and `traces/attempt-12.jsonl` turns 1-13. Attempt 07 also shows the student checking and repairing SQLite import mode after diagnostics, so the student already has a debug-and-retry habit. The input currently spares every attempt real CSV parsing: every field is unquoted, no field contains a comma, and every record is one physical line.

No rewarded trace violated the instruction or edited grading material. A separate adversarial trial later exposed a reusable-program verifier gap; that repair is recorded in `run/repair.json`.

## Primary sources and pinned artifact

- https://www.rfc-editor.org/rfc/rfc4180.txt — section 2 says that each field may be enclosed in double quotes, that fields containing commas and line breaks should be enclosed in double quotes, and that a double quote inside a quoted field is represented by two double quotes.
- https://www.sqlite.org/cli.html#csv — SQLite's CLI documentation says `.import` interprets non-ASCII input as RFC 4180 records and removes field quoting.

The image will fetch the RFC itself from `https://www.rfc-editor.org/rfc/rfc4180.txt`, verify SHA-256 `5d9ec035aa40c0108362cb767c9e2d08c30f7225cb2a4715f484cb13c29b5f21`, and retain it as `/usr/local/share/doc/rfc4180.txt`. This pins the upstream artifact that defines the introduced behavior.

## Proposed change and new decision

The supplied CSV will use valid RFC 4180 quoted fields, including a comma inside a quoted `DateString`, a CRLF inside another quoted `DateString`, and doubled quotes in an ignored date field. The public goal remains the same and will state that the supplied CSV follows RFC 4180. A correct C program must now distinguish record/field delimiters from commas, CRLFs, and quotes inside quoted fields before applying the existing email regex and aggregation. The old physical-line/comma splitter silently omits or corrupts valid rows, yielding wrong totals rather than announcing a fix.

This is one modest step beyond the attempts: they already write and debug a C ingestion pipeline, but must replace the easy-subset splitter with a small stateful CSV reader. The environment contains the standards document and malformed totals identify which records were lost; it contains no patch or note naming an implementation.

## Reproduction before task edit

On the seed image, SQLite 3.37.2 was confirmed with `sqlite3 --version`. A separate constraint reproduction used `.import` against a pre-populated primary-key table and observed `/tmp/in.csv:1: INSERT failed: UNIQUE constraint failed: UserTotals.MaskedEmail`; this was rejected as the hardening axis because the diagnostic names the immediate failure and importing into a staging table is too directly suggested by normal SQLite practice.

## Observed behavior in the revised image

After a fresh `./sandbox reset`, `sed -n '1,14l' /home/user/raw_data.csv` displayed the embedded comma, the embedded CRLF as two physical lines, and doubled quotes. A deliberately naive `fgets`/`strchr` parser reported `alice=49.50 bob=25.00 charlie=75.25 d=10.00`; SQLite's RFC-aware `.import` over the same file reported `alice.smith@gmail.com,200.0`, `bob_jones123@yahoo.com,225.0`, `charlie.brown@company.co.uk,75.25`, and `d@short.com,10.0`. Thus the documented behavior occurs here and the old parser silently loses exactly the quoted records.

## Plausible first fixes tried

1. I rebuilt the existing physical-line parser with ordinary compile warnings and regenerated both deliverables. It still produced totals 75.25, 49.50, 25.00, and 10.00; `./sandbox grade` returned reward 0 because the CSV content was wrong. Recompiling or regenerating does not reveal a diagnostic or repair the parser.
2. I replaced data processing with SQLite's own RFC-aware `.import` and SQL aggregation, the natural reach for a tool that already handles the input. It produced correct final values and the seed scratch verifier returned reward 1, exposing that the old verifier did not establish the required C implementation. I repaired the scratch verifier to delete `clean_data.csv`, execute `/home/user/process`, and compare its regenerated result. With that retained public requirement checked, the SQLite-only replacement returns reward 0 because the stale naive C executable regenerates wrong values. This is recorded as a verifier repair rather than counting a legal fix as a failed strategy.

The intended solution investigates RFC 4180 and implements quote-aware field parsing in the required C program. The reference workflow passes and remains substantially under the seed solution-size bound.

## Successful-attempt replays

- `traces/attempt-01.jsonl` replayed from a fresh reset. Its final splitter produced `c***@company.co.uk,75.25`, `a***@gmail.com,49.50`, `b***@yahoo.com,25.00`, `d***@short.com,10.00`. Grade reward was 0; `test_clean_data_csv` failed at the first data row, and the database inherited the same missing quoted transactions.
- `traces/attempt-02.jsonl` replayed from another fresh reset. It diagnosed its regex and rewrote the program several times but retained physical-line comma splitting, ending with the same 75.25/49.50/25.00/10.00 values. Grade reward was 0; `test_clean_data_csv` failed at the first data row, and the database content was correspondingly wrong.

Both paths now stop at the new CSV parsing decision rather than an unrelated setup issue. Given that all 12 prior attempts used this easy-subset strategy, but several demonstrated iterative debugging and one used `getline`, I expect **6 of 12** attempts to solve the revised task.
