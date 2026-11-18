#GUI
import argparse
import threading
import time
import tkinter as tk
from enum import Enum, auto
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

import numpy as np
from PIL import Image, ImageTk
#from playsound3 import playsound

#from imaging import single_image, set_photo_folder
from gantry_controls import (MotionController, force_guard, home, motionstop, _stop, enable_metrics,
                             disconnect, move_absolute, _quickstop as quickstop, _lock as gantry_lock,
                             home_xy, send_gcode, read_position, bed_up_refusal)
from machine_settings import SETTINGS
from strain_gauge import StrainGauge

script_folder = Path(__file__).resolve().parent

CANVASHEIGHT = 120
CANVASWIDTH = 400

GUARD_TRIP_G = SETTINGS.forces.guard_trip_g   # force guard: drop the bed above this |force| (g)
GUARD_WINDOW = 16           # samples (~50 ms) averaged by the guard, and by the force label


class State(Enum):
    IDLE = auto()
    CALIBRATING = auto()
    CONFIRMED = auto()
    CAPTURING = auto()


class App:
    def __init__(self, root, link=None, hidden=False):
        self.root = root
        self.link = link              # CalibratorClient when started by the path programmer
        self._creep_cancel = threading.Event()   # stops a Meten creep (IPC "halt")
        self.hidden = hidden and link is not None   # no window: messages go to the wizard instead
        self.root.title("PRUSA Core One load cell and control tool")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        #root.bind("<Escape>", lambda e: self.stop())

        self.motion = MotionController(on_idle=self._on_motion_idle, on_error=self._on_motion_error)
        self.gauge = StrainGauge(debug=False)
        self.state = State.IDLE
        self.output_dir = None
        self._capturing = False
        self._probing = False
        self._gauge_running = False
        self._tared = False
        self._alert_job = None        # pending after() id of the flashing alert
        self._alert_end = 0.0
        self._calib_points = []       # (reading, grams) pairs
        self.calib_a, self.calib_b = None, None

        # --- Load cell display ---
        # fixed labels, only the numbers change: right-aligned in fixed-width monospace fields
        big, num = ("Arial", 16), ("Consolas", 16)
        readout = tk.Frame(root)
        tk.Label(readout, text="Load cell:", font=big).pack(side="left")
        self.force_var = tk.StringVar(value="--")
        tk.Label(readout, textvariable=self.force_var, font=num, width=6, anchor="e").pack(side="left")
        tk.Label(readout, text="g      peak:", font=big).pack(side="left")
        self.peak_var = tk.StringVar(value="--")
        self.peak_label = tk.Label(readout, textvariable=self.peak_var, font=num, width=6, anchor="e")
        self.peak_label.pack(side="left")
        tk.Label(readout, text="g", font=big).pack(side="left")
        readout.pack()
        self.gauge_canvas = tk.Canvas(root, width=CANVASWIDTH, height=CANVASHEIGHT, bg="black")
        self.gauge_canvas.pack()

        gauge_frame = tk.Frame(root)
        tk.Button(gauge_frame, text="TARE", command=self.gauge.tare).grid(row=0, column=0, columnspan=2)
        tk.Label(gauge_frame, text="Load cell smoothing samples (1 for raw data):").grid(row=1, column=0)
        self.smooth_var = tk.StringVar(value="32")
        tk.Entry(gauge_frame, textvariable=self.smooth_var, width=6).grid(row=1, column=1)
        tk.Label(gauge_frame, text="Draw line at:").grid(row=2, column=0, padx=(15, 0))
        self.line_var = tk.StringVar(value="")
        tk.Entry(gauge_frame, textvariable=self.line_var, width=8).grid(row=2, column=1, padx=5)
        gauge_frame.pack()

        # --- Load cell calibration points (separate window, hidden until opened) ---
        tk.Button(root, text="Weight calibration...", command=self.open_calibration_window).pack(pady=5)
        self.calib_win = tk.Toplevel(root)
        self.calib_win.title("Load cell calibration")
        self.calib_win.protocol("WM_DELETE_WINDOW", self.calib_win.withdraw)  # hide instead of destroy
        self.calib_win.withdraw()
        calib_pts_frame = tk.Frame(self.calib_win, padx=10, pady=10)
        tk.Button(calib_pts_frame, text="Add calibration point", command=self.start_calibration_point).grid(row=0, column=0, padx=5)
        tk.Button(calib_pts_frame, text="Finish calibration", command=self.finish_calibration).grid(row=0, column=1, padx=5)
        tk.Button(calib_pts_frame, text="Plot calibration", command=self.plot_calibration).grid(row=0, column=2, padx=5)
        self.calib_listbox = tk.Listbox(calib_pts_frame, height=6, width=30)
        self.calib_listbox.grid(row=1, column=0, columnspan=3, pady=5, sticky="ew")
        tk.Button(calib_pts_frame, text="Delete selected", command=self.delete_calibration_point).grid(row=2, column=0, columnspan=3, pady=(0, 5))
        self.calib_pts_label = tk.Label(calib_pts_frame, text="0 points collected")
        self.calib_pts_label.grid(row=3, column=0, columnspan=3)
        calib_pts_frame.pack()

        # --- Output folder picker ---
        #self.folder_label = tk.Label(root, text="No output folder selected")
        #self.folder_label.pack(pady=5)
        #tk.Button(root, text="Choose output folder", command=self.choose_folder).pack()

        # --- Preview image ---
        #self.preview_label = tk.Label(root, text="[Preview will appear here]")
        #self.preview_label.pack(pady=10)

        # --- Motion / touch probe controls ---
        move_frame = tk.Frame(root)

        # (text, direction, row, column)
        jog_buttons = [
            ("↑", (0, +1, 0), 2, 1),
            ("←", (-1, 0, 0), 3, 0),
            ("→", (+1, 0, 0), 3, 2),
            ("↓", (0, -1, 0), 4, 1),
            ("↑", (0, 0, -1), 2, 3),   # Z up
            ("↓", (0, 0, +1), 3, 3),   # Z down
        ]
        for text, d, row, col in jog_buttons:
            tk.Button(move_frame, text=text, command=lambda d=d: self.nudge(d)).grid(row=row, column=col)

        tk.Button(move_frame, text="STOP", bg="red", fg="white", command=self.stop).grid(row=3, column=1)
        tk.Button(move_frame, text="HOME AXES", command=home).grid(row=4, column=3)

        tk.Label(move_frame, text="X/Y speed (mm/s):").grid(row=5, column=0)
        tk.Label(move_frame, text="1 - 20").grid(row=6, column=1)
        self.speed_var = tk.StringVar(value="20")
        tk.Entry(move_frame, textvariable=self.speed_var, width=6).grid(row=5, column=1)

        tk.Label(move_frame, text="Step (mm):").grid(row=5, column=2)
        tk.Label(move_frame, text="0.02 - 100").grid(row=6, column=3)
        self.step_var = tk.StringVar(value="10")
        tk.Entry(move_frame, textvariable=self.step_var, width=6).grid(row=5, column=3)

        self.confirm_btn = tk.Button(move_frame, text="Confirm calibration", command=self.confirm, state="disabled")
        self.confirm_btn.grid(row=7, column=0, columnspan=4, pady=10)

        mono = ("Consolas", 11)
        self.cur_label = tk.Label(move_frame, text="Current: X --  Y --  Z --", font=mono)
        # target row: live target, but editable; Enter or GO moves there
        tgt_frame = tk.Frame(move_frame)
        tk.Label(tgt_frame, text="Target: ", font=mono).pack(side="left")
        self.tgt_vars, self.tgt_entries = [], []
        for axis in "XYZ":
            tk.Label(tgt_frame, text=axis, font=mono).pack(side="left")
            var = tk.StringVar(value="--")
            entry = tk.Entry(tgt_frame, textvariable=var, width=6, font=mono)
            entry.pack(side="left", padx=12)
            entry.bind("<Return>", lambda e: self.go_to_target())
            self.tgt_vars.append(var)
            self.tgt_entries.append(entry)
        #tk.Button(tgt_frame, text="GO", command=self.go_to_target).pack(side="left")
        self.queue_label = tk.Label(move_frame, text="Idle")
        self.cur_label.grid(row=8, column=0, columnspan=4)
        tgt_frame.grid(row=9, column=0, columnspan=4)
        self.queue_label.grid(row=10, column=0, columnspan=4)
        move_frame.pack()

        # --- Path programmer link: send the current head position as a calibration point ---
        if self.link is not None:
            link_frame = tk.Frame(root, pady=5)
            tk.Label(link_frame, text="Send position as:").pack(side="left")
            from pathprogrammer.calibration import LABELS, POINTS
            for point in POINTS:          # fiducial cones F1/F2/F3 on the printed plate
                tk.Button(link_frame, text=LABELS[point],
                          command=lambda p=point: self.send_position(p)).pack(side="left", padx=5)
            link_frame.pack()

        # --- Bulk capture ---
        self.capture_btn = tk.Button(root, text="START PROGRAM", command=self.start_bulk_capture, state="disabled")
        self.capture_btn.pack(pady=10)
        self.progress_label = tk.Label(root, text="")
        self.progress_label.pack()
        self._label_bg, self._label_font = self.progress_label.cget("bg"), self.progress_label.cget("font")

        # start the strain gauge display when everything is in place
        root.after_idle(self.start_gauge)

    # --- Motion ---

    def stop(self):
        self.motion.halt()
        self.queue_label.config(text="STOP")
        def work():
            try:
                motionstop()
            except Exception as e:
                msg = str(e)
                self.root.after(0, lambda: self.report_error("Printer stop error", msg))
        threading.Thread(target=work, daemon=True).start()

    def get_step(self):
        try:
            step = float(self.step_var.get())
            if not 0.02 <= step <= 100:
                raise ValueError
            return step
        except ValueError:
            messagebox.showwarning("Step size", "Enter a step between 0.02 and 100 mm.")
            return None

    def get_speed(self):
        try:
            speed = float(self.speed_var.get())
            if not 1 <= speed <= 20:
                raise ValueError
            return speed * 60   # mm/s -> mm/min, which the printer uses
        except ValueError:
            messagebox.showwarning("Speed", "Enter a speed between 1 and 20 mm/s.")
            return None

    def nudge(self, direction):
        step = self.get_step()
        speed = self.get_speed()
        if step is None or speed is None:
            return
        try:
            self.motion.nudge(*(d * step for d in direction), speed)
        except Exception as e:
            messagebox.showerror("Printer", str(e))
            return
        self.update_position_labels()

    def update_position_labels(self):
        fmt = lambda v: "  --  " if v is None else f"{v:7.2f}"
        p = self.gauge.pos
        self.cur_label.config(text=f"Current: X {fmt(p['x'])}  Y {fmt(p['y'])}  Z {fmt(p['z'])}")
        t = self.motion.target
        if t:
            focused = self.root.focus_get()
            for var, entry, v in zip(self.tgt_vars, self.tgt_entries, t):
                if entry is not focused:      # don't overwrite while the user is typing
                    var.set(f"{v:.2f}")
        n = self.motion.pending
        self.queue_label.config(text=f"Moving ({n} click(s) pending)" if n else "Idle")

    def go_to_target(self):
        try:
            xyz = [None if var.get().strip() in ("", "--") else float(var.get())
                   for var in self.tgt_vars]
        except ValueError:
            messagebox.showwarning("Target", "Enter numbers for X, Y and Z (mm).")
            return
        speed = self.get_speed()
        if speed is None:
            return
        try:
            self.motion.move_to(*xyz, speed)
        except Exception as e:
            messagebox.showerror("Printer", str(e))
            return
        self.root.focus_set()     # release the entry so live updates resume
        self.update_position_labels()

    def report_error(self, title, msg):
        """A pop-up, or (running hidden for the wizard) a status message the wizard shows."""
        if self.hidden:
            self.link.send_status(False, f"{title}: {msg}")
        else:
            messagebox.showerror(title, msg)

    def on_link_message(self, msg):
        """Message from the path programmer (main thread). "goto": drive the head to an absolute
        position for the calibration wizard: Z first (the bed goes to the travel height, so the
        sensor is clear of the plate), then X/Y. "jog": a small fine-tuning move from the wizard.
        "request_position": send the current position as that fiducial."""
        kind = msg.get("type")
        if kind == "quit":
            self.on_close()
            return
        if kind == "creep":
            self.link_creep(msg)
            return
        if kind == "halt":
            self._creep_cancel.set()
            self.motion.halt()
            quickstop()
            return
        if kind == "jog":
            self.link_jog(msg)
            return
        if kind == "request_position":
            from pathprogrammer.calibration import POINTS
            if msg.get("point") in POINTS:
                self.send_position(msg["point"])
            return
        if kind != "goto":
            return
        label = msg.get("label", "")
        try:
            x, y, z = float(msg["x"]), float(msg["y"]), float(msg["z"])
            z_final = None if msg.get("z_final") is None else float(msg["z_final"])
        except (KeyError, TypeError, ValueError):
            return
        (x0, x1), (y0, y1) = SETTINGS.printer.travel
        if not (x0 <= x <= x1 and y0 <= y <= y1 and 0 <= z <= SETTINGS.printer.z_max
                and (z_final is None or 0 <= z_final <= SETTINGS.printer.z_max)):
            text = f"Refused to go to X{x:.1f} Y{y:.1f} Z{z:.1f}: outside the travel limits"
            self.progress_label.config(text=text)
            self.link.send_goto_done(label, False, text)
            return
        # A command still waiting for the printer (e.g. M334 while the printer asks on its screen
        # to confirm the metrics address) holds the serial lock: say so instead of waiting silently.
        if not gantry_lock.acquire(blocking=False):
            text = (f"Cannot go to {label}: the printer is still busy with an earlier command. "
                    "Check the printer's screen for a question (e.g. confirm the metrics address) "
                    "and try again.")
            self.progress_label.config(text=text)
            self.link.send_goto_done(label, False, text)
            return
        gantry_lock.release()
        if self.gauge.pos.get("z") is None:
            self.progress_label.config(text="Note: no live position from the printer yet (metrics); moving anyway.")
        feed = self.get_speed() or 1200.0         # mm/min (get_speed converts the mm/s field)
        self.motion.halt()
        home = bool(msg.get("home_xy"))
        self.progress_label.config(text=f"Going to {label}: Z{z:.1f}, " + ("home X/Y, " if home else "")
                                   + f"then X{x:.1f} Y{y:.1f} ...")

        def work():
            try:
                move_absolute(z=z)                                     # bed to travel height first
                if home:
                    home_xy()          # the head may have been moved by hand; Z is kept (sensor!)
                move_absolute(x=x, y=y, XYfeed=feed)
                if z_final is not None:
                    move_absolute(z=z_final)                           # above the cone, at the saved spot
                zz = z if z_final is None else z_final
                ok, text = True, f"At {label}: X{x:.2f} Y{y:.2f} Z{zz:.2f}. Fine-jog, then send the position."
            except Exception as e:
                ok, text = False, f"Going to {label} failed: {e}"
            self.motion.halt()                                         # next jog starts from the real position
            self.link.send_goto_done(label, ok, text)
            self.root.after(0, lambda: (self.progress_label.config(text=text),
                                        self.update_position_labels()))
        threading.Thread(target=work, daemon=True).start()

    def link_creep(self, msg):
        """Meten: bed up very slowly until the sensor just touches the object (load cell), then stay."""
        from touch import creep_to_contact
        try:
            fast_mm = max(0.0, float(msg.get("fast_mm", 0.0)))
            max_mm = float(msg.get("max_mm", 15.0))
        except (TypeError, ValueError):
            return
        if not 0 < max_mm <= 30 or fast_mm >= max_mm:
            self.link.send_touch_done(False, "error", "Aanraken geweigerd: max 30 mm en het snelle stuk korter dan het geheel")
            return
        if not self._tared:
            self.link.send_touch_done(False, "error", "Nog geen krachtdata van de load cell; wacht even en probeer opnieuw.")
            return
        self.motion.halt()
        self._creep_cancel.clear()

        def work():
            res = creep_to_contact(self.gauge, contact_g=SETTINGS.forces.contact_g,
                                   max_force_g=SETTINGS.forces.max_force_g, max_travel=max_mm, fast_mm=fast_mm,
                                   cancel=self._creep_cancel, on_status=lambda m: None)
            self.motion.halt()                       # next jog starts from the real position
            texts = {"ok": "De sensor raakt het object.", "no_contact": res.message,
                     "overload": f"Te veel kracht ({res.peak_g:.0f} g): gestopt.", "aborted": "Gestopt."}
            self.link.send_touch_done(res.status == "ok", res.status, texts.get(res.status, res.message),
                                      res.end_z, res.final_g)
            self.root.after(0, self.update_position_labels)
        threading.Thread(target=work, daemon=True).start()

    def link_jog(self, msg):
        try:
            dx, dy, dz = float(msg["dx"]), float(msg["dy"]), float(msg["dz"])
            feed = float(msg["feed"])
        except (KeyError, TypeError, ValueError):
            return
        if max(abs(dx), abs(dy), abs(dz)) > 10 or not 30 <= feed <= 1200:
            self.link.send_status(False, "Jog refused: steps up to 10 mm, speed 0.5-20 mm/s")
            return
        force = self.gauge.force_g(16) if self._tared else None
        refusal = bed_up_refusal(dz, force, SETTINGS.forces.jog_stop_g)
        if refusal:
            self.link.send_status(False, refusal)
            self.root.after(0, lambda: self.progress_label.config(text=refusal))
            return
        # the serial lock taken while no jog is queued: something else waits (e.g. M334 for a
        # confirmation on the printer's screen); queued jogs would only pile up behind it
        if self.motion.pending == 0:
            if not gantry_lock.acquire(blocking=False):
                self.link.send_status(False, "The printer is still busy with an earlier command; "
                                             "check its screen for a question.")
                return
            gantry_lock.release()
        try:
            self.motion.nudge(dx, dy, dz, feed)
        except Exception as e:
            self.link.send_status(False, f"Jog failed: {e}")
            return
        self.update_position_labels()

    def send_position(self, point):
        def work():
            ok = False
            try:
                # actual position from the metrics stream (what the "Current" label shows);
                # M114 only reports the commanded position, which equals it once the head stands still
                p = self.gauge.pos
                if None not in (p["x"], p["y"], p["z"]):
                    x, y, z = p["x"], p["y"], p["z"]
                elif self.motion.pending == 0:
                    x, y, z = self.motion.read_position()
                else:
                    raise RuntimeError("the head is still moving; wait until it stands still")
                ok = self.link.send_position(point, x, y, z)
                text = f"Sent {point.replace('_', ' ')}: X{x:.2f} Y{y:.2f}" if ok else "Path programmer not connected"
            except Exception as e:
                text = f"Send failed: {e}"
            if not ok:
                self.link.send_status(False, text)
            self.root.after(0, lambda: self.progress_label.config(text=text))
        threading.Thread(target=work, daemon=True).start()

    def _on_motion_idle(self):
        if self.state == State.CALIBRATING:
            self.root.after(0, self.refresh_preview)

    def _on_guard_trip(self):
        self.motion.halt()      # drop queued nudges
        self.root.after(0, lambda: self.alert("FORCE LIMIT: bed dropped"))

    ALERT_S = 10.0          # how long an alert stays
    ALERT_BLINK_MS = 400

    def alert(self, text):
        """Flashing message (white on red <-> black on default) in the bottom label, cleared
        after ALERT_S. A new alert restarts the time."""
        self._alert_end = time.monotonic() + self.ALERT_S
        self.progress_label.config(text=text, font=("Arial", 14, "bold"))
        if self._alert_job is None:
            self._blink(True)

    def _blink(self, on):
        lbl = self.progress_label
        if time.monotonic() >= self._alert_end:
            lbl.config(text="", bg=self._label_bg, fg="black", font=self._label_font)
            self._alert_job = None
            return
        lbl.config(bg="red" if on else self._label_bg, fg="white" if on else "black")
        self._alert_job = self.root.after(self.ALERT_BLINK_MS, self._blink, not on)

    def _on_motion_error(self, err):
        self.root.after(0, lambda: self.report_error("Printer", str(err)))


    # --- Load cell display ---

    def start_gauge(self):
        """Turn on the printer's metrics stream in a background thread: M334 waits until the printer
        is confirmed on its own screen when the metrics address changed, and doing that here in the
        Tk thread froze the whole window (no jogging, no 'Rijd erheen')."""
        if self._gauge_running:
            return
        self._gauge_running = True
        self.progress_label.config(text="Connecting to the printer for the force data... If the printer asks "
                                        "on its screen to confirm the metrics address, confirm it there.")

        def work():
            try:
                enable_metrics()
            except Exception as e:
                msg = str(e)

                def failed():
                    self._gauge_running = False
                    self.progress_label.config(text=f"No force data: {msg}")
                    self.report_error("Geen krachtdata", msg)
                self.root.after(0, failed)
                return

            # the printer only streams pos_x/y/z while moving: start from M114 (exact when idle)
            try:
                pos = read_position()
            except Exception as e:
                print(f"Initial position read failed: {e}")
                pos = None

            def started():
                self.gauge.start()
                if pos:
                    for k, v in zip("xyz", pos):
                        if self.gauge.pos[k] is None:
                            self.gauge.pos[k] = v
                self.poll_gauge()
                self.progress_label.config(text="Printer connected; force data is coming in.")
            self.root.after(0, started)
        threading.Thread(target=work, daemon=True).start()

    def get_smoothing(self):
        try:
            return max(1, min(300, int(self.smooth_var.get())))
        except ValueError:
            return 1

    def get_line_value(self):
        try:
            return float(self.line_var.get())
        except ValueError:
            return None      # empty or invalid -> no line

    def poll_gauge(self):
        ZERO_Y = CANVASHEIGHT * 3/4         # zero line, a quarter from the bottom
        UP_ROOM = ZERO_Y - 5                # px available above zero
        DOWN_ROOM = CANVASHEIGHT / 4 - 5    # px available below zero
        self.update_position_labels()
        if self.gauge.drain():
            s = self.gauge.grams(self.gauge.view_smoothed(self.get_smoothing()))  # grams, like the label and the line
            # same 16-sample window as the force guard (a 300-sample average hid ~100 ms jabs of
            # 800 g as ~80-250 g), plus the peak over the buffered history (~3 s)
            now = self.gauge.force_g(GUARD_WINDOW)
            if now is not None:
                hist = self.gauge.grams(self.gauge.view_smoothed(GUARD_WINDOW))
                peak = max(hist, key=abs) if hist else now
                self.force_var.set(f"{now:+.0f}")
                self.peak_var.set(f"{peak:+.0f}")
                self.peak_label.config(fg="red" if abs(peak) > GUARD_TRIP_G * 0.8 else "black")
            c = self.gauge_canvas
            c.delete("all")
            # tare automatically once enough data points are in
            if not self._tared and len(self.gauge.samples) >= 50:
                self._tared = self.gauge.tare()
                if self._tared:
                    # over 500 g: stop everything and drop the bed until under 50 g, plus extra_s
                    # (only armed once tared, the raw offset would trip it)
                    force_guard(self.gauge, trip_g=SETTINGS.forces.guard_trip_g,
                                release_g=SETTINGS.forces.guard_release_g, window=GUARD_WINDOW,
                                on_trip=self._on_guard_trip)
            if len(s) > 1:
                lv = self.get_line_value()
                peak_up = max(max(s), 0)
                peak_dn = max(-min(s), 0)
                if lv is not None:
                    peak_up = max(peak_up, lv)
                    peak_dn = max(peak_dn, -lv)
                step = CANVASWIDTH / (len(s) - 1)
                half = max(peak_up / UP_ROOM, peak_dn / DOWN_ROOM, 50.0 / 55)   # units per pixel
                to_y = lambda v: ZERO_Y + v / half

                # zero line (solid)
                y0 = to_y(0)
                if 0 <= y0 <= CANVASHEIGHT:
                    c.create_line(0, y0, CANVASWIDTH, y0, fill="cyan")
                    c.create_text(4, y0 - 7, text="0", fill="cyan", anchor="w")

                # user line (dashed)
                if lv is not None:
                    yl = to_y(-lv)
                    if 0 <= yl <= CANVASHEIGHT:
                        c.create_line(0, yl, CANVASWIDTH, yl, fill="cyan", dash=(3, 4))
                        c.create_text(CANVASWIDTH - 4, yl - 7, text=f"{lv:g}", fill="cyan", anchor="e")

                pts = []
                for i, v in enumerate(s):
                    pts += [i * step, to_y(v)]
                c.create_line(*pts, fill="orange", width=1)
        self.root.after(30, self.poll_gauge)

    # --- Load cell calibration ---

    def open_calibration_window(self):
        self.calib_win.deiconify()
        self.calib_win.lift()

    def collect_point_blocking(self, smoothing=32, samples=100, delay=0.02):
        """Runs in a worker thread; only reads gauge state, doesn't touch Tk."""
        vals = []
        for _ in range(samples):
            s = self.gauge.view_smoothed(smoothing)
            if s:
                vals.append(s[-1])
            time.sleep(delay)
        if not vals:
            raise RuntimeError("No samples collected")
        return sum(vals) / len(vals)

    def start_calibration_point(self):
        self.progress_label.config(text="Collecting calibration point...")
        result = {}

        def work():
            try:
                result["reading"] = self.collect_point_blocking()
            except Exception as e:
                result["error"] = str(e)

        threading.Thread(target=work, daemon=True).start()

        # dialog opens immediately, in parallel with the sampling thread above —
        # read the scale and type the value right away
        weight = simpledialog.askfloat("Weight", "Grams on the scale right now:", parent=self.calib_win)

        def finish():
            if "error" in result:
                self.progress_label.config(text=f"Failed: {result['error']}")
                return
            if "reading" not in result:
                self.root.after(200, finish)   # thread not done yet, check again shortly
                return
            if weight is None:
                self.progress_label.config(text="Point discarded.")
                return
            r = result["reading"]
            self._calib_points.append((r, weight))
            self.refresh_calib_listbox()
            self.progress_label.config(text=f"Point {len(self._calib_points)}: reading {r:.2f} -> {weight:g} g")

        finish()

    def finish_calibration(self):
        if len(self._calib_points) < 2:
            messagebox.showwarning("Calibration", "Collect at least 2 points first.")
            return
        readings, grams = zip(*self._calib_points)
        a, b = np.polyfit(readings, grams, 1)
        residuals = [g - (a * r + b) for r, g in zip(readings, grams)]
        rmse = (sum(r**2 for r in residuals) / len(residuals)) ** 0.5
        self.calib_a, self.calib_b = a, b
        messagebox.showinfo("Calibration", f"grams = {a:.6f} * reading + {b:.4f}\nRMSE: {rmse:.3f} g")

    def plot_calibration(self):
        if not self._calib_points:
            messagebox.showwarning("Calibration", "No points collected yet.")
            return
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        readings, grams = zip(*self._calib_points)
        win = tk.Toplevel(self.root)
        win.title("Load cell calibration")
        fig, ax = plt.subplots(figsize=(5, 4))
        ax.scatter(readings, grams, label="measured points")
        if self.calib_a is not None:
            xs = np.linspace(min(readings), max(readings), 100)
            ax.plot(xs, self.calib_a * xs + self.calib_b, color="orange",
                    label=f"fit: {self.calib_a:.4f}x + {self.calib_b:.2f}")
        ax.set_xlabel("Load cell reading")
        ax.set_ylabel("Grams")
        ax.legend()
        ax.grid(True, alpha=0.3)
        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    def refresh_calib_listbox(self):
        self.calib_listbox.delete(0, tk.END)
        for r, g in self._calib_points:
            self.calib_listbox.insert(tk.END, f"reading {r:.2f}  ->  {g:g} g")
        self.calib_pts_label.config(text=f"{len(self._calib_points)} points collected")

    def delete_calibration_point(self):
        sel = self.calib_listbox.curselection()
        if not sel:
            messagebox.showinfo("Delete point", "Select a point in the list first you fool")
            return
        del self._calib_points[sel[0]]
        self.refresh_calib_listbox()

    # --- Imaging workflow ---

    def choose_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.output_dir = path
            #set_photo_folder(path)
            #playsound(script_folder / "folderselect.mp3")
            self.folder_label.config(text=f"Output: {path}")

    def start_calibration(self):
        if not self.output_dir:
            messagebox.showwarning("Missing folder", "Please choose an output folder first.")
            return
        self.state = State.CALIBRATING
        self.confirm_btn.config(state="normal")
        self.refresh_preview()

    def refresh_preview(self):
        if self._capturing:
            return
        self._capturing = True
        print('Image would be made now')
        """
        def work():
            try:
                img = Image.fromarray(single_image()).resize((400, 300))
            except Exception:
                img = None
            self.root.after(0, lambda: self._show_preview(img))
        threading.Thread(target=work, daemon=True).start()
        """

    def _show_preview(self, img):
        self._capturing = False
        if img is not None:
            self.tk_img = ImageTk.PhotoImage(img)
            self.preview_label.config(image=self.tk_img, text="")

    def confirm(self):
        self.state = State.CONFIRMED
        self.capture_btn.config(state="normal")
        self.confirm_btn.config(state="disabled")
        #playsound(script_folder / "calibrationcomplete.mp3")
        messagebox.showinfo("Calibration confirmed", "Please insert program or start below")

    def start_bulk_capture(self):
        self.state = State.CAPTURING
        self.capture_btn.config(state="disabled")

    def on_capture_done(self):
        self.progress_label.config(text="Done!")
        messagebox.showinfo("Finished", f"Images saved to {self.output_dir}")

    def on_close(self):
        if gantry_lock.acquire(blocking=False):      # give the X/Y idle release back (home_xy kept them on)
            try:
                send_gcode("M84 S120", timeout=2)
            except Exception:
                pass
            finally:
                gantry_lock.release()
        _stop.set()             # stop the probe and any chunked move
        self.motion.halt()
        quickstop()             # M410 straight away; no thread that could keep the port busy
        self.gauge.stop()
        if self.link is not None:
            self.link.close()
        self.root.destroy()
        # release the serial port now, even if a command is still waiting for its 'ok';
        # otherwise the process can stay alive without a window and keep the port locked
        disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--link", type=int, help="path programmer port (set by pathprogrammer)")
    parser.add_argument("--hidden", action="store_true", help="no window (the path programmer's wizard drives it)")
    args = parser.parse_args()
    link = None
    if args.link:
        from pathprogrammer.ipc import CalibratorClient
        link = CalibratorClient(args.link)
    root = tk.Tk()
    if args.hidden and link is not None:
        root.withdraw()
    app = App(root, link, hidden=args.hidden)
    if link is not None:
        link.on_message = lambda msg: root.after(0, app.on_link_message, msg)
    root.mainloop()
