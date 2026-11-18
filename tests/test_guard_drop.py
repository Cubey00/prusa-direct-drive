"""The force guard drops the bed at most [forces] guard_drop_mm, also when the force stays high (drift)."""
import time

import gantry_controls as gc


class StuckGauge:
    """Load cell that keeps reading 600 g whatever the bed does (drift)."""

    def __init__(self, z=100.0, force=600.0):
        self.pos = {"z": z}
        self.force = force

    def force_g(self, window=16):
        return self.force


def fake_link(monkeypatch):
    sent = []
    monkeypatch.setattr(gc, "motionstop", lambda: None)
    monkeypatch.setattr(gc, "_send", lambda cmd, *a, **k: sent.append(cmd))
    monkeypatch.setattr(gc, "_send_nowait", lambda cmd: sent.append(cmd))
    monkeypatch.setattr(gc, "halt_move", lambda: sent.append("M410"))
    monkeypatch.setattr(gc, "DROP_FEED", 60000)      # the timeout ends the wait quickly
    return sent


def test_drop_is_relative_and_capped(monkeypatch):
    sent = fake_link(monkeypatch)
    assert gc.emergency_drop(StuckGauge(z=100.0), max_drop=30.0) == 30.0
    assert sent == ["G91", "G1 Z30.00 F60000", "M410", "G90"]


def test_drop_never_past_the_bottom(monkeypatch):
    sent = fake_link(monkeypatch)
    assert gc.emergency_drop(StuckGauge(z=gc.Z_MAX - 10), max_drop=30.0) == 10.0
    assert "G1 Z10.00 F60000" in sent


def test_stuck_force_drops_only_once(monkeypatch):
    sent = fake_link(monkeypatch)
    gauge = StuckGauge(z=100.0)

    def move(cmd):                     # the bed really gets there: the drop ends, the guard loops on
        sent.append(cmd)
        gauge.pos["z"] += float(cmd.split()[1][1:])
    monkeypatch.setattr(gc, "_send_nowait", move)
    stop = gc.force_guard(gauge, trip_g=500, release_g=50, max_drop=30.0)
    time.sleep(0.5)
    stop.set()
    time.sleep(0.05)
    assert sum(c.startswith("G1 Z") for c in sent) == 1 and gauge.pos["z"] == 130.0
