FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install required packages into the verifier Python
RUN /opt/verifier/bin/python3 -m ensurepip --upgrade \
    && /opt/verifier/bin/python3 -m pip install --no-cache-dir \
    torch --index-url https://download.pytorch.org/whl/cpu \
    && /opt/verifier/bin/python3 -m pip install --no-cache-dir numpy

# Copy the buggy model implementation
COPY files/model.py /app/

