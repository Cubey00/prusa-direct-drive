"""Link between the Tk calibrator (GUI.py, child process) and the path programmer. No Qt here.

Transport: multiprocessing.connection over 127.0.0.1. The main app listens on a free port and
starts `GUI.py --link <port>` with a random authkey in PRINTERPROJECT_LINK_KEY (hex).

Message contract (dicts, all with "v": 1):
    {"v": 1, "type": "hello"}                                   calibrator connected
    {"v": 1, "type": "position", "point": "back_left" | "front_right",
     "x": float, "y": float, "z": float, "t": iso timestamp}    absolute head position (mm)
    {"v": 1, "type": "bye"}                                     calibrator closing
Unknown types are ignored by the receiver, so either side can add messages.
"""
import os
import secrets
import subprocess
import sys
import threading
from datetime import datetime
from multiprocessing.connection import Client, Listener

VERSION = 1
KEY_ENV = "PRINTERPROJECT_LINK_KEY"
POINTS = ("back_left", "front_right")


def position_msg(point, x, y, z):
    if point not in POINTS:
        raise ValueError(f"Unknown calibration point {point!r}")
    return {"v": VERSION, "type": "position", "point": point, "x": float(x), "y": float(y),
            "z": float(z), "t": datetime.now().isoformat(timespec="milliseconds")}


class CalibratorServer:
    """Listens for calibrator messages; on_message(dict) is called from a background thread."""

    def __init__(self, on_message):
        self.on_message = on_message
        self.key = secrets.token_bytes(16)
        self.listener = Listener(("127.0.0.1", 0), authkey=self.key)
        self.port = self.listener.address[1]
        self.proc = None
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while True:
            try:
                conn = self.listener.accept()
            except Exception:
                return          # listener closed (or a bad authkey: try again would be pointless)
            threading.Thread(target=self._recv_loop, args=(conn,), daemon=True).start()

    def _recv_loop(self, conn):
        with conn:
            while True:
                try:
                    msg = conn.recv()
                except (EOFError, OSError):
                    return
                if isinstance(msg, dict) and msg.get("v") == VERSION:
                    self.on_message(msg)

    def launch(self, script):
        if self.is_running():
            return
        env = dict(os.environ, **{KEY_ENV: self.key.hex()})
        self.proc = subprocess.Popen([sys.executable, str(script), "--link", str(self.port)],
                                     cwd=os.path.dirname(str(script)), env=env)

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def terminate(self):
        if self.is_running():
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def close(self):
        try:
            self.listener.close()
        except Exception:
            pass


class CalibratorClient:
    """Used by GUI.py. Fails softly: the calibrator keeps working if the main app is gone."""

    def __init__(self, port, key=None):
        key = key if key is not None else bytes.fromhex(os.environ.get(KEY_ENV, ""))
        self.conn = None
        self._lock = threading.Lock()
        try:
            self.conn = Client(("127.0.0.1", int(port)), authkey=key)
            self._send({"v": VERSION, "type": "hello"})
        except Exception as e:
            print(f"Calibrator link unavailable: {e}")
            self.conn = None

    @property
    def connected(self):
        return self.conn is not None

    def _send(self, msg):
        if self.conn is None:
            return False
        with self._lock:
            try:
                self.conn.send(msg)
                return True
            except Exception as e:
                print(f"Calibrator link lost: {e}")
                self.conn = None
                return False

    def send_position(self, point, x, y, z):
        return self._send(position_msg(point, x, y, z))

    def close(self):
        self._send({"v": VERSION, "type": "bye"})
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
