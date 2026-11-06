A video file at `/app/corrupted_video.mp4` has been partially damaged during transfer. In some frames, local spatial continuity has been disrupted even though broad color statistics may remain similar. The rest of the frames are intact and show a smooth animation of moving shapes on a dark background.

Write a script named `video_repair.py` and place it at `/app/video_repair.py`. The script must:

1. Analyze the video frame-by-frame to detect which frames are corrupted.
2. Write a JSON file to `/app/corrupted_frames.json` containing a list of corrupted frame indices (0-based), e.g. `[5, 42, 99]`.
3. Repair the video by replacing each corrupted frame with an interpolated frame derived from neighboring good frames. Save the repaired video to `/app/repaired_video.mp4`.
4. The repaired video must have the same frame count, resolution, and frame rate as the original.

## Dependencies
You have access to the `cv2` (OpenCV) and `numpy` libraries. `ffmpeg` is also available on the system.

## Constraints
- The script should accept no command-line arguments — all paths are hardcoded as above.
- The output JSON must be valid and contain only the list of corrupted frame indices as integers.
- The repaired video must be playable and visually smooth — corrupted frames should be replaced with reasonable interpolations, not black frames or duplicates.
