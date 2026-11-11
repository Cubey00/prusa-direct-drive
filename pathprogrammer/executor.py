"""Ordered execution of a program. run_program is plain Python; ExecutorWorker wraps it for a QThread."""
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config
from . import program as prog_mod


@dataclass
class RunSummary:
    outcome: str = "error"          # completed | aborted | failed | error
    done: int = 0
    total: int = 0
    message: str = ""
    log_path: str = ""
    results: list = field(default_factory=list)


def _now():
    return datetime.now().isoformat(timespec="milliseconds")


class RunLog:
    """JSON log rewritten after every point, so a crash still leaves the data on disk."""

    def __init__(self, program, log_dir=config.RUN_LOG_DIR):
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / f"run_{datetime.now():%Y%m%d_%H%M%S}.json"
        self.data = {"started": _now(), "program": prog_mod.to_dict(program), "points": [], "outcome": None}
        self.flush()

    def add(self, record):
        self.data["points"].append(record)
        self.flush()

    def finish(self, summary):
        self.data.update(outcome=summary.outcome, message=summary.message, finished=_now())
        self.flush()

    def flush(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def run_program(program, transform, hw, on_event=lambda *a: None, should_abort=lambda: False,
                log_dir=config.RUN_LOG_DIR):
    """on_event(kind, *args): ("status", msg), ("point_started", i), ("point_finished", i, result).
    Any touch status other than "ok" ends the run."""
    pts = program.points
    summary = RunSummary(total=len(pts))
    log = RunLog(program, log_dir)
    summary.log_path = str(log.path)
    (x0, x1), (y0, y1) = config.MACHINE_XY_BOUNDS
    try:
        on_event("status", "Connecting...")
        hw.open()
        travel_z = hw.position()[2]
        on_event("status", f"Travel height Z{travel_z:.2f}")
        for i, p in enumerate(pts):
            if should_abort():
                summary.outcome, summary.message = "aborted", "Stopped by user"
                break
            X, Y = transform.face_to_machine(p.x, p.y)
            if not (x0 <= X <= x1 and y0 <= Y <= y1):
                summary.outcome, summary.message = "failed", f"Point {i + 1} maps outside the machine area"
                break
            on_event("point_started", i)
            on_event("status", f"Point {i + 1}/{len(pts)}: moving to X{X:.2f} Y{Y:.2f}")
            t_start = _now()
            hw.move_xy(X, Y, program.settings.travel_speed)
            if should_abort():
                summary.outcome, summary.message = "aborted", "Stopped by user"
                break
            on_event("status", f"Point {i + 1}/{len(pts)}: touching ({p.describe()}, {p.speed:g} mm/s)")
            res = hw.touch(p, program.settings)
            log.add({"index": i + 1, "face": [p.x, p.y], "machine": [X, Y], "mode": p.mode.value,
                     "value": p.value, "speed": p.speed, "started": t_start, "finished": _now(),
                     **res.to_dict()})
            summary.results.append(res)
            on_event("point_finished", i, res)
            if res.status != "ok":
                summary.outcome = "aborted" if res.status == "aborted" else "failed"
                summary.message = f"Point {i + 1}: {res.status}" + (f" ({res.message})" if res.message else "")
                break
            summary.done += 1
        else:
            summary.outcome, summary.message = "completed", f"All {len(pts)} points done"
    except Exception as e:
        summary.outcome = "aborted" if should_abort() else "error"
        summary.message = str(e)
    finally:
        try:
            hw.close()
        except Exception as e:
            summary.message += f" (close failed: {e})"
        log.finish(summary)
    on_event("status", summary.message)
    return summary


try:
    from PySide6.QtCore import QObject, Signal, Slot

    class ExecutorWorker(QObject):
        pointStarted = Signal(int)
        pointFinished = Signal(int, object)
        status = Signal(str)
        finished = Signal(object)

        def __init__(self, program, transform, hw, log_dir=config.RUN_LOG_DIR):
            super().__init__()
            self.program, self.transform, self.hw = program, transform, hw
            self.log_dir = Path(log_dir)
            self._abort = False

        @Slot()
        def run(self):
            def on_event(kind, *args):
                {"status": self.status, "point_started": self.pointStarted,
                 "point_finished": self.pointFinished}[kind].emit(*args)
            summary = run_program(self.program, self.transform, self.hw, on_event, lambda: self._abort,
                                 log_dir=self.log_dir)
            self.finished.emit(summary)

        def abort(self):
            """Called from the UI thread."""
            self._abort = True
            self.hw.stop()
except ImportError:          # executor logic stays usable without Qt
    pass
