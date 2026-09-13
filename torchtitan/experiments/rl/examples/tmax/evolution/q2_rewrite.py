#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Run a frozen controlled rewrite through the existing author and verifier roles."""

import argparse
import fcntl
import hashlib
import importlib.metadata
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from q2_prepare import freeze


def subscription_driver(module, settings):
    profile = Path(settings["codex_profile"])
    binary = Path(settings["codex_binary"])
    assert profile.joinpath("auth.json").is_file() and binary.is_file()
    module._codex_bin = lambda: binary

    def environment(session):
        env = module._harness_env()
        env["CODEX_HOME"] = str(profile)
        env.pop("OPENAI_API_KEY", None)
        env.pop("OPENAI_BASE_URL", None)
        return env

    def command(cwd, resume=None):
        args = [str(binary), "exec"]
        if resume:
            args += ["resume", resume]
        args += [
            "--ignore-user-config",
            "--ignore-rules",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "-c",
            "features.apps=false",
            "-c",
            "model_provider=openai",
            "-c",
            f"model_reasoning_effort={module.CODEX_EFFORT}",
        ]
        if not resume:
            args += ["-C", str(cwd)]
        return args + ["-m", module.CODEX_MODEL, "-"]

    original = module._run_codex

    def run(session, cwd, prompt, *, resume=None):
        try:
            return original(session, cwd, prompt, resume=resume)
        finally:
            ids = {resume} if resume else set()
            if session.dir.stdout.exists():
                for line in session.dir.stdout.read_text().splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("type") == "thread.started":
                        ids.add(event["thread_id"])
            for thread_id in ids:
                for source in (profile / "sessions").rglob(f"*{thread_id}.jsonl"):
                    target = (
                        session.dir.codex_home
                        / "sessions"
                        / source.relative_to(profile / "sessions")
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)

    module._codex_env = environment
    module._codex_cmd = command
    module._run_codex = run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    config = json.loads((campaign / "input.json").read_text())
    settings = json.loads((campaign / "execution.json").read_text())
    item = config["tasks"][args.run_id]
    checkout = Path(__file__).resolve().parents[6]
    revision = subprocess.check_output(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    assert revision == settings["code_revision"]
    assert not subprocess.check_output(
        ["git", "-C", str(checkout), "diff", "HEAD"], text=True
    )
    os.environ.update(
        TRL_BASE=settings["project_root"],
        TRL_TT=str(checkout),
        PYTHONPATH=str(checkout),
        TRL_VENV_PY=sys.executable,
        SYNTH_MODEL=config["model"],
        SYNTH_EFFORT=config["reasoning_effort"],
        EVOLVE_AGENT_TIMEOUT=str(config["session_timeout_sec"]),
        EVOLVE_CODEX_DRIVER="exec",
        DAYTONA_ENV_FILE=settings["daytona_env"],
        SWE_BOOT_RETRIES="1",
        TT_DAYTONA_CREATE_RETRIES="1",
        TT_DAYTONA_LABEL="andy-q2-trusted-inputs",
        SWE_BOOT_CONCURRENCY="4",
        TT_DAYTONA_CREATE_CONCURRENCY="4",
    )
    sys.path.insert(0, str(checkout))
    import evolve as ev
    import evolve_codex as ec
    import pack_to_dataset as pack
    from torchtitan.experiments.rl.examples.tmax import layout

    subscription_driver(ec, settings)
    directory = campaign / "runs" / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    lock = (directory / "run.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        handlers=[
            logging.FileHandler(directory / "progress.log"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    log = logging.getLogger("q2")

    def save(name, value):
        freeze(directory / name, json.dumps(value, indent=2) + "\n")

    dependencies = dict(
        sorted(
            (d.metadata["Name"], d.version)
            for d in importlib.metadata.distributions()
            if d.metadata["Name"]
        )
    )
    save(
        "input.json",
        dict(
            item=item,
            config={k: v for k, v in config.items() if k != "tasks"},
            execution=settings,
            dependencies=dependencies,
            role_prompts={p.name: p.read_text() for p in (ec.SPEC, ec.VERIFIER_SPEC)},
            uv_lock_sha256=hashlib.sha256(
                (checkout / "uv.lock").read_bytes()
            ).hexdigest(),
        ),
    )
    if (directory / "result.json").exists() or (directory / "failure.json").exists():
        log.info(
            "item=%s status=resume_skip immutable_terminal_record=true", args.run_id
        )
        return
    seed = campaign / "seeds" / item["seed_id"]
    for relative, expected in item["seed_sha256"].items():
        assert hashlib.sha256((seed / relative).read_bytes()).hexdigest() == expected
    rewrite = layout.RewriteDir(directory / "rewrite")
    if not rewrite.package.exists():
        shutil.copytree(seed, rewrite.package)
    task = ev.load(seed)
    md = item["row"]["metadata"]
    task["_harder_mode"] = "student"
    task["_resources"] = {
        key: md["daytona_" + key] for key in ("cpu", "mem_gb", "disk_gb")
    }
    task["_resources"]["source"] = "published v3 row"
    grading = md["tmax"]
    if grading.get("pre_test_sh"):
        task["_pretest"] = (
            grading["pre_test_sh"],
            grading.get("pretest_env_identity", ""),
        )
    task["_protected"] = {
        key: grading[key]
        for key in ("protected_paths", "protected_cmds")
        if grading.get(key)
    }
    fmap = ev.file_map(task)
    log.info(
        "item=%s status=start job=%s model=%s revision=%s",
        args.run_id,
        item["job"],
        ec.CODEX_MODEL,
        revision,
    )
    try:
        if not (directory / "author.json").exists():
            fmap = ec._prepare_package(rewrite.package, task)
            size = ec.ts.size_of(
                task["solve_sh"],
                task["test_state_py"],
                "python" if fmap["test_state_py"].endswith(".py") else "shell",
            )
            if item["job"] == "harder":
                prompt = ec._HARDER_JOB_BLIND.format(
                    solved=0,
                    attempts=0,
                    guidance="Implement this fixed change: " + item["change"],
                    growth_bound=f"The reference may shrink or grow by at most {ec.ts.MAX_ADDED} non-comment lines.",
                    seed_lines=size["solution_lines"],
                    seed_asserts=size["verifier_asserts"],
                    max_asserts=ec.ts.MAX_ADDED_ASSERTS,
                )
                prompt = prompt.split("\n\n", 1)[1]
            else:
                (rewrite.package / "run/seed_size.json").unlink(missing_ok=True)
                prompt = (
                    "Simplify this task as follows: "
                    + item["change"]
                    + "\nAdapt the instruction, reference and verifier together, as in the easier path. "
                    "Before editing, write run/simplify.json with string fields "
                    "operator='controlled', retained_skill, change, and restore, "
                    "plus removed_requirements and removed_checks as lists of strings. "
                    "Set evidence=[] because this controlled request supplies no student traces. "
                    "This controlled request supplies the change; do not invent student trace evidence "
                    "or claim a measured difficulty effect. Keep checks for every retained requirement. "
                    "Run ./sandbox check after adapting the task."
                )
            prompt = (
                "Controlled Q2 rewrite. There are no student measurements or traces; "
                "this run tests verifier reliability and makes no difficulty claim.\n\n"
                + prompt
                + ec._budget(ec.AGENT_TIMEOUT)
            )
            with ec.session(rewrite, "agent", timeout=ec.AGENT_TIMEOUT) as session:
                try:
                    completed = ec._run_codex(session, rewrite.package, prompt)
                finally:
                    ec._sandbox_down(rewrite.package)
            if completed.returncode:
                raise RuntimeError(f"Author exited {completed.returncode}")
            ec._check_verdict(rewrite.package)
            ec._require_checked(rewrite.package)
            save("author.json", dict(session=str(session.dir.path)))
            log.info("item=%s status=author_pass", args.run_id)
        if item["job"] in ("harder", "easier"):
            if not (directory / "verifier.json").exists():
                vsession, rel = ec._blind_verifier(rewrite, task, fmap)
                fmap["test_state_py"] = rel
                save(
                    "verifier.json", dict(session=str(vsession.path), verifier_rel=rel)
                )
            else:
                saved = json.loads((directory / "verifier.json").read_text())
                vsession = layout.SessionDir(Path(saved["session"]))
                fmap["test_state_py"] = saved["verifier_rel"]
            ec._reconcile_blind(rewrite, vsession, fmap)
        else:
            ec._harness_check(rewrite.package, name="check.easier")
            ec._require_checked(rewrite.package)
        pretest = task.get("_pretest")
        row = pack.to_row(
            str(rewrite.package),
            task_id=args.run_id,
            inject_agent_runtime=False,
            pretest=pretest,
            protected=pack.Protected.from_tmax(grading),
        )
        for key in ("daytona_cpu", "daytona_mem_gb", "daytona_disk_gb"):
            row["metadata"][key] = md[key]
        save("row.json", row)
        save(
            "result.json",
            dict(
                status="native_checks_passed",
                item=item,
                execution=settings,
                package=str(rewrite.package),
                last_check=ec._last_check(rewrite.package),
                heldout_controls="pending",
            ),
        )
        log.info("item=%s status=complete heldout_controls=pending", args.run_id)
    except Exception as exc:
        save(
            "failure.json",
            dict(
                status="failed",
                item=item,
                execution=settings,
                type=type(exc).__name__,
                error=str(exc),
            ),
        )
        log.exception("item=%s status=failed", args.run_id)
        raise


if __name__ == "__main__":
    main()
