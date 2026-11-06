"""Generate the example video with known freeze and black-frame segments.

This script is run during Docker build to create /app/example_video.mp4.
The freeze positions are visible in this file (in the Docker build context),
but the test video has different freeze positions, so the solver must
implement a general algorithm.
"""
import cv2
import numpy as np

W, H = 960, 540
FPS = 30
TOTAL_FRAMES = 300  # 10 seconds
OUT = "/app/example_video.mp4"

fourcc = cv2.VideoWriter_fourcc(*'mp4v')
writer = cv2.VideoWriter(OUT, fourcc, FPS, (W, H))

# Freeze segments: frames 60-89 (1 second), frames 150-194 (1.5 seconds), frames 240-254 (0.5 seconds)
# Black frames: frames 30-32 (intentional fade-to-black transition)
freeze_ranges = [(60, 90), (150, 195), (240, 255)]
black_ranges = [(30, 33)]

rng = np.random.RandomState(42)
frozen_frames = {}

for fn in range(1, TOTAL_FRAMES + 1):
    # Check if this frame is in a freeze range
    in_freeze = False
    for fs, fe in freeze_ranges:
        if fs <= fn < fe:
            in_freeze = True
            if fs not in frozen_frames:
                # Bright background so it's not mistaken for a black frame
                frame = np.full((H, W, 3), (60, 80, 100), dtype=np.uint8)
                cx = int(W * 0.25 + (fs / TOTAL_FRAMES) * W * 0.5)
                cy = int(H * 0.5 + 30 * np.sin(fs * 0.3))
                cv2.circle(frame, (cx, cy), 40, (0, 200, 255), -1)
                cv2.putText(frame, f"Frame {fs}", (50, 50),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
                frozen_frames[fs] = frame
            frame = frozen_frames[fs].copy()
            break
    if in_freeze:
        writer.write(frame)
        continue

    # Check if this is a black frame
    in_black = False
    for bs, be in black_ranges:
        if bs <= fn < be:
            in_black = True
            break
    if in_black:
        writer.write(np.zeros((H, W, 3), dtype=np.uint8))
        continue

    # Normal moving frame: scrolling gradient pattern with moving circles
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    t = fn / FPS
    for y in range(H):
        val = int(128 + 60 * np.sin((y / H) * 6 + t * 2))
        frame[y, :, 0] = np.clip(val, 0, 255).astype(np.uint8)
        frame[y, :, 1] = np.clip(int(128 + 50 * np.cos((y / H) * 4 + t * 1.5)), 0, 255).astype(np.uint8)
        frame[y, :, 2] = np.clip(int(128 + 40 * np.sin((y / H) * 5 + t * 1.8)), 0, 255).astype(np.uint8)

    # Moving circles
    for i in range(3):
        cx = int(W * 0.2 + (W * 0.6) * ((fn + i * 40) % TOTAL_FRAMES) / TOTAL_FRAMES)
        cy = int(H * 0.3 + 100 * np.sin(t * 3 + i))
        color = (
            int(np.clip(200 + 55 * np.sin(t + i), 0, 255)),
            int(np.clip(150 + 105 * np.cos(t * 0.7 + i), 0, 255)),
            int(np.clip(100 + 50 * np.sin(t * 1.3 + i), 0, 255)),
        )
        cv2.circle(frame, (cx, cy), 25, color, -1)

    writer.write(frame)

writer.release()
print(f"Created example video: {OUT}")
print(f"Freeze segments (1-indexed frames): {freeze_ranges}")
print(f"Black frame segments: {black_ranges}")
