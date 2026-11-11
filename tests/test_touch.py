"""touch() against a simulated Z axis and a stiff contact whose force data arrives late."""
import re
import threading

import pytest

import touch as touch_mod

SURFACE = 1.5         # contact height
STIFFNESS = 100.0     # g/mm: ~ the real sample (50 g over 0.57 mm in run_20261031)
LATENCY = 0.05        # s between the bed reaching a position and the PC seeing that force


class Clock:
    """Simulated time advances only by requested delays, independent of host scheduling."""
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeAxis:
    def __init__(self, z, clock):
        self.clock = clock
        self.z0, self.z1, self.t0, self.v = z, z, clock.monotonic(), 1.0
        self.lock = threading.Lock()

    def z_at(self, t):
        if self.z0 == self.z1:
            return self.z1
        dt = abs(self.z1 - self.z0) / self.v
        a = min(max((t - self.t0) / dt, 0.0), 1.0)
        return self.z0 + (self.z1 - self.z0) * a

    def move(self, target, v):
        now = self.clock.monotonic()
        with self.lock:
            self.z0, self.z1, self.t0, self.v = self.z_at(now), target, now, v

    def halt(self):
        self.clock.sleep(0.01)                 # serial + firmware delay of M410
        now = self.clock.monotonic()
        with self.lock:
            self.z0 = self.z1 = self.z_at(now)

    def done_at(self):
        return self.t0 + abs(self.z1 - self.z0) / self.v

    def true_force(self, t):
        return STIFFNESS * max(0.0, SURFACE - self.z_at(t))


class FakeGauge:
    def __init__(self, axis):
        self.axis = axis
        self.pos = {"x": 0.0, "y": 0.0, "z": None}
        self.peak_true = 0.0

    def force_g(self, n=300):
        now = self.axis.clock.monotonic()
        self.peak_true = max(self.peak_true, self.axis.true_force(now))
        self.pos["z"] = self.axis.z_at(now - LATENCY)
        return -self.axis.true_force(now - LATENCY)        # pressing reads negative

    def can_tare(self):
        return True

    def tare_fresh(self, n=None, timeout=5.0):
        return True

    def wait_samples(self, n, timeout=5.0):
        return True


@pytest.fixture
def rig(monkeypatch):
    clock = Clock()
    axis = FakeAxis(5.0, clock)
    gauge = FakeGauge(axis)

    def wait_done():
        clock.sleep(max(0.0, axis.done_at() - clock.monotonic()))

    def nowait(cmd):
        m = re.match(r"G1 Z(-?[\d.]+) F([\d.]+)", cmd)
        axis.move(float(m.group(1)), float(m.group(2)) / 60)

    def send(cmd, timeout=30):
        if cmd.startswith("G1"):         # like the printer: 'ok' only once the move is done
            nowait(cmd)
            wait_done()
        elif cmd == "M400":
            wait_done()
        elif cmd == "M114":
            return f"X:0.00 Y:0.00 Z:{axis.z_at(clock.monotonic()):.4f} E:0\nok"
        return "ok"

    # Replace this module reference, not the shared Python time module used by pytest/Qt.
    monkeypatch.setattr(touch_mod, "time", clock)
    monkeypatch.setattr(touch_mod, "_send", send)
    monkeypatch.setattr(touch_mod, "_send_nowait", nowait)
    monkeypatch.setattr(touch_mod, "_resync_serial", lambda timeout=15: wait_done())
    monkeypatch.setattr(touch_mod, "halt_move", axis.halt)
    monkeypatch.setattr(touch_mod, "SETTLE", 0.0)
    monkeypatch.setattr(touch_mod, "STEP_SETTLE", 2 * LATENCY)
    return axis, gauge


def run(gauge, mode, value, **kw):
    kw = dict(touch_speed=0.5, approach_speed=2.0, contact_g=5.0, max_travel=10.0, hold=0.0,
              search_speed=0.2, on_status=lambda m: None) | kw
    return touch_mod.touch(gauge, mode, value, **kw)


def test_force_touch_stops_near_target(rig):
    axis, gauge = rig
    res = run(gauge, "force", 50.0)
    assert res.status == "ok", res.message
    assert abs(res.contact_z - SURFACE) < 5.0 / STIFFNESS + 0.02   # threshold depth + overtravel
    assert 48 <= abs(res.final_g) <= 55
    assert axis.z_at(axis.clock.monotonic()) == pytest.approx(5.0)      # back at the start


def test_longer_simulated_stop_delay_still_causes_force_overshoot(rig, monkeypatch):
    axis, gauge = rig
    halt = axis.halt

    def delayed_halt():
        axis.clock.sleep(0.1)
        halt()

    monkeypatch.setattr(touch_mod, "halt_move", delayed_halt)
    res = run(gauge, "force", 50.0, expect_z=SURFACE)
    assert res.status == "ok", res.message
    # Virtual time must retain force growth during an explicitly delayed stop.
    assert abs(res.final_g) > 55
    assert axis.z_at(axis.clock.monotonic()) == pytest.approx(5.0)


def test_expected_height_avoids_the_fast_hit(rig):
    axis, gauge = rig
    res = run(gauge, "force", 50.0, expect_z=SURFACE)
    assert res.status == "ok", res.message
    assert gauge.peak_true < 60        # never hit at approach speed


def test_depth_touch(rig):
    axis, gauge = rig
    res = run(gauge, "depth", 0.01, expect_z=SURFACE)
    assert res.status == "ok", res.message
    assert res.contact_z - res.end_z == pytest.approx(0.01, abs=1e-3)


def test_no_contact(rig):
    axis, gauge = rig
    res = run(gauge, "force", 50.0, max_travel=2.0)
    assert res.status == "no_contact"


def test_cancel_returns_to_start(rig):
    axis, gauge = rig
    cancel = threading.Event()
    statuses = []
    def on_status(m):
        statuses.append(m)
        if m.startswith("Contact at Z"):
            cancel.set()                 # STOP pressed while pressing
    res = run(gauge, "force", 50.0, expect_z=SURFACE, cancel=cancel, on_status=on_status)
    assert res.status == "aborted"
    assert gauge.peak_true < 60
    assert axis.z_at(axis.clock.monotonic()) == pytest.approx(5.0)
