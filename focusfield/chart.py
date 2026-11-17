"""Four unique chart markers, followed by local square/edge localization per frame.

Homography is used only for search seeds. SFR always uses original sensor pixels;
local square contours accommodate field distortion without warping the image.
"""
import json
from pathlib import Path
import cv2
import numpy as np


def load_chart(path):
    chart = json.loads(Path(path).read_text(encoding="utf-8"))
    if chart.get("units") != "mm" or chart.get("aruco_dictionary") != "DICT_4X4_50":
        raise ValueError("Chart must use millimetres and DICT_4X4_50")
    if chart.get("aruco_ids") != [0, 1, 2, 3] or sorted(f["id"] for f in chart["fiducials"]) != [0, 1, 2, 3]:
        raise ValueError("Chart requires unique corner marker IDs 0,1,2,3")
    for f in chart["fiducials"]:
        if f["size_mm"] <= 0 or not np.isfinite([f["size_mm"], *f["top_left_mm"]]).all():
            raise ValueError("Invalid marker geometry")
    regions = chart["slanted_squares"]
    if not regions or len({(s["row"], s["col"]) for s in regions}) != len(regions):
        raise ValueError("Chart regions must be nonempty and unique")
    for s in regions:
        if s["side_mm"] <= 0 or not np.isfinite([s["side_mm"], s["rotation_deg"], *s["center_mm"]]).all():
            raise ValueError("Invalid square geometry")
    field = chart["visible_field"]
    if (not np.isfinite([chart["page_width"], chart["page_height"], *field.values()]).all()
            or min(chart["page_width"], chart["page_height"], field["width"], field["height"]) <= 0):
        raise ValueError("Invalid page / visible field dimensions")
    return chart


def detect_chart(gray, chart):
    image = np.uint8(np.clip(gray, 0, 1) * 255)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    corners, ids, _ = cv2.aruco.ArucoDetector(dictionary).detectMarkers(image)
    if ids is None or sorted(ids.ravel().tolist()) != [0, 1, 2, 3]:
        raise ValueError("All four unique ArUco markers 0,1,2,3 must be visible")
    found = dict(zip(ids.ravel().tolist(), corners))
    source, target = [], []
    for f in chart["fiducials"]:
        x, y = f["top_left_mm"]
        s = f["size_mm"]
        source.extend([[x, y], [x+s, y], [x+s, y+s], [x, y+s]])
        target.extend(found[f["id"]].reshape(4, 2))
    h, _ = cv2.findHomography(np.float32(source), np.float32(target), 0)
    if h is None:
        raise ValueError("Degenerate chart localization")
    mapped = project(h, source)
    error = float(np.sqrt(np.mean(np.sum((mapped - target)**2, axis=1))))
    # Reject excessive distortion rather than asserting a homography corrects it.
    if error > max(3, min(image.shape) * 0.01):
        raise ValueError("Marker reprojection error too large; calibrate lens distortion / field of view")
    return h, error


def project(h, points):
    return cv2.perspectiveTransform(np.float32(points).reshape(-1, 1, 2), h).reshape(-1, 2)


def edge_rois(gray, h, square):
    cx, cy = square["center_mm"]
    side = square["side_mm"]
    predicted = project(h, [[cx, cy], [cx+side, cy], [cx, cy+side]])
    scale = min(np.linalg.norm(predicted[1] - predicted[0]), np.linalg.norm(predicted[2] - predicted[0]))
    px, py = predicted[0]
    radius = int(scale * 0.95)
    x0, y0 = int(px) - radius, int(py) - radius
    x1, y1 = int(px) + radius, int(py) + radius
    if x0 < 0 or y0 < 0 or x1 >= gray.shape[1] or y1 >= gray.shape[0] or scale < 30:
        raise ValueError("Square outside frame or too small")
    patch = gray[y0:y1, x0:x1]
    low, high = np.percentile(patch, [10, 90])
    mask = np.uint8(patch < (low+high)/2) * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        polygon = cv2.approxPolyDP(contour, 0.025 * cv2.arcLength(contour, True), True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        if not 0.4*scale**2 <= cv2.contourArea(polygon) <= 1.8*scale**2:
            continue
        vertices = polygon.reshape(4, 2) + [x0, y0]
        candidates.append(vertices)
    if not candidates:
        raise ValueError("Local slanted square not found")
    vertices = min(candidates, key=lambda v: np.linalg.norm(v.mean(axis=0)-predicted[0]))
    if np.linalg.norm(vertices.mean(axis=0)-predicted[0]) > scale * 0.4:
        raise ValueError("Local square too far from chart registration")
    rois = []
    for i in range(4):
        a, b = vertices[i], vertices[(i+1) % 4]
        mid = (a+b)/2
        horizontal = abs(a[0]-b[0]) > abs(a[1]-b[1])
        length = np.linalg.norm(a-b)
        half_w, half_h = (int(length*0.30), int(length*0.26))
        if not horizontal:
            half_w, half_h = half_h, half_w
        x, y = map(int, mid)
        roi = gray[y-half_h:y+half_h, x-half_w:x+half_w]
        # Sort by physical image side, retaining two orientation groups.
        label = ("top" if mid[1] < vertices[:, 1].mean() else "bottom") if horizontal else (
            "left" if mid[0] < vertices[:, 0].mean() else "right")
        rois.append((label, horizontal, roi, float(mid[0]), float(mid[1])))
    return rois
