# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Serve this node to a Monarch client until the client goes away.

launch_9b.sh starts one of these per node for a multi-node run (RL_NUM_NODES > 1)
and hands the addresses to train.py as RL_TRAINER_HOST_ADDR / RL_GENERATOR_HOST_ADDR.
Processes the client spawns here inherit this process's environment, which is why
the launcher starts it only after the run's environment is complete.

    python monarch_worker.py tcp://<this node's FQDN>:<port>
"""

import sys

from monarch.actor import run_worker_loop_forever

if __name__ == "__main__":
    run_worker_loop_forever(ca="trust_all_connections", address=sys.argv[1])
