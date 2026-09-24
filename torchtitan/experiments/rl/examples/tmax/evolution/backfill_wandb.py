#!/usr/bin/env python3
"""Replace a finished training run's legacy evolve charts from its run ledger."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import layout  # noqa: E402

metrics_spec = importlib.util.spec_from_file_location(
    "evolution_metrics", Path(__file__).resolve().parents[1] / "evolution_metrics.py"
)
assert metrics_spec is not None and metrics_spec.loader is not None
metrics_module = importlib.util.module_from_spec(metrics_spec)
with patch.dict(
    sys.modules,
    {"torchtitan.experiments.rl.examples.tmax": SimpleNamespace(layout=layout)},
):
    metrics_spec.loader.exec_module(metrics_module)
EvolutionMetrics = metrics_module.EvolutionMetrics


def build(
    root: layout.Root, run: layout.Run, step: int
) -> tuple[dict, EvolutionMetrics]:
    metrics = EvolutionMetrics(
        root.evolution.run_outcomes(run.name), run=run, root=root
    )
    values = metrics.poll(step=step)
    if len(metrics.seen_signals) != len(run.signal_files()):
        raise ValueError("some signals have no claim-time policy step")
    if metrics.pending_origin:
        raise ValueError("some rewrite outcomes have no origin signal")
    if any(signal not in metrics.signal_origin for signal in metrics.signal_outcomes):
        raise ValueError("some ledger decisions have no origin signal")
    if (
        values["evolution/run/signal_consumed_total"]
        > values["evolution/run/signal_issued_total"]
    ):
        raise ValueError("consumed signals exceed issued signals")
    return values, metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--wandb", required=True, help="entity/project/run-id")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()

    import wandb

    root = layout.Root(args.root.resolve())
    run = root.run(args.run)
    api_run = wandb.Api().run(args.wandb)
    if api_run.state not in {"finished", "failed", "crashed", "killed"}:
        raise ValueError(f"training run is still live: {api_run.state}")
    # scan_history can omit earlier backfill rows; the server's last step is
    # the value the resumed SDK uses to reject out-of-order writes.
    last_step = api_run.lastHistoryStep
    values, metrics = build(root, run, last_step)
    xs, ys, keys = metrics.signal_flow_series(last_step)
    args.out.mkdir(parents=True, exist_ok=False)
    snapshot = {
        "source_run": str(run.path),
        "wandb": args.wandb,
        "last_step": last_step,
        "values": values,
        "signal_flow": {"x": xs, "y": ys, "keys": keys},
        "previous_summary": {
            key: api_run.summary.get(key)
            for key in (
                "evolution/run/signal_flow",
                "evolution/run/signal_issued_total",
                "evolution/run/signal_consumed_total",
                "evolution/run/signal_handled_total",
                "evolution/run/rewrite_finalized_total",
            )
        },
    }
    (args.out / "before.json").write_text(json.dumps(snapshot, indent=2) + "\n")
    if not args.upload:
        print(json.dumps({"output": str(args.out), "values": values}, sort_keys=True))
        return

    entity, project, run_id = args.wandb.split("/")
    wb = wandb.init(
        entity=entity,
        project=project,
        id=run_id,
        resume="must",
        dir=str(args.out),
    )
    assert wb is not None and wb.id == run_id
    payload = {
        "evolution/run/signal_flow": wandb.plot.line_series(
            xs,
            ys,
            keys=keys,
            title="Evolution signal flow",
            xname="Origin policy step",
        ),
        **{
            key: value
            for key, value in values.items()
            if key.startswith("evolution/run/")
        },
    }
    wb.log(payload, step=last_step + 1)
    wb.finish()
    print(f"updated {api_run.url} from {args.out / 'before.json'}")


if __name__ == "__main__":
    main()
