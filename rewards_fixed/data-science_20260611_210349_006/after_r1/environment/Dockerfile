FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install pandas and pyarrow for the verifier Python (used by both solver and tests)
RUN /opt/verifier/bin/python -m ensurepip \
    && /opt/verifier/bin/python -m pip install --no-cache-dir pandas==2.2.3 pyarrow==18.1.0

# Copy the data generation script and generate the CSV files
COPY files/generate_data.py /app/
RUN /opt/verifier/bin/python /app/generate_data.py && rm /app/generate_data.py

