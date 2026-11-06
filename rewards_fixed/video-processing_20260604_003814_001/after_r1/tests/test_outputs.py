import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _run_freeze_detector(video_path):
    """Run freeze_detector.py on a video and return the parsed TOML as a dict."""
    result = subprocess.run(
        [find_system_python(), "/app/freeze_detector.py", str(video_path)],
        capture_output=True,
        text=True,
        cwd="/app",
        timeout=180,
    )
    assert result.returncode == 0, (
        f"freeze_detector.py failed with exit code {result.returncode}\n"
        f"STDERR: {result.stderr}\nSTDOUT: {result.stdout}"
    )

    report_path = Path("/app/freeze_report.toml")
    assert report_path.exists(), (
        f"freeze_report.toml was not created. STDOUT: {result.stdout}"
    )

    with open(report_path) as f:
        raw = f.read()
    return _parse_toml_simple(raw)


def _parse_toml_simple(text):
    """Parse a simple TOML file into a nested dict."""
    result = {}
    current_table = result
    array_tables = {}

    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        aot_match = re.match(r'^\[\[([^]]+)\]\]\s*$', stripped)
        if aot_match:
            name = aot_match.group(1).strip()
            if name not in array_tables:
                array_tables[name] = []
                result[name] = array_tables[name]
            array_tables[name].append({})
            current_table = array_tables[name][-1]
            continue

        table_match = re.match(r'^\[([^]]+)\]$', stripped)
        if table_match:
            name = table_match.group(1).strip()
            parts = name.split('.')
            current_table = result
            for part in parts:
                if part not in current_table:
                    current_table[part] = {}
                current_table = current_table[part]
            continue

        kv_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_.]*)\s*=\s*(.+)$', stripped)
        if kv_match:
            key = kv_match.group(1).strip()
            val_str = kv_match.group(2).strip()
            current_table[key] = _parse_toml_value(val_str)

    return result


def _parse_toml_value(val_str):
    """Parse a TOML value string into a Python object."""
    val_str = val_str.strip()

    if (val_str.startswith('"') and val_str.endswith('"')) or \
       (val_str.startswith("'") and val_str.endswith("'")):
        return val_str[1:-1]

    if val_str.lower() == 'true':
        return True
    if val_str.lower() == 'false':
        return False

    if '.' in val_str or 'e' in val_str.lower():
        try:
            return float(val_str)
        except ValueError:
            pass

    try:
        return int(val_str)
    except ValueError:
        pass

    inline_match = re.match(r'^\{\s*(.*)\s*\}$', val_str)
    if inline_match:
        inner = inline_match.group(1)
        result = {}
        parts = _split_inline_table(inner)
        for part in parts:
            part = part.strip()
            if '=' in part:
                k, v = part.split('=', 1)
                result[k.strip()] = _parse_toml_value(v.strip())
        return result

    if val_str.startswith('[') and val_str.endswith(']'):
        inner = val_str[1:-1].strip()
        if not inner:
            return []
        parts = _split_inline_table(inner)
        return [_parse_toml_value(p.strip()) for p in parts]

    return val_str


def _split_inline_table(text):
    """Split a comma-separated list respecting nested brackets."""
    parts = []
    depth = 0
    current = []
    for ch in text:
        if ch in '{[(':
            depth += 1
        elif ch in '}])':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append(''.join(current))
    return parts


_SYSTEM_PYTHON = None


