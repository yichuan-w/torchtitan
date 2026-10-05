# Trainer-vs-generator logprob check

`run.sh` checks offline whether the tmax 9B generator's decode logprobs match the
trainer model. It needs 3 free GPUs and takes about 10 minutes.

```
bash run.sh <model_dir> <rollout_samples.jsonl> <out_dir>
```

It replays 64 tmax trajectories for 8 turns through vLLM, so each turn reuses the
previous turn's prefix cache, as in a rollout. The decode logprobs are then
re-scored with the trainer's TorchTitan model (varlen/FA4 attention, fla GDN, fp32
lm_head). Three generator configs run side by side:

| tag | vLLM config |
|---|---|
| `default` | vLLM default compile: inductor `VLLM_COMPILE`, `custom_ops=['none']` |
| `rope` | same, with `rotary_embedding` run as its CUDA op (the generator's current setting) |
| `eager` | `enforce_eager` |

CoreWeave B300 results, 2026-10-05 (144k decode tokens):

```
default  3630 tok/s | mean|d|=0.0187 max=3.52 k1=0.00169
rope     3618 tok/s | mean|d|=0.0063 max=0.95 k1=0.00017
eager    1525 tok/s | mean|d|=0.0063 max=0.95 k1=0.00008
```

`k1` is the same estimator as the training metric `debug/vllm_local_kl_k1_mean`.
With `default`, step-1 training KL on CoreWeave was 0.0015-0.0018, against 0.00013
on della with the same torch/vLLM nightlies. `envfp.txt` in the output dir records
GPU, driver, ptxas and env, for comparing machines.
