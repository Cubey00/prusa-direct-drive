"""Four unique chart markers, followed by local square/edge localization per frame.

Homography is used only for search seeds. SFR always uses original sensor pixels;
local square contours accommodate field distortion without warping the image.
"""
import json
from pathlib import Path
import cv2
import numpy as np

from .lens import distort_points, rectification_maps


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
    if "checkerboard" in chart:
        board = chart["checkerboard"]
        cols, rows = board["inner_corners"]
        if (any(type(n) is not int or not 3 <= n <= 30 for n in (cols, rows))
                or not np.isfinite(board["square_mm"]) or board["square_mm"] <= 0):
            raise ValueError("Invalid checkerboard spacing or inner corner counts")
    return chart


def _missing_markers(detected_ids, expected_ids):
    unique_ids = set(detected_ids)
    return len(unique_ids) == len(detected_ids) < len(expected_ids) and unique_ids.issubset(expected_ids)


def register_chart(gray, chart, *, optical_center_px=None, sensor_pitch=1.0,
                   max_reprojection_rms_px=3.0, lens_calibration=None, sensor_offset=(0, 0)):
    image = np.uint8(np.clip(gray, 0, 1) * 255)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    detector = cv2.aruco.ArucoDetector(dictionary)
    corners, ids, _ = detector.detectMarkers(image)
    expected_ids = [0, 1, 2, 3]
    detected_ids = ids.ravel().tolist() if ids is not None else []
    rectified_matrix = None
    detection_image = image
    if lens_calibration is not None and _missing_markers(detected_ids, expected_ids):
        rectified_matrix, maps = rectification_maps(
            lens_calibration, sensor_pitch, sensor_offset, (image.shape[1], image.shape[0]))
        detection_image = cv2.remap(image, *maps, cv2.INTER_LINEAR)
        corners, ids, _ = detector.detectMarkers(detection_image)
        detected_ids = ids.ravel().tolist() if ids is not None else []
    if _missing_markers(detected_ids, expected_ids):
        # Uneven light can break a marker contour at the default threshold.
        params = cv2.aruco.DetectorParameters()
        params.adaptiveThreshConstant += 2
        retry = cv2.aruco.ArucoDetector(dictionary, params)
        retry_corners, ids, _ = retry.detectMarkers(detection_image)
        retry_ids = ids.ravel().tolist() if ids is not None else []
        if sorted(retry_ids) == expected_ids:
            corners, detected_ids = retry_corners, retry_ids
    if sorted(detected_ids) != expected_ids:
        missing_ids = sorted(set(expected_ids).difference(detected_ids))
        raise ValueError(
            "All four unique ArUco markers 0,1,2,3 must be visible. "
            f"Detected IDs {sorted(detected_ids) or 'none'}, missing IDs {missing_ids or 'none'}. "
            "Check the printed chart and the light margin around each marker."
        )
    found = dict(zip(detected_ids, corners))
    source, observed = [], []
    for f in chart["fiducials"]:
        x, y = f["top_left_mm"]
        s = f["size_mm"]
        source.extend([[x, y], [x+s, y], [x+s, y+s], [x, y+s]])
        observed.extend(found[f["id"]].reshape(4, 2))
    observed = np.asarray(observed, dtype=float)
    ideal = observed
    if lens_calibration is not None:
        raw_matrix = lens_calibration.analysis_matrix(sensor_pitch, sensor_offset)
        coeffs = np.asarray(lens_calibration.dist_coeffs)
        ideal_matrix = raw_matrix if rectified_matrix is None else rectified_matrix
        if rectified_matrix is None:
            ideal = lens_calibration.undistort(observed, raw_matrix)
            round_trip = distort_points(ideal, ideal_matrix, raw_matrix, coeffs, model=lens_calibration.model)
            if not np.isfinite(round_trip).all() or np.max(np.linalg.norm(round_trip - observed, axis=1)) * sensor_pitch > 0.5:
                raise ValueError("Lens calibration could not invert observed marker coordinates")
        else:
            observed = distort_points(ideal, ideal_matrix, raw_matrix, coeffs, model=lens_calibration.model)
    h, _ = cv2.findHomography(np.asarray(source, dtype=float), ideal, 0)
    if h is None:
        raise ValueError("Degenerate chart localization")

    def project_chart(points):
        mapped = project(h, points)
        if lens_calibration is not None:
            mapped = distort_points(mapped, ideal_matrix, raw_matrix, coeffs, model=lens_calibration.model)
        if not np.isfinite(mapped).all():
            raise ValueError("Chart projection produced nonfinite coordinates")
        return mapped

    mapped = project_chart(source)
    error_px = float(np.sqrt(np.mean(np.sum((mapped - observed)**2, axis=1)))) * sensor_pitch
    # Reject excessive distortion rather than asserting a homography corrects it.
    if error_px > max_reprojection_rms_px:
        suggestion = ("Verify the lens profile matches this camera setup."
                      if lens_calibration is not None else "For a distorted lens, load a measured lens calibration.")
        raise ValueError(
            f"All four ArUco markers decoded. Marker reprojection RMS {error_px:.3f} sensor pixels "
            f"exceeds the {max_reprojection_rms_px:g} sensor-pixel limit. "
            f"Check the printed layout and chart flatness. {suggestion}"
        )
    f = chart["visible_field"]
    field_points = [[f["left"], f["top"]], [f["left"]+f["width"], f["top"]],
                    [f["left"]+f["width"], f["top"]+f["height"]], [f["left"], f["top"]+f["height"]]]
    center = project_chart([[f["left"]+f["width"]/2, f["top"]+f["height"]/2]])[0] * sensor_pitch + sensor_offset
    width, height = (lens_calibration.image_size_px if lens_calibration is not None else
                     (gray.shape[1]*sensor_pitch, gray.shape[0]*sensor_pitch))
    target = np.asarray(optical_center_px if optical_center_px is not None else
                        [width/2, height/2], float)
    field_corners = project_chart(field_points) * sensor_pitch + sensor_offset
    perimeter = field_corners
    if lens_calibration is not None:
        boundary = np.concatenate([np.linspace(a, b, 17) for a, b in
                                   zip(field_points, field_points[1:] + field_points[:1])])
        perimeter = project_chart(boundary) * sensor_pitch + sensor_offset
    inside = bool(np.all(perimeter >= 2*sensor_pitch) and
                  np.all(perimeter < [width-2*sensor_pitch, height-2*sensor_pitch]))
    native_corners = observed.reshape(-1, 4, 2)*sensor_pitch + sensor_offset
    marker_corners = {str(f["id"]): corners for f, corners in zip(chart["fiducials"], native_corners)}
    return dict(homography=h, project_chart=project_chart, reprojection_rms_px=error_px,
                lens_corrected=lens_calibration is not None, rectified_marker_detection=rectified_matrix is not None,
                marker_corners_px={k: v.tolist() for k, v in marker_corners.items()},
                marker_centers_px={k: v.mean(axis=0).tolist() for k, v in marker_corners.items()},
                field_corners_px=field_corners.tolist(), chart_center_px=center.tolist(),
                optical_center_px=target.tolist(), center_error_px=(center-target).tolist(),
                optical_center_calibrated=optical_center_px is not None, full_field_visible=inside)


