FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Copy the data files and documentation
COPY files/sensor_data.bin /app/
COPY files/example.bin /app/
COPY files/example_tail.hex /tmp/example_tail.hex
RUN python3 -c "from pathlib import Path; p=Path('/app/example.bin'); p.write_bytes(p.read_bytes()+bytes.fromhex(Path('/tmp/example_tail.hex').read_text()))" && \
    rm /tmp/example_tail.hex
COPY files/README.md /app/
