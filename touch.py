"""
touch.py
Force/depth touch. The load cell data reaches the PC with a delay (UDP batching + averaging),
and the head/sample contact is stiff, so a continuous move overshoots by roughly
stiffness x speed x delay: at 2 mm/s a 5 g contact stop ended at 90-225 g. Moves are continuous
and stopped with M410 (an immediate stop in the modified firmware); don't chunk them, that makes
the motion jittery. Therefore:

1. approach: continuous move at the approach speed until the load cell sees contact, M410. If an
   expected contact height is given (the previous point's), the fast move stops SLOW_ZONE above
   it and the expected zone is searched at the search speed, so it isn't hit fast.
2. if contact was made at the approach speed: back off until the force is gone, then find contact
   again at the (slow) search speed. contact_z comes from this slow contact.
3. press: continuous at the touch speed; FORCE stops (M410) at the target force, DEPTH at `value`
   mm past contact.
Then return to the start height. Every touch keeps a trace (time, phase, Z, force) for the run log.

Z convention (see gantry_controls): higher Z = lower bed, so touching means decreasing Z.
Forces are in grams via StrainGauge.force_g (calibration.json scale).
"""
import time
from dataclasses import dataclass, asdict, field

from gantry_controls import _lock, _send, _send_nowait, _resync_serial, _stop, _POS_RE, halt_move, PrinterError

FAST = 16                 # samples (~50 ms at ~320 Hz) for the force reading while moving
POLL = 0.002              # s between force checks
RETURN_SPEED = 10.0       # mm/s back to the start height
REACH_TOL = 0.02          # mm: "target reached" tolerance on the metrics Z
SETTLE = 0.1              # s after the XY move before tare samples are collected
TARE_SAMPLES = 300        # ~1 s of samples, all taken while standing still
TRACE_DT = 0.02           # s between trace entries
STOP_SETTLE = 0.05        # s after M410 before the stop position is read

SEARCH_SPEED = 0.1        # mm/s: slow contact search (capped at the touch speed)
SLOW_ZONE = 0.5           # mm above/below the expected contact height searched slowly
BACKOFF = 0.3             # mm per back-off after a fast contact
MAX_BACKOFFS = 5

STEP_SETTLE = 0.15        # s after a back-off before the force is read (covers the data delay)


@dataclass
class TouchResult:
    status: str = "error"          # ok | no_contact | overload | aborted | error
    start_z: float = None
    contact_z: float = None
    end_z: float = None
    peak_g: float = 0.0
    final_g: float = None
    message: str = ""
    trace: list = field(default_factory=list)   # [t (s), phase, metrics Z, |force| (g)]
    images: list = field(default_factory=list)  # one dict per capture(): increment, target, forces, z

    def to_dict(self):
        return asdict(self)


class _Abort(Exception):
    """Ends the touch with res.status already set."""


class _Tracer:
    def __init__(self, res, gauge):
        self.res, self.gauge, self.t0, self.last, self.phase = res, gauge, time.monotonic(), -1.0, "tare"

    def __call__(self, f, always=False):
        t = time.monotonic() - self.t0
        if always or t - self.last >= TRACE_DT:
            self.last = t
            self.res.trace.append([round(t, 3), self.phase, self.gauge.pos.get("z"), round(f, 1)])


def _z():
    m = _POS_RE.search(_send("M114"))
    if not m:
        raise PrinterError("Could not parse M114 response")
    return float(m.group(3))


def _stopped_z(gauge, trace):
    """Z where an M410 left the bed: the streamed metrics Z (M114 as fallback); both go into the
    trace. (They agreed on the printer; kept as a cross-check.)"""
    time.sleep(STOP_SETTLE)                 # let the metrics Z catch up with the stop
    m114, metric = _z(), gauge.pos.get("z")
    trace.res.trace.append([round(time.monotonic() - trace.t0, 3), "stopped", metric, m114])
    return m114 if metric is None else metric


def _force(gauge):
    f = gauge.force_g(FAST)
    if f is None:
        raise PrinterError("Lost load cell data")
    return f


def _wait_for_data(gauge, timeout=3.0):
    t0 = time.monotonic()
    while not gauge.can_tare():
        if time.monotonic() - t0 > timeout:
            raise PrinterError("No load cell data")
        time.sleep(0.05)


def _check(gauge, res, max_force_g, cancel, trace):
    """|force| now; raises _Abort on stop/overload."""
    if _stop.is_set() or (cancel is not None and cancel.is_set()):
        res.status = "aborted"
        raise _Abort
    f = abs(_force(gauge))
    res.peak_g = max(res.peak_g, f)
    trace(f)
    if f > max_force_g:
        res.status = "overload"
        raise _Abort
    return f


