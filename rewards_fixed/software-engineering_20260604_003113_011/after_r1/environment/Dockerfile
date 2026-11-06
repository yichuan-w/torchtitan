FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install websocket-client for the verifier Python (needed by the test suite)
RUN /opt/verifier/bin/python -m ensurepip --upgrade \
    && /opt/verifier/bin/python -m pip install --quiet websocket-client

# Copy the broken server implementation
COPY files/ws_server.py /app/ws_server.py

