#!/usr/bin/env python3
"""Generate a synthetic test video with known shot boundaries."""
import cv2
import numpy as np
import sys

FPS, W, H = 30, 640, 480
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
out = cv2.VideoWriter(sys.argv[1], fourcc, FPS, (W, H))


def make_frame(color_bgr, shape_type='circle', shape_color=(255, 255, 255)):
    frame = np.full((H, W, 3), color_bgr, dtype=np.uint8)
    cx, cy = W // 2, H // 2
    if shape_type == 'circle':
        cv2.circle(frame, (cx, cy), 80, shape_color, -1)
    elif shape_type == 'square':
        cv2.rectangle(frame, (cx - 70, cy - 70), (cx + 70, cy + 70), shape_color, -1)
    elif shape_type == 'triangle':
        pts = np.array([[cx, cy - 90], [cx - 80, cy + 70], [cx + 80, cy + 70]], np.int32)
        cv2.fillPoly(frame, [pts], shape_color)
    elif shape_type == 'diamond':
        pts = np.array([[cx, cy - 80], [cx - 80, cy], [cx, cy + 80], [cx + 80, cy]], np.int32)
        cv2.fillPoly(frame, [pts], shape_color)
    elif shape_type == 'star':
        pts = []
        for i in range(10):
            angle = i * np.pi / 5 - np.pi / 2
            r = 80 if i % 2 == 0 else 35
            pts.append([int(cx + r * np.cos(angle)), int(cy + r * np.sin(angle))])
        cv2.fillPoly(frame, [np.array(pts, np.int32)], shape_color)
    elif shape_type == 'cross':
        cv2.rectangle(frame, (cx - 12, cy - 80), (cx + 12, cy + 80), shape_color, -1)
        cv2.rectangle(frame, (cx - 80, cy - 12), (cx + 80, cy + 12), shape_color, -1)
    return frame


shots = [
    (90, (0, 0, 220), 'circle', 'cut', 0),
    (90, (0, 180, 0), 'square', 'cut', 0),
    (60, (200, 80, 0), 'triangle', 'fade', 30),
    (90, (0, 200, 200), 'diamond', 'cut', 0),
    (60, (200, 0, 150), 'star', 'fade', 30),
    (90, (60, 60, 60), 'cross', 'cut', 0),
]

fi = 0
for i, (nf, col, shp, tt, tf) in enumerate(shots):
    if i > 0 and shots[i - 1][3] == 'fade':
        pc, ps = shots[i - 1][1], shots[i - 1][2]
        fl = shots[i - 1][4]
        for f in range(fl):
            a = (f + 1) / (fl + 1)
            f1 = make_frame(pc, ps)
            f2 = make_frame(col, shp)
            out.write(cv2.addWeighted(f1, 1.0 - a, f2, a, 0))
            fi += 1
    for f in range(nf):
        out.write(make_frame(col, shp))
        fi += 1

out.release()
print(f'Generated {fi} frames')
