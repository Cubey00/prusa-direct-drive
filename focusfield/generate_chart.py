#!/usr/bin/env python3
"""Generate a printable multi-region slanted-edge chart with four ArUco tags.

Example:
    python generate_focus_chart.py --output focus_chart.svg \
        --field-width 60 --field-height 45

Requires: pip install opencv-contrib-python numpy

Outputs:
    focus_chart.svg        True vector artwork, sized in millimetres.
    focus_chart.pdf        A4 vector artwork at actual size when the output ends in .pdf.
    focus_chart_layout.json  Positions/IDs for subsequent automatic analysis.

Important: --field-width and --field-height are the physical *visible area*
           expected in the camera image at the test working distance.
           Adjust them after measuring your real field of view.
"""

import argparse
import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    import cv2
except ImportError as exc:
    raise SystemExit("OpenCV not found. Install: pip install opencv-contrib-python") from exc


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def svg_element(parent, tag, **attributes):
    return ET.SubElement(parent, f"{{{SVG_NS}}}{tag}", {key.replace("_", "-"): str(value) for key, value in attributes.items()})


def add_text(parent, x, y, value, **attrs):
    element = svg_element(parent, "text", x=f"{x:.3f}", y=f"{y:.3f}", **attrs)
    element.text = value
    return element


def add_focus_aid(svg, x, y, radius, focus_distance_mm):
    """An alternating radial pattern for comparing the blurred center while focusing."""
    star = svg_element(svg, "g", id="lens-focus-star")
    for sector in range(0, 48, 2):
        a, b = math.tau * sector / 48, math.tau * (sector + 1) / 48
        ax, ay = x + radius * math.cos(a), y + radius * math.sin(a)
        bx, by = x + radius * math.cos(b), y + radius * math.sin(b)
        path = f"M {x:.4f},{y:.4f} L {ax:.4f},{ay:.4f} A {radius:g},{radius:g} 0 0 1 {bx:.4f},{by:.4f} Z"
        # Set an explicit negligible width for Qt SVG/PDF, which can otherwise stroke paths.
        svg_element(star, "path", d=path, fill="black", stroke="none", stroke_width="0.0001")
    text_x = x + radius + 7
    font = {"font_family": "Arial, sans-serif", "font_size": "2.5"}
    add_text(svg, text_x, y - 5, "LENS FOCUS  |  SIEMENS STAR", **font)
    distance = (f"Set the working distance to {focus_distance_mm:g} mm." if focus_distance_mm is not None
                else "Set the planned working distance.")
    add_text(svg, text_x, y, distance, **font)
    add_text(svg, text_x, y + 5, "Adjust focus until the blurred center is smallest. Lock the lens.", **font)


def save_a4_pdf(svg, out):
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QMarginsF, QRectF
    from PySide6.QtGui import QColor, QGuiApplication, QPageLayout, QPageSize, QPainter, QPdfWriter
    from PySide6.QtSvg import QSvgRenderer

    _, _, width, height = map(float, svg.attrib["viewBox"].split())
    margin = 5
    paper = QPageSize(QPageSize.PageSizeId.A4)
    size = paper.size(QPageSize.Unit.Millimeter)
    paper_width, paper_height = size.width(), size.height()
    orientation = QPageLayout.Orientation.Portrait
    if width + 2*margin > paper_width or height + 2*margin > paper_height:
        orientation = QPageLayout.Orientation.Landscape
        paper_width, paper_height = paper_height, paper_width
    if width + 2*margin > paper_width or height + 2*margin > paper_height:
        raise ValueError("The sheet is too large for one A4 page at actual size. Save the SVG for larger paper.")

    # Keep the application alive during vector export when invoked from the CLI.
    _app = QGuiApplication.instance() or QGuiApplication(["focusfield-chart", "-platform", "offscreen"])
    renderer = QSvgRenderer(QByteArray(ET.tostring(svg, encoding="utf-8")))
    if not renderer.isValid():
        raise ValueError("The target sheet could not be rendered")
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QPdfWriter(buffer)
    writer.setTitle("Focus-Field MTF target sheet")
    writer.setResolution(720)
    writer.setPageLayout(QPageLayout(paper, orientation, QMarginsF(), QPageLayout.Unit.Millimeter))
    units_per_mm = writer.resolution() / 25.4
    rect = QRectF((paper_width - width) / 2 * units_per_mm, (paper_height - height) / 2 * units_per_mm,
                  width * units_per_mm, height * units_per_mm)
    painter = QPainter()
    if not painter.begin(writer):
        raise OSError("Could not start the A4 PDF export")
    try:
        painter.fillRect(QRectF(0, 0, paper_width*units_per_mm, paper_height*units_per_mm), QColor("white"))
        renderer.render(painter, rect)
    finally:
        painter.end()
    out.write_bytes(bytes(buffer.data()))


