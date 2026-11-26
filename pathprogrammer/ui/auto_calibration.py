"""Automatic calibration: find two cones with touches and photos, no jogging by hand.

Per cone (driven through the calibrator over IPC, photos from the Photo control camera):
  1. To the cone: bed to the travel height, then X/Y (homed before the first cone), then down to
     HOVER_MM above where the tip is expected (the saved cone position, else the sensor offset).
  2. Photo BEFORE, the sensor in the air.
  3. Bed up quite fast until the load cell feels the tip (creep with high speeds: Z accuracy matters
     little here), then PRESS_MM deeper so the tip leaves a clear imprint in the gel.
  4. Photo AFTER. The difference shows the imprint; its centre against the image centre gives the
     head correction (autocal.contact_centre / correction_mm).
  5. Lift to RETOUCH_HOVER_MM, move by the correction, touch again, until the cone is in the centre
     within TOL_MM (at most MAX_TOUCHES touches). A centre the head can't reach (the spacer blocks)
     is computed from the last photo instead.
The camera scale (pixels per mm, and how the image is turned) is measured on the first cone with
two extra touches SCALE_STEP_MM apart in X and in Y.
With both cones the plate is known (shift and rotation); the head positions on them and the
sensor offset are saved in machine_settings.toml as by the manual calibration, so Measure uses
them. Every photo, difference image and a log go into <photo folder>/autocal_<date_time>/.
"""
import json
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

import machine_settings
from .. import autocal, calibration

HOVER_MM = 5.0            # above the expected tip before the first touch of a cone
RETOUCH_HOVER_MM = 1.5    # above the contact height found, for the next touches of that cone
SAFE_MM = 1.0             # the last part of the approach, slow with the sensitive detection
FAST_SPEED = 3.0          # mm/s over the known-free part
SLOW_SPEED = 0.5          # mm/s near the tip: quick, Z accuracy is not important here
PRESS_MM = 0.3            # deeper after the contact, for a clear imprint
PRESS_G = 150.0           # ...but stop at this force
TOL_MM = 0.1              # centred when the correction is smaller than this
MAX_TOUCHES = 8           # touches per cone before giving up (scale touches not counted)
DAMP = 0.85               # move this part of the computed correction: no overshoot, no swinging
SCALE_STEP_MM = 1.0       # head shift for measuring the camera scale
SETTLE_MS = 400           # bed standing still before a photo
ANSWER_MS = 120000        # no answer from the calibrator within this: stop
LABEL = "auto "           # goto labels: "auto F2"


