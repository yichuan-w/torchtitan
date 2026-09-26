#!/usr/bin/env python3
"""Derive the v13-harbor prompt and schema from the frozen v13 pair, by explicit replacement.

v13-harbor is a PACKAGE-LAYOUT ADAPTATION, not a rubric revision. Harbor-style corpora (SWE-Smith,
SWE-Rebench, TerminalWorld) ship no setup.sh: the image is built by environment/Dockerfile and part of the
verifier lives in a companion file beside tests/test.sh. Every question, field, tier rule, enum and allOf
consistency rule is carried over byte-identical; only the file contract moves, and the schema moves exactly
as far as it must for an anchor into those files to be expressible.

Run it to regenerate both artefacts and print their shas; the replacement lists below ARE the changelog.

    python3 derive_v13_harbor.py [--check]     --check verifies the on-disk pair matches this derivation
"""
import hashlib, json, os, re, sys

V13 = os.path.dirname(os.path.abspath(__file__)) + "/.."
SRC_PROMPT, SRC_SCHEMA = f"{V13}/v13_prompt.md", f"{V13}/output_schema_v13.json"
OUT_PROMPT = f"{V13}/harbor/v13_harbor_prompt.md"
OUT_SCHEMA = f"{V13}/harbor/output_schema_v13_harbor.json"
SRC_PROMPT_SHA = "96fbc03bc81ab8760aa011ad53ba25fa95490d35dff291d4d905d52d638049f6"
SRC_SCHEMA_SHA = "f8a47506d0d060c12d149e427d59f3b211f03e56cb20bcfb9b74e0c8a3d2db1e"

# the union of files any harbor corpus may be told to read; the renderer names only the corpus's own
ANCHOR_FILES = ["instruction.md", "tests/test.sh", "tests/test_state.py", "tests/test.patch",
                "environment/bug.patch", "environment/Dockerfile"]

PROMPT_EDITS = [
 # (what, from, to) -- each must match exactly once
 ("the file contract, and the read order",
  """online evolve loop (§3 fact 10). For each task id in your batch you read three files IN FULL, in this order, and
nothing else about the task:

    {TASK_ROOT}/<task_id>/instruction.md    the specification the graded agent is shown
    {TASK_ROOT}/<task_id>/setup.sh          what the image bakes in before the agent starts
    {TASK_ROOT}/<task_id>/tests/test.sh     the verifier that scores the agent's rollout
""",
  """online evolve loop (§3 fact 10). For each task id in your batch you read the task's files IN FULL, in this order,
and nothing else about the task:

    {TASK_ROOT}/<task_id>/instruction.md            the specification the graded agent is shown
    {TASK_ROOT}/<task_id>/environment/Dockerfile    what the image bakes in before the agent starts
    {TASK_ROOT}/<task_id>/tests/test.sh             the verifier that scores the agent's rollout
{COMPANION_LINE}
This corpus ships no setup.sh: the image is built by that Dockerfile, and the files it and the build create are the
shipped state. Where this rubric says "the files", it means exactly the files listed above and no others.
"""),
 ("never execute the task",
  "the task's setup.sh, tests/test.sh or any task program, and you use no network.",
  "the task's Dockerfile build, tests/test.sh, its verifier companion or any task program, and you use no network."),
 ("shipped state",
  "- **shipped state** — the image exactly as setup.sh, the seeds and the ENTRYPOINT leave it, with no agent action.",
  "- **shipped state** — the image exactly as the Dockerfile build, the seeds and the ENTRYPOINT leave it, with no\n  agent action."),
 ("harness fact 1",
  """1. `setup.sh` ran at image build time; everything it created — compiled programs, generators, corpora, golden files,
   repositories with their history — is in the container during rollout, together with seeds uploaded to `/workspace`
   and whatever the detached ENTRYPOINT creates (you do not speculate about whether the harness ran it). The network
   is open during rollout. setup.sh itself is not shown to the agent; the files it writes are.""",
  """1. The `environment/Dockerfile` build ran at image build time; everything it created — compiled programs,
   generators, corpora, golden files, repositories with their history — is in the container during rollout, together
   with seeds uploaded to `/workspace` and whatever the detached ENTRYPOINT creates (you do not speculate about
   whether the harness ran it). The network is open during rollout. The Dockerfile itself is not shown to the agent;
   the files its build writes are."""),
 ("step 2 inventory",
  "The inventory is the `programs` list of `{CANDIDATES_PATH}`, plus every program setup.sh compiles, copies or marks",
  "The inventory is the `programs` list of `{CANDIDATES_PATH}`, plus every program the Dockerfile build compiles, copies or marks"),
 ("removed_before_rollout",
  "| `removed_before_rollout` | setup.sh deletes it before the image is finalised |",
  "| `removed_before_rollout` | the Dockerfile build deletes it before the image is finalised |"),
 ("the A4 example",
  "whose generator setup.sh deleted, with no hash",
  "whose generator the Dockerfile build deleted, with no hash"),
 ("evidence_file in the output block",
  '"evidence_file": "instruction.md | setup.sh | tests/test.sh",',
  '"evidence_file": "one of the files named at the top of this prompt",'),
]


