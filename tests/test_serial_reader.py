"""The serial reader splits the printer's output: metric lines to the gauge, the rest to _send."""
import threading
import time

import pytest

import gantry_controls as gc
from strain_gauge import StrainGauge


class FakeSerial:
    """Serves scripted output in arbitrary chunks; answers each written line via `respond`."""

    def __init__(self, respond=None):
        self.is_open = True
        self._out = bytearray()
        self._cv = threading.Condition()
        self.written = []
        self.respond = respond or (lambda cmd: b"ok\n")

    def feed(self, data):
        with self._cv:
            self._out += data
            self._cv.notify_all()

    @property
    def in_waiting(self):
        return len(self._out)

    def read(self, n):
        with self._cv:
            if not self.is_open:
                raise OSError("closed")
            if not self._out:
                self._cv.wait(0.05)
            data = bytes(self._out[:min(n, 7)])     # small chunks: lines get split across reads
            del self._out[:len(data)]
            return data

    def write(self, data):
        for cmd in data.decode().splitlines():
            if cmd:
                self.written.append(cmd)
                self.feed(self.respond(cmd))

    def close(self):
        with self._cv:
            self.is_open = False
            self._cv.notify_all()


@pytest.fixture
def fake(monkeypatch):
    ser = FakeSerial()
    monkeypatch.setattr(gc, "_ser", ser)
    gc._drain_replies()
    t = threading.Thread(target=gc._reader_loop, args=(ser,), daemon=True)
    t.start()
    yield ser
    ser.close()
    t.join(timeout=2)
    monkeypatch.setattr(gc, "_ser", None)
    gc._metric_listeners.clear()


def test_metrics_go_to_the_gauge_and_replies_to_send(fake):
    g = StrainGauge()
    g.start()
    try:
        def respond(cmd):
            # metric lines before, between and after the reply lines
            return (b"metric:loadcell r=100i,o=0i,s=0.5 1\n"
                    b"X:1.00 Y:2.00 Z:3.00 E:0.00\n"
                    b"metric:pos_z v=3.000000 2\n"
                    b"metric:loadcell r=102i,o=0i,s=0.5 3\n"
                    b"ok\n")
        fake.respond = respond
        reply = gc.send_gcode("M114", timeout=2)
        assert reply.splitlines() == ["X:1.00 Y:2.00 Z:3.00 E:0.00", "ok"]
        assert gc.read_position() == (1.0, 2.0, 3.0)
        assert list(g.samples)[:2] == [50.0, 51.0]
        assert g.pos["z"] == 3.0
    finally:
        g.stop()


def test_error_is_raised_after_its_ok(fake):
    fake.respond = lambda cmd: b"metric:loadcell r=1i,o=0i,s=1 1\nError:Unknown command\nok\n"
    with pytest.raises(gc.PrinterError):
        gc.send_gcode("M9999", timeout=2)
    fake.respond = lambda cmd: b"ok\n"
    assert gc.send_gcode("G90", timeout=2) == "ok"     # nothing left over from the error


def test_resync_skips_stale_replies_amid_metrics(fake):
    # a stale 'ok' (e.g. of a G1 sent with _send_nowait) and metrics arrive before the marker
    fake.feed(b"ok\nmetric:loadcell r=1i,o=0i,s=1 1\n")

    def respond(cmd):
        if cmd.startswith("M118 "):
            return f"metric:pos_z v=1.0 5\n{cmd[5:]}\nmetric:pos_z v=1.0 6\nok\n".encode()
        return b"ok\n"
    fake.respond = respond
    with gc._lock:
        gc._resync_serial(timeout=2)
    assert gc._replies.empty()


def test_reply_cut_by_a_metric_line_is_kept(fake):
    got = []
    gc.add_metric_listener(got.append)
    fake.feed(b"echo:busy: processingmetric:loadcell r=5i,o=0i,s=1 7\n")
    deadline = time.monotonic() + 2
    while not got and time.monotonic() < deadline:
        time.sleep(0.01)
    assert got == ["loadcell r=5i,o=0i,s=1 7"]
    assert gc._next_reply(1) == "echo:busy: processing"


def test_metrics_without_listener_are_dropped(fake):
    fake.feed(b"metric:loadcell r=5i,o=0i,s=1 7\n" * 50)
    fake.respond = lambda cmd: b"ok\n"
    assert gc.send_gcode("G90", timeout=2) == "ok"
