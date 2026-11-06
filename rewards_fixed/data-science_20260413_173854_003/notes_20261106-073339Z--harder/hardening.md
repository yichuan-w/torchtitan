# Hardening rationale

## Attempt evidence

All 12 current attempts passed. Their common strategy was to inspect the CSV and pipeline, replace `pipeline.py` with straightforward pandas cleanup and aggregation code, and run it. Every attempt used `pd.read_csv(filepath)` without an encoding argument. The successful runs therefore currently receive a CSV in pandas' default UTF-8 encoding and are spared diagnosing a real text-encoding mismatch.

The earlier mixed group confirms that aggregation semantics, rather than environment operation, was the old separator: `traces/revision/mixed/g1123-r2.jsonl` and `g1123-r9.jsonl` cleaned dates/categories/revenue but summed gross revenue instead of subtracting discounts; both failed. Current attempts all learned that decision. No rewarded attempt edited grading files, hard-coded expected totals, or skipped the requested pipeline repair.

## Primary sources and pin

- https://pandas.pydata.org/docs/reference/api/pandas.read_csv.html — the `read_csv` reference says its `encoding` parameter has default `utf-8` and selects the encoding used when reading.
- https://docs.python.org/3/library/codecs.html — Python's codec documentation says UTF-16 streams use a byte-order mark to indicate byte order and lists `utf_16` as the codec for all languages.
- https://pypi.org/pypi/pandas/2.2.0/json — PyPI reports version `2.2.0`; the Dockerfile already pins the upstream artifact as `pandas==2.2.0`, and that pin is retained.

## Reproduction in this environment

I converted the supplied CSV text to UTF-16 with its BOM inside the running image, then ran pandas 2.2.0's unchanged loader. The command was:

`Path('/app/data/sales.csv').write_text(Path('/tmp/sales-utf8.csv').read_text(), encoding='utf-16'); pd.read_csv('/app/data/sales.csv')`

It reproduced `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte`. Reading the same file with `pd.read_csv(..., encoding='utf-16')` produced shape `(200, 5)`, the five expected headers, and the first sales record intact.

## Student-facing change

The image will ship the real sales CSV as BOM-marked UTF-16 rather than UTF-8. The goal and output remain unchanged. A student must identify the encoding from the decode failure or file bytes and configure the existing pandas loader before doing the same cleanup and aggregation work demonstrated by the successful attempts. This is one modest operator/debugging step adjacent to their existing CSV inspection and pandas repair.

The decode exception identifies UTF-8 as the failed assumption but does not prescribe UTF-16. The intended decision remains discoverable with `file`, a hex/BOM inspection, or a deliberate decode test.

## Obvious-fix controls

- Error-directed tolerance attempt: I changed only date parsing to `errors='coerce'`, reran the pipeline, and graded it. It still stopped at `read_csv` with the UTF-8 `UnicodeDecodeError`; grade was `0.0`.
- First failure-recovery attempt: I upgraded pandas from 2.2.0 to the then-current 3.1.1 in the running sandbox, reran the pipeline, and graded it. The same UTF-8 `UnicodeDecodeError` remained; grade was `0.0`.

The intended fix edits `/app/pipeline.py`, which the user explicitly asks the student to repair and which is not protected.

## Repair

The previous wording requested a sum of `revenue`, while the shipped pipeline calls the measure net revenue and the grader expects revenue less discount. Earlier gross-summing attempts were consequently plausible but failed. The instruction will make the retained net-revenue definition explicit; this is recorded separately in `run/repair.json`.

## Replay and prediction

I replayed the full pipeline replacements from two successful current attempts, each after a fresh `./sandbox reset`, then ran their pipeline and `./sandbox grade`:

- `traces/attempt-01.jsonl` reaches its unchanged `pd.read_csv(filepath)` and stops with `UnicodeDecodeError` before producing the output. Grade was `0.0`; all nine scratch checks failed because `/app/output/monthly_revenue.csv` was absent.
- `traces/attempt-10.jsonl` stops at the same unchanged loader with the same exception. Grade was `0.0`; again all nine scratch checks failed because the promised output was absent.

This touches every observed current path while leaving a short, standard diagnosis available. I expect **6 of 12** in the next same-size group: about half should inspect the BOM or try an explicit encoding, while agents that immediately replace the transformation but retain the default loader will stop exactly as both replays did.