class AutoCalibration(QObject):
    statusChanged = Signal(str, str)      # text, level (info / busy / ok / error)
    finished = Signal(bool)

    def __init__(self, main, parent=None):
        super().__init__(parent or main)
        self.main = main
        self.active = False
        self._token = 0

    # ---------------- start / stop ----------------
    def start(self, cones):
        """Calibrate on the two cones (names like "F1", "F2")."""
        if self.active:
            return
        m = self.main
        cones = [c for c in cones if c in calibration.POINTS]
        if len(set(cones)) != 2:
            return self._say("Choose two different cones.", "error")
        if not m.photo.feed.connected:
            return self._say("No camera connected: the automatic calibration works with photos.", "error")
        manifest = calibration.manifest_for(m.model_path) if m.model_path else None
        if not manifest:
            return self._say("Open a plate first (Print plates): the cones are on the plate.", "error")
        self.settings = machine_settings.load()
        self.fids = {f["name"]: f for f in manifest["fiducials"]}
        tallest = max([f["z"] for f in manifest["fiducials"]] + [p["size"][2] for p in manifest["parts"]])
        self.travel_z = tallest + self.settings.sensor.hover_clearance - self.settings.sensor.offset[2]
        # a cone with a saved position first: the other one is then predicted from it
        self._tries = {}
        self.queue = sorted(cones, key=lambda c: self.settings.calibration.cone_position(c) is None)
        self.results = {}                 # cone -> dict(x, y, z, touches, reached)
        self.j = None                     # pixels per mm of head movement (2x2)
        self.disc = None                  # the sensor window in the photos (its centre = the target)
        self.centre = None
        self.polarity = None              # does an imprint brighten (+1) or darken (-1) the image
        start = datetime.now()
        self.folder = Path(m.photo.folder()) / f"autocal_{start:%Y-%m-%d_%H-%M-%S}"
        self.log = {"start": start.isoformat(timespec="seconds"), "cones": cones, "touches": []}
        self.first_goto = True
        self.phase = "start"
        self.wait_hello = False
        self.active = True
        if m.server.is_running():
            self._next_cone()
        else:
            self.wait_hello = True
            m.open_calibrator()
            self._say("Automatic calibration: starting the printer connection...", "busy")

    def abort(self, text="Stopped."):
        if not self.active:
            return
        self._end(False, f"Automatic calibration {text[0].lower() + text[1:]}", "error")

    def _end(self, ok, text, level):
        self.active = False
        self._token += 1
        self._write_log(text)
        self._say(text, level)
        self.finished.emit(ok)

    def _say(self, text, level="info"):
        self.statusChanged.emit(text, level)

    def _arm(self, ms=ANSWER_MS):
        self._token += 1
        token = self._token
        QTimer.singleShot(ms, self, lambda: self._timed_out(token))

    def _timed_out(self, token):
        if self.active and token == self._token:
            self._end(False, "Automatic calibration stopped: no answer from the printer (is it on, is "
                             "the USB cable in?).", "error")

    # ---------------- per cone ----------------
    def _expected(self, name):
        """(x, y, contact z) where the sensor should meet this cone's tip: the saved position, else
        from a cone measured in this run plus the distance between the two on the plate, else from
        the sensor offset (a guess until it has been measured)."""
        pos = self.settings.calibration.cone_position(name)
        if pos:
            return pos
        known = {p: self.settings.calibration.cone_position(p) for p in calibration.POINTS}
        known.update({p: (r["x"], r["y"], r["z"]) for p, r in self.results.items()})   # this run first
        pred, _ = calibration.predict_cone(name, self.fids, known,
                                           prefer=tuple(self.results) + ("F2", "F1", "F3"))
        if pred:
            return pred
        f = self.fids[name]
        ox, oy, oz = self.settings.sensor.offset
        return f["x"] - ox, f["y"] - oy, f["z"] - oz

    def _reachable(self, x, y):
        (x0, x1), (y0, y1) = self.settings.printer.travel
        return x0 <= x <= x1 and y0 <= y <= y1

    def _clamp(self, x, y):
        (x0, x1), (y0, y1) = self.settings.printer.travel
        return min(max(x, x0), x1), min(max(y, y0), y1)

    def _next_cone(self):
        if not self.queue:
            return self._finish()
        self.cone = self.queue.pop(0)
        x, y, z = self._expected(self.cone)
        self.target = self._clamp(x, y)
        self.contact_z = z
        self.touches = 0
        self.scale = 0 if self.j is None else None   # 0, 1, 2: measuring the camera scale
        self.c0 = self.origin = None
        self.prev = None                  # (head xy, imprint px) of the last touch on this cone
        self._goto(self.target, travel=True, hover=HOVER_MM)

    def _goto(self, xy, travel, hover):
        m = self.main
        self.hover_z = self.contact_z + hover
        self.phase = "goto"
        z = max(self.travel_z, self.hover_z) if travel else self.hover_z
        if not m.server.goto(xy[0], xy[1], z, LABEL + self.cone, home_xy=self.first_goto, z_final=self.hover_z):
            return self._end(False, "Automatic calibration stopped: the printer connection does not respond.",
                             "error")
        self.at = xy
        self._arm()
        what = "homing X/Y, then " if self.first_goto else ""
        self._say(f"Automatic calibration, cone {self.cone[1]}: {what}to X{xy[0]:.2f} Y{xy[1]:.2f}...", "busy")

    def on_message(self, msg):
        if not self.active:
            return
        kind = msg.get("type")
        if kind == "hello" and getattr(self, "wait_hello", False):
            self.wait_hello = False
            return self._next_cone()
        if kind == "bye":
            return self._end(False, "Automatic calibration stopped: the printer connection closed.", "error")
        if kind == "status" and msg.get("ok") and self.phase in ("goto", "touch", "press"):
            return self._say(str(msg.get("message", "")), "busy")
        if kind == "goto_done" and msg.get("label") == LABEL + getattr(self, "cone", "") and self.phase == "goto":
            if not msg.get("ok"):
                return self._end(False, f"Automatic calibration stopped: {msg.get('message', '')}", "error")
            self.first_goto = False
            self.main._needs_home = False
            return self._photo("before")
        if kind == "touch_done" and self.phase == "touch":
            if not msg.get("ok"):
                if msg.get("status") == "no_contact":
                    return self._end(False, f"Automatic calibration stopped: no cone under the sensor at "
                                            f"X{self.at[0]:.2f} Y{self.at[1]:.2f} (is the plate on the bed, "
                                            "and the calibration roughly right?).", "error")
                return self._end(False, f"Automatic calibration stopped: {msg.get('message', '')}", "error")
            self.contact_z = float(msg.get("z") or self.contact_z)
            self.phase = "press"
            if not self.main.server.step(PRESS_MM, PRESS_G):
                return self._end(False, "Automatic calibration stopped: the printer connection does not respond.",
                                 "error")
            return self._arm()
        if kind == "touch_done" and self.phase == "press":
            if msg.get("status") not in ("ok", "limit"):
                return self._end(False, f"Automatic calibration stopped: {msg.get('message', '')}", "error")
            return self._photo("after")
        if kind == "status" and not msg.get("ok") and self.phase in ("goto", "touch", "press"):
            return self._end(False, f"Automatic calibration stopped: {msg.get('message', '')}", "error")

    # ---------------- photos ----------------
    def _photo(self, which):
        self.phase = "photo_" + which
        self._token += 1
        QTimer.singleShot(SETTLE_MS, self, lambda: self._grab(which))

    def _grab(self, which):
        if not self.active or self.phase != "photo_" + which:
            return
        if not self.main.photo.grab(("autocal", which)):
            return self._end(False, "Automatic calibration stopped: the camera is gone.", "error")
        self._arm(20000)

    def on_frame(self, tag, frame):
        if not self.active or not (isinstance(tag, tuple) and tag[0] == "autocal"):
            return
        which = tag[1]
        if self.phase != "photo_" + which:
            return
        self._token += 1
        if which == "before":
            self.before = frame
            if self.centre is None:
                # the target: [camera] centre_px if set, else the centre of the round sensor window
                # found in this photo, else (window not found) the centre of the frame
                self.disc = autocal.sensor_disc(frame)
                cp = self.settings.camera.centre_px
                if cp and (cp[0] or cp[1]):
                    self.centre = (float(cp[0]), float(cp[1]))
                elif self.disc is not None:
                    self.centre = (self.disc.x, self.disc.y)
                else:
                    self.centre = autocal.image_centre(np.asarray(frame).shape)
                self.log["sensor_disc_px"] = (None if self.disc is None else
                                              [round(self.disc.x, 1), round(self.disc.y, 1), round(self.disc.r, 1)])
                self.log["target_px"] = [round(self.centre[0], 1), round(self.centre[1], 1)]
                try:                                 # a record of what the camera saw first
                    self.folder.mkdir(parents=True, exist_ok=True)
                    autocal.gray_png(frame, str(self.folder / "00_first_before.png"))
                except Exception:                    # noqa: BLE001
                    pass
            self.phase = "touch"
            fast = max(0.0, (self.hover_z - self.contact_z) - SAFE_MM)
            if not self.main.server.creep(fast, (self.hover_z - self.contact_z) + 5.0, FAST_SPEED, SLOW_SPEED):
                return self._end(False, "Automatic calibration stopped: the printer connection does not respond.",
                                 "error")
            self._arm()
            return self._say(f"Automatic calibration, cone {self.cone[1]}: touching...", "busy")
        self._analyse(frame)

    # ---------------- the imprint ----------------
    def _analyse(self, after):
        k = len(self.log["touches"]) + 1
        try:
            c = autocal.contact_centre(self.before, after, disc=self.disc, polarity=self.polarity)
        except autocal.AutoCalError as e:
            self._save_images(k, after, None)
            return self._end(False, f"Automatic calibration stopped at cone {self.cone[1]}: {e}", "error")
        if self.polarity is None:
            self.polarity = c.polarity            # learned on a clean gel: the first touch
            self.log["imprint_polarity"] = c.polarity
        centre = self.centre
        self._save_images(k, after, c, centre)
        entry = {"cone": self.cone, "head_x": self.at[0], "head_y": self.at[1], "contact_z": self.contact_z,
                 "imprint_px": [round(c.x, 1), round(c.y, 1)], "contrast": round(c.strength, 1)}
        self.log["touches"].append(entry)

        if self.scale is not None:                       # measuring the camera scale on this cone
            return self._scale_step(c, centre)
        self.touches += 1
        if self.prev is not None:                 # learn from this move: the scale gets better each touch
            (hx, hy), (px, py) = self.prev
            self.j = autocal.broyden(self.j, (self.at[0] - hx, self.at[1] - hy), (c.x - px, c.y - py))
        self.prev = (self.at, (c.x, c.y))
        dx, dy = autocal.correction_mm(self.j, (c.x, c.y), centre)
        entry["correction_mm"] = [round(dx, 4), round(dy, 4)]
        if np.hypot(dx, dy) < TOL_MM:
            return self._cone_done(self.at[0], self.at[1], reached=True)
        if not self._reachable(self.at[0] + dx, self.at[1] + dy) and self.touches >= 3:
            # the head can't go there (spacer blocks): the photo gives the centre all the same
            return self._cone_done(self.at[0] + dx, self.at[1] + dy, reached=False)
        nx, ny = self._clamp(self.at[0] + DAMP * dx, self.at[1] + DAMP * dy)
        if self.touches >= MAX_TOUCHES:
            return self._end(False, f"Automatic calibration stopped: cone {self.cone[1]} not centred after "
                                    f"{MAX_TOUCHES} touches (still {np.hypot(dx, dy):.2f} mm off).", "error")
        self._say(f"Automatic calibration, cone {self.cone[1]}: {np.hypot(dx, dy):.2f} mm off, correcting...",
                  "busy")
        self._goto((nx, ny), travel=False, hover=RETOUCH_HOVER_MM)

    def _scale_step(self, c, centre):
        """Touches 1-3 of the first cone: at the start, SCALE_STEP_MM further in X, then in Y."""
        (x0, x1), (y0, y1) = self.settings.printer.travel
        if self.scale == 0:
            self.c0, self.origin = (c.x, c.y), self.at
            self.sx = SCALE_STEP_MM if self.at[0] + SCALE_STEP_MM <= x1 else -SCALE_STEP_MM
            self.sy = SCALE_STEP_MM if self.at[1] + SCALE_STEP_MM <= y1 else -SCALE_STEP_MM
            self.scale = 1
            self._say("Automatic calibration: measuring the camera scale (touch 1 mm further in X)...", "busy")
            return self._goto((self.origin[0] + self.sx, self.origin[1]), travel=False, hover=RETOUCH_HOVER_MM)
        if self.scale == 1:
            self.cx = (c.x, c.y)
            self.scale = 2
            self._say("Automatic calibration: measuring the camera scale (touch 1 mm further in Y)...", "busy")
            return self._goto((self.origin[0], self.origin[1] + self.sy), travel=False, hover=RETOUCH_HOVER_MM)
        try:
            self.j = autocal.jacobian(self.c0, self.cx, (c.x, c.y), self.sx, self.sy)
        except autocal.AutoCalError as e:
            return self._end(False, f"Automatic calibration stopped: {e}", "error")
        self.scale = None
        self.log["camera_px_per_mm_first"] = self.j.round(3).tolist()
        # correct from the last touch (the gel there is the freshest), learning from it next time
        self.touches = 1
        self.prev = (self.at, (c.x, c.y))
        dx, dy = autocal.correction_mm(self.j, (c.x, c.y), centre)
        if np.hypot(dx, dy) < TOL_MM:
            return self._cone_done(*self.at, reached=True)
        nx, ny = self._clamp(self.at[0] + DAMP * dx, self.at[1] + DAMP * dy)
        self._goto((nx, ny), travel=False, hover=RETOUCH_HOVER_MM)

    def _cone_done(self, x, y, reached):
        self.results[self.cone] = {"x": round(x, 3), "y": round(y, 3), "z": round(self.contact_z, 3),
                                   "touches": self.touches, "reached": reached}
        how = "centred" if reached else "centre computed from the photo (out of the head's reach)"
        self._say(f"Automatic calibration: cone {self.cone[1]} at X{x:.3f} Y{y:.3f} ({how}).", "busy")
        self._next_cone()

    # ---------------- result ----------------
    def _finish(self):
        m = self.main
        names = list(self.results)
        bed = {p: (self.fids[p]["x"], self.fids[p]["y"]) for p in calibration.POINTS}
        head = {p: (self.results[p]["x"], self.results[p]["y"]) for p in names}
        (c,) = [p for p in calibration.POINTS if p not in names]
        head[c] = autocal.third_from_two(bed[names[0]], head[names[0]], bed[names[1]], head[names[1]], bed[c])
        for p in calibration.POINTS:
            m.left.set_machine_point(p, *head[p])            # this session's calibration is complete
        cones = {f"cone_{p}": ([self.results[p]["x"], self.results[p]["y"], self.results[p]["z"]]
                               if p in self.results else []) for p in calibration.POINTS}
        off = [(bed[p][0] - head[p][0], bed[p][1] - head[p][1]) for p in names]
        off_x, off_y = (round(sum(v[i] for v in off) / len(off), 2) for i in (0, 1))
        off_z = round(sum(self.fids[p]["z"] - self.results[p]["z"] for p in names) / len(names), 2)
        saved = ""
        try:
            self.settings = machine_settings.save_values("calibration", cones)
            try:
                self.settings = machine_settings.save_values(
                    "sensor", {"offset_x": off_x, "offset_y": off_y, "offset_z": off_z, "measured": True})
                saved = f" Sensor offset X {off_x:+.2f}, Y {off_y:+.2f}, Z {off_z:+.2f} mm saved."
            except machine_settings.SettingsError:
                self.settings = machine_settings.save_values("sensor", {"offset_z": off_z})
                saved = f" Sensor height Z {off_z:+.2f} mm saved."
        except machine_settings.SettingsError as e:
            return self._end(False, f"Automatic calibration done but not saved: {e}", "error")
        self.log.update(result=self.results, third=c, offset=[off_x, off_y, off_z])
        if self.j is not None:
            self.log["camera_px_per_mm"] = self.j.round(3).tolist()
            self.log["camera_mm_per_px"] = round(autocal.mm_per_px(self.j), 5)
        cal = m.left.cal_points()
        rot = ""
        try:
            rot = f" Plate rotation {calibration.fit(cal).rotation_deg:.2f}°."
        except Exception:                     # noqa: BLE001  report only
            pass
        parts = ", ".join(f"cone {p[1]} X{v['x']:.3f} Y{v['y']:.3f}" for p, v in self.results.items())
        self._end(True, f"Automatic calibration successful: {parts}.{rot}{saved} Photos in {self.folder}.", "ok")

    def _save_images(self, k, after, contact, centre=None):
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            stem = self.folder / f"{k:02d}_cone{self.cone[1]}"
            autocal.gray_png(self.before, f"{stem}_before.png")
            autocal.gray_png(after, f"{stem}_after.png")
            if contact is not None:
                autocal.diff_png(contact, f"{stem}_difference.png", centre)
            # the overview of this touch: before, after, difference, and the points with a legend
            tries = getattr(self, "_tries", {})
            tries[self.cone] = tries.get(self.cone, 0) + 1
            self._tries = tries
            name = f"calibration cone {self.cone[1]}, try {tries[self.cone]}"
            autocal.touch_panel(self.before, after, contact, centre or self.centre, str(self.folder / f"{name}.png"),
                                f"{self.folder.name}  -  {name}")
        except Exception as e:                # noqa: BLE001  images are a record, never a reason to stop
            print(f"Could not save the autocal images: {e}")

    def _write_log(self, result):
        try:
            if not getattr(self, "folder", None):
                return
            self.folder.mkdir(parents=True, exist_ok=True)
            self.log["end"] = datetime.now().isoformat(timespec="seconds")
            self.log["outcome"] = result
            (self.folder / "autocal.json").write_text(json.dumps(self.log, indent=2), encoding="utf-8")
        except OSError:
            pass
