"""
gantry_controls.py – USB serial link to a Prusa Core One used as a gantry. Nothing touches the
serial port at import time; the connection is opened on first use.

The serial port carries both the G-code replies and the metrics stream (load cell, position):
the modified firmware sends metrics over USB after `M334 usb`, one `metric:<name> ...` line per
point. A reader thread owns all reads: metric lines go to the listeners (the StrainGauge), every
other line to a reply queue that _send/_resync_serial consume.

Z convention: a higher Z means the bed is LOWER. Z_MAX is the lowest the bed
can safely go.
"""
import atexit
import queue, re, threading, time

import serial
from serial.tools import list_ports

# ---------------- settings ----------------
BAUD = 115200
PRUSA_VID = 0x2C99          # Prusa Research USB vendor ID
PORT_OVERRIDE = None        # e.g. "COM5" to skip auto-detection
XYFEEDRATE = 1200            # mm/min for nudges
ZFEEDRATE = 600             # mm/min, kept slow so the bed never slams into anything during normal moves

Z_MAX = 273.0               # mm, lowest bed position before crashing (higher Z = lower bed)
DROP_FEED = 1200            # mm/min for the emergency drop
# ------------------------------------------

_ser = None
_lock = threading.RLock()   # serial access must be one command at a time
_stop = threading.Event()   # set while a stop is in progress; long-running moves check it
_sync_count = 0

METRIC_PREFIX = "metric:"
_replies = queue.Queue()    # non-metric lines from the printer, in order
_metric_listeners = []      # callables(line), called from the reader thread
_reader = None
REPLY_POLL = 0.2            # s; how often reply waits re-check their timeout

_POS_RE = re.compile(r'X:(-?\d+(?:\.\d+)?)\s+Y:(-?\d+(?:\.\d+)?)\s+Z:(-?\d+(?:\.\d+)?)')


class PrinterError(Exception):
    pass


# ---------------- connection ----------------

def find_port():
    """Pick the printer's port automatically, but never guess between several."""
    if PORT_OVERRIDE:
        return PORT_OVERRIDE
    ports = [p for p in list_ports.comports() if p.vid == PRUSA_VID]
    if len(ports) == 1:
        return ports[0].device
    if not ports:
        raise PrinterError("No Prusa printer found. Plug it in or set PORT_OVERRIDE.")
    names = ", ".join(p.device for p in ports)
    raise PrinterError(f"Multiple Prusa ports found ({names}). Set PORT_OVERRIDE.")


def add_metric_listener(fn):
    """Call fn(line) for every metric line (without the 'metric:' prefix), from the reader thread."""
    if fn not in _metric_listeners:
        _metric_listeners.append(fn)


def remove_metric_listener(fn):
    try:
        _metric_listeners.remove(fn)
    except ValueError:
        pass


def _dispatch_line(text):
    """Route one received line: metrics to the listeners, everything else to the reply queue."""
    text = text.strip()
    if not text:
        return
    i = text.find(METRIC_PREFIX)
    if i < 0:
        _replies.put(text)
        return
    if i > 0:
        # a reply cut by a metric line (only if the printer left a line open for a long time)
        _replies.put(text[:i].strip())
    metric = text[i + len(METRIC_PREFIX):]
    for fn in list(_metric_listeners):
        try:
            fn(metric)
        except Exception as e:
            print(f"Metric listener failed on {metric!r}: {e}")


def _reader_loop(ser):
    """Owns all reads from `ser` until it is closed. Reads in chunks: the metrics stream is
    hundreds of lines a second, too much for byte-by-byte readline()."""
    buf = b""
    while True:
        try:
            data = ser.read(ser.in_waiting or 1)
        except (serial.SerialException, OSError, TypeError, AttributeError):
            return                      # port closed (disconnect) or unplugged
        if not data:
            continue
        buf += data
        *lines, buf = buf.split(b"\n")
        for raw in lines:
            _dispatch_line(raw.decode("utf-8", errors="ignore"))


def _drain_replies():
    try:
        while True:
            _replies.get_nowait()
    except queue.Empty:
        pass


def _next_reply(timeout=REPLY_POLL):
    """Next non-metric line, or '' if none arrived within `timeout` seconds."""
    try:
        return _replies.get(timeout=timeout)
    except queue.Empty:
        return ""


def connect():
    global _ser, _reader
    with _lock:
        if _ser is not None and _ser.is_open:
            return
        _ser = serial.Serial(find_port(), BAUD, timeout=0.2)
        time.sleep(2)                 # let the printer finish its serial handshake
        _ser.reset_input_buffer()     # discard boot chatter
        _drain_replies()
        _reader = threading.Thread(target=_reader_loop, args=(_ser,), daemon=True, name="serial-reader")
        _reader.start()


