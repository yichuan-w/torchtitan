"""Stage the Hugging Face release of the TB2.1 top-10 selection (the "-full" release).

Writes release/ in the run directory: the final and per-group top-10s as
parquet, the 89 K3 judgement files, contamination and dedup records, the
blind audit, provenance, and a dataset card with one row per source. Runs
offline; uploading is a separate step.

Instruction text is included only for sources whose Hugging Face card
declares a licence that permits redistribution; for the rest a row carries
the source repository, revision and task id. Turing Labs tasks are private
and never leave as text or original id. Every staged file is checked for
internal paths and private names before anything is uploaded.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict

import pyarrow as pa
import pyarrow.parquet as pq

from common import GROUPS, ROOT, extra_docs, read_jsonl, texts_of

STAGE = ROOT / "release"
REDISTRIBUTABLE = {"apache-2.0", "mit", "cc-by-4.0", "odc-by"}
PRIVATE = {"Turing-Labs"}
GITHUB = {"TermiGen", "LHTB", "SETA"}  # hosted on GitHub, not the Hugging Face Hub; licences read from GitHub
CODE = "https://github.com/yichuan-w/torchtitan/tree/andy/tb21-top10-pool57/torchtitan/experiments/rl/examples/tmax/data-selection/agentic_pool57"
V2 = "https://github.com/yichuan-w/torchtitan/tree/yichuan/qwen35-port-cotrain/torchtitan/experiments/rl/examples/tmax/data-selection/agentic"
EXCLUDED_BENCH = {
    "TB2-Verified": "is TB2.1 (verified TB2.0)", "TAB": "TB2.1 tasks rewritten for an alignment benchmark", "tb21_docs": "the TB2.1 queries",
    "AgentWorldBench": "single observation turns, not tasks", "WorldVQA": "image QA, not terminal tasks",
}


def sources_table():
    rows = {}
    for line in (ROOT / "release_sources.txt").read_text().splitlines():
        src, repo, rev, kind, lic = line.split("|")
        rows[src] = {"source": src, "repo": repo, "revision": rev[:12], "revision_full": rev, "kind": kind, "license": lic}
    rows["Turing-Labs"] = {"source": "Turing-Labs", "repo": "private delivery (2026-09-20)", "revision": "-",
                           "kind": "environment", "license": "private"}
    return rows


def main():
    work = ROOT / "work"
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "data").mkdir(parents=True)
    (STAGE / "judgements").mkdir()
    (STAGE / "audit").mkdir()
    src = sources_table()
    group_of = {s: g for g, ss in GROUPS.items() for s in ss}
    queries = {q["query_id"]: q for q in read_jsonl(work / "queries.jsonl")}
    turing_names = {d["task_id"].split("/")[-1] for d in extra_docs().values()}
    turing_names |= set((work / "extra/turing/oracle_failed.txt").read_text().split())

    def open_text(sources):
        lic = [src[s]["license"] for s in sources if s in src]
        return any(l in REDISTRIBUTABLE for l in lic) and not (set(sources) & PRIVATE)

    def table(path):
        out = []
        rows = list(csv.DictReader(path.open()))
        need = [r["id"] for r in rows if open_text(r["sources"].split("|"))]
        text = texts_of(sorted(set(need)))
        for r in rows:
            sources = r["sources"].split("|")
            private = bool(set(sources) & PRIVATE)
            ids = [] if private else [
                {"source": s, "task_id": t, "repo": src.get(s, {}).get("repo", ""), "revision": src.get(s, {}).get("revision", "")}
                for s, t in (x.split(":", 1) for x in r["original_ids"].split("|") if x)]
            out.append({
                "query_id": r["query_id"].removeprefix("tb21__"), "rank": int(r["rank"]), "group": r["group"],
                "id": r["id"], "sources": sources, "original_ids": ids, "family_size": int(r["family_size"]),
                "verifier": r["verifier"], **{k: int(r[k]) for k in ("skill", "domain", "task_form", "overall")},
                "reason": "" if private else r["reason"],
                "instruction": text.get(r["id"]) if open_text(sources) else None,
                "instruction_withheld": None if open_text(sources) else (
                    "private source" if private else "source declares no redistributable licence; fetch by original_ids"),
            })
        return out

    top = table(ROOT / "results/tb21_top10.csv")
    dense = {(r["query_id"], r["id"]): r for r in csv.DictReader((ROOT / "results/tb21_top10_dense.csv").open())}
    for r in top:
        d = dense[("tb21__" + r["query_id"], r["id"])]
        r["dense_cos"] = float(d["dense_cos"]); r["dense_rank_in_pool"] = int(d["dense_rank_in_pool"])
    per = table(ROOT / "results/tb21_per_group_top10.csv")
    pq.write_table(pa.Table.from_pylist(top), STAGE / "data/top10.parquet")
    pq.write_table(pa.Table.from_pylist(per), STAGE / "data/per_group_top10.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"query_id": q.removeprefix("tb21__"), "instruction": v["instruction"]} for q, v in queries.items()]),
        STAGE / "data/queries.parquet")

    for f in sorted((ROOT / "rerank").glob("tb21__*.json")):
        if ".invalid-" in f.name:
            continue
        (STAGE / "judgements" / f.name.removeprefix("tb21__")).write_text(
            json.dumps(redact_private(json.loads(f.read_text())), indent=1, ensure_ascii=False))

    contamination = {
        "lexical_screen": [json.loads(l) for l in (work / "contamination.jsonl").open()],
        "dense_screen": json.loads((ROOT / "results/dense_contamination.json").read_text()),
        "k3_near_copy_notes": json.loads((ROOT / "results/summary.json").read_text())["near_copy_flags"],
    }
    (STAGE / "contamination.json").write_text(json.dumps(contamination, indent=1, ensure_ascii=False))
    for f in sorted((ROOT / "audit").glob("*.json")) + [ROOT / "audit/score_pilot10.txt"]:
        shutil.copyfile(f, STAGE / "audit" / f.name)

    dedup = json.loads((work / "dedup_summary.json").read_text())
    exported = json.loads((work / "export_counts.json").read_text())["per_source"]
    final_slots, rank1, group_slots = Counter(), Counter(), Counter()
    for r in top:
        for s in set(r["sources"]):
            final_slots[s] += 1
            rank1[s] += r["rank"] == 1
    for r in per:
        for s in set(r["sources"]):
            group_slots[s] += 1
    table_rows = []
    for name, info in sorted(src.items(), key=lambda kv: (-final_slots[kv[0]], kv[0])):
        stats = dedup["per_source"].get(name, {})
        if name in EXCLUDED_BENCH:
            status = "excluded: " + EXCLUDED_BENCH[name]
        n_in = exported.get(name, 38 if name == "Turing-Labs" else 0)
        # A source is a candidate when any of its rows were exported; AgentTrove holds
        # both environment and trajectory rows and only the former were exported.
        if name in EXCLUDED_BENCH:
            pass
        elif n_in:
            status = "candidate"
            if info["kind"] in ("trajectory", "step"):
                info = {**info, "kind": "environment + trajectory"}
        else:
            status = "not a candidate: " + info["kind"] + " data"
        table_rows.append({**info, "status": status, "group": group_of.get(name, "-") if status == "candidate" else "-",
                           "rows_exported": n_in, "representatives": stats.get("representative", n_in if name == "Turing-Labs" else 0),
                           "tb21_overlap_removed": stats.get("contaminated", 0),
                           "per_group_slots": group_slots[name], "final_slots": final_slots[name], "rank1": rank1[name]})
    (STAGE / "sources.json").write_text(json.dumps(table_rows, indent=1))

    summary = json.loads((ROOT / "results/summary.json").read_text())
    provenance = {
        "code": CODE, "code_commit": (ROOT / "CODE_COMMIT").read_text().strip(), "method_origin": V2,
        "rubric_sha256": hashlib.sha256((ROOT / "AGENT_SPEC_V2.md").read_bytes()).hexdigest(),
        "queries": "harborframework/terminal-bench-2.1 @ e92c0b648708 (89 tasks)",
        "search_pool": "terminal-search-pool-20261002: 3,403,604 unique instructions from 57 sources",
        "dedup": dedup["params"], "candidates_after_dedup": dedup["representatives"] + 38,
        "retrievers": ["tfidf_word 1-2gram", "tfidf_char 3-5gram (sources <= 200k docs)", "bm25 1-2gram",
                       "dense Qwen/Qwen3-Embedding-8B @ 1d8ad4ca9b3d, 2048 tokens incl. EOS"],
        "per_retriever_top": 25, "fusion": "reciprocal rank, k=60", "packet": {"shown_per_group": 16, "snippet_chars": 460},
        "reranker": "moonshotai/Kimi-K3, reasoning effort high, one agent per query with corpus search tools",
        "validation": "every id exists and sits in its group; 10 per group and 10 final; scores identical between lists; no FREE verifier in final",
        "results": {k: summary[k] for k in ("final_group_slots", "rank1_group_wins", "final_verifier",
                                            "mean_scores_final", "overall_histogram", "unique_selected")},
    }
    (STAGE / "provenance.json").write_text(json.dumps(provenance, indent=1))
    (STAGE / "README.md").write_text(card(table_rows, provenance, summary))

    # Private names (delivery directory, task names) are read from beside the data, not written here.
    private_terms = (work / "extra/turing/private_terms.txt").read_text().split()
    leaks = ["/data/yichuan_wang", "/scratch/gpfs", "/Users/andyl", "inferact", "K3_API", "terminal-rl/data"] \
        + private_terms + sorted(turing_names)
    for p in STAGE.rglob("*"):
        if not p.is_file():
            continue
        body = json.dumps(pq.read_table(p).to_pylist(), ensure_ascii=False) if p.suffix == ".parquet" else p.read_text()
        body = scrub(body, p)
        hit = [w for w in leaks if w.lower() in body.lower()] + re.findall(r"hf_[A-Za-z0-9]{30,}", body)
        assert not hit, (str(p.relative_to(STAGE)), hit)
    print(json.dumps({"files": sorted(str(p.relative_to(STAGE)) for p in STAGE.rglob("*") if p.is_file()),
                      "top10_rows": len(top), "per_group_rows": len(per),
                      "text_withheld_final": sum(r["instruction"] is None for r in top)}, indent=1))


PRIVATE_ID = re.compile(r"\bx\d{3}\b")


def redact_private(j):
    """The reranker's reasons and notes describe the private tasks it scored; keep the ids, drop the descriptions."""
    for lst in list(j.get("per_source", {}).values()) + [j.get("final_top10", [])]:
        for e in lst:
            if PRIVATE_ID.fullmatch(e["task_id"]):
                e["reason"] = "private task (Turing Labs); not described"
    for k in ("notes", "verifier_notes"):
        if isinstance(j.get(k), str) and PRIVATE_ID.search(j[k]):
            j[k] = " ".join(x for x in re.split(r"(?<=[.;])\s+", j[k]) if not PRIVATE_ID.search(x))
    return j


