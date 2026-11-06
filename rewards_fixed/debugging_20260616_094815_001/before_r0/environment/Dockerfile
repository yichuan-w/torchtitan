FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install C development and debugging tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gdb \
    valgrind \
    && rm -rf /var/lib/apt/lists/*

# Copy the image processing program source and test images
COPY files/imgproc.c /app/
COPY files/imgproc.h /app/
COPY files/utils.c /app/
COPY files/utils.h /app/
COPY files/Makefile /app/
COPY files/small.ppm /app/
COPY files/sample3.ppm /app/
COPY files/test_img.ppm /app/

