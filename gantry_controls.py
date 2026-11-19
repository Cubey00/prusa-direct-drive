"""
gantry_controls.pyUSB serial link to a Prusa Core One used as a gantry. Nothing touches the
serial port at import time; the connection is opened on first use.

Z convention: a higher Z means the bed is LOWER. Z_MAX is the lowest the bed
can safely go.
"""
import atexit
import queue, re, socket, threading, time

import serial
from serial.tools import list_ports

from machine_settings import SETTINGS
from device_session import DeviceLease

# ---------------- settings ----------------
BAUD = 115200
PRUSA_VID = 0x2C99          # Prusa Research USB vendor ID
PORT_OVERRIDE = None        # e.g. "COM5" to skip auto-detection
XYFEEDRATE = 1200            # mm/min for nudges
ZFEEDRATE = 600             # mm/min, kept slow so the bed never slams into anything during normal moves

Z_MAX = SETTINGS.printer.z_max   # mm, lowest bed position before crashing (machine_settings.toml)
DROP_FEED = 1200            # mm/min for the emergency drop
CONNECT_SETTLE = 2.0        # s after opening the port before talking to the printer
DISCONNECT_WAIT = 2.0       # s disconnect() waits for a running command before closing anyway
# ------------------------------------------

_ser = None
_device_lease = None
_lock = threading.RLock()   # serial access must be one command at a time
_write_lock = threading.Lock()  # serialize command dispatch with immediate STOP
_stop = threading.Event()   # set while a stop is in progress; long-running moves check it
_closing = threading.Event()  # set by disconnect(): a command still waiting for its 'ok' gives up
_sync_count = 0

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


def get_local_ip():
    """IP the printer should stream metrics to.

    The default route can be a VPN (public-looking address the printer can't reach), so
    prefer a private LAN address. Override with the PRINTER_METRICS_IP environment variable.
    """
    import ipaddress
    import os
    override = os.environ.get("PRINTER_METRICS_IP")
    if override:
        return override
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        addrs = set()
    lan = [a for a in addrs
           if ipaddress.ip_address(a).is_private and not ipaddress.ip_address(a).is_link_local
           and not ipaddress.ip_address(a).is_loopback]
    if lan:
        lan.sort(key=lambda a: not a.startswith("192.168."))   # home/lab LAN first
        return lan[0]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))      # no packet is sent; just picks the default interface
        return s.getsockname()[0]
    finally:
        s.close()


def connect():
    global _ser, _device_lease
    with _lock:
        if _ser is not None and _ser.is_open:
            return
        port = find_port()
        lease = DeviceLease("printer").acquire()
        try:
            _closing.clear()
            _ser = serial.Serial(port, BAUD, timeout=1)
            time.sleep(CONNECT_SETTLE)    # let the printer finish its serial handshake
            _ser.reset_input_buffer()     # discard boot chatter
            _device_lease = lease
        except Exception:
            if _ser is not None:
                _ser.close()
                _ser = None
            lease.release()
            raise


def disconnect(wait=None):
    """Close the serial link without hanging. A command still waiting for its 'ok' holds the lock,
    and the printer's 'busy' lines keep that wait alive forever (e.g. while a question waits on
    its screen), so after `wait` seconds the port is closed anyway; that command then fails."""
    global _ser, _device_lease
    _closing.set()
    got = _lock.acquire(timeout=DISCONNECT_WAIT if wait is None else wait)
    try:
        ser, _ser = _ser, None
        if ser is not None and ser.is_open:
            try:
                ser.close()
            except (serial.SerialException, OSError):
                pass
    finally:
        if _device_lease is not None:
            _device_lease.release()
            _device_lease = None
        if got:
            _lock.release()


atexit.register(disconnect)


def _connected():
    return _ser is not None and _ser.is_open


