FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install pandas for the verifier Python
RUN /opt/verifier/bin/python3 -m ensurepip --upgrade || true \
    && /opt/verifier/bin/python3 -m pip install --no-cache-dir pandas==2.2.3

# Copy pipeline and data files
COPY files/pipeline.py /app/
COPY files/products.csv /app/
COPY files/stores.csv /app/
COPY files/sales_data/ /app/sales_data/

