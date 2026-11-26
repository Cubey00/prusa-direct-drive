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
DISC = (430.0, 285.0, 270.0)                         # the lit sensor window: NOT centred in the frame
YY, XX = np.mgrid[0:H, 0:W]


def frame(rng, at=None, residual=None, dark=False):
    """A noisy frame like the real ones: a bright round window on a dark surround, an imprint (a
    dark spot) at pixel `at`, and what is left of an earlier imprint at `residual` (fading)."""
    lit = ((XX - DISC[0]) ** 2 + (YY - DISC[1]) ** 2) <= DISC[2] ** 2
    img = np.where(lit, 80.0 if dark else 1000.0, 60.0) + rng.normal(0.0, 4.0, (H, W))

    def spot(c, depth):
        return depth * np.exp(-((XX - c[0]) ** 2 + (YY - c[1]) ** 2) / (2 * 18.0 ** 2))
    if at is not None:
        img -= spot(at, 250.0)
    if residual is not None:
        img -= spot(residual, 120.0)
    return img.astype(np.float32)


# ---------------- the pure parts ----------------

def test_imprint_centre_is_found():
    rng = np.random.default_rng(0)
    c = autocal.contact_centre(frame(rng), frame(rng, (512.0, 233.0)))
    assert (c.x, c.y) == pytest.approx((512.0, 233.0), abs=0.6) and c.polarity == -1


def test_sensor_window_centre_is_found():
    d = autocal.sensor_disc(frame(np.random.default_rng(4)))
    assert (d.x, d.y) == pytest.approx(DISC[:2], abs=3) and d.r == pytest.approx(DISC[2], rel=0.05)


def test_a_fading_old_imprint_does_not_pull_the_centre():
    rng = np.random.default_rng(5)
    before = frame(rng, residual=(300.0, 300.0))          # the gel still shows the last touch
    after = frame(rng, at=(500.0, 260.0), residual=(300.0, 300.0))
    after += 0.6 * 120.0 * np.exp(-((XX - 300) ** 2 + (YY - 300) ** 2) / (2 * 18.0 ** 2))   # ... fading
    c = autocal.contact_centre(before, after, polarity=-1)
    assert (c.x, c.y) == pytest.approx((500.0, 260.0), abs=1.0)


def test_rim_outside_the_window_is_ignored():
    rng = np.random.default_rng(6)
    d = autocal.Disc(*DISC)
    after = frame(rng, at=(450.0, 300.0))
    after[0:40, 0:40] -= 800.0                            # light change in a corner, outside the window
    c = autocal.contact_centre(frame(rng), after, disc=d)
    assert (c.x, c.y) == pytest.approx((450.0, 300.0), abs=1.0)


def test_broyden_learns_the_true_scale():
    j = np.array([[25.0, 0.0], [0.0, 58.0]])              # X scale half the truth, like on the printer
    for dh in ((0.7, 0.1), (-0.4, 0.3), (0.2, -0.5)):
        j = autocal.broyden(j, dh, J_TRUE @ np.array(dh))
    assert j @ np.array([1.0, 0.0]) == pytest.approx(J_TRUE[:, 0], rel=0.2)


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
    centre = np.array(DISC[:2])                  # the target: the centre of the sensor window
    truth = {}
    last = {"spot": None}

    def grab(tag):
        head = np.array(win.server.head)
        if tag[1] == "before":                   # the gel still shows the previous imprint, fading
            img = frame(rng, residual=last["spot"])
        else:                                    # the tip's imprint, where the cone really is
            spot = centre - J_TRUE @ (np.array(truth[win.autocal.cone]) - head)
            img = frame(rng, spot, residual=last["spot"])
            last["spot"] = spot
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
    assert np.array(log["camera_px_per_mm"]) == pytest.approx(J_TRUE, rel=0.25)
    assert log["target_px"] == pytest.approx(list(DISC[:2]), abs=3)          # aimed at the window centre
    assert list(folder.glob("*_difference.png")) and w.left.auto_btn.isEnabled()


def test_a_cone_just_out_of_reach_is_computed_from_the_photo(sim, qtbot):
    w, truth, fids = sim
    machine_settings.save_values("calibration", {"cone_F1": [-2.0, fids["F1"]["y"] + 40, 85.0]})
    truth["F1"] = (-2.17, fids["F1"]["y"] + 40)                 # 0.17 mm past the head's travel
    truth["F2"] = (truth["F1"][0] + fids["F2"]["x"] - fids["F1"]["x"] + 0.05, fids["F2"]["y"] + 40)
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