def _readline(ser):
    """One line from the printer; PrinterError once the link is closed or closing."""
    if _closing.is_set():
        raise PrinterError("Serial connection is closing")
    try:
        return ser.readline().decode("utf-8", errors="ignore").strip()
    except (serial.SerialException, OSError, TypeError, AttributeError):
        raise PrinterError("Serial connection was closed") from None


# ---------------- G-code ----------------

def _write_line(ser, data, cancel=None):
    with _write_lock:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Motion cancelled before command dispatch")
        ser.write(data)


def _send(cmd, timeout=30, *, cancel=None):
    """Send one line and block until 'ok'. Caller must hold _lock.
    `timeout` is the max silence (seconds) before giving up; busy/status
    lines from the printer reset it, so long moves and homing are fine.
    The printer still sends 'ok' after an error, so an error is raised only
    once that 'ok' is read; otherwise the next command would take it as its own."""
    connect()
    ser = _ser
    _write_line(ser, f"{cmd}\n".encode(), cancel)
    response = []
    error = None
    last_activity = time.monotonic()
    while True:
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Motion cancelled while awaiting command acknowledgement")
        line = _readline(ser)
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
    _write_line(_ser, f"{cmd}\n".encode())


def send_gcode(cmd, timeout=30, *, cancel=None):
    """Thread-safe public version of _send."""
    with _lock:
        return _send(cmd, timeout, cancel=cancel)


