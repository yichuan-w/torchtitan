FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# The solver needs to parse a malformed CSV and produce JSON.
# Python is available from the base image for processing.
# No additional dependencies needed — the solver can use stdlib csv + json modules.

# Copy the malformed orders CSV file
COPY files/orders.csv /app/