def derive_prompt(src: str) -> str:
    out = src
    for what, a, b in PROMPT_EDITS:
        assert out.count(a) == 1, f"{what}: matched {out.count(a)} times, expected 1"
        out = out.replace(a, b, 1)
    # "three files" is now the file list at the top; the phrase would be wrong in six places
    out = out.replace("the three files", "the files")
    out = out.replace("three files IN FULL", "the files IN FULL")
    # the only setup.sh left must be the sentence that says the corpus does not have one: an assertion of
    # "the string is gone" would have failed on my own replacement text, which is the wrong thing to check
    left = [l for l in out.splitlines() if "setup.sh" in l]
    assert left == ["This corpus ships no setup.sh: the image is built by that Dockerfile, and the files it and "
                    "the build create are the"], f"unexpected setup.sh mention(s): {left}"
    return out


def derive_schema(src: dict) -> dict:
    s = json.loads(json.dumps(src))          # deep copy; the source is never touched
    s["properties"]["task_id"] = {"type": "string", "minLength": 1}
    pat = "^(" + "|".join(re.escape(f) for f in ANCHOR_FILES) + r"):[0-9]+(-[0-9]+)?\b"
    n = 0

    def rewrite(node):
        """EVERY anchor pattern, at any depth. The first version walked properties[*].anyOf only and missed
        the one nested in excluded_material.items.basis -- which a judge then hit on tw_287590 by anchoring
        into environment/Dockerfile exactly as the prompt told it to. A schema edit scoped to the shape you
        happen to remember is not a schema edit."""
        nonlocal n
        if isinstance(node, dict):
            if str(node.get("pattern", "")).startswith("^(instruction"):
                node["pattern"] = pat
                n += 1
            for v in node.values():
                rewrite(v)
        elif isinstance(node, list):
            for v in node:
                rewrite(v)

    rewrite(s)
    assert n == 12, f"expected 12 anchor patterns, rewrote {n}"
    assert s["properties"]["evidence_file"]["enum"] == ["instruction.md", "setup.sh", "tests/test.sh"]
    s["properties"]["evidence_file"]["enum"] = list(ANCHOR_FILES)
    return s


def main() -> int:
    assert hashlib.sha256(open(SRC_PROMPT, "rb").read()).hexdigest() == SRC_PROMPT_SHA, "v13_prompt.md moved"
    assert hashlib.sha256(open(SRC_SCHEMA, "rb").read()).hexdigest() == SRC_SCHEMA_SHA, "the v13 schema moved"
    prompt = derive_prompt(open(SRC_PROMPT).read())
    schema = derive_schema(json.load(open(SRC_SCHEMA)))
    text = json.dumps(schema, indent=1, sort_keys=True) + "\n"
    if "--check" in sys.argv:
        ok = (open(OUT_PROMPT).read() == prompt and open(OUT_SCHEMA).read() == text)
        print("on-disk v13-harbor matches this derivation" if ok else "MISMATCH: regenerate")
        return 0 if ok else 1
    open(OUT_PROMPT, "w").write(prompt)
    open(OUT_SCHEMA, "w").write(text)
    for p in (OUT_PROMPT, OUT_SCHEMA):
        print(f"  {os.path.basename(p)}  sha256 {hashlib.sha256(open(p,'rb').read()).hexdigest()}")
    print(f"  derived from {SRC_PROMPT_SHA[:16]}… / {SRC_SCHEMA_SHA[:16]}… by "
          f"{len(PROMPT_EDITS)} prompt edits and 3 schema edits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
