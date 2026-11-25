"""Sequence validation, positioning and CSV logging for manual dial readings."""

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from uuid import uuid4

from . import config

MOVE = "Move"
HOME = "Home X/Y"
MEASURE = "Measure"
SEQUENCE_FORMAT = "prusa-repeatability-sequence/1"


@dataclass(frozen=True)
class Step:
    action: str
    position: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class Plan:
    name: str
    measurement: tuple[float, float, float]
    clearance_z: float
    cycles: int
    dials: tuple[str, ...]
    steps: tuple[Step, ...]
    home_first: bool
    simulate: bool

    def validate(self, printer):
        if not 1 <= len(self.dials) <= 3 or not all(name.strip() for name in self.dials):
            raise ValueError("Enter a name for each of the one to three dials.")
        if len(set(self.dials)) != len(self.dials):
            raise ValueError("Give each dial a different name.")
        if self.cycles < 1 or not any(step.action == MEASURE for step in self.steps):
            raise ValueError("Use at least one cycle and add a Measure step.")
        if not 0 <= self.clearance_z <= printer.z_max:
            raise ValueError("Clearance Z is outside the printer travel limits.")
        bounds = (*printer.travel, (0, printer.z_max))

        def check(position, label):
            if position is None or len(position) != 3 or not all(
                lo <= value <= hi for value, (lo, hi) in zip(position, bounds)
            ):
                raise ValueError(f"{label} is outside the printer travel limits.")
            if position[2] > self.clearance_z:
                raise ValueError(f"Clearance Z must be at least as high as {label.lower()} Z.")

        check(self.measurement, "The measurement location")
        for number, step in enumerate(self.steps, 1):
            if step.action == MOVE:
                check(step.position, f"Move in row {number}")
            elif step.action not in (HOME, MEASURE):
                raise ValueError(f"Unknown action in row {number}.")

    def target(self, step):
        return self.measurement if step.action == MEASURE else step.position

    def sequence(self):
        if self.home_first:
            yield 0, 0, Step(HOME)
        for cycle in range(1, self.cycles + 1):
            for number, step in enumerate(self.steps, 1):
                yield cycle, number, step


def dial_value(text):
    value = float(text.strip().replace(",", "."))
    if not math.isfinite(value):
        raise ValueError("Reading must be finite.")
    return value


def load_sequence(path):
    """Read an editable setup. Run validation happens separately when starting it."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != SEQUENCE_FORMAT:
        raise ValueError("Choose a repeatability sequence saved by this tab.")

    def text_list(values, minimum, maximum):
        return (isinstance(values, list) and minimum <= len(values) <= maximum
                and all(isinstance(value, str) for value in values))

    cycles = data.get("cycles")
    if (not isinstance(data.get("name"), str) or not isinstance(data.get("clearance_z"), str)
            or not text_list(data.get("measurement"), 3, 3) or not text_list(data.get("dials"), 1, 3)
            or type(cycles) is not int or not 1 <= cycles <= 999
            or type(data.get("home_first")) is not bool or not isinstance(data.get("steps"), list)):
        raise ValueError("The repeatability sequence has invalid setup fields.")
    for number, row in enumerate(data["steps"], 1):
        if (not text_list(row, 4, 4) or row[0] not in (MOVE, HOME, MEASURE)
                or (row[0] != MOVE and any(row[1:]))):
            raise ValueError(f"Invalid action or coordinates in step {number}.")
    return data


class RunLog:
    """Write the full plan before moving, then flush each event as it happens."""

    def __init__(self, stream, plan):
        self.stream = stream
        self.plan = plan
        self.run_id = uuid4().hex
        self.writer = csv.writer(stream)
        dial_fields = [field for n in range(1, 4) for field in (f"dial_{n}_name", f"dial_{n}_mm")]
        self.writer.writerow([
            "timestamp", "run_id", "test_name", "mode", "cycles", "cycle", "step", "action",
            "event", "target_x_mm", "target_y_mm", "target_z_mm", "clearance_z_mm",
            *dial_fields, "details",
        ])
        if plan.home_first:
            self.record("planned", cycle=0, number=0, step=Step(HOME), details="Before first cycle")
        for number, step in enumerate(plan.steps, 1):
            self.record("planned", number=number, step=step)

    def record(self, event, *, cycle="", number="", step=None, readings=(), details=""):
        plan = self.plan
        target = plan.target(step) if step is not None else None
        if step is not None and step.action == HOME:
            target = ("", "", plan.clearance_z)
        dial_columns = []
        for i in range(3):
            dial_columns.extend((
                plan.dials[i] if i < len(plan.dials) else "",
                readings[i] if i < len(readings) else "",
            ))
        self.writer.writerow([
            datetime.now().astimezone().isoformat(timespec="milliseconds"), self.run_id, plan.name,
            "simulation" if plan.simulate else "printer", plan.cycles, cycle, number,
            step.action if step is not None else "", event, *(target or ("", "", "")),
            plan.clearance_z, *dial_columns, details,
        ])
        self.stream.flush()


class PrinterMotion:
    """Use existing gantry commands with cancellation between every leg of a move."""

    def __init__(self, cancel):
        self.cancel = cancel
        self.opened = False
        self.xy = None
        self._stop_lock = Lock()

    def open(self):
        import gantry_controls as gc

        gc.connect()
        self.opened = True
        gc.send_gcode(f"M84 S{gc.XY_HOLD_S}", cancel=self.cancel)
        self.xy = gc.read_position(cancel=self.cancel)[:2]

    def execute(self, step, target, clearance_z):
        import gantry_controls as gc

        if step.action == HOME:
            gc.move_absolute(z=clearance_z, cancel=self.cancel)
            gc.home_xy(cancel=self.cancel)
            self.xy = None
            return
        x, y, z = target
        if self.xy == (x, y):
            # Preserve the programmed approach direction for Z-only returns.
            gc.move_absolute(z=z, Zfeed=config.APPROACH_SPEED * 60, cancel=self.cancel)
            return
        gc.move_absolute(z=clearance_z, cancel=self.cancel)
        gc.move_absolute(x=x, y=y, cancel=self.cancel)
        if z != clearance_z:
            gc.move_absolute(z=z, Zfeed=config.APPROACH_SPEED * 60, cancel=self.cancel)
        self.xy = x, y

    def interrupt(self):
        import gantry_controls as gc

        with self._stop_lock:
            if self.opened:
                gc._quickstop()

    def close(self, *, abort):
        import gantry_controls as gc

        with self._stop_lock:
            if not self.opened:
                return
            self.opened = False
        try:
            if abort:
                gc.motionstop()
            gc.send_gcode("M84 S120", timeout=2)
        finally:
            gc.disconnect()