def _move_watch(gauge, res, from_z, to_z, speed, stop_when, max_force_g, cancel, trace, phase):
    """Continuous move from_z -> to_z, watched: M410 on a hit, M400 when reached.
    Returns 'hit' or 'reached'. Raises _Abort on overload/stop (after halting the move)."""
    trace.phase = phase
    # the printer sends the G1 'ok' only when the move is done: don't wait for it, watch instead
    _send_nowait(f"G1 Z{to_z:.4f} F{speed * 60:.2f}")
    deadline = time.monotonic() + abs(from_z - to_z) / speed + 1.0
    try:
        while True:
            f = _check(gauge, res, max_force_g, cancel, trace)
            if stop_when(f):
                trace(f, always=True)
                halt_move()
                return "hit"
            z = gauge.pos.get("z")
            if (z is not None and abs(z - to_z) <= REACH_TOL) or time.monotonic() > deadline:
                _resync_serial(timeout=120)     # drops the G1 'ok', returns once the move is done
                _send("M400", timeout=120)
                return "reached"
            time.sleep(POLL)
    except _Abort:
        if not _stop.is_set():          # a motionstop halts the move itself
            halt_move()
        raise


def _settled_force(gauge, res, max_force_g, cancel, trace):
    """|force| once the bed stands still and the delayed data has caught up."""
    end = time.monotonic() + STEP_SETTLE
    while time.monotonic() < end:
        _check(gauge, res, max_force_g, cancel, trace)
        time.sleep(POLL)
    if not gauge.wait_samples(FAST, timeout=2.0):
        raise PrinterError("Lost load cell data")
    f = abs(_force(gauge))
    res.peak_g = max(res.peak_g, f)
    trace(f, always=True)
    return f


def _approach(gauge, res, start_z, limit_z, approach_speed, search_speed, contact_g, max_force_g,
              expect_z, cancel, trace, on_status):
    """Returns the contact Z, found at the search speed."""
    contact = lambda f: f >= contact_g
    segments = [(limit_z, approach_speed)]
    if expect_z is not None and limit_z < expect_z + SLOW_ZONE < start_z:
        lo = max(expect_z - SLOW_ZONE, limit_z)
        segments = [(expect_z + SLOW_ZONE, approach_speed), (lo, search_speed), (limit_z, approach_speed)]
    z = start_z
    for to_z, speed in segments:
        if to_z >= z:
            continue
        phase = "approach" if speed > search_speed else "search"
        if _move_watch(gauge, res, z, to_z, speed, contact, max_force_g, cancel, trace, phase) == "hit":
            break
        z = to_z
    else:
        res.status, res.message = "no_contact", f"No contact within {start_z - limit_z:g} mm"
        raise _Abort
    if speed <= search_speed:
        return _stopped_z(gauge, trace)

    # contact at approach speed: back off and find it again slowly
    on_status("Contact at approach speed, backing off to find it slowly")
    trace.phase = "backoff"
    z = None
    for _ in range(MAX_BACKOFFS):
        z = min((_stopped_z(gauge, trace) if z is None else z) + BACKOFF, start_z)
        _send(f"G1 Z{z:.4f} F{RETURN_SPEED * 60:.0f}")
        _send("M400", timeout=60)
        if _settled_force(gauge, res, max_force_g, cancel, trace) < contact_g:
            break
    else:
        res.status, res.message = "error", "Force stays above the contact threshold after backing off"
        raise _Abort
    if _move_watch(gauge, res, z, limit_z, search_speed, contact, max_force_g, cancel, trace,
                   "search") != "hit":
        res.status, res.message = "no_contact", "Contact lost during the slow search"
        raise _Abort
    return _stopped_z(gauge, trace)


def _press_force(gauge, res, target_g, speed, limit_z, max_force_g, cancel, trace):
    """Continuous press at the touch speed, M410 at target_g. Returns the final Z."""
    if _move_watch(gauge, res, res.contact_z, limit_z, speed, lambda f: f >= target_g, max_force_g,
                   cancel, trace, "press") != "hit":
        res.status, res.message = "no_contact", f"{target_g:g} g not reached within travel limit"
        raise _Abort
    return _stopped_z(gauge, trace)


def _image_stop(gauge, res, k, target, z, max_force_g, cancel, trace, capture):
    """Bed stands still (M410 done): settle, expose, settle again. Force = mean of before/after."""
    trace.phase = "image"
    before = _settled_force(gauge, res, max_force_g, cancel, trace)
    capture(k, target)                      # blocks for the exposure; the bed does not move
    after = _settled_force(gauge, res, max_force_g, cancel, trace)
    res.images.append({"increment": k, "target": target, "force_before_g": round(before, 2),
                       "force_after_g": round(after, 2), "force_g": round((before + after) / 2, 2),
                       "z": z, "t": round(time.monotonic() - trace.t0, 3)})


