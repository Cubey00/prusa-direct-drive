"""Ordered execution of a program. run_program is plain Python; ExecutorWorker wraps it for a QThread."""
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import config, heightmap
from . import program as prog_mod
from .imagesave import ImageSaver


@dataclass
class RunSummary:
    outcome: str = "error"          # completed | aborted | failed | error
    done: int = 0
    total: int = 0
    message: str = ""
    log_path: str = ""              # the run folder
    results: list = field(default_factory=list)


def _now():
    return datetime.now().isoformat(timespec="milliseconds")


class _JsonFile:
    """JSON document rewritten atomically after every change, so a crash still leaves the data on disk."""

    def __init__(self, path, data):
        self.path, self.data = path, data
        self.flush()

    def flush(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.path)


class RunLog(_JsonFile):
    def __init__(self, program, run_dir, run_name=""):
        super().__init__(Path(run_dir) / "run_log.json",
                         {"run_name": run_name, "started": _now(), "program": prog_mod.to_dict(program),
                          "points": [], "outcome": None})

    def add(self, record):
        self.data["points"].append(record)
        self.flush()

    def finish(self, summary):
        self.data.update(outcome=summary.outcome, message=summary.message, finished=_now())
        self.flush()


class ImageIndex(_JsonFile):
    """images.json: every image with the measured force and the gantry XY in PRINT (face) coordinates."""

    def __init__(self, run_dir, run_name=""):
        super().__init__(Path(run_dir) / "images.json", {
            "run_name": run_name, "started": _now(),
            "exposure_us": config.CAMERA_EXPOSURE_US,
            "coordinates": "x, y = gantry position in print (face) coordinates, mm: origin back-left "
                           "corner of the touched face, x right, y toward the front. machine = printer X, Y.",
            "naming": "images/point_<point>_<increment>.tiff; increment k = k-th image target of the point",
            "images": []})

    def add(self, records):
        self.data["images"].extend(records)
        self.flush()


def image_name(point_no, k, n):
    return f"point_{point_no:02d}_{k:0{max(2, len(str(n)))}d}.tiff"


def run_program(program, transform, hw, on_event=lambda *a: None, should_abort=lambda: False,
                run_dir=None, mesh=None, run_name=""):
    """on_event(kind, *args): ("status", msg), ("point_started", i), ("point_finished", i, result).
    Any touch status other than "ok" ends the run. Everything goes into run_dir: run_log.json,
    images.json, images/*.tiff and (with a mesh) heightmap.csv/.png."""
    pts = program.points
    summary = RunSummary(total=len(pts))
    if run_dir is None:
        run_dir = config.RUNS_DIR / f"run_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir = Path(run_dir)
    (run_dir / "images").mkdir(parents=True, exist_ok=True)
    log = RunLog(program, run_dir, run_name)
    index = ImageIndex(run_dir, run_name)
    saver = ImageSaver()
    summary.log_path = str(run_dir)
    (x0, x1), (y0, y1) = config.MACHINE_XY_BOUNDS
    try:
        if mesh is not None:
            on_event("status", "Computing heightmap...")
            try:
                xs, ys, H = heightmap.compute(mesh)
                heightmap.save_csv(run_dir / "heightmap.csv", xs, ys, H)
                heightmap.save_png(run_dir / "heightmap.png", xs, ys, H, [(p.x, p.y) for p in pts])
                log.data["heightmap"] = {"csv": "heightmap.csv", "png": "heightmap.png",
                                         "cell_mm": float(xs[1] - xs[0]) if len(xs) > 1 else None}
            except Exception as e:
                log.data["heightmap"] = {"error": str(e)}
                on_event("status", f"Heightmap failed: {e}")
            log.flush()
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
            # where the head really is, in print coordinates (not the commanded point)
            pos = hw.position()
            fx, fy = transform.machine_to_face(pos[0], pos[1])
            files = {}
            n_img = p.n_images()

            def capture(k, target, i=i, files=files, n_img=n_img):
                name = image_name(i + 1, k, n_img)
                saver.put(hw.snap(), run_dir / "images" / name)
                files[k] = name

            on_event("status", f"Point {i + 1}/{len(pts)}: touching ({p.describe()}, {p.speed:g} mm/s)")
            res = hw.touch(p, program.settings, capture if n_img else None)
            records = [{"file": f"images/{files[im['increment']]}", "point": i + 1,
                        "increment": im["increment"], "mode": p.mode.value, "target": im["target"],
                        "unit": p.mode.unit, "force_g": im["force_g"],
                        "force_before_g": im["force_before_g"], "force_after_g": im["force_after_g"],
                        "x": round(fx, 3), "y": round(fy, 3),
                        "machine": [round(pos[0], 3), round(pos[1], 3)],
                        "z_machine": im["z"], "time": im["t"]}
                       for im in res.images if im["increment"] in files]
            if records:
                index.add(records)
            result = res.to_dict()
            result["images"] = [r["file"] for r in records]
            log.add({"index": i + 1, "face": [p.x, p.y], "machine": [X, Y], "measured_face": [fx, fy],
                     "mode": p.mode.value, "value": p.value, "speed": p.speed, "image_step": p.image_step,
                     "started": t_start, "finished": _now(), **result})
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
        saver.close()
        if saver.errors:
            summary.message += f" (image save errors: {'; '.join(saver.errors)})"
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

        def __init__(self, program, transform, hw, run_dir, mesh=None, run_name=""):
            super().__init__()
            self.program, self.transform, self.hw = program, transform, hw
            self.run_dir, self.mesh, self.run_name = Path(run_dir), mesh, run_name
            self._abort = False

        @Slot()
        def run(self):
            def on_event(kind, *args):
                {"status": self.status, "point_started": self.pointStarted,
                 "point_finished": self.pointFinished}[kind].emit(*args)
            summary = run_program(self.program, self.transform, self.hw, on_event, lambda: self._abort,
                                  run_dir=self.run_dir, mesh=self.mesh, run_name=self.run_name)
            self.finished.emit(summary)

        def abort(self):
            """Called from the UI thread."""
            self._abort = True
            self.hw.stop()
except ImportError:          # executor logic stays usable without Qt
    pass
