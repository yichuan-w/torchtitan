FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Python 3 is already available in the base image.
# The solver needs to figure out how to recover the corrupted PNGs.
# Pre-installing PNG recovery tools would defeat the purpose.

RUN mkdir -p /app/corrupted /app/recovered

COPY files/corrupted/ /app/corrupted/

