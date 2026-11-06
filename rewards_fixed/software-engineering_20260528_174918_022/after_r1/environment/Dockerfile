FROM ubuntu:24.04

WORKDIR /app

# Verifier dependencies. Keep uv available in the built task image so tests do
# not spend verifier time installing it from the public internet.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    python3 \
    && curl -LsSf https://astral.sh/uv/0.9.5/install.sh | UV_INSTALL_DIR=/usr/local/bin sh \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/usr/local/bin:${PATH}"

# Copy the database engine skeleton
COPY files/db_engine.py /app/

