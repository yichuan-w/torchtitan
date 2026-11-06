FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

# Install image analysis tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    && pip3 install --no-cache-dir pillow --break-system-packages \
    && rm -rf /var/lib/apt/lists/*

# Copy the steganography image
COPY files/image.png /app/

WORKDIR /app

