# Frozen audit-workflow launch scripts (V2–V6)

These are the **Workflow launch scripts** (the JS passed to the Claude Code
`Workflow` tool) that drove each version of the TMAX rubric audit. They were
previously only under
`tb_check/rebench_tmax_recovery_2026-09-02/work/tmax-audit-files/`, which is
**not a git repo** (single-disk, no backup). Archived here for durability.

The matching **prompt/core** text for each version already lives one level up in
`rubrics/reference/` (v2_agent_prompt.md, v3_agent_prompt.md, v4_prompt.md,
v4b2_prompt.md, v5_core.md/v5_prompt.md, v6_core.md/v6_prompt.md). The V7-era
workflow scripts are separate, already tracked under
`reaudit_398/tools/workflows/` (tournament_workflow.js, spec_workflow.js,
wf_axisb_sec_secondread.js).

## Version → launch script → prompt/core

| version | launch script(s) here | prompt/core (in ../) |
|---|---|---|
| V2 | `v2_b1_workflow.js`, `v2_b2_workflow.js`, `v2_b2r_workflow.js` | `v2_agent_prompt.md`, `v2_agent_prompt_v2b.md`, `v2_output_contract.md` |
| V3 | `v3_workflow.js` | `v3_agent_prompt.md` |
| V4 | `v4_workflow.js` | `v4_prompt.md` |
| V4b2 | `v4b2_a.js`, `v4b2_b.js` | `v4b2_prompt.md` |
| V4 disputed-180 re-judge | `v4_disputed.js` | `v4_prompt.md` (same rubric, disputed pool) |
| V5 | `v5_workflow.js` | `v5_core.md`, `v5_prompt.md` |
| V6 | `v6_b1_workflow.js` (axis B1), `v6_cal_workflow.js` (calibration) | `v6_core.md`, `v6_prompt.md` |
| V7 | (in `tools/workflows/`) | `rubrics/v7_prompt.md`, `rubrics/v7_rubric_delta.md` |

## V1

V1 was the initial **RIVER rubric** implementation (arXiv 2608.22631, App. C,
8-category taxonomy) — see `hf_Tmax-Tasks-Clean_docs_20260902/AUDIT_PROMPT.md:15`
("v1 implemented the RIVER rubric ... faithfully"). It has **no standalone
prompt or workflow artifact** on disk; only the paper itself survives, at
`tb_check/rebench_tmax_recovery_2026-09-02/work/tmax-audit-files/tmax-paper.{pdf,html}`.

Source of truth for the version narrative and sub-versions (v3.1/3.2/3.3,
V7-delta v1/v2/v3, …): `rubrics/CHANGELOG.md`.
