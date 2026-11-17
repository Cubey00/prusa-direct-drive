"""Crash-readable optical runs with immutable input snapshots and lossless frames."""
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
import tifffile

from . import __version__
from .config import ROOT


def atomic_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, default=str, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def source_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def source_dirty():
    try:
        return bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True,
                                            stderr=subprocess.DEVNULL).strip())
    except (OSError, subprocess.CalledProcessError):
        return None


class RunWriter:
    def __init__(self, root, recipe, fixture, settings, chart, *, simulated=False, dry_run=False,
                 operator_checks=None):
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:10]
        self.path = Path(root) / self.run_id
        (self.path / "images").mkdir(parents=True)
        atomic_json(self.path / "config.json", recipe.to_dict())
        atomic_json(self.path / "chart_layout.json", chart)
        self.manifest = dict(schema_version=1, run_id=self.run_id, analysis_version=__version__,
                             source_commit=source_commit(), source_dirty=source_dirty(), settings=asdict(settings),
                             wd_calibration=asdict(fixture), simulated=simulated, dry_run=dry_run,
                             operator_checks=operator_checks or {},
                             started=datetime.now(timezone.utc).isoformat(), state="PREFLIGHT",
                             frames=[], message="", expected_frames=(0 if dry_run else len(recipe.schedule(fixture))
                                 * recipe.frames_per_position), measurement_validated=False)
        self.update("PREFLIGHT")

    def update(self, state, message=""):
        self.manifest.update(state=state, message=message, updated=datetime.now(timezone.utc).isoformat())
        atomic_json(self.path / "manifest.json", self.manifest)

    def add(self, capture, **position):
        capture.validate()
        image_id = len(self.manifest["frames"]) + 1
        relative = f"images/frame_{image_id:06d}.tiff"
        target = self.path / relative
        temp = target.with_suffix(".tmp.tiff")
        tifffile.imwrite(temp, capture.pixels, photometric="rgb" if capture.pixels.ndim == 3 else "minisblack")
        temp.replace(target)
        record = dict(run_id=self.run_id, image_id=image_id, image=relative,
                      analysis_version=__version__, **capture.metadata, **position)
        record["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        self.manifest["frames"].append(record)
        self.update("SCANNING")
        # The manifest is authoritative if a crash occurs between it and CSV output.
        with (self.path / "frames.csv").open("a", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(record))
            if image_id == 1:
                writer.writeheader()
            writer.writerow(record)
            stream.flush()
            os.fsync(stream.fileno())
        return record
