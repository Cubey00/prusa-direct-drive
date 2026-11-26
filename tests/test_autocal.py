"""Automatic calibration: imprint centre from before/after photos, camera scale, two cones."""
import json

import numpy as np
import pytest
from PySide6.QtCore import QTimer

import machine_settings
from pathprogrammer import autocal, measure
from pathprogrammer.ui import auto_calibration as auto_mod
from tests.test_calibration_wizard import FakeServer, fiducials, msg, plate, win   # noqa: F401

W, H = 800, 600
J_TRUE = np.array([[55.0, 9.0], [-7.0, 58.0]])      # px per mm: a slightly turned camera


def frame(rng, at=None):
    """A noisy grey frame, with an imprint (gaussian blob) at pixel `at`."""
    img = rng.normal(1000.0, 4.0, (H, W))
    if at is not None:
        yy, xx = np.mgrid[0:H, 0:W]
        img += 250.0 * np.exp(-((xx - at[0]) ** 2 + (yy - at[1]) ** 2) / (2 * 18.0 ** 2))
    return img.astype(np.float32)


# ---------------- the pure parts ----------------

def test_imprint_centre_is_found():
    rng = np.random.default_rng(0)
    c = autocal.contact_centre(frame(rng), frame(rng, (512.0, 233.0)))
    assert (c.x, c.y) == pytest.approx((512.0, 233.0), abs=0.6)


def test_no_imprint_is_refused():
    rng = np.random.default_rng(1)
    with pytest.raises(autocal.AutoCalError, match="No imprint"):
        autocal.contact_centre(frame(rng), frame(rng))


def test_scale_and_correction_bring_the_imprint_to_the_centre():
    c0 = np.array([300.0, 200.0])
    cx, cy = c0 + J_TRUE[:, 0] * 1.0, c0 + J_TRUE[:, 1] * 1.0
    j = autocal.jacobian(c0, cx, cy, 1.0, 1.0)
    assert j == pytest.approx(J_TRUE)
    centre = autocal.image_centre((H, W))
    d = autocal.correction_mm(j, c0, centre)
    assert c0 + J_TRUE @ np.array(d) == pytest.approx(np.array(centre))


def test_third_cone_from_two_follows_rotation():
    rot = np.radians(1.5)
    k = complex(np.cos(rot), np.sin(rot))
    bed = {"F1": (10, 66), "F2": (190, 66), "F3": (10, 169)}
    head = {n: (complex(*b) * k + complex(3, 40)) for n, b in bed.items()}
    hc = autocal.third_from_two(bed["F1"], (head["F1"].real, head["F1"].imag),
                                bed["F2"], (head["F2"].real, head["F2"].imag), bed["F3"])
    assert hc == pytest.approx((head["F3"].real, head["F3"].imag))


# ---------------- the whole flow, simulated ----------------

class SimPrinter(FakeServer):
    """Answers like the calibrator: goto, creep and step come back at once."""

    def __init__(self, w):
        super().__init__()
        self.w = w
        self.head = None
        self.creeps, self.steps = [], []

    def goto(self, x, y, z, label="", home_xy=False, z_final=None):
        super().goto(x, y, z, label, home_xy, z_final)
        self.head = (x, y)
        QTimer.singleShot(0, lambda: msg(self.w, "goto_done", label=label, ok=True, message=""))
        return True

    def creep(self, fast_mm, max_mm, fast_speed=None, speed=None):
        self.creeps.append((round(fast_mm, 3), round(max_mm, 3), fast_speed, speed))
        QTimer.singleShot(0, lambda: msg(self.w, "touch_done", ok=True, status="ok", message="", z=85.0,
                                         force_g=12.0))
        return True

    def step(self, mm, stop_g):
        self.steps.append((mm, stop_g))
        QTimer.singleShot(0, lambda: msg(self.w, "touch_done", ok=True, status="ok", message="", z=84.7,
                                         force_g=140.0))
        return True


