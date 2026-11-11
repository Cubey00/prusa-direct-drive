import queue

from pathprogrammer.ipc import CalibratorClient, CalibratorServer


def test_roundtrip():
    q = queue.Queue()
    srv = CalibratorServer(q.put)
    try:
        cli = CalibratorClient(srv.port, srv.key)
        assert cli.connected
        assert q.get(timeout=5)["type"] == "hello"
        cli.send_position("back_left", 1.5, 2.5, 3.5)
        m = q.get(timeout=5)
        assert (m["type"], m["point"], m["x"], m["y"], m["z"]) == ("position", "back_left", 1.5, 2.5, 3.5)
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