def scrub(body, path):
    """Judgement files quote the agents' tool calls, which carry the run directory; strip it in place."""
    if path.parent.name != "judgements":
        return body
    clean = re.sub(r"/data/yichuan_wang/[A-Za-z0-9_./-]*?(tools\.py)", r"\1", body)
    clean = re.sub(r"/data/yichuan_wang/[A-Za-z0-9_./-]+", "<run-dir>", clean)
    if clean != body:
        path.write_text(clean)
    return clean


def card(rows, prov, summary):
    def link(r):
        if r["source"] in GITHUB:
            return f"[{r['repo']}](https://github.com/{r['repo']}/tree/{r['revision_full']}) (GitHub)"
        if r["repo"].startswith("http"):
            return f"[{r['repo'].split('/')[-1]}]({r['repo']}) (Harbor hub)"
        if re.fullmatch(r"[\w.-]+/[\w.-]+", r["repo"]):
            return f"[{r['repo']}](https://huggingface.co/datasets/{r['repo']})"
        return r["repo"]

    head = ("| Source | Repository | Revision | Licence | Kind | Status | Group | Rows | After dedup | TB2.1 overlap removed "
            "| Per-group slots | Final slots | Rank-1 |\n|" + "---|" * 13 + "\n")
    body = "".join(
        f"| {r['source']} | {link(r)} | `{r['revision']}` | {r['license']} | {r['kind']} | {r['status']} | {r['group']} "
        f"| {r['rows_exported']:,} | {r['representatives']:,} | {r['tb21_overlap_removed']} | {r['per_group_slots']} "
        f"| {r['final_slots']} | {r['rank1']} |\n" for r in rows)
    res = prov["results"]
    groups = ", ".join(f"{g} {n}" for g, n in sorted(res["final_group_slots"].items(), key=lambda x: -x[1]))
    n_cand = sum(r["status"] == "candidate" for r in rows) - 1  # minus the private set
    turing = next(r for r in rows if r["source"] == "Turing-Labs")
    return CARD.format(table=head + body, n_cand=n_cand, turing_final=turing["final_slots"], groups=groups, unique=res["unique_selected"],
                       commit=prov["code_commit"], code=prov["code"], v2=prov["method_origin"],
                       cands=f"{prov['candidates_after_dedup']:,}")