def add_aruco(parent, dictionary, marker_id, x, y, side_mm, quiet_mm):
    """Draw a true vector ArUco 4x4 marker with the standard 1-cell black border."""
    cells = 6  # DICT_4X4_50: 4 payload cells + 1 black-border cell each side
    pixels_per_cell = 24
    marker = cv2.aruco.generateImageMarker(
        dictionary, marker_id, cells * pixels_per_cell, borderBits=1
    )
    # Add a pure white quiet zone around the marker: needed for robust detection.
    svg_element(
        parent, "rect", x=f"{x-quiet_mm:.3f}", y=f"{y-quiet_mm:.3f}",
        width=f"{side_mm + 2*quiet_mm:.3f}",
        height=f"{side_mm + 2*quiet_mm:.3f}", fill="#FFFFFF"
    )
    marker_group = svg_element(parent, "g", id=f"aruco-{marker_id}")
    # A single solid black backing avoids anti-aliased seams between black modules.
    svg_element(marker_group, "rect", x=f"{x:.6f}", y=f"{y:.6f}",
                width=f"{side_mm:.6f}", height=f"{side_mm:.6f}", fill="#000000")
    cell_mm = side_mm / cells
    for row in range(cells):
        for col in range(cells):
            if int(marker[(row * pixels_per_cell) + pixels_per_cell // 2,
                          (col * pixels_per_cell) + pixels_per_cell // 2]) >= 128:
                svg_element(
                    marker_group, "rect",
                    x=f"{x + col * cell_mm:.6f}",
                    y=f"{y + row * cell_mm:.6f}",
                    width=f"{cell_mm:.6f}", height=f"{cell_mm:.6f}",
                    fill="#FFFFFF"
                )
    return {
        "id": marker_id,
        "top_left_mm": [round(x, 4), round(y, 4)],
        "size_mm": side_mm,
        "center_mm": [round(x + side_mm/2, 4), round(y + side_mm/2, 4)],
    }


def generate_chart(args):
    focus_distance = getattr(args, "focus_distance_mm", None)
    if focus_distance is not None and (not math.isfinite(focus_distance) or focus_distance <= 0):
        raise ValueError("The focus working distance must be a positive number")
    if args.rows < 3 or args.cols < 3:
        raise ValueError("Use at least 3 rows and 3 columns.")
    if not (2.5 <= abs(args.angle) <= 7.0):
        raise ValueError("Use an edge tilt between 2.5 and 7 degrees.")
    if args.field_width <= 0 or args.field_height <= 0:
        raise ValueError("Field dimensions must be positive.")
    if args.marker_size <= 0 or args.quiet_zone <= 0:
        raise ValueError("Marker size and quiet zone must be positive.")
    if args.page_width < args.field_width + 20 or args.page_height < args.field_height + 20:
        raise ValueError("Page must be at least 20 mm larger than the field in each dimension.")

    # Center the chart's intended visible field on the physical sheet.
    left = (args.page_width - args.field_width) / 2
    top = (args.page_height - args.field_height) / 2
    right = left + args.field_width
    bottom = top + args.field_height
    step_x = args.field_width / (args.cols + 1)
    step_y = args.field_height / (args.rows + 1)
    side = args.square_size if args.square_size is not None else min(step_x, step_y) * 0.48
    if side <= 0 or side > min(step_x, step_y) * 0.68:
        raise ValueError("Square size must be positive and at most 0.68 of grid spacing.")

    # Put the four corner tags entirely inside the intended visible field.
    # IDs: 0=top-left, 1=top-right, 2=bottom-right, 3=bottom-left.
    inset = args.quiet_zone + 0.25
    marker_xy = [
        (left + inset, top + inset),
        (right - inset - args.marker_size, top + inset),
        (right - inset - args.marker_size, bottom - inset - args.marker_size),
        (left + inset, bottom - inset - args.marker_size),
    ]
    if args.marker_size + 2 * (inset + args.quiet_zone) >= min(args.field_width, args.field_height) / 2:
        raise ValueError("Marker is too large for the selected visible field.")

    svg = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": f"{args.page_width:g}mm",
        "height": f"{args.page_height:g}mm",
        "viewBox": f"0 0 {args.page_width:g} {args.page_height:g}",
        "version": "1.1",
    })
    desc = ET.SubElement(svg, f"{{{SVG_NS}}}desc")
    desc.text = (
        f"Focus-field slanted edge chart. Field {args.field_width}x{args.field_height} mm; "
        f"{args.cols} columns x {args.rows} rows minus corners; slant {args.angle} degrees; "
        "ArUco DICT_4X4_50 IDs 0 TL, 1 TR, 2 BR, 3 BL. Print at 100% scale."
    )
    svg_element(svg, "rect", x="0", y="0", width=f"{args.page_width:g}",
                height=f"{args.page_height:g}", fill="#FFFFFF")
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)

    # Square centers: row-major order. Each square gives four slanted-edge ROIs.
    # The four corner grid positions are intentionally left empty for the tags.
    squares = []
    square_group = svg_element(svg, "g", id="slanted-edge-squares")
    cos_a = abs(math.cos(math.radians(args.angle)))
    sin_a = abs(math.sin(math.radians(args.angle)))
    square_radius = (side/2) * (cos_a + sin_a)
    for row in range(args.rows):
        for col in range(args.cols):
            if row in (0, args.rows - 1) and col in (0, args.cols - 1):
                continue
            cx = left + (col + 1) * step_x
            cy = top + (row + 1) * step_y
            # Guarantee ROIs cannot collide with the quiet zone of a fiducial.
            for mx, my in marker_xy:
                zone_left = mx - args.quiet_zone
                zone_top = my - args.quiet_zone
                zone_right = mx + args.marker_size + args.quiet_zone
                zone_bottom = my + args.marker_size + args.quiet_zone
                if (cx + square_radius > zone_left and cx - square_radius < zone_right
                        and cy + square_radius > zone_top and cy - square_radius < zone_bottom):
                    raise ValueError(
                        f"Square at row={row}, col={col} overlaps an ArUco quiet zone. "
                        "Try smaller squares/markers, more field area, or fewer grid squares."
                    )
            group = svg_element(square_group, "g", id=f"edge-r{row}-c{col}",
                                transform=f"translate({cx:.5f} {cy:.5f}) rotate({args.angle:g})")
            svg_element(group, "rect", x=f"{-side/2:.5f}", y=f"{-side/2:.5f}",
                        width=f"{side:.5f}", height=f"{side:.5f}",
                        fill=args.edge_color)
            squares.append({
                "row": row, "col": col,
                "center_mm": [round(cx, 4), round(cy, 4)],
                "side_mm": round(side, 4), "rotation_deg": args.angle,
            })

    tags_group = svg_element(svg, "g", id="aruco-fiducials")
    tag_positions = ("top_left", "top_right", "bottom_right", "bottom_left")
    tags = []
    for marker_id, ((x, y), position) in enumerate(zip(marker_xy, tag_positions)):
        tag = add_aruco(tags_group, dictionary, marker_id, x, y,
                        args.marker_size, args.quiet_zone)
        tag["position"] = position
        tags.append(tag)

    # Documentation and a physical scale reference outside the image field.
    # These are NOT measurement features and should not be in the camera image.
    header_y = top / 2 + 0.8
    add_text(svg, left, header_y, "FOCUS-FIELD / SLANTED-EDGE TARGET",
             fill="#333333", font_size="2.7", font_family="Arial, sans-serif")
    scale_y = bottom + (args.page_height - bottom) / 2 - 0.5
    svg_element(svg, "line", x1=f"{left:.3f}", y1=f"{scale_y:.3f}",
                x2=f"{left+10:.3f}", y2=f"{scale_y:.3f}",
                stroke="#000000", stroke_width="0.3")
    svg_element(svg, "line", x1=f"{left:.3f}", y1=f"{scale_y-1.0:.3f}",
                x2=f"{left:.3f}", y2=f"{scale_y+1.0:.3f}",
                stroke="#000000", stroke_width="0.3")
    svg_element(svg, "line", x1=f"{left+10:.3f}", y1=f"{scale_y-1.0:.3f}",
                x2=f"{left+10:.3f}", y2=f"{scale_y+1.0:.3f}",
                stroke="#000000", stroke_width="0.3")
    add_text(svg, left+12, scale_y+0.8, "10 mm  |  print at 100% size",
             fill="#333333", font_size="2.4", font_family="Arial, sans-serif")
    add_text(svg, left, header_y + 3.5,
             f"Visible field: {args.field_width:g} x {args.field_height:g} mm  |  ArUco 4x4 IDs: 0, 1, 2, 3",
             fill="#666666", font_size="2.1",
             font_family="Arial, sans-serif")

    checkerboard = None
    if getattr(args, "with_checkerboard", False):
        # The layout retains the MTF panel's coordinate system and simulation size.
        # The additional artwork is placed to its right on the same sheet.
        cols, rows = 8, 6
        square_mm = math.floor(10 * min(args.field_width / (2 * cols),
                                        args.field_height / (2 * rows))) / 10
        board_width, board_height = cols * square_mm, rows * square_mm
        panel_width = max(board_width + 24, 62)
        sheet_width = args.page_width + panel_width
        star_radius = min(12, min(args.field_width, args.field_height) / 5)
        focus_height = 20 + 2*star_radius
        sheet_height = args.page_height + focus_height
        svg.set("width", f"{sheet_width:g}mm")
        svg.set("height", f"{sheet_height:g}mm")
        svg.set("viewBox", f"0 0 {sheet_width:g} {sheet_height:g}")
        svg_element(svg, "rect", x=f"{args.page_width:g}", y="0", width=f"{panel_width:g}",
                    height=f"{args.page_height:g}", fill="white")
        x, y = args.page_width + (panel_width - board_width) / 2, (args.page_height - board_height) / 2
        add_text(svg, args.page_width + 8, y - 5, f"LENS CHECKERBOARD  |  {square_mm:g} mm squares",
                 font_size="2.3", font_family="Arial, sans-serif")
        group = svg_element(svg, "g", id="lens-checkerboard")
        for row in range(rows):
            for col in range(cols):
                if (row + col) % 2 == 0:
                    svg_element(group, "rect", x=f"{x+col*square_mm:.4f}", y=f"{y+row*square_mm:.4f}",
                                width=f"{square_mm:g}", height=f"{square_mm:g}", fill="black")
        checkerboard = {"inner_corners": [cols - 1, rows - 1], "square_mm": square_mm}
        svg_element(svg, "rect", x="0", y=f"{args.page_height:g}", width=f"{sheet_width:g}",
                    height=f"{focus_height:g}", fill="white")
        add_focus_aid(svg, 10 + star_radius, args.page_height + focus_height / 2,
                      star_radius, focus_distance)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() == ".pdf":
        save_a4_pdf(svg, out)
    else:
        ET.ElementTree(svg).write(out, encoding="utf-8", xml_declaration=True)
    layout = {
        "description": "Focus-field test chart physical design (not an MTF measurement).",
        "units": "mm", "page_width": args.page_width, "page_height": args.page_height,
        "visible_field": {"left": round(left, 4), "top": round(top, 4),
                          "width": args.field_width, "height": args.field_height},
        "aruco_dictionary": "DICT_4X4_50", "aruco_ids": [0, 1, 2, 3],
        "quiet_zone_mm": args.quiet_zone,
        "fiducials": tags,
        "slanted_squares": squares,
        "notes": [
            "Each square is rotated inside the flat target plane; do not tilt the whole plate.",
            "Printer/laser edge quality may limit measured system MTF.",
            "The fiducials locate chart geometry; they are not the slanted edges used for MTF.",
            "Chart physical dimensions must be verified after printing.",
        ],
    }
    layout_path = out.with_name(out.stem + "_layout.json")
    if checkerboard is not None:
        layout["checkerboard"] = checkerboard
    layout_path.write_text(json.dumps(layout, indent=2) + "\n", encoding="utf-8")
    print(f"Created: {out.resolve()}")
    print(f"Created: {layout_path.resolve()}")
    print(f"Slanted squares: {len(squares)}; fiducials: {len(tags)}")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="focus_field_chart.svg", help="Output SVG or A4 PDF filename")
    parser.add_argument("--field-width", type=float, default=60.0,
                        help="Expected visible target width at working distance, in mm")
    parser.add_argument("--field-height", type=float, default=45.0,
                        help="Expected visible target height at working distance, in mm")
    parser.add_argument("--page-width", type=float, default=100.0,
                        help="Total print sheet artwork width in mm")
    parser.add_argument("--page-height", type=float, default=80.0,
                        help="Total print sheet artwork height in mm")
    parser.add_argument("--rows", type=int, default=5)
    parser.add_argument("--cols", type=int, default=7)
    parser.add_argument("--square-size", type=float, default=None,
                        help="Square side in mm (automatic if omitted)")
    parser.add_argument("--marker-size", type=float, default=5.0,
                        help="ArUco marker side length in mm")
    parser.add_argument("--quiet-zone", type=float, default=1.25,
                        help="Minimum white space around ArUco markers, in mm")
    parser.add_argument("--angle", type=float, default=5.0,
                        help="Slanted edge angle in degrees (default: 5)")
    parser.add_argument("--edge-color", default="#404040",
                        help="Slanted square color, in SVG hex RGB; marker remains black")
    parser.add_argument("--with-checkerboard", action="store_true",
                        help="Add a lens checkerboard and a manual focus star to the sheet")
    parser.add_argument("--focus-distance-mm", type=float, default=None,
                        help="Working distance to print beside the focus star")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        generate_chart(args)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
