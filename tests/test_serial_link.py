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
