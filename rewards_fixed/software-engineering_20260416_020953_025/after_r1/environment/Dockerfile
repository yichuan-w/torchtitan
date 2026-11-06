FROM ubuntu:24.04

# Install build tools and debugging utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    gdb \
    valgrind \
    python3 \
    file \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy library source files
COPY files/libbinparse/ /app/libbinparse/

# Copy test data generator and generate test files
COPY files/generate_testdata.py /app/generate_testdata.py
RUN python3 /app/generate_testdata.py /app/testdata && rm /app/generate_testdata.py
RUN ln -s /app/testdata /app/libbinparse/testdata