def _resync_serial(timeout=15):
    """Discard stale replies after a stop. Caller must hold _lock.
    Replies come back in order, so everything before the echo of a unique
    marker is left over from aborted commands (including the M410 'ok')."""
    global _sync_count
    _sync_count += 1
    marker = f"resync{_sync_count}"
    ser = _ser
    _write_line(ser, f"M118 {marker}\n".encode())
    deadline = time.monotonic() + timeout
    got_marker = False
    while time.monotonic() < deadline:
        line = _readline(ser)
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
    an M400 that only finishes once the motion is stopped. Does nothing when not connected."""
    ser = _ser
    if ser is None or not ser.is_open:
        return
    try:
        _write_line(ser, b"\nM410\n")
    except (serial.SerialException, OSError):
        pass


METRICS = ("loadcell", "pos_x", "pos_y", "pos_z")     # the only metrics the PC reads


def enable_metrics(port=8514, pc_ip=None):
    pc_ip = pc_ip or get_local_ip()
    # M334 asks for confirmation on the printer screen if the metrics settings change
    send_gcode(f"M334 {pc_ip} {port}", timeout=300)
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

def bed_up_refusal(dz, force_g, limit_g):
    """Why a jog must not run, or None. Moving the bed up (dz < 0) is refused once the sensor
    already feels more than limit_g grams, or while the force is unknown (no tared data):
    otherwise a jog could press the sensor into the plate."""
    if dz >= 0:
        return None
    if force_g is None:
        return "Bed up refused: no force data yet (the load cell is not tared); wait a moment."
    if abs(force_g) > limit_g:
        return (f"Bed up refused: the sensor already feels {abs(force_g):.0f} g (limit {limit_g:g} g). "
                "It touches the cone; lower the bed or move X/Y.")
    return None


def read_position(*, cancel=None, timeout=30):
    m = _POS_RE.search(send_gcode("M114", timeout=timeout, cancel=cancel))
    if not m:
        raise PrinterError("Could not parse M114 response")
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


def read_position_snapshot(*, cancel=None):
    """Read idle position for a camera preview without retaining a new connection."""
    with _lock:
        was_connected = _ser is not None and _ser.is_open
        try:
            return read_position(cancel=cancel, timeout=3)
        except (PrinterError, OSError):
            # A timed-out command can leave a reply pending on the serial link.
            if was_connected:
                disconnect()
            raise
        finally:
            if not was_connected and _ser is not None:
                disconnect()


_homing = threading.Event()  # set while G28 runs; force_guard ignores force then


def home():
    """Home all axes. Do this once before nudging."""
    _homing.set()
    try:
        return send_gcode("G28", timeout=120)
    finally:
        time.sleep(0.5)  # let the gauge window flush the bed-touch readings
        _homing.clear()


XY_HOLD_S = 1800   # idle seconds before the printer releases X/Y (firmware default 120)


def home_xy():
    """Home X and Y only (G28 X Y): the head may have been moved by hand while X/Y were released
    (the firmware releases them after 2 min idle and then forgets where they are). Z is not homed:
    its motors stay on, so it keeps its position, and Z homing probes the bed with the nozzle,
    which the mounted sensor would hit. Lower the bed first. Afterwards X/Y stay powered for
    XY_HOLD_S of idle, long enough to fine-tune a calibration point."""
    _homing.set()
    try:
        send_gcode("G28 X Y", timeout=120)
        send_gcode(f"M84 S{XY_HOLD_S}")
    finally:
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


def move_absolute(x=None, y=None, z=None, XYfeed=XYFEEDRATE, Zfeed=ZFEEDRATE, *, cancel=None):
    """Absolute move and wait for it; None keeps that axis where it is."""
    with _lock:
        if _stop.is_set():
            raise PrinterError("Motion stopped")
        _send("G90", cancel=cancel)
        xy = " ".join(f"{a}{v:.3f}" for a, v in (("X", x), ("Y", y)) if v is not None)
        if xy:
            _send(f"G1 {xy} F{XYfeed}", cancel=cancel)
        if z is not None:
            _send(f"G1 Z{z:.3f} F{Zfeed}", cancel=cancel)
        _send("M400", timeout=120, cancel=cancel)


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


def emergency_drop(gauge, release_g=50.0, window=16, max_drop=30.0):
    """Stop all motion and drop the bed fast until the load cell reads below `release_g` grams,
    but never more than `max_drop` mm (nor past Z_MAX), then stop the drop. Returns the mm asked.
    The cap matters when the force stays high anyway (load cell drift, a stuck reading): the bed
    must not run all the way to the bottom then."""
    motionstop()
    z0 = gauge.pos.get("z")
    drop = max_drop if z0 is None else max(0.0, min(max_drop, Z_MAX - z0))
    if drop <= 0:
        return 0.0
    with _lock:
        _send("G91")            # relative: the cap holds even when the metrics Z is unknown
        try:
            # the G1 'ok' only comes once the move is done: don't wait for it, watch the force instead
            _send_nowait(f"G1 Z{drop:.2f} F{DROP_FEED}")
            t_end = time.monotonic() + drop / (DROP_FEED / 60.0) + 2.0
            while abs(gauge.force_g(window) or 0) >= release_g:
                z = gauge.pos.get("z")
                if (_stop.is_set() or time.monotonic() > t_end
                        or (z is not None and z0 is not None and z >= z0 + drop - 0.05)):
                    break       # a user stop is waiting for the lock, or the drop is done
                time.sleep(0.005)
        finally:
            halt_move()         # M410 + drop the pending G1 'ok'
            _send("G90")
    return drop


def force_guard(gauge, trip_g=500.0, release_g=50.0, window=16, on_trip=None, stop_event=None,
                max_drop=30.0):
    """Start a background thread that runs emergency_drop() whenever |force| > trip_g.
    Only start it after the gauge has been tared, or the raw offset trips it.
    One trip drops at most `max_drop` mm; if the force is still over trip_g after a drop, it is
    not the bed pressing (that went away): the guard waits until the force has been under
    trip_g once before it may drop again, so drift never walks the bed to the bottom.
    Returns the event that ends the guard when set."""
    stop_event = stop_event or threading.Event()
    def loop():
        armed = True
        while not stop_event.is_set():
            f = abs(gauge.force_g(window) or 0)
            if not armed and f <= trip_g:
                armed = True
            if armed and not _homing.is_set() and f > trip_g:
                if on_trip:
                    on_trip()
                try:
                    emergency_drop(gauge, release_g, window, max_drop=max_drop)
                except Exception as e:
                    print(f"Emergency drop failed: {e}")
                armed = False
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
