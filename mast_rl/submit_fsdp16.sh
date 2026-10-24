#!/bin/bash
# Submit the 2-host-trainer (FSDP=16) + 3-generator Qwen3-32B Search-R1 variant.
#
# Trainer shards 16-way across 2 hosts (data_parallel_shard_degree=16, TP=1); each
# of the 3 generators is TP=8 on its own host. The launcher infers the host count
# from the parallelism, so the MAST job is:
#     1 controller + 2 trainer + 3 generator = 6 hosts.
#
# This is a thin wrapper over submit.sh; every extra arg is forwarded, e.g.
#   conda activate rlmast
#   bash mast_rl/submit_fsdp16.sh                 # default 3 generators
#   bash mast_rl/submit_fsdp16.sh --no-reinstall  # skip torchtitan reinstall
#
# Note: the FSDP group spans 2 hosts. run.sh defaults NCCL_NET=Socket (the rlmast
# env lacks the mlx5 RDMA driver), so cross-host all-gather/reduce-scatter go over
# TCP -- functional but slower than RDMA. Pass NCCL_NET=IB (with libmlx5 present)
# for full cross-host speed.
exec bash "$(dirname "$0")/submit.sh" \
    --config rl_grpo_qwen3_32b_search_r1_fsdp16 "$@"
