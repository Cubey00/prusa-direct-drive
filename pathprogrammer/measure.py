"""Meten: where the head goes to put the sensor above a part of the plate. No Qt.

The plate manifest has every part and fiducial cone in bed coordinates. The calibration gives the
head position with the sensor on each cone (this session's wizard, or the positions saved in
machine_settings.toml [calibration]). A 3-point fit from the cones' bed X/Y to those head X/Y
maps any bed point to the head X/Y that puts the sensor right above it; Z comes from the
measured sensor offset: head Z = height under the sensor + clearance - offset_z.
"""
from dataclasses import dataclass

from . import calibration

HOVER_MM = 10.0             # "1 cm boven": sensor this far above the top of the part


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


SINGLE_POINT = "F2"          # "Alleen kegel 2": only this cone saved


def machine_points(session_cal, settings, manifest=None):
    """Head X/Y on F1-F3: this session's calibration if complete, else the saved cone positions,
    else only the saved F2 plus each cone's bed distance from F2 (shift only, plate square).
    Returns (dict name -> (x, y), source text) or (None, reason)."""
    if session_cal is not None and all(session_cal.machine[p] is not None for p in calibration.POINTS):
        return dict(session_cal.machine), "kalibratie van deze sessie"
    saved = {p: settings.calibration.cone_position(p) for p in calibration.POINTS}
    if all(saved.values()):
        return {p: (v[0], v[1]) for p, v in saved.items()}, "opgeslagen kegelposities"
    f2 = saved[SINGLE_POINT]
    fids = {f["name"]: f for f in (manifest or {}).get("fiducials", [])}
    if f2 and all(p in fids for p in calibration.POINTS):
        b2 = fids[SINGLE_POINT]
        return ({p: (f2[0] + fids[p]["x"] - b2["x"], f2[1] + fids[p]["y"] - b2["y"]) for p in calibration.POINTS},
                "opgeslagen positie van kegel 2")
    return None, "Kalibreer eerst (Start kalibratie): de printer weet nog niet waar de kegels zijn."


def bed_transform(manifest, machine):
    """Fit bed X/Y -> head X/Y on the three cones. Face coordinates are the bed ones mirrored in Y
    (face y = -bed Y), which is what calibration.fit expects."""
    cal = calibration.CalPoints()
    fids = {f["name"]: f for f in manifest["fiducials"]}
    for p in calibration.POINTS:
        if p not in fids:
            raise MeasureError(f"Kegel {p} ontbreekt in het plaatbestand.")
        cal.face[p] = (float(fids[p]["x"]), -float(fids[p]["y"]))
        cal.machine[p] = tuple(map(float, machine[p]))
    try:
        t = calibration.fit(cal)
    except ValueError as e:
        raise MeasureError(f"Kalibratie klopt niet: {e}")
    return lambda bx, by: t.face_to_machine(bx, -by)


def hover_target(manifest, settings, session_cal=None, part_index=0, hover=HOVER_MM):
    """Head position that hangs the sensor `hover` mm above part `part_index` of the plate."""
    if not manifest or not manifest.get("parts"):
        raise MeasureError("Open eerst een plaat (Print plates): er zijn geen onderdelen geladen.")
    if not 0 <= part_index < len(manifest["parts"]):
        raise MeasureError(f"Punt {part_index + 1} bestaat niet: de plaat heeft {len(manifest['parts'])} onderdelen.")
    if not settings.sensor.measured and not any(settings.calibration.cone_position(p) for p in calibration.POINTS):
        raise MeasureError("De hoogte van de sensor is nog niet gemeten: er is nog geen geslaagde kalibratie "
                           "opgeslagen. Kalibreer eerst tot 'Kalibratie geslaagd!' (Start kalibratie), anders kan de "
                           "sensor op het onderdeel botsen.")
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
        raise MeasureError(f"Punt {part_index + 1} ligt buiten bereik van de kop (X{x:.1f} Y{y:.1f}).")
    if not 0 <= z <= travel_z <= settings.printer.z_max:
        raise MeasureError(f"Hoogte buiten bereik (Z{z:.1f} / {travel_z:.1f}, max {settings.printer.z_max:g}).")
    return HoverTarget(part["id"], bx, by, top, x, y, travel_z, z, source)
