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


def test_graph_shows_the_calibrators_force_stream(win):
    assert win.graph.source() is None
    for g in (0.5, 1.0, 12.0):
        msg(win, "force", g=g)
    assert win.graph.source() == [0.5, 1.0, 12.0]
    msg(win, "bye")
    assert win.graph.source() is None


def test_step_needs_a_first_touch_then_counts_the_indentation(win, plate):
    win.server.creep = lambda fast, mx: True
    win.server.steps = []
    win.server.step = lambda mm, g: win.server.steps.append((mm, g)) or True
    win.measure.step_btn.click()
    assert win.server.steps == [] and "Raak eerst het object aan" in win.measure.status.text()
    assert win._photo_tag() is None                                 # a photo now is a plain photo

    calibrate(plate)
    win.open_model(plate)
    win.measure.hover_btn.click()
    msg(win, "goto_done", label="punt 1", ok=True, message="")
    part = win._hover_target.part_id
    win.measure.touch_btn.click()
    msg(win, "touch_done", ok=True, status="ok", message="De sensor raakt het object.", z=93.2, force_g=8.0)
    assert win._photo_tag() == {"object": part, "depth_mm": 0.0, "force_g": 8.0}

    win.measure.step_btn.click()
    assert win.server.steps == [(0.1, 300.0)] and not win.measure.step_btn.isEnabled()
    msg(win, "touch_done", ok=True, status="ok", message="", z=93.1, force_g=18.0)
    msg(win, "force", g=18.4)                                       # live force at the photo moment
    assert win._photo_tag() == {"object": part, "depth_mm": pytest.approx(0.1), "force_g": 18.4}
    assert "indrukking 0.100 mm" in win.measure.depth_label.text()

    win.measure.step_btn.click()
    msg(win, "touch_done", ok=False, status="limit", message="Gestopt: de kracht kwam boven 300 g.",
        z=93.05, force_g=301.0)
    assert "boven 300 g" in win.measure.status.text() and win._contact["depth_mm"] == pytest.approx(0.15)

    win.measure.hover_btn.click()                                   # another spot: start again
    assert win._photo_tag() is None


def test_next_point_lifts_and_goes_to_point_2(win, plate):
    calibrate(plate)
    win.open_model(plate)
    manifest = json.loads(plate.with_suffix(".json").read_text())
    assert len(manifest["parts"]) >= 2
    assert not win.measure.next_btn.isEnabled()                    # not before point 1
    win.measure.hover_btn.click()
    msg(win, "goto_done", label="punt 1", ok=True, message="")
    assert win.measure.next_btn.isEnabled() and "punt 2" in win.measure.next_btn.text()
    win.measure.next_btn.click()
    label, x, y, z = win.server.gotos[-1]
    t = measure.hover_target(manifest, machine_settings.load(), part_index=1)
    assert label == "punt 2" and (x, y) == pytest.approx((t.x, t.y), abs=1e-3)
    assert z == pytest.approx(t.travel_z, abs=1e-3) and win.server.finals[-1] == pytest.approx(t.z, abs=1e-3)
    assert z >= t.z                                                # up first, then down to 1 cm above
    assert win.server.homes[-1] is False and not win.measure.next_btn.isEnabled()
    msg(win, "goto_done", label="punt 2", ok=True, message="")
    assert "boven punt 2" in win.measure.status.text()
    if len(manifest["parts"]) == 2:
        assert not win.measure.next_btn.isEnabled() and "Laatste punt" in win.measure.next_btn.text()
    win.measure.hover_btn.click()                                  # back to the start
    assert win.server.gotos[-1][0] == "punt 1"
