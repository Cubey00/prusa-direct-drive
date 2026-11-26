"""Measure: where the head goes to put the sensor above a part of the plate. No Qt.

The plate manifest has every part and fiducial cone in bed coordinates. The calibration gives the
head position with the sensor on each cone (this session's wizard, or the positions saved in
machine_settings.toml [calibration]). A 3-point fit from the cones' bed X/Y to those head X/Y
maps any bed point to the head X/Y that puts the sensor right above it; Z comes from the
measured sensor offset: head Z = height under the sensor + clearance - offset_z.
"""
from dataclasses import dataclass

from . import calibration

# Defaults of the approach settings in the Measure box (changeable there, remembered per laptop):
HOVER_MM = 3.0              # sensor this far above the top of the part before touching (less = faster)
HOVER_TEXT = f"{HOVER_MM:g} mm"
CREEP_SAFE_MM = 1.5         # of the hover gap, this last part is crept slowly (the part may be a bit higher)
CREEP_FREE_MM = max(0.0, HOVER_MM - CREEP_SAFE_MM)   # the rest is known free: faster
FAST_SPEED = 2.0            # mm/s over the known-free part
SLOW_SPEED = 0.1            # mm/s over the last part, where the object is expected


class MeasureError(Exception):
    pass


@dataclass
class HoverTarget:
    part_id: str
    bed_x: float
    bed_y: float
    top_z: float              # bed Z of the part's top
    x: float                  # head position
    y: float
    travel_z: float           # head Z to drive over the plate at
    z: float                  # head Z with the sensor HOVER_MM above the part
    source: str               # where the cone positions came from


SINGLE_POINT = "F2"          # "Only cone 2": only this cone saved


def machine_points(session_cal, settings, manifest=None):
    """Head X/Y on F1-F3: this session's calibration if complete, else the saved cone positions,
    else two saved cones (automatic calibration: the third follows from a similarity fit), else
    only the saved F2 plus each cone's bed distance from F2 (shift only, plate square).
    Returns (dict name -> (x, y), source text) or (None, reason)."""
    if session_cal is not None and all(session_cal.machine[p] is not None for p in calibration.POINTS):
        return dict(session_cal.machine), "calibration of this session"
    saved = {p: settings.calibration.cone_position(p) for p in calibration.POINTS}
    if all(saved.values()):
        return {p: (v[0], v[1]) for p, v in saved.items()}, "saved cone positions"
    fids = {f["name"]: f for f in (manifest or {}).get("fiducials", [])}
    two = [p for p in calibration.POINTS if saved[p]]
    if len(two) == 2 and all(p in fids for p in calibration.POINTS):     # automatic calibration
        from .autocal import third_from_two
        a, b = two
        (c,) = [p for p in calibration.POINTS if p not in two]
        bed = {p: (fids[p]["x"], fids[p]["y"]) for p in calibration.POINTS}
        pts = {a: saved[a][:2], b: saved[b][:2]}
        pts[c] = third_from_two(bed[a], pts[a], bed[b], pts[b], bed[c])
        return pts, f"saved positions of cones {a[1]} and {b[1]}"
    f2 = saved[SINGLE_POINT]
    if f2 and all(p in fids for p in calibration.POINTS):
        b2 = fids[SINGLE_POINT]
        return ({p: (f2[0] + fids[p]["x"] - b2["x"], f2[1] + fids[p]["y"] - b2["y"]) for p in calibration.POINTS},
                "saved position of cone 2")
    return None, "Calibrate first (Start calibration): the printer does not know where the cones are yet."


def bed_transform(manifest, machine):
    """Fit bed X/Y -> head X/Y on the three cones. Face coordinates are the bed ones mirrored in Y
    (face y = -bed Y), which is what calibration.fit expects."""
    cal = calibration.CalPoints()
    fids = {f["name"]: f for f in manifest["fiducials"]}
    for p in calibration.POINTS:
        if p not in fids:
            raise MeasureError(f"Cone {p} is missing from the plate file.")
        cal.face[p] = (float(fids[p]["x"]), -float(fids[p]["y"]))
        cal.machine[p] = tuple(map(float, machine[p]))
    try:
        t = calibration.fit(cal)
    except ValueError as e:
        raise MeasureError(f"Calibration is not right: {e}")
    return lambda bx, by: t.face_to_machine(bx, -by)


def hover_target(manifest, settings, session_cal=None, part_index=0, hover=HOVER_MM):
    """Head position that hangs the sensor `hover` mm above part `part_index` of the plate."""
    if not manifest or not manifest.get("parts"):
        raise MeasureError("Open a plate first (Print plates): no parts are loaded.")
    if not 0 <= part_index < len(manifest["parts"]):
        raise MeasureError(f"Point {part_index + 1} does not exist: the plate has {len(manifest['parts'])} parts.")
    if not settings.sensor.measured and not any(settings.calibration.cone_position(p) for p in calibration.POINTS):
        raise MeasureError("The sensor height has not been measured yet: no successful calibration has been "
                           "saved. Calibrate first until 'Calibration successful!' (Start calibration), otherwise the "
                           "sensor can crash into the part.")
    machine, source = machine_points(session_cal, settings, manifest)
    if machine is None:
        raise MeasureError(source)
    to_head = bed_transform(manifest, machine)
    part = manifest["parts"][part_index]
    bx, by = float(part["x"]), float(part["y"])
    top = float(part["size"][2])
    x, y = to_head(bx, by)
    offset_z = settings.sensor.offset[2]
    z = top + hover - offset_z
    tallest = max([float(f["z"]) for f in manifest["fiducials"]] + [float(p["size"][2]) for p in manifest["parts"]])
    travel_z = max(z, tallest + settings.sensor.hover_clearance - offset_z)
    (x0, x1), (y0, y1) = settings.printer.travel
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        raise MeasureError(f"Point {part_index + 1} is out of reach of the head (X{x:.1f} Y{y:.1f}).")
    if not 0 <= z <= travel_z <= settings.printer.z_max:
        raise MeasureError(f"Height out of range (Z{z:.1f} / {travel_z:.1f}, max {settings.printer.z_max:g}).")
    return HoverTarget(part["id"], bx, by, top, x, y, travel_z, z, source)
