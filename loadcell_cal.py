"""Automatic load cell calibration against a kitchen/lab scale placed under the sensor.

For every target load (e.g. 100, 200 ... 1000 g):
  1. bed down until the sensor is clear of the scale, settle, TARE (a fresh zero right before
     every touch, so load cell drift between points doesn't count);
  2. bed up until the sensor touches the scale (the same contact detection as Measure);
  3. press on until the load cell says the target (M410: the bed then stands still);
  4. while the bed stands still, ask what the scale reads; meanwhile the tared load cell reading
     is averaged, so both are taken at the same time, under the same load.
Then a straight line grams = a * reading + b through all points: `a` is the load cell scale that
`calibration.json` holds (`b` is what is left after the tare and is not used). A graph (load cell
reading against the weight on the scale, the fit, the residuals), the points (CSV) and the fit
(JSON) go into logs/loadcell_calibration_<date_time>/.

The hardware is reached through a small adapter (see `GaugeHardware` in GUI.py), so the whole
procedure also runs against a simulated scale in the tests.
"""
import csv
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event

import numpy as np

MAX_G = 1000.0            # up to this load
N_POINTS = 10
LIFT_MM = 2.0             # above the last contact before the next tare
SEARCH_MM = 15.0          # how far the first approach may go to find the scale
PRESS_MAX_MM = 4.0        # never press further than this past the contact
SETTLE_S = 1.0            # bed still before the reading starts
MIN_SAMPLES = 20


class LoadCellCalError(Exception):
    pass


@dataclass
class Point:
    target_g: float           # what the load cell (old scale) was pressed to
    reading: float            # tared load cell reading (the unit calibration.json's scale turns into g)
    scale_g: float            # what the scale under the sensor read
    spread: float             # standard deviation of the reading while the question was open
    samples: int
    contact_z: float = None
    z: float = None


@dataclass
class Fit:
    scale: float              # g per reading unit: calibration.json "scale"
    offset_g: float           # always 0: the line goes through zero (the load cell is tared)
    rmse_g: float
    r2: float
    points: int               # points used (on the scale up to max_g)
    max_g: float = None       # the range fitted
    excluded: int = 0         # points above max_g, shown but not used
    worst_g: float = 0.0      # largest residual of the points used
    straight: bool = True     # False: the points bend away from a straight line (see the graph)


def targets(max_g=MAX_G, n=N_POINTS):
    """n loads, evenly spread up to max_g (the smallest one max_g / n)."""
    if not 0 < max_g <= 1000 or n < 2:
        raise LoadCellCalError("Use at most 1000 g and at least 2 points")
    return [round(max_g * (i + 1) / n, 1) for i in range(n)]


def magnitudes(points):
    """|reading|: pressing gives a negative reading on this printer (and grams = scale * reading
    then negative, the convention the rest of the software uses), so the fit uses the size of
    the reading and the scale stays positive whatever the sign."""
    x = np.array([p.reading for p in points], float)
    return x * (1.0 if x.mean() >= 0 else -1.0)


