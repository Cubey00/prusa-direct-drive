"""Link between the Tk calibrator (GUI.py, child process) and the path programmer. No Qt here.

Transport: multiprocessing.connection over 127.0.0.1. The main app listens on a free port and
starts `GUI.py --link <port>` with a random authkey in PRINTERPROJECT_LINK_KEY (hex).

Message contract (dicts, all with "v": 1):
  calibrator -> path programmer
    {"v": 1, "type": "hello"}                                   calibrator connected
    {"v": 1, "type": "position", "point": "F1" | "F2" | "F3",
     "x": float, "y": float, "z": float, "t": iso timestamp}    absolute head position (mm)
    {"v": 1, "type": "goto_done", "label": str, "ok": bool, "message": str}
    {"v": 1, "type": "status", "ok": bool, "message": str}       e.g. why a position could not be sent
    {"v": 1, "type": "bye"}                                     calibrator closing
  path programmer -> calibrator
    {"v": 1, "type": "goto", "x": float, "y": float, "z": float, "label": str, "home_xy": bool,
     "z_final": float | None}
        move the head to this absolute position: Z first (bed down to the travel height), then
        (home_xy) G28 X Y, then X/Y, then (z_final) Z to that height
    {"v": 1, "type": "jog", "dx": float, "dy": float, "dz": float, "feed": float}
        small relative move (mm, feed in mm/min) for fine-tuning onto a cone
    {"v": 1, "type": "request_position", "point": "F1" | "F2" | "F3"}
        answer with a "position" message for that point (the head's current position)
Unknown types are ignored by the receiver, so either side can add messages.
"""
import os
import secrets
import subprocess
import sys
import threading
from datetime import datetime
from multiprocessing.connection import Client, Listener

from .calibration import POINTS   # pure module, no Qt

VERSION = 1
KEY_ENV = "PRINTERPROJECT_LINK_KEY"


def goto_msg(x, y, z, label="", home_xy=False, z_final=None):
    return {"v": VERSION, "type": "goto", "x": float(x), "y": float(y), "z": float(z), "label": str(label),
            "home_xy": bool(home_xy), "z_final": None if z_final is None else float(z_final)}


def jog_msg(dx, dy, dz, feed):
    return {"v": VERSION, "type": "jog", "dx": float(dx), "dy": float(dy), "dz": float(dz),
            "feed": float(feed)}


def request_position_msg(point):
    if point not in POINTS:
        raise ValueError(f"Unknown calibration point {point!r}")
    return {"v": VERSION, "type": "request_position", "point": point}


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
        self.conn = None                # the calibrator's connection, for messages to it
        self._send_lock = threading.Lock()
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while True:
            try:
                conn = self.listener.accept()
            except Exception:
                return          # listener closed (or a bad authkey: try again would be pointless)
            self.conn = conn
            threading.Thread(target=self._recv_loop, args=(conn,), daemon=True).start()

    def _recv_loop(self, conn):
        with conn:
            while True:
                try:
                    msg = conn.recv()
                except (EOFError, OSError):
                    if self.conn is conn:
                        self.conn = None
                    return
                if isinstance(msg, dict) and msg.get("v") == VERSION:
                    self.on_message(msg)

    def send(self, msg):
        """Send to the calibrator; False if it isn't connected."""
        conn = self.conn
        if conn is None:
            return False
        with self._send_lock:
            try:
                conn.send(msg)
                return True
            except (OSError, EOFError, ValueError):
                self.conn = None
                return False

    def goto(self, x, y, z, label="", home_xy=False, z_final=None):
        return self.send(goto_msg(x, y, z, label, home_xy, z_final))

    def jog(self, dx, dy, dz, feed):
        return self.send(jog_msg(dx, dy, dz, feed))

    def request_position(self, point):
        return self.send(request_position_msg(point))

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

    def __init__(self, port, key=None, on_message=None):
        """on_message(dict) gets messages from the path programmer (e.g. "goto"), called from a
        background thread."""
        key = key if key is not None else bytes.fromhex(os.environ.get(KEY_ENV, ""))
        self.conn = None
        self.on_message = on_message
        self._lock = threading.Lock()
        try:
            self.conn = Client(("127.0.0.1", int(port)), authkey=key)
            self._send({"v": VERSION, "type": "hello"})
        except Exception as e:
            print(f"Calibrator link unavailable: {e}")
            self.conn = None
            return
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def _recv_loop(self):
        while True:
            conn = self.conn
            if conn is None:
                return
            try:
                msg = conn.recv()
            except (EOFError, OSError):
                return
            if self.on_message is not None and isinstance(msg, dict) and msg.get("v") == VERSION:
                self.on_message(msg)

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

    def send_goto_done(self, label, ok, message=""):
        return self._send({"v": VERSION, "type": "goto_done", "label": str(label), "ok": bool(ok),
                           "message": str(message)})

    def send_status(self, ok, message=""):
        return self._send({"v": VERSION, "type": "status", "ok": bool(ok), "message": str(message)})

    def close(self):
        """Say bye and stop using the link. The socket itself is left to the process exit: closing
        it while the receive thread waits on it makes Windows drop the 'bye' still in flight."""
        self._send({"v": VERSION, "type": "bye"})
        self.conn = None
