# Seed verifier repair implementation plan

Goal: Repair demonstrated grading defects in the frozen 1,119-task seed release and publish verified replacements.

Architecture: Keep Daytona provisioning, Terminus reference execution, integrity checks and grading unchanged. An audit command reuses the existing runner to compare frozen positive and negative cases against original and repaired grading payloads.

1. Compare the two verifier audit documents and their linked evidence with the published task packages. Record confirmed source defects separately from evolved-only defects and invalid references.
2. Prepare minimal changes to the affected tests. Preserve public instructions and environment files. Keep original reference files; a reference that contradicts its public instructions requires a separate repair record before acceptance.
3. Add `torchtitan/experiments/rl/examples/tmax/evolution/seed_verifier_audit.py`. Store complete case inputs, hashes, expected rewards, terminal transcripts and grading diagnostics. Resume from immutable per-case results.
4. Run one case end to end through Daytona from the configured owner profile, verify result logging and resume, then run the bounded candidate batch. Each accepted repair must preserve a legitimate solution and reject its demonstrated wrong solution. Record any extra independent probes and their limits.
5. Build a versioned prepared JSONL and task archives from accepted repairs. Preserve the previous release. Publish the data and evidence to the existing Hugging Face repository, download them again and verify the training loader reads the repaired grading payloads.

Completion: Every candidate identified in the two audit documents and linked source-repair records has a recorded disposition. Accepted repairs have positive and negative execution evidence and are available to training. Unresolved specification or reference defects remain explicit and are not described as validated repairs.
