import queue

from pathprogrammer.ipc import CalibratorClient, CalibratorServer


def test_roundtrip():
    q = queue.Queue()
    srv = CalibratorServer(q.put)
    try:
        cli = CalibratorClient(srv.port, srv.key)
        assert cli.connected
        assert q.get(timeout=5)["type"] == "hello"
        cli.send_position("F1", 1.5, 2.5, 3.5)
        m = q.get(timeout=5)
        assert (m["type"], m["point"], m["x"], m["y"], m["z"]) == ("position", "F1", 1.5, 2.5, 3.5)
        cli.close()
        assert q.get(timeout=5)["type"] == "bye"
    finally:
        srv.close()


def test_bad_key_fails_softly():
    srv = CalibratorServer(lambda m: None)
    try:
        assert not CalibratorClient(srv.port, b"wrong").connected
    finally:
        srv.close()


def test_goto_reaches_the_calibrator_and_done_comes_back():
    to_programmer, to_calibrator = queue.Queue(), queue.Queue()
    srv = CalibratorServer(to_programmer.put)
    try:
        cli = CalibratorClient(srv.port, srv.key, on_message=to_calibrator.put)
        assert to_programmer.get(timeout=5)["type"] == "hello"
        assert srv.goto(10.0, 106.0, 95.0, "F1")
        m = to_calibrator.get(timeout=5)
        assert (m["type"], m["x"], m["y"], m["z"], m["label"]) == ("goto", 10.0, 106.0, 95.0, "F1")
        cli.send_goto_done("F1", True, "at F1")
        d = to_programmer.get(timeout=5)
        assert (d["type"], d["label"], d["ok"]) == ("goto_done", "F1", True)
        cli.close()
    finally:
        srv.close()


def test_goto_without_calibrator_returns_false():
    srv = CalibratorServer(lambda m: None)
    try:
        assert srv.goto(0, 0, 0) is False
    finally:
        srv.close()


def test_jog_and_position_request_reach_the_calibrator():
    to_programmer, to_calibrator = queue.Queue(), queue.Queue()
    srv = CalibratorServer(to_programmer.put)
    try:
        cli = CalibratorClient(srv.port, srv.key, on_message=to_calibrator.put)
        assert to_programmer.get(timeout=5)["type"] == "hello"
        assert srv.jog(0.1, 0, -0.05, 120)
        m = to_calibrator.get(timeout=5)
        assert (m["type"], m["dx"], m["dy"], m["dz"], m["feed"]) == ("jog", 0.1, 0.0, -0.05, 120.0)
        assert srv.request_position("F2")
        assert to_calibrator.get(timeout=5) == {"v": 1, "type": "request_position", "point": "F2"}
        cli.send_status(False, "head still moving")
        s = to_programmer.get(timeout=5)
        assert (s["type"], s["ok"], s["message"]) == ("status", False, "head still moving")
        cli.close()
    finally:
        srv.close()


def test_home_reaches_the_calibrator():
    to_programmer, to_calibrator = queue.Queue(), queue.Queue()
    srv = CalibratorServer(to_programmer.put)
    try:
        cli = CalibratorClient(srv.port, srv.key, on_message=to_calibrator.put)
        assert to_programmer.get(timeout=5)["type"] == "hello"
        assert srv.home(95.0, "home")
        m = to_calibrator.get(timeout=5)
        assert (m["type"], m["z"], m["label"]) == ("home", 95.0, "home")
        cli.close()
    finally:
        srv.close()