def scale_through_zero(x, y):
    """Least-squares slope of y = k * x (no offset)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = float(x @ x)
    if n <= 0:
        raise LoadCellCalError("The load cell gave no reading")
    return float(x @ y) / n


def fit(points, max_g=None):
    """|grams| = scale * |reading|: a straight line THROUGH ZERO (the load cell is tared right before
    every touch, so no load reads 0 g, and the software only uses the scale), fitted on the points
    whose scale value is up to max_g (the range that is measured; above ~1 kg the load cell bends
    away from a straight line). straight=False when a used point lies more than 25 g and 5 % off."""
    if len(points) < 2:
        raise LoadCellCalError("At least two points are needed for a fit")
    x = magnitudes(points)
    y = np.array([p.scale_g for p in points], float)
    use = np.ones(len(points), bool) if max_g is None else y <= max_g * 1.02
    if use.sum() < 2:
        raise LoadCellCalError(f"Fewer than two points up to {max_g:g} g")
    xu, yu = x[use], y[use]
    k = scale_through_zero(xu, yu)
    res = yu - k * xu
    ss = float(np.sum((yu - yu.mean()) ** 2)) or 1.0
    worst = float(np.abs(res).max())
    straight = bool(np.all(np.abs(res) <= np.maximum(25.0, 0.05 * yu)))
    return Fit(k, 0.0, float(np.sqrt(np.mean(res ** 2))), float(1 - np.sum(res ** 2) / ss), int(use.sum()),
               max_g, int((~use).sum()), worst, straight)


class Question:
    """A scale reading asked from the worker thread; the UI answers with answer()."""

    def __init__(self, index, target_g, load_cell_g):
        self.index, self.target_g, self.load_cell_g = index, target_g, load_cell_g
        self.value = None
        self.done = Event()

    def answer(self, value):
        self.value = value
        self.done.set()


def run(hw, ask, on_status=print, max_g=MAX_G, n=N_POINTS, cancel=None, press_speed=0.2):
    """The whole calibration. hw: tare(), z(), move_z(z), creep(max_mm, fast_mm) -> TouchResult,
    press(stop_g, max_mm, speed) -> TouchResult, reading() (tared, raw units), grams() (with the
    current scale). ask(Question): show the question; answer it later (None = stop).
    Returns the points; the bed ends where it started."""
    cancel = cancel or Event()
    z_start = hw.z()
    contact = None
    points = []
    old = hw.scale()                    # the scale the load cell's grams use now (maybe far off)
    try:
        for i, t in enumerate(targets(max_g, n)):
            if cancel.is_set():
                break
            free_z = z_start if contact is None else min(z_start, contact + LIFT_MM)
            on_status(f"Point {i + 1} of {n} ({t:g} g): sensor clear of the scale, taring...")
            hw.move_z(free_z)
            time.sleep(SETTLE_S / 2)
            if not hw.tare():
                raise LoadCellCalError("No fresh load cell data for the tare")
            on_status(f"Point {i + 1} of {n} ({t:g} g): touching the scale...")
            if contact is None:
                res = hw.creep(SEARCH_MM, 0.0)
            else:
                res = hw.creep(free_z - contact + 3.0, max(0.0, free_z - contact - 1.0))
            if res.status != "ok":
                raise LoadCellCalError(f"The sensor did not find the scale ({res.status}: {res.message}). "
                                       "Is the scale under the sensor, within 15 mm?")
            contact = res.end_z
            # the load cell's grams use the old scale; the scale values so far tell the real one, so
            # the press stops at the REAL target (the old scale once made "1000 g" 1.9 kg)
            est = scale_through_zero(magnitudes(points), [q.scale_g for q in points]) if points else old
            stop = t * old / est if est > 0 else t
            on_status(f"Point {i + 1} of {n}: pressing to {t:g} g...")
            res = hw.press(stop, PRESS_MAX_MM, press_speed)
            if res.status not in ("ok", "limit"):
                raise LoadCellCalError(f"Pressing stopped ({res.status}): {res.message}")
            time.sleep(SETTLE_S)
            q = Question(i, t, hw.grams() * (est / old if old else 1.0))
            ask(q)
            on_status(f"Point {i + 1} of {n}: holding still; type what the scale reads.")
            vals = []
            while not q.done.wait(0.05):
                if cancel.is_set():
                    break
                vals.append(hw.reading())
            if q.value is None or cancel.is_set():
                break
            vals = vals[-200:] if len(vals) > 200 else vals          # the last ~10 s
            if len(vals) < MIN_SAMPLES:
                vals += [hw.reading() for _ in range(MIN_SAMPLES - len(vals))]
            points.append(Point(t, float(np.mean(vals)), float(q.value), float(np.std(vals)), len(vals),
                                contact, getattr(res, "end_z", None)))
            on_status(f"Point {i + 1} of {n}: scale {q.value:g} g, load cell reading {np.mean(vals):.1f}")
    finally:
        try:
            hw.move_z(z_start)                                     # off the scale, back where it began
        except Exception:                                          # noqa: BLE001  report the first error
            pass
    return points


def plot(points, result, path, old_scale=None, title="Load cell calibration"):
    """The graph: load cell reading against the weight on the scale, the line through zero fitted on
    the points up to max_g (green), points above it grey (not used), the previous scale dashed, and
    the residuals of the points used."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x = magnitudes(points)
    y = np.array([p.scale_g for p in points])
    spread = np.array([p.spread for p in points])
    use = np.ones(len(points), bool) if result.max_g is None else y <= result.max_g * 1.02
    fig, (ax, axr) = plt.subplots(2, 1, figsize=(8, 7.5), gridspec_kw={"height_ratios": [3, 1.3]},
                                  constrained_layout=True)
    top = x[use].max() * 1.08
    xs = np.linspace(0, top, 100)
    ax.plot(xs, result.scale * xs, color="#2563eb", lw=2,
            label=f"fit through 0: grams = {result.scale:.4f} × reading")
    if old_scale:
        ax.plot(xs, old_scale * xs, color="#94a3b8", lw=1.5, ls="--",
                label=f"previous scale {old_scale:.4f}")
    ax.errorbar(x[use], y[use], xerr=spread[use], fmt="o", ms=7, color="#16a34a", ecolor="#16a34a",
                capsize=3, label=f"measured, used ({use.sum()})")
    if (~use).any():
        ax.plot(x[~use], y[~use], "o", ms=6, mfc="none", color="#9ca3af",
                label=f"measured above {result.max_g:g} g, not used ({(~use).sum()})")
        top = max(top, x.max() * 1.03)
    ax.plot([0], [0], "s", color="#2563eb", ms=6)
    ax.set_xlim(0, top)
    ax.set_ylim(0, max(y.max(), result.scale * top) * 1.05)
    ax.set_xlabel("Load cell reading (tared, raw units, size)")
    ax.set_ylabel("Weight on the scale (g)")
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left")
    note = ("" if result.straight else
            "\nNot straight: the points bend\naway from the line")
    ax.text(0.98, 0.04, f"{result.points} points up to {y[use].max():.0f} g\nRMSE {result.rmse_g:.1f} g, "
                        f"worst {result.worst_g:.1f} g\nR² {result.r2:.4f}{note}",
            transform=ax.transAxes, ha="right", va="bottom",
            bbox={"boxstyle": "round", "fc": "white" if result.straight else "#fff7ed", "ec": "#cbd5e1"})
    resid = y[use] - result.scale * x[use]
    axr.axhline(0, color="#2563eb", lw=1)
    axr.bar(y[use], resid, width=max(y[use].max(), 1) / 40, color="#f97316")
    axr.set_xlabel("Weight on the scale (g)")
    axr.set_ylabel("Residual (g)")
    axr.grid(True, alpha=0.3)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def save(points, result, folder, old_scale=None, name="calibration_plot.png"):
    """points.csv, fit.json and the graph in folder (made). Returns the plot path."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "points.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(points[0])))
        w.writeheader()
        for p in points:
            w.writerow(asdict(p))
    (folder / "fit.json").write_text(json.dumps({"time": datetime.now().isoformat(timespec="seconds"),
                                                 "fit": asdict(result), "previous_scale": old_scale},
                                                indent=2), encoding="utf-8")
    path = folder / name
    plot(points, result, path, old_scale)
    return path


def write_scale(result, file, folder=None):
    """The new scale into calibration.json (what strain_gauge.py reads at start)."""
    note = (f"grams = scale * tared reading (line through zero). Automatic calibration "
            f"{datetime.now():%Y-%m-%d %H:%M}, {result.points} points up to {result.max_g or 0:g} g, "
            f"RMSE {result.rmse_g:.1f} g"
            + (f", see {Path(folder).as_posix()}" if folder else ""))
    Path(file).write_text(json.dumps({"scale": round(result.scale, 6), "note": note}, indent=2) + "\n",
                          encoding="utf-8")


class GaugeHardware:
    """The real printer for run(): gantry_controls / touch / the StrainGauge of the calibrator."""

    def __init__(self, gauge, settings, cancel=None):
        self.gauge, self.fo, self.cancel = gauge, settings.forces, cancel

    def tare(self):
        return self.gauge.tare_fresh(300)

    def z(self):
        from gantry_controls import read_position
        return read_position()[2]

    def move_z(self, z):
        from gantry_controls import move_absolute
        move_absolute(z=z)

    def creep(self, max_mm, fast_mm):
        from touch import creep_to_contact
        fo = self.fo
        return creep_to_contact(self.gauge, contact_g=fo.contact_g, max_force_g=fo.max_force_g, max_travel=max_mm,
                                fast_mm=fast_mm, fast_speed=1.0, speed=0.2, cancel=self.cancel,
                                on_status=lambda m: None, rise_g=fo.contact_rise_g, rise_s=fo.contact_rise_s,
                                abs_g=fo.jog_stop_g, hold_s=fo.contact_hold_s, verify_s=fo.contact_verify_s)

    def press(self, stop_g, max_mm, speed):
        from touch import press_step
        return press_step(self.gauge, step_mm=max_mm, stop_g=stop_g, max_force_g=self.fo.max_force_g,
                          speed=speed, cancel=self.cancel)

    def scale(self):
        import strain_gauge
        return strain_gauge.SCALE

    def reading(self):
        s = self.gauge.recent(32)
        return (sum(s) / len(s) - self.gauge.offset) if s else 0.0

    def grams(self):
        return abs(self.gauge.force_g(32) or 0.0)
