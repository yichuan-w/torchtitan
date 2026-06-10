# Search-R1: multi-turn retrieval-augmented GRPO

A minimal reproduction of [Search-R1](https://github.com/PeterGriffinJin/Search-R1)
in torchtitan's RL experiment, modeled on slime's `examples/search-r1`. The model
reasons in `<think>`, issues `<search>query</search>` calls (answered with retrieved
`<information>...</information>`), and finishes with `<answer>...</answer>`. Reward is
exact-match (EM) against gold answers plus a small format bonus.

This is the first multi-turn / tool-using example in the RL experiment. It exercises
the multi-turn rollout controller (`trainer.py`), the per-token `loss_mask` that
masks retrieved (env-injected) tokens out of the GRPO loss
(`rollout/utils.py:rollout_to_episode`, `batcher.py`), generator stop-strings
(`actors/generator.py`), and the `TokenEnv.max_num_turns` cap.

## Files
- `data.py` — `SearchR1Dataset` / `SearchR1Example`: reads the Search-R1 NQ/HotpotQA parquet.
- `env.py` — `SearchR1Env(MessageEnv)`: text-tag `<search>`/`<answer>` protocol, injects `<information>`.
- `retrieval.py` — async client for the local dense retrieval server.
- `rubric.py` — `RewardAnswerEM` (EM) + `RewardFormat`.
- `rollouter.py` — wires datasets + env + rubric; `token_env.max_num_turns` bounds turns.

## Prerequisites

### 1. Data
Prepare the Search-R1 NQ/HotpotQA parquet (via Search-R1's
`scripts/data_process/qa_search_{train,test}_merge.py`). The default paths are in
`rollouter.py` (`DEFAULT_TRAIN_PARQUET` / `DEFAULT_TEST_PARQUET`); override
`train_dataset`/`validation_dataset` `data_path` to point elsewhere.

### 2. Local dense retrieval server
Start the Search-R1 / slime dense retriever (e5 index over wiki-18) listening on
`http://127.0.0.1:8000/retrieve` **before** training. Pin it to a spare GPU so it
doesn't clash with the RL GPUs, e.g.:

```bash
source /home/yichuan/retriever-venv/bin/activate
CUDA_VISIBLE_DEVICES=7 python \
  /home/yichuan/slime/examples/search-r1/local_dense_retriever/retrieval_server.py \
  --index_path /home/yichuan/search-r1-index/e5_Flat.index \
  --corpus_path /home/yichuan/search-r1-index/wiki-18.jsonl \
  --topk 3 --retriever_name e5 --retriever_model intfloat/e5-base-v2
```

The faiss index (~64GB) lives in RAM; only the small e5 encoder uses the GPU. Override
`message_env.search_url` / `message_env.topk` in the config if needed.

## Run

```bash
# fast smoke (Qwen3-0.6B, 4 GPUs: 2 gen + 2 train)
python torchtitan/experiments/rl/train.py --module rl \
  --config rl_grpo_qwen3_0_6b_search_r1 --metrics.no-enable-wandb --num-steps 3

# eval run (Qwen3-1.7B, 6 GPUs: 4 gen + 2 train), W&B on
python torchtitan/experiments/rl/train.py --module rl \
  --config rl_grpo_qwen3_1_7b_search_r1
```

Watch `validation_reward` (EM) trend up. Multi-turn rollouts show `rollout/response_length`
growth as the model learns to search.
