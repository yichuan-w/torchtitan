FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Create data and output directories
RUN mkdir -p /app/data /app/output

# Copy the source data file
COPY files/sales.log /app/data/

