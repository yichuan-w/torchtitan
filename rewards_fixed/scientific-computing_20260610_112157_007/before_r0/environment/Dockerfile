FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install numpy for the Ising model simulation
RUN uv pip install --python /opt/verifier/bin/python numpy

# Copy the simulation code
COPY files/simulation.py /app/

