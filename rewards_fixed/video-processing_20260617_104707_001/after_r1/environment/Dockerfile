FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install ffmpeg and OpenCV system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install numpy and opencv for the solver and tests
# Bootstrap pip for the verifier's Python, then install packages
RUN /opt/verifier/bin/python -m ensurepip --upgrade
RUN /opt/verifier/bin/python -m pip install --no-cache-dir \
    numpy \
    opencv-python

# Generate the corrupted test video
RUN <<'PYEOF' /opt/verifier/bin/python
import cv2, numpy as np, os, random
random.seed(42)
np.random.seed(42)
W, H, FPS, N = 640, 480, 30, 450
# Corrupted frames: some isolated, some consecutive, some near edges
CORRUPT = {12, 45, 88, 130, 131, 175, 220, 265, 310, 355, 356, 357, 400, 435}
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
out = cv2.VideoWriter('/app/corrupted_video.mp4', fourcc, FPS, (W, H))
for i in range(N):
    # Create a base frame with a complex gradient background and moving shapes
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    t = i / FPS
    # Gradient background that shifts over time
    for y in range(H):
        yf = y / H
        b_val = int(30 + 25 * np.sin(yf * 5 + t * 0.3))
        g_val = int(20 + 20 * np.cos(yf * 4 + t * 0.4))
        r_val = int(25 + 20 * np.sin(yf * 3 + t * 0.5 + 1))
        frame[y, :] = (b_val, g_val, r_val)
    # Add vertical gradient bands
    for x in range(W):
        xf = x / W
        band = int(10 * np.sin(xf * 12 + t * 0.2))
        frame[:, x, 0] = np.clip(frame[:, x, 0].astype(int) + band, 0, 255).astype(np.uint8)
        frame[:, x, 1] = np.clip(frame[:, x, 1].astype(int) + band, 0, 255).astype(np.uint8)
    # Moving circle
    cx = int(W/2 + 180 * np.sin(t * 0.7))
    cy = int(H/2 + 130 * np.cos(t * 0.55))
    r = int(120 + 40 * np.sin(t * 1.3))
    g = int(150 + 60 * np.sin(t * 0.9 + 2))
    b = int(100 + 80 * np.sin(t * 1.1 + 4))
    cv2.circle(frame, (cx, cy), max(30, 25 + int(15 * np.sin(t * 2.0))), (b, g, r), -1)
    # Moving rectangle
    rx = int(W/2 + 200 * np.cos(t * 0.9))
    ry = int(H/2 + 150 * np.sin(t * 0.65 + 1))
    cv2.rectangle(frame, (rx-20, ry-20), (rx+20, ry+20), (255-b, 255-g, 255-r), -1)
    # Small moving dots
    for d in range(5):
        dx = int(W * (0.2 + 0.6 * np.sin(t * 1.7 + d * 1.3)))
        dy = int(H * (0.2 + 0.6 * np.cos(t * 1.4 + d * 1.1)))
        cv2.circle(frame, (dx, dy), 5, (255, 255, 200 + int(55 * np.sin(t + d))), -1)

    if i in CORRUPT:
        # Corruption: scramble pixels within 8x8 blocks.
        # This preserves color statistics (mean, variance, entropy)
        # while destroying spatial structure. Block boundaries create
        # artificial edges detectable via Laplacian variance.
        block_size = 8
        for by in range(0, H, block_size):
            for bx in range(0, W, block_size):
                bh = min(block_size, H - by)
                bw = min(block_size, W - bx)
                block = frame[by:by+bh, bx:bx+bw].copy()
                flat = block.reshape(-1, 3)
                np.random.shuffle(flat)
                frame[by:by+bh, bx:bx+bw] = flat.reshape(bh, bw, 3)

    out.write(frame)
out.release()
print(f'Generated {N} frames, corrupted: {sorted(CORRUPT)}')
PYEOF

