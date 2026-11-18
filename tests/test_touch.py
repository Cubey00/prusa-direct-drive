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


def test_force_images_taken_while_standing_still(rig):
    axis, gauge = rig
    calls = []

    def capture(k, target):
        a = axis
        calls.append((k, target, a.z0 == a.z1, abs(gauge.force_g())))

    res = run(gauge, "force", 20.0, expect_z=SURFACE, image_step=5.0, capture=capture)
    assert res.status == "ok", res.message
    assert [c[0] for c in calls] == [1, 2, 3, 4]
    assert all(c[2] for c in calls)                       # bed stationary during every exposure
    assert all(c[3] >= c[1] - 1 for c in calls)           # at least the target (data lags a bit)
    assert [i["increment"] for i in res.images] == [1, 2, 3, 4]
    assert all(i["force_g"] >= i["target"] - 1 for i in res.images)
    assert 18 <= abs(res.final_g) <= 25


def test_depth_images_step_by_step(rig):
    axis, gauge = rig
    res = run(gauge, "depth", 0.06, expect_z=SURFACE, image_step=0.02, capture=lambda k, t: None)
    assert res.status == "ok", res.message
    zs = [i["z"] for i in res.images]
    assert len(zs) == 3
    assert [round(res.contact_z - z, 2) for z in zs] == [0.02, 0.04, 0.06]


def test_no_images_without_step(rig):
    axis, gauge = rig
    calls = []
    res = run(gauge, "force", 20.0, expect_z=SURFACE, image_step=0.0, capture=lambda k, t: calls.append(k))
    assert res.status == "ok" and calls == [] and res.images == []


def creep(gauge, **kw):
    return touch_mod.creep_to_contact(gauge, **({"contact_g": 5.0, "on_status": lambda m: None} | kw))


def test_creep_stops_on_first_contact_and_stays(rig):
    axis, gauge = rig
    res = creep(gauge, fast_mm=2.5, max_travel=10.0)      # 3.5 mm gap: 2.5 at 0.5 mm/s, the rest slowly
    assert res.status == "ok", res.message
    assert abs(res.contact_z - SURFACE) < 0.1
    assert gauge.peak_true < 15 and 0 < abs(res.final_g) < 15          # just touching, not pressing
    assert axis.z_at(axis.clock.monotonic()) == pytest.approx(res.end_z, abs=0.01)   # stays there (end_z = lagging metrics Z)


def test_creep_without_object_gives_up(rig):
    axis, gauge = rig
    res = creep(gauge, max_travel=2.0)
    assert res.status == "no_contact" and "2 mm" in res.message


def test_creep_can_be_stopped(rig):
    axis, gauge = rig
    cancel = threading.Event()
    cancel.set()
    assert creep(gauge, cancel=cancel).status == "aborted"


class DriftingGauge(FakeGauge):
    """Load cell that drifts `rate` g/s on top of the real force."""

    def __init__(self, axis, rate):
        super().__init__(axis)
        self.rate = rate

    def force_g(self, n=300):
        return super().force_g(n) - self.rate * self.axis.clock.monotonic()


def rise_creep(gauge, **kw):
    return creep(gauge, rise_g=10.0, rise_s=1.0, abs_g=100.0, **kw)


def test_drift_alone_is_no_contact(rig):
    axis, _ = rig
    gauge = DriftingGauge(axis, rate=2.0)                 # 2 g/s: 5 g after 2.5 s, 20 g after 10 s
    res = rise_creep(gauge, max_travel=0.5)              # no object within reach (surface 1.5 below)
    assert res.status == "no_contact"


def test_level_contact_would_have_stopped_on_drift(rig):
    axis, _ = rig
    gauge = DriftingGauge(axis, rate=2.0)
    assert creep(gauge, max_travel=0.5).status == "ok"   # the old 5 g level: a false contact


def test_rise_finds_the_real_contact_despite_drift(rig):
    axis, _ = rig
    gauge = DriftingGauge(axis, rate=2.0)
    res = rise_creep(gauge, fast_mm=2.5, max_travel=10.0)
    assert res.status == "ok", res.message
    assert abs(res.contact_z - SURFACE) < 0.2
    assert gauge.peak_true < 30                            # touching, not pressing hard


def test_press_step_goes_0_1_mm_deeper(rig):
    axis, gauge = rig
    axis.move(SURFACE, 1.0)                               # sensor just on the object
    axis.clock.sleep(10)
    res = touch_mod.press_step(gauge, step_mm=0.1, stop_g=300.0)
    assert res.status == "ok", res.message
    assert res.start_z - res.end_z == pytest.approx(0.1, abs=0.01)
    assert res.final_g == pytest.approx(10.0, abs=1.5)     # 100 g/mm x 0.1 mm


def test_press_step_stops_at_the_force_limit(rig):
    axis, gauge = rig
    axis.move(SURFACE - 0.25, 1.0)                        # already pressing 25 g
    axis.clock.sleep(10)
    res = touch_mod.press_step(gauge, step_mm=0.1, stop_g=30.0)
    assert res.status == "limit" and gauge.peak_true < 40
    res = touch_mod.press_step(gauge, step_mm=0.1, stop_g=20.0)       # already above: no move
    assert res.status == "limit" and res.end_z == res.start_z
