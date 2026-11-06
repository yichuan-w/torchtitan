# Hardening rationale

## Attempt evidence and current shortcut

All 12 supplied attempts earned reward 1 without editing the model architecture. Their common strategy was to inspect `train.py` and `model.py`, switch the model back to training mode during each training epoch, optimize all model parameters (often by replacing SGD with Adam), tune the learning rate/scheduler, run training, and check that `trained_model.pth` exists. Examples are `traces/attempt-04.jsonl` turns 9 and 15-19 and `traces/attempt-02.jsonl` turns 10-12. The task currently spares them from diagnosing whether BatchNorm's evaluation behavior is compatible with the required saved-model inference.

Before hardening I found no rewarded attempt that violated the stated accuracy/checkpoint goal or edited grading files. The seed verifier does, however, require one-sample inference while the public instruction does not state that behavior. I record that hidden requirement in `run/repair.json` and make it explicit as part of the user-visible saved-model contract.

## Primary sources and pinned artifact

- https://raw.githubusercontent.com/pytorch/pytorch/v2.2.0/torch/nn/modules/batchnorm.py (tag `v2.2.0`, resolved through the GitHub API to commit `8ac9b20d4b090c213799e81acf48a55ea8d437d6`): the `BatchNorm1d` documentation says that when `track_running_stats` is false its running-statistics buffers are `None` and it always uses batch statistics in both training and evaluation modes.
- The same tagged source's `_BatchNorm.forward` sets `bn_training` true in evaluation when the running-statistics buffers are `None`, then calls `F.batch_norm` with that setting.
- https://raw.githubusercontent.com/pytorch/pytorch/v2.2.0/torch/nn/functional.py: `batch_norm` calls `_verify_batch_size(input.size())` when `training` is true; that check raises `ValueError("Expected more than 1 value per channel when training, got input size ...")` when there is only one value per channel.

The image already pins the actual upstream artifact `torch==2.2.0` from PyTorch's CPU wheel index, and that pin is retained.

## Reproduction in this environment

With both `BatchNorm1d` layers configured as `track_running_stats=False`, the intended training fixes still train and validate successfully on validation batches, but a fresh loaded model cannot infer one sample:

```
cd /app
# configure each BatchNorm1d with track_running_stats=False, apply the seed training fixes, train
python3 train.py
python3 -c '... load trained_model.pth; model.eval(); model(torch.randn(1, 20)) ...'
```

Observed training reached over 80% validation accuracy, while the one-sample call raised `ValueError: Expected more than 1 value per channel when training, got input size torch.Size([1, 128])`. This is PyTorch's documented behavior in the pinned version, not an imitation.

## Changed decision

The revised model starts with BatchNorm configured not to retain running statistics. A student must now distinguish successful batched validation from deployable evaluation, inspect the BatchNorm configuration/semantics, and choose a normalization setup that supports one-at-a-time inference. This is one modest step beyond the attempts' demonstrated ability to diagnose model mode and optimizer-parameter bugs. The environment contains only the problematic upstream-supported configuration; the fix is present only in the reference actions.

## Obvious-fix controls and replay results

1. **Exception-directed workaround:** after applying the seed training fixes, I duplicated the input to make an inference batch of two. PyTorch accepted it and returned shape `(2, 5)`, but this does not satisfy one-sample inference. `./sandbox grade` returned `reward=0.0`; `test_model_can_inference` still failed with the documented `ValueError` (and the seed validation-data check also rejected the checkpoint).
2. **Attempts' first fallback:** from a fresh reset I switched the epoch to training mode, changed to Adam over all parameters, raised the learning rate, and disabled the scheduler, matching the path in attempt 04. Training reported 94.00% best validation accuracy, but `./sandbox grade` returned `reward=0.0`; one-sample inference still raised the BatchNorm error (and the seed validation-data check rejected the checkpoint). Thus neither upgrading/tuning the optimizer nor obeying the batch-size clue solves the stated deliverable.

### Successful-attempt replays

- **Attempt 04 path** (fresh reset): its initial run and mode-only rerun still expose poor training; its final Adam/all-parameters/lr/scheduler edits report 94.00% best batched validation accuracy. It stops at the new deployment condition: grading fails both saved-model accuracy on the verifier's evaluation fixture and `test_model_can_inference`, the latter with `ValueError: Expected more than 1 value per channel when training, got input size torch.Size([1, 128])`.
- **Attempt 02 path** (fresh reset): its rewrite again repairs training mode, includes all parameters, and raises the SGD learning rate. The replay spent its 120-second command budget in the repeated training/check loop rather than completing, and grading the resulting state returned `reward=0.0`. Its code leaves both `BatchNorm1d(..., track_running_stats=False)` layers unchanged, so the same one-sample inference check necessarily fails; the attempt's final model-loading command does not call inference and therefore does not discover it.

These replays show the change directly intersects the student's successful path: both preserve the new model configuration while concentrating on the old training-loop and optimizer defects. I expect **6 of 12** in the next same-sized group: agents that test a freshly loaded checkpoint on a singleton should follow the named BatchNorm failure into the model configuration, while agents that stop after batched validation and file loading should fail as these replays did.
