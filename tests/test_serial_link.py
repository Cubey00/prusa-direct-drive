"""The serial link must never hang on close, even when a command never gets its ok."""
import queue
import threading
import time

import pytest
import serial

import gantry_controls as gc


class FakeSerial:
    """Answers M118 with its marker + ok and other commands with ok, unless `busy`: then it only
    sends 'echo:busy: processing' (like the printer while a question waits on its screen)."""

    def __init__(self, stale=(), busy=False):
        self.lines = queue.Queue()
        for line in stale:
            self.lines.put(line)
        self.busy = busy
        self.is_open = True
        self.written = []

    def write(self, data):
        if not self.is_open:
            raise serial.SerialException("closed")
        for cmd in data.decode().split("\n"):
            cmd = cmd.strip()
            if not cmd:
                continue
            self.written.append(cmd)
            if self.busy:
                continue
            if cmd.startswith("M118 "):
                self.lines.put(cmd[5:])
            self.lines.put("ok")

    def readline(self):
        if not self.is_open:
            raise serial.SerialException("closed")
        if self.busy:
            time.sleep(0.05)
            return b"echo:busy: processing\n"
        try:
            return (self.lines.get(timeout=0.05) + "\n").encode()
        except queue.Empty:
            return b""

    def reset_input_buffer(self):
        pass

    def close(self):
        self.is_open = False


@pytest.fixture
def fake_port(monkeypatch):
    made = []

    def open_port(*a, **k):
        return made[-1]

    monkeypatch.setattr(gc, "find_port", lambda: "COMTEST")
    monkeypatch.setattr(gc.serial, "Serial", open_port)
    monkeypatch.setattr(gc, "CONNECT_SETTLE", 0)
    monkeypatch.setattr(gc, "_ser", None)
    yield made
    gc._closing.clear()
    monkeypatch.setattr(gc, "_ser", None)


def test_disconnect_does_not_hang_on_a_command_waiting_forever(fake_port):
    port = FakeSerial()
    fake_port.append(port)
    gc.connect()
    port.busy = True                          # from now on only 'busy' lines, never an ok
    errors = []

    def stuck():
        try:
            gc.send_gcode("M334 1.2.3.4 8514", timeout=300)
        except gc.PrinterError as e:
            errors.append(str(e))

    t = threading.Thread(target=stuck, daemon=True)
    t.start()
    time.sleep(0.3)
    start = time.monotonic()
    gc.disconnect(wait=0.5)
    assert time.monotonic() - start < 2
    t.join(timeout=3)
    assert not t.is_alive() and errors and "clos" in errors[0]
    assert not port.is_open and gc._ser is None


def test_quickstop_is_safe_without_a_connection(fake_port):
    gc._quickstop()                           # must not raise


def test_home_xy_homes_only_x_and_y_and_keeps_them_powered(fake_port):
    port = FakeSerial()
    fake_port.append(port)
    gc.connect()
    sent_before = len(port.written)
    gc.move_absolute(z=95.0)
    gc.home_xy()
    sent = [c for c in port.written[sent_before:] if not c.startswith("M118")]
    assert "G1 Z95.000 F" in " ".join(sent)
    homes = [c for c in sent if c.startswith("G28")]
    assert homes == ["G28 X Y"]                       # never Z: the sensor sits below the nozzle
    assert sent.index("G28 X Y") > next(i for i, c in enumerate(sent) if c.startswith("G1 Z"))
    assert sent[-1] == f"M84 S{gc.XY_HOLD_S}"
    gc.disconnect(wait=0.5)


def test_cancel_between_absolute_mode_and_z_dispatch_sends_no_motion(fake_port):
    port = FakeSerial()
    fake_port.append(port)
    gc.connect()
    cancel = threading.Event()
    original = port.write
    def write(data):
        original(data)
        if data == b"G90\n":
            cancel.set()
    port.write = write
    try:
        with pytest.raises(InterruptedError, match="cancelled"):
            gc.move_absolute(z=100, cancel=cancel)
        assert port.written == ["G90"]
    finally:
        gc.disconnect()


@pytest.mark.parametrize('operation', ['move', 'readback'])
def test_optical_cancel_breaks_forever_busy_acknowledgement_and_allows_stop(fake_port, monkeypatch, operation):
    port = FakeSerial()
    fake_port.append(port)
    gc.connect()
    cancel, dispatched = threading.Event(), threading.Event()
    original_write = port.write
    def write(data):
        if data.startswith(b'G1 ') or data == b'M114\n':
            port.busy = True
            dispatched.set()
        original_write(data)
    port.write = write
    errors = []
    def run():
        try:
            if operation == 'move':
                gc.move_absolute(z=100, cancel=cancel)
            else:
                gc.read_position(cancel=cancel)
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert dispatched.wait(2)
    cancel.set()
    original_resync = gc._resync_serial
    monkeypatch.setattr(gc, '_resync_serial', lambda: original_resync(timeout=0.1))
    try:
        with pytest.raises(gc.PrinterError, match='resync'):
            gc.motionstop()
        worker.join(timeout=2)
        assert not worker.is_alive() and isinstance(errors[0], InterruptedError)
        assert 'M410' in port.written
        assert 'M400' not in port.written
        with pytest.raises(InterruptedError):
            gc.move_absolute(z=100.1, cancel=cancel)
    finally:
        gc.disconnect()
