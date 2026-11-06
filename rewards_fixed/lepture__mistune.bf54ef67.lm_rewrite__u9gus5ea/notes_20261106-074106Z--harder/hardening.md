# Hardening rationale

## Observed successful strategy

All 12 current attempts passed. They inspect `HTMLRenderer.block_code`, add or reuse an escaping helper, run the selected CommonMark entity-reference test, then iterate until both the reported special-character output and the existing suite pass. For example, `traces/attempt-04.jsonl` turns 22-24 first replace `escape_text(lang)`, then run the suite; turns 55-67 add the missing context-specific characters and validate a tilde-fence reproduction. `traces/attempt-05.jsonl` turns 45-61 similarly introduce `escape_attr`, then add `unescape` only after CommonMark example 34 exposes the double-encoding. The successful work is legitimate; no attempt edits the grader or hard-codes a verifier answer.

## Primary sources and pinned artifact

- https://markupsafe.palletsprojects.com/en/stable/escaping/ — fetched documentation says `escape()` replaces `&`, `<`, `>`, `'`, and `"` with safe sequences for HTML text.
- https://raw.githubusercontent.com/pallets/markupsafe/3.0.3/src/markupsafe/__init__.py — fetched 3.0.3 source says the same and shows that `escape()` delegates plain strings to `_escape_inner`; the pinned release intentionally does not encode the backtick required by this task's existing expected output.
- https://pypi.org/pypi/MarkupSafe/3.0.3/json — fetched PyPI metadata identifies the CPython 3.10 x86-64 manylinux wheel with SHA-256 `f42d0984e947b8adf7dd6dde396e720934d12c506ce84eea8476409563607591`.
- https://raw.githubusercontent.com/commonmark/commonmark-spec/0.31.2/spec.txt — fetched CommonMark 0.31.2 says entity references are recognized in fenced-code info strings and its example 34 renders `f&ouml;&ouml;` as the language `föö`; fetched file SHA-256 is `257c41ad946f7a1414a499aca402a1aa8fdac3678532266611348c1cf54f4b80`.
- https://raw.githubusercontent.com/lepture/mistune/v3.0.2/src/mistune/renderers/html.py — fetched source for the version in the workspace applies `safe_entity` before extracting and rendering the language class, establishing the retained entity-decoding behavior.

The Dockerfile will pin `MarkupSafe==3.0.3` by version and hash. The planted renderer will use that real encoder for the language value. This is an attractive, plausible implementation that safely encodes the usual HTML metacharacters but remains wrong for this task: it leaves the required backtick representation unchanged and, unless entity decoding is ordered correctly, does not preserve CommonMark info-string semantics.

## Reproduction before editing

On a fresh seed reset, after `pip install -e .`, this command was run:

`python -c 'from mistune import create_markdown; md=create_markdown(); ...'`

Observed output for a tilde-fenced language `html&"<>\x27` + backtick was:

`<pre><code class="language-html&amp;&quot;&lt;&gt;'`">some code\n</code></pre>`

Observed output for `f&ouml;&ouml;` was:

`<pre><code class="language-f&amp;ouml;&amp;ouml;">foo\n</code></pre>`

Thus the special characters are not fully encoded and the standards fixture is double-encoded in this environment.

## New decision and scope

The student must now recognize that a reputable general HTML encoder already present at the failure site is only part of the operation, determine which retained CommonMark transformation must happen before attribute encoding, and account for the task's existing backtick contract without globally changing text escaping. The old attempts already demonstrate the adjacent skills (inspecting renderer/parser flow, introducing a scoped helper, and using a failing conformance case), so this is one modest debugging decision rather than unrelated work. The original wording and deliverable stay unchanged.

The direct public example was not checked by the seed verifier; the scratch checker will repair that omission while retaining CommonMark example 34. The reference workflow will also be corrected to satisfy the full public output rather than merely reverse the seed patch.

## Trial fixes and grades

1. On a fresh reset, forcibly reinstalling/upgrading `MarkupSafe==3.0.3` left the planted source unchanged. `./sandbox grade` returned reward `0.0`; the direct output assertion failed.
2. On another fresh reset, I applied the first scoped escaping-only repair used by the attempts: normalize MarkupSafe's numeric quote entities and add backtick encoding, but do not decode the info string first. The direct output then passed, but `./sandbox grade` returned reward `0.0` because CommonMark example 34 produced `language-f&amp;ouml;&amp;ouml;` instead of `language-föö`.

Neither failure message dictates the complete fix. The second requires correlating the retained conformance failure with parser/renderer transformation order.

## Attempt replays and prediction

I replayed the decisive command path of two successful attempts, each from a fresh reset:

- `attempt-04`: its initial inspection still finds the renderer and utility, but its first real edit (`escape_text(lang)` to a scoped `escape_attr(lang)`) no longer applies because the planted renderer already uses the pinned encoder. Replaying the equivalent scoped escape-only change leaves both checks failing initially; after normalizing the reported special-character output, grading still fails CommonMark example 34 because `f&ouml;&ouml;` remains double-encoded. It therefore stops at the new transformation-order decision rather than passing via its old first fix.
- `attempt-05`: its early scoped helper/renderer edits are likewise redundant against the pinned encoder. Replaying the equivalent `html.escape`-plus-backtick implementation passes the direct special-character assertion but `./sandbox grade` gives reward `0.0`; CommonMark example 34 fails with `language-f&amp;ouml;&amp;ouml;` instead of `language-föö`. Its later original-path decision to inspect that fixture and add `unescape` remains capable of reaching the fix.

Both replays fail the direct output check until encoder-specific entity normalization is handled, and fail the retained CommonMark check until decoding is ordered before encoding. The second trace explicitly made the latter decision in the original run, while the first arrived there through broader helper changes. I expect this extra pinned-library and ordering diagnosis to yield **6 of 12** in the next same-size group.
