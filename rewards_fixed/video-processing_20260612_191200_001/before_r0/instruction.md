Write a Python script named `shot_detector.py` and place it at `/app/shot_detector.py`. The script analyzes MP4 videos and detects all shot boundaries (scene changes), extracts a representative keyframe from each shot, and generates a visual contact sheet.

There's an example video at `/app/example_video.mp4` for development and testing.

## Dependencies
You have access to `cv2` (OpenCV) and `numpy`. You must only use these libraries plus the Python standard library.

## Input
Your script receives one command-line argument: the path to an MP4 video file.
```bash
python3 /app/shot_detector.py /path/to/video.mp4
```

## Required Output
Your script must produce exactly two files in `/app/`:

### 1. `/app/shots.json`
A JSON file describing every detected shot:

```json
{
  "total_frames": 480,
  "fps": 30.0,
  "shots": [
    {
      "shot_id": 0,
      "start_frame": 0,
      "end_frame": 89,
      "transition_after": "cut",
      "keyframe_frame": 45
    },
    {
      "shot_id": 1,
      "start_frame": 90,
      "end_frame": 179,
      "transition_after": "fade",
      "keyframe_frame": 135
    }
  ]
}
```

Field definitions:
- `total_frames`: total number of frames in the video
- `fps`: frames per second of the video
- `shots`: ordered list of detected shots
  - `shot_id`: zero-based index
  - `start_frame`: first frame (inclusive) of the shot
  - `end_frame`: last frame (inclusive) of the shot
  - `transition_after`: either `"cut"` (abrupt change) or `"fade"` (gradual dissolve/blend)
  - `keyframe_frame`: the frame number within this shot that best represents it (sharpest, clearest frame)

### 2. `/app/contact_sheet.png`
A single PNG image arranging all keyframes in a grid layout:
- 3 thumbnails per row
- Each thumbnail is exactly 160×120 pixels
- A 2-pixel white border around each thumbnail
- Thumbnails arranged left-to-right, top-to-bottom in shot order
- Background (gaps) should be black
- The total image dimensions should accommodate all thumbnails

## What Constitutes a Shot Boundary
- **Hard cut**: an instantaneous change between two visually distinct shots. Consecutive frames across the boundary look very different.
- **Fade/dissolve**: a gradual transition where one shot blends into the next over several frames.

## Constraints
- All test videos are 640×480 resolution at 30 fps
- The first frame of the video is always the start of shot 0
- Every shot contains at least 30 frames
- Fade transitions span between 15 and 45 frames
- The example video has the same format and characteristics as test videos