def disconnect():
    global _ser, _reader
    with _lock:
        if _ser is not None and _ser.is_open:
            _ser.close()              # ends the reader thread
        _ser = None
        if _reader is not None and _reader is not threading.current_thread():
            _reader.join(timeout=2)
        _reader = None


atexit.register(disconnect)


def _connected():
    return _ser is not None and _ser.is_open


# ---------------- G-code ----------------

def _send(cmd, timeout=30):
    """Send one line and block until 'ok'. Caller must hold _lock.
    `timeout` is the max silence (seconds) before giving up; busy/status
    lines from the printer reset it, so long moves and homing are fine.
    The printer still sends 'ok' after an error, so an error is raised only
    once that 'ok' is read; otherwise the next command would take it as its own."""
    connect()
    _ser.write(f"{cmd}\n".encode())
    response = []
    error = None
    last_activity = time.monotonic()
    while True:
        line = _next_reply()
        if line:
            last_activity = time.monotonic()
            response.append(line)
            if line.startswith("ok"):
                if error:
                    raise PrinterError(f"{cmd!r} -> {error}")
                return "\n".join(response)
            if line.startswith("Error:Warning"):
                print(f"Printer warning after {cmd!r}: {line}")
            elif line.startswith("Error:"):
                error = line
        elif time.monotonic() - last_activity > timeout:
            raise PrinterError(f"Timed out waiting for 'ok' after {cmd!r}")


def _send_nowait(cmd):
    """Write one line without waiting for its 'ok'. Caller must hold _lock and later drop the
    pending 'ok' with _resync_serial() (halt_move does). The printer answers a G1 only once the
    move is done, so this is the only way to watch the load cell while a move runs."""
    connect()
    _ser.write(f"{cmd}\n".encode())


def send_gcode(cmd, timeout=30):
    """Thread-safe public version of _send."""
    with _lock:
        return _send(cmd, timeout)


def _resync_serial(timeout=15):
    """Discard stale replies after a stop. Caller must hold _lock.
    Replies come back in order, so everything before the echo of a unique
    marker is left over from aborted commands (including the M410 'ok')."""
    global _sync_count
    _sync_count += 1
    marker = f"resync{_sync_count}"
    _ser.write(f"M118 {marker}\n".encode())
    deadline = time.monotonic() + timeout
    got_marker = False
    while time.monotonic() < deadline:
        line = _next_reply()
        if not line:
            continue
        if line == marker:
            got_marker = True
        elif got_marker and line.startswith("ok"):
            return
    raise PrinterError("Could not resync serial connection after stop")


def _quickstop():
    """M410: abort the current move and flush the planner.
    Deliberately NOT under _lock: whoever holds it may be blocked waiting on
    an M400 that only finishes once the motion is stopped."""
    _ser.write(b"\nM410\n")


METRICS = ("loadcell", "pos_x", "pos_y", "pos_z")     # the only metrics the PC reads


def enable_metrics():
    """Stream the metrics over this serial link (modified firmware: `M334 usb`)."""
    # M334 asks for confirmation on the printer screen if the metrics settings change
    send_gcode("M334 usb", timeout=300)
    # The printer can have dozens of diagnostic metrics on (after a firmware update/reset). Its
    # sender then drops points, and the low-rate pos_x/y/z are the ones that go missing while the
    # load cell keeps arriving. Switch off everything we don't read.
    for line in send_gcode("M333", timeout=10).splitlines():
        parts = line.removeprefix("echo:").split()
        if len(parts) == 2 and parts[1] == "1" and parts[0] not in METRICS:
            send_gcode(f"M332 {parts[0]}")
    for name in METRICS:
        send_gcode(f"M331 {name}")


# ---------------- motion ----------------

def read_position():
    m = _POS_RE.search(send_gcode("M114"))
    if not m:
        raise PrinterError("Could not parse M114 response")
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


_homing = threading.Event()  # set while G28 runs; force_guard ignores force then


def home():
    """Home all axes. Do this once before nudging."""
    _homing.set()
    try:
        return send_gcode("G28", timeout=120)
    finally:
        time.sleep(0.5)  # let the gauge window flush the bed-touch readings
        _homing.clear()


def move_relative(dx=0.0, dy=0.0, dz=0.0, XYfeed=XYFEEDRATE, Zfeed=ZFEEDRATE):
    """Relative move; XY and Z are separate so Z can have a lower speed cap."""
    with _lock:
        if _stop.is_set():
            raise PrinterError("Motion stopped")
        _send("G91")
        try:
            if dx or dy:
                _send(f"G1 X{dx:.2f} Y{dy:.2f} F{XYfeed}")
            if dz:
                _send(f"G1 Z{dz:.2f} F{Zfeed}")
            _send("M400", timeout=60)          # wait for motion to complete
        finally:
            _send("G90")                       # ALWAYS return to absolute


