FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install pandas (needed by the buggy analysis script)
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip && \
    pip3 install --break-system-packages --no-cache-dir pandas && \
    rm -rf /var/lib/apt/lists/*

# Copy the sales data and the buggy analysis script
COPY files/sales_data.csv /app/sales_data.csv
COPY files/analyze.py /app/analyze.py

# Use the task's pandas-capable system Python by default; grading calls its own Python directly.
ENV PATH=/usr/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/sbin:/bin