def find_system_python():
    """Find a Python that has cv2, numpy, and toml available."""
    global _SYSTEM_PYTHON
    if _SYSTEM_PYTHON is not None:
        return _SYSTEM_PYTHON

    candidates = [
        "/usr/local/bin/python3", "/usr/local/bin/python",
        "/usr/bin/python3", "/usr/bin/python",
        "python3", "python",
    ]
    for candidate in candidates:
        try:
            result = subprocess.run(
                [candidate, "-c", "import cv2, numpy, toml; print('OK')"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and "OK" in result.stdout:
                _SYSTEM_PYTHON = candidate
                return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    try:
        result = subprocess.run(
            [sys.executable, "-c", "import cv2, numpy, toml; print('OK')"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and "OK" in result.stdout:
            _SYSTEM_PYTHON = sys.executable
            return sys.executable
    except subprocess.TimeoutExpired:
        pass

    raise RuntimeError("Cannot find a Python interpreter with cv2, numpy, and toml")


def _generate_test_video_subprocess(output_path, freeze_ranges, black_ranges):
    """Generate a test video using the system Python (which has cv2, numpy)."""
    code = '''
import cv2, numpy as np, sys, json

output_path = sys.argv[1]
freeze_ranges = json.loads(sys.argv[2])
black_ranges = json.loads(sys.argv[3])
total_frames = 300
fps = 30
width, height = 960, 540

fourcc = cv2.VideoWriter_fourcc(*'mp4v')
writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
rng = np.random.RandomState(12345)
frozen_frames = {}

for fn in range(1, total_frames + 1):
    in_freeze = False
    for fs, fe in freeze_ranges:
        if fs <= fn < fe:
            in_freeze = True
            if fs not in frozen_frames:
                frame = np.full((height, width, 3), (50, 70, 90), dtype=np.uint8)
                cx = int(width * 0.3 + (fs / total_frames) * width * 0.4)
                cy = int(height * 0.4 + 40 * np.sin(fs * 0.25))
                cv2.circle(frame, (cx, cy), 35, (255, 180, 50), -1)
                cv2.putText(frame, f"F{fs}", (30, 40),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
                frozen_frames[fs] = frame
            frame = frozen_frames[fs].copy()
            break
    if in_freeze:
        writer.write(frame)
        continue

    in_black = False
    for bs, be in black_ranges:
        if bs <= fn < be:
            in_black = True
            break
    if in_black:
        writer.write(np.zeros((height, width, 3), dtype=np.uint8))
        continue

    frame = np.zeros((height, width, 3), dtype=np.uint8)
    t = fn / fps
    for y in range(height):
        frame[y, :, 0] = np.clip(128 + 55 * np.sin((y / height) * 5 + t * 1.7), 0, 255).astype(np.uint8)
        frame[y, :, 1] = np.clip(128 + 45 * np.cos((y / height) * 3.5 + t * 1.3), 0, 255).astype(np.uint8)
        frame[y, :, 2] = np.clip(128 + 35 * np.sin((y / height) * 4.5 + t * 1.9), 0, 255).astype(np.uint8)

    for i in range(4):
        cx = int(width * 0.15 + (width * 0.7) * ((fn + i * 35) % total_frames) / total_frames)
        cy = int(height * 0.25 + 120 * np.sin(t * 2.5 + i * 1.2))
        color = (
            int(180 + 75 * np.sin(t * 0.8 + i)),
            int(140 + 100 * np.cos(t * 0.6 + i * 0.7)),
            int(90 + 60 * np.sin(t * 1.1 + i * 0.5)),
        )
        cv2.circle(frame, (cx, cy), 22, color, -1)

    writer.write(frame)

writer.release()
print("OK")
'''
    result = subprocess.run(
        [find_system_python(), "-c", code, str(output_path),
         json.dumps(freeze_ranges), json.dumps(black_ranges)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"Failed to generate test video: {result.stderr}"
    )


def test_freeze_detector_exists():
    """Verify the freeze_detector.py script exists."""
    assert Path("/app/freeze_detector.py").exists(), (
        "freeze_detector.py does not exist at /app/freeze_detector.py"
    )


def test_example_video_report():
    """Run freeze_detector on the example video and verify report structure."""
    data = _run_freeze_detector("/app/example_video.mp4")

    # Check top-level fields
    for key in ("video_path", "video_fps", "total_frames", "parameters",
                "freeze_events"):
        assert key in data, f"Missing top-level key: {key}"

    # Check parameters now use ssim_threshold
    params = data["parameters"]
    for key in ("ssim_threshold", "min_freeze_pairs", "black_frame_threshold"):
        assert key in params, f"Missing parameter: {key}"
    assert params["ssim_threshold"] > 0.9, "ssim_threshold should be near 0.995"

    assert isinstance(data["freeze_events"], list), "freeze_events must be a list"

    for event in data["freeze_events"]:
        for field in ("start_frame", "end_frame", "duration_pairs",
                       "duration_seconds", "mean_ssim"):
            assert field in event, f"Missing field '{field}' in freeze event"
        assert event["start_frame"] >= 1
        assert event["end_frame"] > event["start_frame"]
        assert event["duration_pairs"] > 0
        assert event["duration_seconds"] > 0
        assert 0 <= event["mean_ssim"] <= 1.0, (
            f"mean_ssim {event['mean_ssim']} out of [0,1] range"
        )

    # At least 2 freeze events should be detected
    assert len(data["freeze_events"]) >= 2, (
        f"Expected at least 2 freeze events, got {len(data['freeze_events'])}"
    )

    # No freeze event should overlap with black frames (frames 30-32)
    for event in data["freeze_events"]:
        sf, ef = event["start_frame"], event["end_frame"]
        overlap = not (ef < 30 or sf > 32)
        assert not overlap, (
            f"Freeze at {sf}-{ef} overlaps with black frames (30-32)"
        )

    # Verify both visualization outputs exist
    for fname in ("freeze_timeline.png", "ssim_plot.png"):
        assert Path(f"/app/{fname}").exists(), f"{fname} was not created"


def test_test_video_report():
    """Generate a hidden test video with known freeze positions and verify detection."""
    test_video_path = "/tmp/test_video.mp4"

    freeze_ranges = [(30, 56), (110, 161), (200, 211), (250, 301)]
    # Longer than min_freeze_duration so a detector that treats black as
    # static content would report it as a freeze.
    black_ranges = [(70, 96)]

    _generate_test_video_subprocess(test_video_path, freeze_ranges, black_ranges)
    assert Path(test_video_path).exists(), "Test video was not generated"

    data = _run_freeze_detector(test_video_path)

    assert data["total_frames"] == 300
    assert abs(data["video_fps"] - 30.0) < 1.0

    events = data["freeze_events"]

    # Expected freezes: frames 30-55, 110-160, 250-300
    # NOT detected: frames 200-210 (too short, < 0.5s)
    expected_freezes = [
        (30, 56, 25),
        (110, 161, 50),
        (250, 301, 50),
    ]

    # No black frame overlap
    for event in events:
        sf, ef = event["start_frame"], event["end_frame"]
        assert ef < 70 or sf > 95, (
            f"Freeze at {sf}-{ef} overlaps with black frames (70-95)"
        )

    # Each expected freeze should be detected
    for exp_start, exp_end, exp_pairs in expected_freezes:
        found = False
        for event in events:
            sf = event["start_frame"]
            ef = event["end_frame"]
            if abs(sf - exp_start) <= 3 and abs(ef - exp_end) <= 3:
                found = True
                assert abs(event["duration_pairs"] - exp_pairs) <= 5, (
                    f"Freeze ~{exp_start}-{exp_end}: expected ~{exp_pairs} pairs, "
                    f"got {event['duration_pairs']}"
                )
                break
        assert found, (
            f"Expected freeze ~{exp_start}-{exp_end} not found. "
            f"Detected: {[(e['start_frame'], e['end_frame']) for e in events]}"
        )

    # Short freeze (frames 200-210, ~0.33s) should NOT be detected
    for event in events:
        sf, ef = event["start_frame"], event["end_frame"]
        if sf <= 210 and ef >= 200:
            detected_span = ef - sf + 1
            short_span = 211 - 200
            assert detected_span > short_span * 3, (
                f"Short freeze at 200-210 ({short_span}frames, 0.33s) "
                f"should NOT be detected. Got {sf}-{ef}."
            )


def test_timeline_image():
    """Verify the timeline visualization image has proper colors."""
    code = '''
import cv2, numpy as np

img = cv2.imread("/app/freeze_timeline.png")
assert img is not None, "Could not read freeze_timeline.png"
h, w = img.shape[:2]
assert w >= 800, f"Width {w} too small"
assert h >= 60, f"Height {h} too small"

hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
lr1, ur1 = np.array([0, 50, 50]), np.array([10, 255, 255])
lr2, ur2 = np.array([170, 50, 50]), np.array([180, 255, 255])
red = cv2.countNonZero(cv2.inRange(hsv, lr1, ur1)) + cv2.countNonZero(cv2.inRange(hsv, lr2, ur2))
lg, ug = np.array([40, 50, 50]), np.array([80, 255, 255])
green = cv2.countNonZero(cv2.inRange(hsv, lg, ug))
total = h * w
assert red > 0.01 * total, f"Only {100*red/total:.1f}% red, need >1%"
assert green > 0.05 * total, f"Only {100*green/total:.1f}% green, need >5%"
print("OK")
'''
    result = subprocess.run(
        [find_system_python(), "-c", code],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"Timeline check failed: {result.stderr}"
    )


def test_ssim_plot():
    """Verify the SSIM plot image exists and has expected properties."""
    code = '''
import cv2, numpy as np

img = cv2.imread("/app/ssim_plot.png")
assert img is not None, "Could not read ssim_plot.png"
h, w = img.shape[:2]
assert w >= 800, f"Width {w} too small"
assert h >= 200, f"Height {h} too small"
# Should have at least 1% non-white pixels (plot content)
white_mask = np.all(img > 245, axis=2)
non_white_pct = 100 * np.sum(~white_mask) / (h * w)
assert non_white_pct > 0.5, f"Only {non_white_pct:.2f}% non-white pixels — no plot content?"
print("OK")
'''
    result = subprocess.run(
        [find_system_python(), "-c", code],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"SSIM plot check failed: {result.stderr}"
    )


def test_imports_only_allowed():
    """Verify freeze_detector.py only uses allowed third-party imports.

    Also checks that cv2.quality / cv2.QualitySSIM are NOT used.
    """
    import ast

    analyzer_path = Path("/app/freeze_detector.py")
    assert analyzer_path.exists(), "freeze_detector.py does not exist"

    allowed_third_party = {"cv2", "numpy", "toml"}

    source = analyzer_path.read_text()

    tree = ast.parse(source)
    found_imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = (alias.name or "").split(".")[0]
                if module_name:
                    found_imports.add(module_name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module_name = node.module.split(".")[0]
                if module_name:
                    found_imports.add(module_name)

    third_party_imports = set(
        m for m in found_imports if m not in sys.stdlib_module_names
    )
    forbidden_imports = third_party_imports - allowed_third_party

    forbidden_ssim_apis = (
        "cv2.quality" in source
        or "cv2.QualitySSIM" in source
        or "QualitySSIM_compute" in source
    )

    assert not forbidden_imports and not forbidden_ssim_apis, (
        f"Forbidden third-party imports found: {forbidden_imports}. "
        f"Only allowed: {allowed_third_party}; forbidden pre-built SSIM API "
        f"used: {forbidden_ssim_apis}"
    )
