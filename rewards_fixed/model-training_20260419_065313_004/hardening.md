# Hardening rationale

## Observed strategy

All 12 attempts inspected `model.py` and `train.py`, replaced the clone in `_tie_weights`, and ran `train.py`. Attempts 02 and 05 used `.data` aliasing; attempts 07, 08, and 10 assigned the `Parameter`; the remaining attempts rewrote the small model around the same direct fix. None had to preserve aliasing across a checkpoint load.

## Upstream evidence

- https://github.com/pytorch/pytorch/issues/188346 — fetched through the GitHub REST API on 2026-11-05. The issue states that `load_state_dict(state_dict, assign=True)` replaces tied names with fresh objects, so the names stop aliasing, while default copy mode preserves tying.
- https://github.com/pytorch/pytorch/pull/188347 — fetched through the GitHub REST API on 2026-11-05. The proposed fix explains that assign mode reassigns each name independently and that preserving ties requires recording aliases before load and restoring them afterward. The pull request is closed and unmerged, so no upstream fix is placed in the image.

## Pinned artifact and reproduction

The existing Dockerfile pins the actual upstream artifact `torch==2.2.0` from PyTorch's CPU wheel index. In the running sandbox, a minimal model with `output_proj.weight = embedding.weight` produced:

```
2.2.0+cpu
before True True
after False True
['embedding.weight', 'output_proj.weight']
```

after `dst.load_state_dict(src.state_dict(), assign=True)`. Thus assign-mode loading turns one shared `Parameter` into two parameter objects in this environment, even though their initial storage pointer remains equal.

## Proposed change

The supplied training workflow will warm-start through `load_state_dict(..., assign=True)`. The model must keep the embedding and projection as one shared `Parameter` through that load and training. A direct constructor-time assignment, the strategy used by every successful attempt, is overwritten by assign-mode loading. The new decision is where and how to restore the model invariant after state assignment (for example, using the module's load lifecycle), rather than merely choosing copying versus aliasing during construction.

This is one modest step beyond the demonstrated skill: attempts already identify and repair a bad PyTorch parameter tie, but must now debug a real checkpoint-loading behavior that invalidates that repair later in the same workflow.

## Shortcut trials

- I applied the error/tool-output-level fix `output_proj.weight = embedding.weight` and trained for two epochs. Training printed `Initial weight tying - same storage: False`, and grading returned reward 0 at `test_weight_tying` because assign-mode load replaced the constructor tie.
- I then tried the attempts' common recovery of reinstalling the dependency (`pip install ... torch==2.2.0`) before the same edit and training. Pip reported the pinned version already satisfied; grading again returned reward 0 at `test_weight_tying`.

Neither output names the needed model-side lifecycle fix. The visible `False` and the duplicate parameter count give an investigating student evidence to isolate the checkpoint-load transition.

## Successful-attempt replay

- Attempt 07's commands (direct `Parameter` assignment followed by default training) were replayed from a fresh reset. Training completed and produced `model.pt`, but the initial and final storage checks were false; grading failed only the weight-tying check and returned reward 0.
- Attempt 02's commands (`.data` aliasing followed by default training) were replayed from another fresh reset. Training completed and produced `model.pt`, but assign loading broke storage aliasing; grading failed only the weight-tying check and returned reward 0.

Both paths now stop at the new checkpoint-load invariant rather than passing unchanged. Based on 12 of 12 agents previously making only the constructor-time edit, while several already used direct `Parameter` assignment and validation commands, I expect **6 of 12** in the next group to diagnose the later load and preserve the tie.