@pytest.fixture
def sim(win, plate, tmp_path, monkeypatch):
    monkeypatch.setattr(auto_mod, "SETTLE_MS", 0)
    win.server = SimPrinter(win)
    win.open_model(plate)
    win.photo.settings.setValue("photo_folder", str(tmp_path / "photos"))
    win.photo.feed.connected = True
    rng = np.random.default_rng(3)
    centre = np.array(autocal.image_centre((H, W)))
    truth = {}

    def grab(tag):
        head = np.array(win.server.head)
        if tag[1] == "before":
            img = frame(rng)
        else:                                    # the tip's imprint, where the cone really is
            img = frame(rng, centre + J_TRUE @ (np.array(truth[win.autocal.cone]) - head))
        QTimer.singleShot(0, lambda: win.photo.frameGrabbed.emit(tag, img))
        return True
    win.photo.grab = grab
    return win, truth, fiducials(plate)


def run(w, qtbot, cones=("F1", "F2")):
    a, b = w.left.auto_cone_a, w.left.auto_cone_b
    a.setCurrentIndex(a.findData(cones[0]))
    b.setCurrentIndex(b.findData(cones[1]))
    w.left.auto_btn.click()
    qtbot.waitUntil(lambda: not w.autocal.active, timeout=20000)


def test_two_cones_are_centred_and_saved(sim, qtbot):
    w, truth, fids = sim
    # predicted from the settings' offset (0, -40): head = bed + (0, 40); the plate lies a bit off
    truth["F1"] = (fids["F1"]["x"] + 0.42, fids["F1"]["y"] + 40 - 0.31)
    truth["F2"] = (fids["F2"]["x"] + 0.35, fids["F2"]["y"] + 40 - 0.12)
    run(w, qtbot)
    assert "successful" in w.left.auto_status.text(), w.left.auto_status.text()
    s = machine_settings.load()
    for p in ("F1", "F2"):
        assert s.calibration.cone_position(p)[:2] == pytest.approx(truth[p], abs=0.05)
    assert s.calibration.cone_position("F3") is None
    assert w.server.homes[0] is True and not any(w.server.homes[1:])    # X/Y homed once, at the start
    assert all(c[2] == auto_mod.FAST_SPEED and c[3] == auto_mod.SLOW_SPEED for c in w.server.creeps)
    assert w.server.steps and all(s_ == (auto_mod.PRESS_MM, auto_mod.PRESS_G) for s_ in w.server.steps)
    # Measure uses the two saved cones (the third follows)
    manifest = json.loads(w.model_path.with_suffix(".json").read_text())
    pts, source = measure.machine_points(None, s, manifest)
    assert "cones 1 and 2" in source and set(pts) == {"F1", "F2", "F3"}
    # a record of every touch
    folder = w.autocal.folder
    log = json.loads((folder / "autocal.json").read_text())
    assert np.array(log["camera_px_per_mm"]) == pytest.approx(-J_TRUE, abs=1.0)   # the head moves, the cone stays
    assert list(folder.glob("*_difference.png")) and w.left.auto_btn.isEnabled()


def test_a_cone_just_out_of_reach_is_computed_from_the_photo(sim, qtbot):
    w, truth, fids = sim
    machine_settings.save_values("calibration", {"cone_F1": [-2.0, fids["F1"]["y"] + 40, 85.0]})
    truth["F1"] = (-2.17, fids["F1"]["y"] + 40)                 # 0.17 mm past the head's travel
    truth["F2"] = (fids["F2"]["x"] + 0.2, fids["F2"]["y"] + 40)
    run(w, qtbot)
    assert "successful" in w.left.auto_status.text(), w.left.auto_status.text()
    assert machine_settings.load().calibration.cone_position("F1")[0] == pytest.approx(-2.17, abs=0.05)
    assert min(g[1] for g in w.server.gotos) >= -2.0             # never sent past the travel


def test_no_imprint_stops_with_a_reason(sim, qtbot):
    w, truth, fids = sim
    w.photo.grab = lambda tag: (QTimer.singleShot(0, lambda: w.photo.frameGrabbed.emit(
        tag, frame(np.random.default_rng(9)))), True)[1]
    run(w, qtbot)
    assert "No imprint" in w.left.auto_status.text() and w.left.auto_btn.isEnabled()


def test_needs_a_camera(win, plate):
    win.open_model(plate)
    win.left.auto_btn.click()
    assert "No camera" in win.left.auto_status.text() and not win.autocal.active
