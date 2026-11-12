"""Hardware abstraction: the real printer (gantry_controls + StrainGauge + touch) and a simulator."""
import math
import threading
import time
from typing import Protocol

from . import config


class Hardware(Protocol):
    def open(self): ...
    def position(self): ...                          # (x, y, z)
    def move_xy(self, x, y, speed): ...              # mm, mm/s; blocks until done
    def touch(self, point, settings): ...            # -> touch.TouchResult
    def stop(self): ...                              # thread-safe, from the UI thread
    def close(self): ...


class PrinterHardware:
    """Owns the serial port and the UDP metrics port for the length of a run."""

    def __init__(self, on_status=print):
        self.on_status = on_status
        self.gauge = None
        self._guard_stop = None
        self._last_contact_z = None      # the next point's contact is expected near here
        self._cancel = threading.Event()  # STOP pressed: the touch ends after its current chunk
        self.safe_z = None               # Z at open() (the travel height), restored after a stop

    def open(self):
        import gantry_controls as gc
        from strain_gauge import StrainGauge
        self._last_contact_z = None
        self._cancel.clear()
        gc.connect()
        self.safe_z = gc.read_position()[2]
        gc.enable_metrics()
        self.gauge = StrainGauge()
        self.gauge.start()
        t0 = time.monotonic()
        while not self.gauge.tare():
            if time.monotonic() - t0 > 5:
                raise gc.PrinterError("No load cell data over UDP metrics")
            time.sleep(0.1)
        self._guard_stop = gc.force_guard(self.gauge, trip_g=config.GUARD_TRIP_G, release_g=config.GUARD_RELEASE_G,
                                          on_trip=lambda: self.on_status("FORCE LIMIT: bed dropped"))

    def position(self):
        import gantry_controls as gc
        return gc.read_position()

    def move_xy(self, x, y, speed):
        import gantry_controls as gc
        gc.move_absolute(x, y, None, XYfeed=speed * 60)

    def touch(self, point, settings):
        from touch import touch
        res = touch(self.gauge, point.mode.value, point.value, point.speed,
                    approach_speed=settings.approach_speed, contact_g=settings.contact_g,
                    max_force_g=config.MAX_FORCE_G, max_travel=settings.max_travel,
                    hold=config.HOLD_S, expect_z=self._last_contact_z,
                    search_speed=config.SEARCH_SPEED, cancel=self._cancel, on_status=self.on_status)
        if res.contact_z is not None:
            self._last_contact_z = res.contact_z
        return res

    def force_trace(self):
        """Recent tared force in grams for the live graph (pressing = positive, as in GUI.py), None before open()."""
        if self.gauge is None:
            return None
        return [-g for g in self.gauge.grams(self.gauge.view_smoothed())]

    def stop(self):
        import gantry_controls as gc
        self._cancel.set()
        def work():
            try:
                gc.motionstop()
            except Exception as e:
                self.on_status(f"Stop error: {e}")
        threading.Thread(target=work, daemon=True).start()

    def close(self):
        import gantry_controls as gc
        if self._guard_stop:
            self._guard_stop.set()
        if self._cancel.is_set() and self.safe_z is not None:
            self._retreat(gc)
        if self.gauge:
            self.gauge.stop()
        gc.disconnect()

    def _retreat(self, gc):
        """After a stop: lift the head off the part, back to the travel height."""
        t0 = time.monotonic()
        while gc._stop.is_set() and time.monotonic() - t0 < 20:     # let motionstop finish
            time.sleep(0.05)
        try:
            gc.move_absolute(z=self.safe_z, Zfeed=config.RETURN_SPEED * 60)
            self.on_status(f"Stopped: returned to safe Z{self.safe_z:.2f}")
        except Exception as e:
            self.on_status(f"Could not return to safe Z: {e}")


class SimHardware:
    """Dry run: logs, sleeps in proportion to distance/speed (time_scale 0 for tests)."""

    def __init__(self, time_scale=1.0, fail_at=None, on_status=print):
        self.time_scale, self.fail_at, self.on_status = time_scale, fail_at, on_status
        self.pos = [100.0, 100.0, 150.0]
        self.calls = []
        self._stopped = threading.Event()
        self._touches = 0

    def _sleep(self, s):
        end = time.monotonic() + s * self.time_scale
        while time.monotonic() < end and not self._stopped.is_set():
            time.sleep(0.01)

    def open(self):
        self.calls.append(("open",))
        self._stopped.clear()

    def position(self):
        return tuple(self.pos)

    def move_xy(self, x, y, speed):
        from gantry_controls import PrinterError
        if self._stopped.is_set():
            raise PrinterError("Motion stopped")
        self.calls.append(("move_xy", x, y, speed))
        self._sleep(math.hypot(x - self.pos[0], y - self.pos[1]) / speed)
        self.pos[0], self.pos[1] = x, y

    def touch(self, point, settings):
        from touch import TouchResult
        self.calls.append(("touch", point.mode.value, point.value, point.speed))
        self._touches += 1
        z = self.pos[2]
        res = TouchResult(start_z=z, contact_z=z - 2.0)
        self._sleep(2.0 / settings.approach_speed + 0.5 / point.speed)
        if self._stopped.is_set():
            res.status = "aborted"
        elif self.fail_at is not None and self._touches == self.fail_at:
            res.status, res.message = "no_contact", "simulated failure"
        else:
            res.status = "ok"
            res.end_z = res.contact_z - (point.value if point.mode.value == "depth" else 0.3)
            res.peak_g = res.final_g = point.value if point.mode.value == "force" else 80.0
        return res

    def stop(self):
        self.calls.append(("stop",))
        self._stopped.set()

    def close(self):
        self.calls.append(("close",))
