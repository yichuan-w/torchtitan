FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install RDKit for cheminformatics (the solver needs to use it)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-rdkit \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/usr/bin:${PATH}"

# Copy the molecular library file
COPY files/library.tsv /app/