def detect_chart(gray, chart):
    registration = register_chart(gray, chart)
    return registration["homography"], registration["reprojection_rms_px"]


def register_capture(capture, chart, recipe, *, optical_center_px=None):
    from .acquisition import analysis_plane

    gray, pitch = analysis_plane(capture.pixels, capture.metadata)
    lens = recipe.lens_calibration
    if lens is not None:
        lens.validate_capture(capture)
    # The native G1 lattice starts one column to the right of the image origin.
    offset = (1, 0) if lens is not None and pitch == 2 else (0, 0)
    registration = register_chart(gray, chart, optical_center_px=optical_center_px,
                                  sensor_pitch=pitch, sensor_offset=offset, lens_calibration=lens,
                                  max_reprojection_rms_px=recipe.quality.max_marker_reprojection_px)
    return registration, gray, pitch


def regional_metrics(capture, chart, recipe, *, optical_center_px=None, require_usable_edges=True):
    from .quality import required_regions, usable_edges
    from .sfr import slanted_edge
    capture.validate()
    registration, gray, pitch = register_capture(capture, chart, recipe, optical_center_px=optical_center_px)
    project_chart = registration.pop("project_chart")
    if not registration["full_field_visible"]:
        raise ValueError("Chart field is clipped; position manually before assisted motion")
    required = required_regions(chart)
    metrics, flags = {}, {}
    for square in chart["slanted_squares"]:
        region = f"r{square['row']}_c{square['col']}"
        if region not in required:
            continue
        values, failures, labels = [], [], []
        try:
            for label, horizontal, roi, _, _ in edge_rois(gray, registration["homography"], square,
                                                          project_chart=project_chart):
                try:
                    values.append(slanted_edge(roi, recipe.fixed_frequency_cpp, horizontal=horizontal,
                                               sensor_pitch=pitch, quality=recipe.quality).fixed_mtf)
                    labels.append(label)
                except ValueError as exc:
                    failures.append(str(exc))
        except ValueError as exc:
            failures.append(str(exc))
        if usable_edges(set(labels), recipe.quality):
            metrics[region] = float(np.median(values))
        elif not failures:
            failures.append("Insufficient usable edges or orientation coverage")
        flags[region] = failures
    missing = set(required) - metrics.keys()
    if missing and require_usable_edges:
        raise ValueError("Unusable center/peripheral edges: " + "; ".join(f"{r}: {flags[r]}" for r in sorted(missing)))
    return registration, metrics, flags


def project(h, points):
    return cv2.perspectiveTransform(np.float32(points).reshape(-1, 1, 2), h).reshape(-1, 2)


def edge_rois(gray, h, square, *, project_chart=None):
    cx, cy = square["center_mm"]
    side = square["side_mm"]
    points = [[cx, cy], [cx+side, cy], [cx, cy+side]]
    predicted = project_chart(points) if project_chart is not None else project(h, points)
    scale = min(np.linalg.norm(predicted[1] - predicted[0]), np.linalg.norm(predicted[2] - predicted[0]))
    px, py = predicted[0]
    radius = int(scale * 0.95)
    x0, y0 = int(px) - radius, int(py) - radius
    x1, y1 = int(px) + radius, int(py) + radius
    if x0 < 0 or y0 < 0 or x1 >= gray.shape[1] or y1 >= gray.shape[0]:
        raise ValueError("Square search ROI crosses image border")
    if scale < 30:
        raise ValueError("Square too small for reliable edge analysis")
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
        if x-half_w < 0 or y-half_h < 0 or x+half_w > gray.shape[1] or y+half_h > gray.shape[0]:
            raise ValueError("Edge ROI crosses image border")
        roi = gray[y-half_h:y+half_h, x-half_w:x+half_w]
        # Sort by physical image side, retaining two orientation groups.
        label = ("top" if mid[1] < vertices[:, 1].mean() else "bottom") if horizontal else (
            "left" if mid[0] < vertices[:, 0].mean() else "right")
        rois.append((label, horizontal, roi, float(mid[0]), float(mid[1])))
    return rois
