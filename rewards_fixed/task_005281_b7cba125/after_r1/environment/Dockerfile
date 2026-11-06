FROM hamishi740/swerl-tmax-v3:eb93524b1127
#
# The task environment is BAKED INTO THE IMAGE above. The lines below are this task's
# setup.sh, inlined as comments so a reader -- and the breeding model, which sees only
# instruction.md, this file and solution/solve.sh -- can tell how the environment was built.
# They are COMMENTS on purpose: these scripts are not idempotent and must never run again as
# RUN steps, and a COPY/RUN here would move the image identity the pre-test hook keys on.
# The executable copy ships beside this file as tasks/<id>/setup.sh.
#
# #!/bin/bash
# set -e
# export DEBIAN_FRONTEND=noninteractive
#     apt-get update && apt-get install -y python3 python3-pip python3-numpy
#     pip3 install pytest
#
#     mkdir -p /home/user
#
#     python3 -c "
# import os
# import numpy as np
#
# os.makedirs('/home/user', exist_ok=True)
#
# # Generate synthetic dataset
# rng = np.random.RandomState(0)
# data = rng.normal(loc=5.0, scale=2.0, size=150)
# np.save('/home/user/data.npy', data)
#
# # Create the buggy MCMC script
# mcmc_code = '''import numpy as np
#
# def log_likelihood(data, mu, sigma):
#     if sigma <= 0: return -np.inf
#     # BUG: set() scrambling iteration order leads to non-deterministic fp addition
#     indices = set(range(len(data)))
#     ll = 0.0
#     for i in indices:
#         ll += -0.5 * np.log(2 * np.pi * sigma**2) - ((data[i] - mu)**2) / (2 * sigma**2)
#     return ll
#
# def sample(data, iters=5000, seed=None):
#     if seed is not None:
#         np.random.seed(seed)
#
#     mu, sigma = 0.0, 1.0
#     chain = []
#     ll_current = log_likelihood(data, mu, sigma)
#
#     for _ in range(iters):
#         mu_prop = mu + np.random.normal(0, 0.1)
#         sigma_prop = sigma + np.random.normal(0, 0.1)
#
#         ll_prop = log_likelihood(data, mu_prop, sigma_prop)
#
#         if sigma_prop > 0:
#             # log acceptance ratio
#             log_accept_ratio = ll_prop - ll_current
#             accept_prob = np.exp(min(0, log_accept_ratio))
#         else:
#             accept_prob = 0.0
#
#         if np.random.rand() < accept_prob:
#             mu, sigma = mu_prop, sigma_prop
#             ll_current = ll_prop
#
#         chain.append((mu, sigma))
#
#     return np.array(chain)
# '''
#
# with open('/home/user/mcmc.py', 'w') as f:
#     f.write(mcmc_code)
# "
#
#     useradd -m -s /bin/bash user || true
#     chmod -R 777 /home/user

RUN python3 -m pip install --no-cache-dir numpy==1.26.4 && \
    test -f /home/user/mcmc.py && \
    sed -i \
      -e "s/# BUG: set() scrambling iteration order leads to non-deterministic fp addition/# BUG: hash-randomized string keys change the float32 reduction order/" \
      -e "s/indices = set(range(len(data)))/indices = set(map(str, range(len(data))))/" \
      -e "s/ll = 0.0/ll = np.float32(0.0)/" \
      -e "s/ll += -0.5 \* np.log(2 \* np.pi \* sigma\*\*2) - ((data\[i\] - mu)\*\*2) \/ (2 \* sigma\*\*2)/ll += np.float32(-0.5 * np.log(2 * np.pi * sigma**2) - ((data[int(i)] - mu)**2) \/ (2 * sigma**2))/" \
      /home/user/mcmc.py
