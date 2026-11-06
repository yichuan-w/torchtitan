FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install DuckDB CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip \
    && curl -L -o /tmp/duckdb.zip https://github.com/duckdb/duckdb/releases/download/v1.2.2/duckdb_cli-linux-amd64.zip \
    && unzip /tmp/duckdb.zip -d /usr/local/bin/ \
    && chmod +x /usr/local/bin/duckdb \
    && rm /tmp/duckdb.zip \
    && rm -rf /var/lib/apt/lists/*

# Copy data files
COPY files/packages.csv /app/
COPY files/dependencies.csv /app/
COPY files/vulnerabilities.csv /app/