def _press_images(gauge, res, mode, value, step, speed, limit_z, max_force_g, cancel, trace, capture,
                  on_status):
    """Press as a series of continuous moves, each stopped with M410 at the next image target
    (not chunked into small steps: one G1 per image). Returns the final Z."""
    n = int(value / step + 1e-9)
    targets = [(k, k * step) for k in range(1, n + 1)]
    z = res.contact_z
    for k, target in targets:
        if mode == "force":
            if abs(_force(gauge)) >= target and res.images:        # overshoot already passed it
                on_status(f"Image target {target:g} g skipped (already passed)")
                continue
            stop = lambda f, t=target: f >= t
            to_z = limit_z
        else:
            stop = lambda f: False
            to_z = max(res.contact_z - target, limit_z)
        r = _move_watch(gauge, res, z, to_z, speed, stop, max_force_g, cancel, trace, "press")
        if mode == "force" and r != "hit":
            res.status, res.message = "no_contact", f"{target:g} g not reached within travel limit"
            raise _Abort
        z = _stopped_z(gauge, trace) if r == "hit" else _z()
        _image_stop(gauge, res, k, target, z, max_force_g, cancel, trace, capture)
    # the remainder up to `value` (value not a multiple of step)
    if mode == "force":
        if abs(_force(gauge)) < value:
            if _move_watch(gauge, res, z, limit_z, speed, lambda f: f >= value, max_force_g, cancel,
                           trace, "press") != "hit":
                res.status, res.message = "no_contact", f"{value:g} g not reached within travel limit"
                raise _Abort
            z = _stopped_z(gauge, trace)
    else:
        target_z = max(res.contact_z - value, limit_z)
        if z - target_z > REACH_TOL:
            _move_watch(gauge, res, z, target_z, speed, lambda f: False, max_force_g, cancel, trace, "press")
            z = _z()
    return z


def touch(gauge, mode, value, touch_speed, approach_speed=2.0, contact_g=5.0, max_force_g=450.0,
          max_travel=15.0, hold=0.1, expect_z=None, search_speed=SEARCH_SPEED, cancel=None,
          on_status=print, image_step=0.0, capture=None):
    """mode: "force" (value in g) or "depth" (value in mm past contact). Speeds in mm/s.
    expect_z: expected contact height (e.g. the previous point's), searched slowly; None = unknown.
    cancel: optional threading.Event that aborts the touch (M410, then the return).
    image_step (g or mm like value) with capture(k, target): the press stops every image_step for an
    exposure, the bed standing still (res.images lists them). 0 or no capture = a plain press.
    Starts from the current Z and returns there, also when cancelled; not during a motionstop."""
    mode = getattr(mode, "value", mode)
    search_speed = min(search_speed, touch_speed)
    res = TouchResult()
    trace = _Tracer(res, gauge)
    with _lock:
        try:
            _wait_for_data(gauge)
            _send("G90")
            _send("M400", timeout=120)      # XY move fully finished before the zero is taken
            res.start_z = _z()
            time.sleep(SETTLE)              # let the cable load and vibrations settle
            # zero from samples taken only after the head stopped: XY moves shift the cable
            # load on the gauge, so anything older pollutes the tare
            if not gauge.tare_fresh(TARE_SAMPLES):
                raise PrinterError("No fresh load cell data for the tare")
            limit_z = res.start_z - max_travel

            res.contact_z = _approach(gauge, res, res.start_z, limit_z, approach_speed, search_speed,
                                      contact_g, max_force_g, expect_z, cancel, trace, on_status)
            on_status(f"Contact at Z{res.contact_z:.3f}")

            if image_step > 0 and capture is not None:
                res.end_z = _press_images(gauge, res, mode, value, image_step, touch_speed, limit_z,
                                          max_force_g, cancel, trace, capture, on_status)
            elif mode == "force":
                res.end_z = _press_force(gauge, res, value, touch_speed, limit_z, max_force_g,
                                         cancel, trace)
            else:
                target_z = max(res.contact_z - value, limit_z)
                _move_watch(gauge, res, res.contact_z, target_z, touch_speed, lambda f: False,
                            max_force_g, cancel, trace, "depth")
                res.end_z = _z()
            res.final_g = _force(gauge)
            res.status = "ok"
            time.sleep(hold)
            return res

        except _Abort:
            return res
        except PrinterError as e:
            res.status = "aborted" if _stop.is_set() else "error"
            res.message = str(e)
            return res
        finally:
            trace.phase = "return"
            if res.start_z is not None and not _stop.is_set():
                try:
                    _send(f"G1 Z{res.start_z:.3f} F{RETURN_SPEED * 60:.0f}")
                    _send("M400", timeout=60)
                except PrinterError as e:
                    res.status, res.message = "error", f"Return failed: {e}"
                    on_status(res.message)