CARD = """---
license: other
task_categories:
- text-generation
tags:
- terminal
- agents
- reinforcement-learning
- data-selection
configs:
- config_name: top10
  data_files:
  - split: tb21
    path: data/top10.parquet
- config_name: per_group_top10
  data_files:
  - split: tb21
    path: data/per_group_top10.parquet
- config_name: queries
  data_files:
  - split: tb21
    path: data/queries.parquet
---

# Terminal-Bench 2.1: ten nearest training tasks per task, full pool

For each of the 89 Terminal-Bench 2.1 tasks, the ten training tasks that would most help a model solve it, chosen from {cands} candidate tasks drawn from {n_cand} public training corpora and benchmarks plus one private set. The method is the agentic selection from [TB2.1 data-selection v2]({v2}), run over a pool 25 times the size of v2's 76,786 tasks.

## Files

| Path | Contents |
|---|---|
| `data/top10.parquet` | 890 rows, ten per TB2.1 task, ranked, with the three axis scores, a reason, the Qwen3-Embedding-8B cosine to the query and the task's rank by that cosine in the whole pool |
| `data/per_group_top10.parquet` | 7,120 rows: the best ten from each of eight source groups per TB2.1 task |
| `data/queries.parquet` | the 89 TB2.1 instructions |
| `judgements/*.json` | the reranking agent's full record per TB2.1 task: task profile, every search it ran, per-group lists, final list, notes |
| `contamination.json` | rows removed as copies of TB2.1, pairs flagged by dense similarity, and the agent's near-copy notes |
| `sources.json` | the table below as data |
| `audit/` | blind second-opinion audit of ten tasks (see below) |
| `provenance.json` | revisions, models, parameters and the code commit |

Each row names the task by `sources` (every dataset that carries it) and `original_ids` (source dataset, task id, repository, revision for each). `family_size` counts near-duplicates merged into it. `instruction` holds the task text only when at least one source declares a licence that permits redistribution (Apache-2.0, MIT, CC-BY-4.0, ODC-By); otherwise it is null and `instruction_withheld` says why, and the text is in the source repository under the listed id. `verifier` grades how hard a task's tests are to pass without solving it, read from its test files by the v2 heuristic: `strong`, `ok`, `weak` and `behavioral` from most to least constraining, `repo-tests` for a project's own suite, `FREE` for tests that existence checks alone satisfy (never selected), and `unknown` for sources whose tests were never graded. Tasks whose `sources` include `Terminal-Wrench` have verifier exploits recorded in [few-sh/terminal-wrench](https://huggingface.co/datasets/few-sh/terminal-wrench); check those verifiers before relying on their reward.

## Sources

One row per dataset in the search pool. `Rows` is the number of unique instruction texts exported and `After dedup` the representatives that remained candidates. `Per-group slots` counts rows of `per_group_top10.parquet`, `Final slots` rows of `top10.parquet` and `Rank-1` rows of `top10.parquet` at rank 1, each counting the rows whose `sources` include the dataset; a task carried by two sources counts once for each, so `Rank-1` sums to more than 89.

{table}
Turing Labs is a private delivery used in the authors' training runs. Its tasks were candidates and fill {turing_final} final slots; those rows carry neither text nor original ids.

## Method

1. Export every environment-type row and every benchmark that is not TB2.1 itself. TB2-Verified, TAB and the TB2.1 tasks are excluded; trajectory and step data are not tasks and are not candidates.
2. Remove copies of TB2.1: a row is dropped when a task id names a TB2.1 task, when its normalised text equals one, or when it contains at least half of a TB2.1 task's word 5-grams; 15 rows were removed.
3. Merge near-duplicates with MinHash (64 hashes, 16 bands of 4) at estimated Jaccard 0.8 on word 5-grams. One representative is kept per family. The threshold also merges tasks built from one long template with a short varying part.
4. Recall, per source: TF-IDF on words and character n-grams, BM25, and Qwen3-Embedding-8B cosine, the top 25 of each, fused by reciprocal rank. Sources are folded into eight groups for presentation to the reranker.
5. Rerank with Kimi-K3, one agent per TB2.1 task, using the v2 rubric (skill, domain and task form, 1 to 5 each, and an overall judgement) and tools to search the whole pool. Every output is validated mechanically; failures are rerun with the validation error.

Code: [`agentic_pool57`]({code}), commit `{commit}`.

## Results

{unique} distinct tasks fill the 890 slots. Final slots by group: {groups}.

- A blind audit on ten TB2.1 tasks gave a different judge, Claude, fifty shuffled candidates per task: the reranker's ten, the twenty highest dense-similarity tasks it did not pick, and the twenty best of its own runners-up. The judge kept 72 of the 100 picks; the reranker's first and second choices were never replaced, and the disagreements fell in ranks five to ten.
- Same-template siblings of a TB2.1 task cannot be separated from copies by dense similarity alone: the known copy of `polyglot-c-py` scores 0.833 and siblings score 0.77 to 0.83. Four selected tasks score at least 0.80; three of them are the TB2.1 task with changed parameters (`train-fasttext`, `git-leak-recovery`, `vulnerable-secret`) and are listed in `contamination.json`. Decide whether to drop them before training if TB2.1 is your evaluation set.
"""


if __name__ == "__main__":
    main()
