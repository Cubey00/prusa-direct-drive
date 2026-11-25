"""When the printer is busy, say with what; a hidden calibrator ends with its app."""
import queue
import time

import device_session
import gantry_controls as gc
from pathprogrammer.ipc import CalibratorClient, CalibratorServer


def test_busy_text_names_the_activity_and_the_last_command():
    gc._last_cmd[:] = ["G28 X Y", time.monotonic() - 4]
    with gc.activity("homing X and Y (G28 X Y)"):
        text = gc.busy_text()
    assert "homing X and Y" in text and "G28 X Y" in text and "s ago" in text
    assert "homing" not in gc.busy_text()                       # done: only the last command is left


def test_nested_activity_reports_the_outer_one():
    with gc.activity("setting up the force data (M334)"):
        with gc.activity("connecting to the printer over USB"):
            assert gc.busy_text().startswith("setting up the force data")


def test_a_refused_lease_says_who_holds_it(tmp_path, monkeypatch):
    monkeypatch.setattr(device_session.tempfile, "gettempdir", lambda: str(tmp_path))
    held = device_session.DeviceLease("camera").acquire()
    try:
        device_session._held.discard("camera")                  # as if another process held it
        try:
            device_session.DeviceLease("camera").acquire()
        except device_session.DeviceBusy as e:
            msg = str(e)
        else:
            raise AssertionError("second lease should be refused")
    finally:
        device_session._held.add("camera")
        held.release()
    assert "PID" in msg and "Task Manager" in msg


def test_calibrator_hears_when_the_app_is_gone():
    srv = CalibratorServer(lambda m: None)
    lost = queue.Queue()
    cli = CalibratorClient(srv.port, srv.key)
    cli.on_lost = lambda: lost.put(True)
    assert cli.connected
    deadline = time.monotonic() + 5
    while srv.conn is None and time.monotonic() < deadline:
        time.sleep(0.01)
    srv.close()                                                  # the path programmer goes away
    assert lost.get(timeout=5) is True


def test_closing_the_link_ourselves_is_not_a_loss():
    srv = CalibratorServer(lambda m: None)
    lost = queue.Queue()
    cli = CalibratorClient(srv.port, srv.key)
    cli.on_lost = lambda: lost.put(True)
    cli.close()
    srv.close()
    try:
        lost.get(timeout=1)
        raise AssertionError("on_lost called after our own close")
    except queue.Empty:
        pass
