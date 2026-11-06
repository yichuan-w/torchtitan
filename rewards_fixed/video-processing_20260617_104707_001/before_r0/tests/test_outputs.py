import json
import subprocess
import sys
from pathlib import Path

import numpy as np


# Known corrupted frame indices (must match Dockerfile generation)
EXPECTED_CORRUPTED = {12, 45, 88, 130, 131, 175, 220, 265, 310, 355, 356, 357, 400, 435}
TOTAL_FRAMES = 450
WIDTH = 640
HEIGHT = 480


def _run_repair_script():
    """Run the video_repair.py script and return the result."""
    script_path = Path("/app/video_repair.py")
    assert script_path.exists(), "video_repair.py does not exist at /app/video_repair.py"

    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        cwd="/app",
        timeout=300,
    )
    return result


def test_video_repair_script_runs():
    """Test that video_repair.py runs without errors."""
    result = _run_repair_script()
    assert result.returncode == 0, (
        f"video_repair.py failed with exit code {result.returncode}\n"
        f"STDERR: {result.stderr}\n"
        f"STDOUT: {result.stdout}"
    )


def test_corrupted_frames_json_exists():
    """Test that corrupted_frames.json was created."""
    # Run the script first
    _run_repair_script()
    json_path = Path("/app/corrupted_frames.json")
    assert json_path.exists(), (
        "corrupted_frames.json was not created at /app/corrupted_frames.json"
    )


def test_corrupted_frames_json_content():
    """Test that corrupted_frames.json contains the correct frame indices."""
    _run_repair_script()
    json_path = Path("/app/corrupted_frames.json")

    with open(json_path) as f:
        data = json.load(f)

    assert isinstance(data, list), (
        f"corrupted_frames.json must contain a JSON list, got {type(data).__name__}"
    )
    assert len(data) > 0, "corrupted_frames.json must not be empty"

    detected = set(data)
    missing = EXPECTED_CORRUPTED - detected
    extra = detected - EXPECTED_CORRUPTED

    assert not missing, (
        f"Missing corrupted frames: {sorted(missing)}. "
        f"Detected: {sorted(detected)}. Expected: {sorted(EXPECTED_CORRUPTED)}"
    )
    assert not extra, (
        f"Extra frames incorrectly flagged as corrupted: {sorted(extra)}. "
        f"Detected: {sorted(detected)}. Expected: {sorted(EXPECTED_CORRUPTED)}"
    )


def test_repaired_video_exists():
    """Test that repaired_video.mp4 was created."""
    _run_repair_script()
    video_path = Path("/app/repaired_video.mp4")
    assert video_path.exists(), (
        "repaired_video.mp4 was not created at /app/repaired_video.mp4"
    )


def test_repaired_video_frame_count():
    """Test that repaired video has the correct number of frames."""
    _run_repair_script()
    import cv2

    cap = cv2.VideoCapture("/app/repaired_video.mp4")
    assert cap.isOpened(), "Could not open repaired_video.mp4"

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    assert frame_count == TOTAL_FRAMES, (
        f"Repaired video has {frame_count} frames, expected {TOTAL_FRAMES}"
    )


def test_repaired_video_resolution():
    """Test that repaired video has the correct resolution."""
    _run_repair_script()
    import cv2

    cap = cv2.VideoCapture("/app/repaired_video.mp4")
    assert cap.isOpened(), "Could not open repaired_video.mp4"

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    assert w == WIDTH, f"Repaired video width is {w}, expected {WIDTH}"
    assert h == HEIGHT, f"Repaired video height is {h}, expected {HEIGHT}"


def test_corrupted_frames_repaired():
    """Test that corrupted frames in the repaired video are NOT identical to
    the corrupted frames in the original video.

    We compare the repaired frames to the original corrupted frames. If the
    repair worked, the repaired frames should be significantly different from
    the corrupted ones (they should be interpolated, not just copied).
    """
    _run_repair_script()
    import cv2

    # Read original corrupted video
    cap_orig = cv2.VideoCapture("/app/corrupted_video.mp4")
    assert cap_orig.isOpened(), "Could not open corrupted_video.mp4"

    # Read repaired video
    cap_rep = cv2.VideoCapture("/app/repaired_video.mp4")
    assert cap_rep.isOpened(), "Could not open repaired_video.mp4"

    for idx in sorted(EXPECTED_CORRUPTED):
        cap_orig.set(cv2.CAP_PROP_POS_FRAMES, idx)
        cap_rep.set(cv2.CAP_PROP_POS_FRAMES, idx)

        ret_orig, frame_orig = cap_orig.read()
        ret_rep, frame_rep = cap_rep.read()

        assert ret_orig and ret_rep, f"Could not read frame {idx}"

        # Compute mean absolute difference between original corrupted frame
        # and repaired frame. If repair worked, they should be different.
        diff = np.mean(np.abs(frame_orig.astype(np.float64) - frame_rep.astype(np.float64)))

        assert diff > 2.0, (
            f"Frame {idx} in repaired video is too similar to corrupted frame "
            f"(mean absolute difference = {diff:.1f}). "
            f"The corrupted frame was likely not repaired."
        )

    cap_orig.release()
    cap_rep.release()


def test_repaired_video_playable():
    """Test that the repaired video is playable and has valid frames."""
    _run_repair_script()
    import cv2

    cap = cv2.VideoCapture("/app/repaired_video.mp4")
    assert cap.isOpened(), "Could not open repaired_video.mp4"

    frames_read = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frames_read += 1
        # Check that frame has valid dimensions and data
        assert frame.shape == (HEIGHT, WIDTH, 3), (
            f"Frame {frames_read} has shape {frame.shape}, expected ({HEIGHT}, {WIDTH}, 3)"
        )

    cap.release()
    assert frames_read == TOTAL_FRAMES, (
        f"Read {frames_read} frames from repaired video, expected {TOTAL_FRAMES}"
    )