def move_absolute(x=None, y=None, z=None, XYfeed=XYFEEDRATE, Zfeed=ZFEEDRATE):
    """Absolute move and wait for it; None keeps that axis where it is."""
    with _lock:
        if _stop.is_set():
            raise PrinterError("Motion stopped")
        _send("G90")
        xy = " ".join(f"{a}{v:.3f}" for a, v in (("X", x), ("Y", y)) if v is not None)
        if xy:
            _send(f"G1 {xy} F{XYfeed}")
        if z is not None:
            _send(f"G1 Z{z:.3f} F{Zfeed}")
        _send("M400", timeout=120)


def halt_move():
    """Quickstop the current move without flagging a global stop. Caller must hold _lock
    (the move was queued without waiting, so nothing is blocked on an M400)."""
    _quickstop()
    _resync_serial()


def motionstop():
    """Stop all motion immediately and drop the moves already sent.
    Returns once the serial link is back in sync, so new commands work."""
    _stop.set()
    try:
        if not _connected():
            return
        _quickstop()
        with _lock:
            _resync_serial()
    finally:
        _stop.clear()


def emergency_drop(gauge, release_g=50.0, window=16):
    """Stop all motion and drop the bed fast towards Z_MAX until the load cell
    reads below `release_g` grams, then keep dropping for `extra_s` seconds."""
    motionstop()
    send_gcode("G90")
    send_gcode(f"G1 Z{Z_MAX:.2f} F{DROP_FEED}")     # 'ok' comes once queued, so we can watch while it moves
    while abs(gauge.force_g(window) or 0) >= release_g: # and (gauge.pos["z"] or 0) < Z_MAX - 0.05:
        time.sleep(0.005)
    motionstop()


def force_guard(gauge, trip_g=500.0, release_g=50.0, window=16, on_trip=None, stop_event=None):
    """Start a background thread that runs emergency_drop() whenever |force| > trip_g.
    Only start it after the gauge has been tared, or the raw offset trips it.
    Returns the event that ends the guard when set."""
    stop_event = stop_event or threading.Event()
    def loop():
        while not stop_event.is_set():
            if not _homing.is_set() and abs(gauge.force_g(window) or 0) > trip_g:
                if on_trip:
                    on_trip()
                try:
                    emergency_drop(gauge, release_g, window)
                except Exception as e:
                    print(f"Emergency drop failed: {e}")
            time.sleep(0.005)
    threading.Thread(target=loop, daemon=True).start()
    return stop_event


class MotionController:
    def __init__(self, on_idle=None, on_error=None):
        self.q = queue.Queue()
        self.target = None          # (x, y, z) where the printer will end up
        self.pending = 0            # clicks not yet completed
        self._lock = threading.Lock()
        self.on_idle, self.on_error = on_idle, on_error
        threading.Thread(target=self._worker, daemon=True).start()

    def halt(self):
        with self._lock:
            self._clear_queue()
            self.pending = 0
            self.target = None

    def read_position(self):
        return read_position()

    def nudge(self, dx, dy, dz, XYfeed):
        """Called from the GUI thread; returns immediately."""
        with self._lock:
            if self.target is None:
                self.target = read_position()
            self.target = (self.target[0] + dx, self.target[1] + dy, self.target[2] + dz)
            self.pending += 1
            self.q.put((dx, dy, dz, XYfeed))

    def move_to(self, x, y, z, XYfeed):
        """Queue a move to an absolute target; None keeps that axis where it is."""
        with self._lock:
            if self.target is None:
                self.target = read_position()
            new = tuple(t if v is None else v for t, v in zip(self.target, (x, y, z)))
            dx, dy, dz = (n - t for n, t in zip(new, self.target))
            self.target = new
            self.pending += 1
            self.q.put((dx, dy, dz, XYfeed))

    def _clear_queue(self):
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    def _worker(self):
        while True:
            dx, dy, dz, XYfeed = self.q.get()
            n = 1
            try:                                   # merge any further queued clicks
                while True:
                    ndx, ndy, ndz, XYfeed = self.q.get_nowait()
                    dx, dy, dz, n = dx + ndx, dy + ndy, dz + ndz, n + 1
            except queue.Empty:
                pass
            try:
                move_relative(dx, dy, dz, XYfeed)
            except Exception as e:
                self._fail(e)
                continue
            idle = False
            with self._lock:
                self.pending = max(0, self.pending - n)
                if self.q.empty():
                    self._resync()                 # snap target to the real position
                    idle = True
            if idle and self.on_idle:
                self.on_idle()

    def _resync(self):
        try:
            self.target = read_position()
        except Exception:
            self.target = None

    def _fail(self, err):
        with self._lock:
            self._clear_queue()

        try:
            self.target = self.read_position()
        except Exception:
            self.target = None

    def _fail(self, err):
        with self._lock:
            while not self.q.empty():
                self.q.get_nowait()
            self.pending = 0
            self._resync()
        if self.on_error:
            self.on_error(err)
