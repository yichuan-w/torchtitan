FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install build tools for compiling the C emulator
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    make \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the CHIP-8 emulator source and test ROMs
COPY files/chip8.c /app/
COPY files/Makefile /app/
COPY files/test_sr.ch8 /app/
COPY files/test_sl.ch8 /app/
COPY files/test_st.ch8 /app/
COPY files/test_ld.ch8 /app/
COPY files/test_bcd.ch8 /app/

