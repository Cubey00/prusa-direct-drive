import json
from pathlib import Path
import xml.etree.ElementTree as ET

import cv2
import numpy as np
import pytest

from focusfield.chart import detect_chart, edge_rois, load_chart
from focusfield.config import DEFAULT_CHART, Recipe, simulation_fixture
from focusfield.generate_chart import build_parser, generate_chart
from focusfield.hardware import SimCamera, SimStage


def test_supplied_chart_and_generated_chart_match(tmp_path):
    output = tmp_path / "target.svg"
    generate_chart(build_parser().parse_args(["--output", str(output)]))
    generated = load_chart(tmp_path / "target_layout.json")
    supplied = load_chart(DEFAULT_CHART)
    assert generated == supplied
    assert len(supplied["slanted_squares"]) == 31
    svg = ET.parse(output).getroot()
    assert svg.attrib["width"] == "100mm" and svg.attrib["height"] == "80mm"


def test_rendered_supplied_svg_has_four_correct_markers(qapp):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    image = QImage(1600, 1280, QImage.Format_RGBA8888)
    image.fill(0xffffffff)
    renderer = QSvgRenderer(str(DEFAULT_CHART.with_name("focus_field_chart.svg")))
    assert renderer.isValid()
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, 1600, 1280))
    painter.end()
    rgb = np.frombuffer(image.bits(), dtype=np.uint8).reshape(1280, 1600, 4)[..., :3]
    gray = rgb.mean(axis=2) / 255
    h, error = detect_chart(gray, load_chart(DEFAULT_CHART))
    assert error < 1
    assert h.shape == (3, 3)


def test_marker_identity_partial_coverage_and_local_edges():
    chart = load_chart(DEFAULT_CHART)
    camera = SimCamera(SimStage(simulation_fixture()), Recipe(), chart)
    gray = camera.capture().pixels[..., 1] / 255
    h, error = detect_chart(gray, chart)
    assert error < 1
    assert all(len(edge_rois(gray, h, square)) == 4 for square in chart["slanted_squares"])
    gray[:400, :480] = 0.8
    with pytest.raises(ValueError, match="four unique"):
        detect_chart(gray, chart)


@pytest.mark.parametrize("args", [["--rows", "2"], ["--angle", "0"], ["--field-width", "-5"],
                                  ["--square-size", "99"], ["--marker-size", "40"]])
def test_generator_rejects_invalid_geometry(tmp_path, args):
    with pytest.raises(ValueError):
        generate_chart(build_parser().parse_args(["--output", str(tmp_path / "bad.svg"), *args]))
    assert not (tmp_path / "bad.svg").exists()
