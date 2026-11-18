"""Meten: 'Ga 1 cm boven punt 1 hangen' puts the sensor 1 cm above the first part of the plate."""
import json

import pytest

import machine_settings
from pathprogrammer import measure
from tests.test_calibration_wizard import FakeServer, msg, plate, win   # noqa: F401 (fixtures)

OFFSET = (0.0, -40.0, -80.0)
SHIFT = (3.0, -2.0)            # the plate lies 3 mm right and 2 mm to the front of the nominal spot


def calibrate(plate_path, z_head=95.0):
    """Save cone positions as a good calibration would: head = cone - offset + shift."""
    manifest = json.loads(plate_path.with_suffix(".json").read_text())
    cones = {f"cone_{f['name']}": [f["x"] - OFFSET[0] + SHIFT[0], f["y"] - OFFSET[1] + SHIFT[1],
                                   f["z"] - OFFSET[2]] for f in manifest["fiducials"]}
    machine_settings.save_values("calibration", cones)
    return machine_settings.save_values("sensor", {"offset_z": OFFSET[2], "measured": True}), manifest


def test_target_is_above_part_one(win, plate):
    settings, manifest = calibrate(plate)
    t = measure.hover_target(manifest, settings)
    part = manifest["parts"][0]
    assert t.part_id == part["id"] and t.source == "opgeslagen kegelposities"
    assert (t.x, t.y) == pytest.approx((part["x"] + SHIFT[0], part["y"] + 40.0 + SHIFT[1]), abs=1e-6)
    assert t.z == pytest.approx(part["size"][2] + 10.0 + 80.0)          # top + 1 cm, sensor 80 mm lower
    assert t.travel_z >= t.z


def test_no_calibration_no_move(win, plate):
    manifest = json.loads(plate.with_suffix(".json").read_text())
    with pytest.raises(measure.MeasureError, match="Kalibreer eerst"):
        measure.hover_target(manifest, machine_settings.load())
    with pytest.raises(measure.MeasureError, match="Open eerst een plaat"):
        measure.hover_target(None, machine_settings.load())


def test_button_sends_the_head_there(win, plate):
    calibrate(plate)
    win.open_model(plate)
    win.measure.hover_btn.click()
    label, x, y, z = win.server.gotos[-1]
    assert label == "punt 1" and win.server.homes[-1] is True             # X/Y homed first
    assert not win.measure.hover_btn.isEnabled()
    msg(win, "goto_done", label="punt 1", ok=True, message="")
    assert win.measure.hover_btn.isEnabled() and "1 cm boven punt 1" in win.measure.status.text()
    win.measure.hover_btn.click()
    assert win.server.homes[-1] is False                                   # already homed
    msg(win, "goto_done", label="punt 1", ok=False, message="Going to punt 1 failed: no printer")
    assert "no printer" in win.measure.status.text() and win.measure.hover_btn.isEnabled()


def test_button_without_plate_explains(win):
    win.measure.hover_btn.click()
    assert win.server.gotos == [] and "Open eerst een plaat" in win.measure.status.text()


def test_touch_button_creeps_after_hovering_and_reports(win, plate):
    win.server.creeps = []
    win.server.halts = 0
    win.server.creep = lambda fast, mx: win.server.creeps.append((fast, mx)) or True
    win.server.halt = lambda: setattr(win.server, "halts", win.server.halts + 1) or True
    calibrate(plate)
    win.open_model(plate)
    win.measure.hover_btn.click()
    msg(win, "goto_done", label="punt 1", ok=True, message="")
    win.measure.touch_btn.click()
    assert win.server.creeps == [(8.0, 15.0)] and not win.measure.touch_btn.isEnabled()
    win.stop()                                                     # STOP also stops the creep
    assert win.server.halts == 1
    msg(win, "touch_done", ok=True, status="ok", message="De sensor raakt het object.", z=93.21, force_g=6.2)
    assert "raakt het object" in win.measure.status.text() and "93.210" in win.measure.status.text()
    assert win.measure.touch_btn.isEnabled()
    win.measure.touch_btn.click()                                  # not hovering any more: all of it slowly
    assert win.server.creeps[-1] == (0.0, 15.0)