def test_no_led_question_but_an_error_without_an_imprint(sim, qtbot):
    """A dull photo is no reason to stop (no LED question), but no recognisable imprint is."""
    w, truth, fids = sim
    w.photo.grab = lambda tag: (QTimer.singleShot(0, lambda: w.photo.frameGrabbed.emit(
        tag, frame(np.random.default_rng(9), dark=True))), True)[1]
    run(w, qtbot)
    text = w.left.auto_status.text()
    assert "LED light on?" not in text and "No imprint" in text and w.left.auto_btn.isEnabled()
    assert (w.autocal.folder / "00_first_before.png").exists()


def test_no_window_found_aims_at_the_frame_centre():
    """Without a window in the photo the target is the centre of the frame."""
    flat = np.full((H, W), 500.0, np.float32)
    assert autocal.sensor_disc(flat) is None
    assert autocal.image_centre(flat.shape) == ((W - 1) / 2, (H - 1) / 2)


def test_second_cone_is_predicted_from_the_first(sim, qtbot):
    """Only cone 2 saved; the sensor offset in the settings is a bad guess (12 mm off in X, like on
    the printer). Cone 1 must be found from cone 2 + their distance on the plate."""
    w, truth, fids = sim
    shift = (12.2, -0.6)
    truth["F2"] = (fids["F2"]["x"] - shift[0], fids["F2"]["y"] + 40 + shift[1])
    truth["F1"] = (fids["F1"]["x"] - shift[0] + 0.1, fids["F1"]["y"] + 40 + shift[1])
    machine_settings.save_values("calibration", {"cone_F2": [truth["F2"][0] + 0.3, truth["F2"][1] - 0.2, 85.0]})
    run(w, qtbot, cones=("F1", "F2"))                     # chosen in this order: F2 (saved) goes first
    assert "successful" in w.left.auto_status.text(), w.left.auto_status.text()
    s = machine_settings.load()
    assert s.calibration.cone_position("F1")[:2] == pytest.approx(truth["F1"], abs=0.1)
    first_f1 = next(g for g in w.server.gotos if g[0] == "auto F1")
    assert first_f1[1:3] == pytest.approx((max(truth["F1"][0] - 0.1, -2.0), truth["F1"][1]), abs=0.6)


def test_needs_a_camera(win, plate):
    win.open_model(plate)
    win.left.auto_btn.click()
    assert "No camera" in win.left.auto_status.text() and not win.autocal.active


def test_predict_cone_from_a_known_one():
    from pathprogrammer import calibration
    fids = {"F1": {"x": 10, "y": 66, "z": 5}, "F2": {"x": 190, "y": 66, "z": 5}, "F3": {"x": 10, "y": 169, "z": 5}}
    pos, ref = calibration.predict_cone("F3", fids, {"F2": (177.83, 106.65, 67.15), "F1": None})
    assert ref == "F2" and pos == pytest.approx((-2.17, 209.65, 67.15))
    assert calibration.predict_cone("F1", fids, {}) == (None, None)


def test_cones_1_and_3_start_from_saved_cone_2(sim, qtbot):
    """Cones 1 and 3 chosen, only cone 2 saved and the offset guess 12 mm off: both are predicted
    from cone 2, so the first touch already lands on them."""
    w, truth, fids = sim
    shift = (12.0, -0.6)
    f2 = (fids["F2"]["x"] - shift[0], fids["F2"]["y"] + 40 + shift[1])
    machine_settings.save_values("calibration", {"cone_F2": [f2[0], f2[1], 85.0]})
    for p in ("F1", "F3"):
        truth[p] = (f2[0] + fids[p]["x"] - fids["F2"]["x"] + 0.15, f2[1] + fids[p]["y"] - fids["F2"]["y"] - 0.1)
    run(w, qtbot, cones=("F1", "F3"))
    assert "successful" in w.left.auto_status.text(), w.left.auto_status.text()
    first_f1 = next(g for g in w.server.gotos if g[0] == "auto F1")
    assert first_f1[1:3] == pytest.approx((f2[0] + fids["F1"]["x"] - fids["F2"]["x"],
                                           f2[1] + fids["F1"]["y"] - fids["F2"]["y"]), abs=0.01)
