FROM ubuntu:24.04

WORKDIR /app

# Install basic tools the solver might use (but not specialized recovery tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gzip \
    tar \
    grep \
    coreutils \
    file \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy the corrupted backup archive
COPY files/backup.tar.gz /app/

