FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --break-system-packages --no-cache-dir pandas==2.2.3 duckdb==1.2.1

# Install duckdb for the verifier Python as well (needed for tests)
RUN /opt/verifier/bin/python -m pip install --break-system-packages duckdb==1.2.1 2>/dev/null || true

RUN mkdir -p /app/data /app/output

COPY files/north_sales.csv /app/data/
COPY files/south_sales.csv /app/data/
COPY files/east_sales.tsv /app/data/

# ===== #313 PATH fix (a: use the task's own system python; drop grader /opt/verifier/bin) =====
# tests/test.sh calls /opt/verifier/bin/python by absolute path, so grading is unaffected.
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
