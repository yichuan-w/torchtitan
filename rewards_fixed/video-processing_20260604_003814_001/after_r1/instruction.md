Write a script named `freeze_detector.py` and place it at `/app/freeze_detector.py`. The script detects "freeze" segments in a video — periods where the visual content remains effectively static, as if the video is stuck or frozen. This is a real problem in video production and streaming QA.

## Dependencies
You have access to `cv2` (OpenCV), `numpy`, and `toml`. You can only use these libraries. You may NOT use any built-in SSIM function from OpenCV or any other library — you must implement the similarity metric yourself.

## What Counts as a Freeze
A "freeze segment" is a contiguous sequence of frames where the Structural Similarity Index (SSIM) between consecutive frames stays above a threshold for at least a minimum duration.

You must implement the SSIM computation yourself using numpy. The SSIM between two image patches x and y is defined as:

```
SSIM(x, y) = (2*μx*μy + C1)*(2*σxy + C2) / ((μx² + μy² + C1)*(σx² + σy² + C2))
```

Where:
- μx, μy are the means of x and y
- σx², σy² are the variances of x and y
- σxy is the covariance of x and y
- C1 = (K1*L)², C2 = (K2*L)² with K1=0.01, K2=0.03, L=255

Compute SSIM using an 11×11 Gaussian window (σ=1.5) over the luminance channel only. Convert each BGR frame to grayscale for the SSIM computation. Average the per-window SSIM values to get a single score per frame pair.

Parameters:
- **ssim_threshold**: SSIM above this value means two consecutive frames are "similar enough" to count as part of a freeze. Default: 0.995.
- **min_freeze_duration**: A freeze must persist for at least this many consecutive frame pairs to be reported. Default: equivalent to 0.5 seconds (i.e., `fps * 0.5` frame pairs).
- **black_frame_threshold**: If the average pixel intensity of a frame (across all BGR channels) is below this value, it's a "black frame." Black frames are NOT considered freezes — they are intentional (fade-to-black, scene transitions). Default: 10.0.

## Input
Your script takes an MP4 video file path as a command-line argument:
```
python3 /app/freeze_detector.py <video_path>
```

There's an example video at `/app/example_video.mp4` for development.

## Required Outputs
Your script must produce three files in `/app/`:

### 1. `/app/freeze_report.toml`
A TOML file with the exact structure below:
```toml
video_path = "/app/example_video.mp4"
video_fps = 30.0
total_frames = 300
parameters = { ssim_threshold = 0.995, min_freeze_pairs = 15, black_frame_threshold = 10.0 }

[[freeze_events]]
start_frame = 60
end_frame = 89
duration_pairs = 30
duration_seconds = 1.0
mean_ssim = 0.9987
```

- `freeze_events`: array of detected freeze events, each with:
  - `start_frame`: frame number (1-indexed) where the freeze begins
  - `end_frame`: frame number (1-indexed) where the freeze ends
  - `duration_pairs`: number of consecutive frame pairs in this freeze
  - `duration_seconds`: duration in seconds
  - `mean_ssim`: average SSIM across all pairs in this freeze segment

### 2. `/app/freeze_timeline.png`
A horizontal bar visualization (~1200×100 pixels) where:
- Background is dark gray
- Normal regions are green
- Freeze events are red
- Bar spans left-to-right representing frame 1 to last frame

### 3. `/app/ssim_plot.png`
A plot of the per-frame-pair SSIM values over time. This should be a 2D line/scatter plot (e.g., 1200×400 pixels) with:
- X-axis: frame pair index (0 to N-2)
- Y-axis: SSIM value (0.0 to 1.0)
- A horizontal dashed line at the ssim_threshold value
- Clear axis labels

## Constraints
- The example video is 960×540 resolution at 30 fps. Test videos will have the same dimensions and frame rate.
- You may NOT call external tools like ffmpeg from your script.
- You may NOT use cv2.quality, cv2.QualitySSIM, skimage, or any pre-built SSIM function. Implement it yourself with numpy.
- Black frames must be excluded from freeze detection.
- SSIM should be computed on grayscale (luminance), not on color channels.
