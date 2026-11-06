FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

RUN /opt/verifier/bin/python -m ensurepip && \
    /opt/verifier/bin/python -m pip install --no-cache-dir rdkit==2025.9.1

# Copy the chemical library files
COPY files/library/ /app/library/
