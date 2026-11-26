"""Automatic load cell calibration against a scale under the sensor, with a simulated scale."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

import loadcell_cal as lc

TRUE_SCALE = 0.60          # g per reading unit, the truth
OLD_SCALE = 0.55           # what calibration.json says now


class SimHardware:
    """Load cell over a scale: pressing gives a NEGATIVE reading (like on the printer)."""

    def __init__(self, rng, true_scale=None):
        self.rng, self.force, self.zpos = rng, 0.0, 50.0
        self.true = true_scale or TRUE_SCALE
        self.tares, self.moves = 0, []

    def scale(self):
        return OLD_SCALE

    def tare(self):
        self.tares += 1
        return self.force == 0.0              # only ever tared with the sensor off the scale

    def z(self):
        return self.zpos

    def move_z(self, z):
        self.moves.append(z)
        self.zpos = z
        if z > 40.0:
            self.force = 0.0                  # lifted off the scale

    def creep(self, max_mm, fast_mm):
        self.zpos = 40.0
        return SimpleNamespace(status="ok", message="", end_z=40.0)

    def press(self, stop_g, max_mm, speed):
        self.force = stop_g * self.true / OLD_SCALE        # the old scale was off
        self.zpos = 39.5
        return SimpleNamespace(status="limit", message="", end_z=39.5)

    def reading(self):
        return -self.force / self.true + self.rng.normal(0, 0.5)

    def grams(self):
        return abs(self.reading() * OLD_SCALE)


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(lc, "SETTLE_S", 0.0)


def test_targets_up_to_one_kilo():
    assert lc.targets(1000, 10) == [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    with pytest.raises(lc.LoadCellCalError):
        lc.targets(1500, 10)


def test_whole_run_finds_the_true_scale_and_keeps_the_sign(tmp_path):
    rng = np.random.default_rng(1)
    hw = SimHardware(rng)
    asked = []

    def ask(q):                                # the person reads the scale: the true weight, to 0.1 g
        asked.append(q)
        q.answer(round(hw.force + rng.normal(0, 0.3), 1))
    points = lc.run(hw, ask, on_status=lambda m: None, max_g=1000, n=10)
    assert len(points) == 10 and len(asked) == 10
    assert hw.tares == 10                      # a fresh tare before every touch
    assert all(p.reading < 0 for p in points)  # pressing reads negative ...
    result = lc.fit(points)
    assert result.scale == pytest.approx(TRUE_SCALE, rel=0.01) and result.scale > 0   # ... the scale stays positive
    assert result.rmse_g < 2 and result.r2 > 0.999
    assert hw.zpos == 50.0                     # back where it started, off the scale
    plot = lc.save(points, result, tmp_path / "run", OLD_SCALE)
    assert plot.exists() and (tmp_path / "run" / "points.csv").exists()
    assert json.loads((tmp_path / "run" / "fit.json").read_text())["fit"]["points"] == 10
    lc.write_scale(result, tmp_path / "calibration.json", tmp_path / "run")
    saved = json.loads((tmp_path / "calibration.json").read_text())
    assert saved["scale"] == pytest.approx(result.scale, abs=1e-6) and "Automatic calibration" in saved["note"]


def test_cancel_at_the_scale_question_stops_and_lifts(tmp_path):
    hw = SimHardware(np.random.default_rng(2))
    answers = iter([150.0, None])
    points = lc.run(hw, lambda q: q.answer(next(answers)), on_status=lambda m: None, max_g=500, n=5)
    assert len(points) == 1 and hw.zpos == 50.0


def test_no_scale_under_the_sensor_is_explained():
    hw = SimHardware(np.random.default_rng(3))
    hw.creep = lambda max_mm, fast_mm: SimpleNamespace(status="no_contact", message="No contact", end_z=35.0)
    with pytest.raises(lc.LoadCellCalError, match="did not find the scale"):
        lc.run(hw, lambda q: q.answer(1.0), on_status=lambda m: None, max_g=200, n=2)
    assert hw.zpos == 50.0



def test_fit_goes_through_zero_and_only_uses_the_range():
    pts = [lc.Point(t, -t / 0.9, t, 1.0, 50) for t in (100, 300, 500, 700, 900)]
    pts += [lc.Point(t, -t / 1.1, t, 1.0, 50) for t in (1300, 1600, 1900)]          # bends above 1 kg
    r = lc.fit(pts, 1000)
    assert r.offset_g == 0 and r.scale == pytest.approx(0.9) and r.points == 5 and r.excluded == 3
    assert r.straight and r.rmse_g == pytest.approx(0, abs=1e-6)


def test_the_real_force_stays_near_the_target_with_a_far_off_scale():
    """The old scale was 1.64x too low on the printer, and "1000 g" became 1.9 kg. The scale values
    entered so far steer the press, so the real force stays near each target."""
    hw = SimHardware(np.random.default_rng(4), true_scale=0.902)     # the old 0.55 is 1.64x too low
    real = []

    def ask(q):
        real.append(hw.force)
        q.answer(round(hw.force, 1))
    points = lc.run(hw, ask, on_status=lambda m: None, max_g=1000, n=10)
    assert len(points) == 10
    assert max(real) <= 1000 * 1.05                          # not 1.64 kg
    assert real[0] == pytest.approx(100 * 0.902 / 0.55, rel=0.01)   # only the first point uses the old scale
