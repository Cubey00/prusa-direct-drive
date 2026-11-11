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
from gantry_controls import MotionController, force_guard, home, motionstop, _stop, enable_metrics
from strain_gauge import StrainGauge

script_folder = Path(__file__).resolve().parent

CANVASHEIGHT = 120
CANVASWIDTH = 400


class State(Enum):
    IDLE = auto()
    CALIBRATING = auto()
    CONFIRMED = auto()
    CAPTURING = auto()


class App:
    def __init__(self, root, link=None):
        self.root = root
        self.link = link              # CalibratorClient when started by the path programmer
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
        self._calib_points = []       # (reading, grams) pairs
        self.calib_a, self.calib_b = None, None

        # --- Load cell display ---
        self.gauge_label = tk.Label(root, text="Load cell: --", font=("Arial", 16))
        self.gauge_label.pack()
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
            tk.Button(link_frame, text="Back-left point",
                      command=lambda: self.send_position("back_left")).pack(side="left", padx=5)
            tk.Button(link_frame, text="Front-right point",
                      command=lambda: self.send_position("front_right")).pack(side="left", padx=5)
            link_frame.pack()

        # --- Bulk capture ---
        self.capture_btn = tk.Button(root, text="START PROGRAM", command=self.start_bulk_capture, state="disabled")
        self.capture_btn.pack(pady=10)
        self.progress_label = tk.Label(root, text="")
        self.progress_label.pack()

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
                self.root.after(0, lambda: messagebox.showerror("Printer stop error", msg))
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

    def send_position(self, point):
        def work():
            try:
                # actual position from the metrics stream (what the "Current" label shows);
                # M114 only reports the commanded position, i.e. the target
                p = self.gauge.pos
                if None in (p["x"], p["y"], p["z"]):
                    raise RuntimeError("no live position from the printer yet")
                x, y, z = p["x"], p["y"], p["z"]
                ok = self.link.send_position(point, x, y, z)
                text = f"Sent {point.replace('_', ' ')}: X{x:.2f} Y{y:.2f}" if ok else "Path programmer not connected"
            except Exception as e:
                text = f"Send failed: {e}"
            self.root.after(0, lambda: self.progress_label.config(text=text))
        threading.Thread(target=work, daemon=True).start()

    def _on_motion_idle(self):
        if self.state == State.CALIBRATING:
            self.root.after(0, self.refresh_preview)

    def _on_guard_trip(self):
        self.motion.halt()      # drop queued nudges
        self.root.after(0, lambda: self.progress_label.config(text="FORCE LIMIT: bed dropped"))

    def _on_motion_error(self, err):
        self.root.after(0, lambda: messagebox.showerror("Printer", str(err)))


    # --- Load cell display ---

    def start_gauge(self):
        if self._gauge_running:
            return
        try:
            enable_metrics()          # runs on the serial link, quick
        except Exception as e:
            messagebox.showerror("Metrics", str(e))
            return
        self.gauge.start()
        self._gauge_running = True
        self.poll_gauge()

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
            stable = self.gauge.force_g()
            if stable is not None:
                self.gauge_label.config(text=f"Load cell: {stable:+.1f} g")
            c = self.gauge_canvas
            c.delete("all")
            # tare automatically once enough data points are in
            if not self._tared and len(self.gauge.samples) >= 50:
                self._tared = self.gauge.tare()
                if self._tared:
                    # over 500 g: stop everything and drop the bed until under 50 g, plus extra_s
                    # (only armed once tared, the raw offset would trip it)
                    force_guard(self.gauge, trip_g=500, release_g=50,
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
        _stop.set()             # stop the probe and any chunked move
        self.stop()             # stop the printer in its tracks
        self.gauge.stop()
        if self.link is not None:
            self.link.close()
        self.root.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--link", type=int, help="path programmer port (set by pathprogrammer)")
    args = parser.parse_args()
    link = None
    if args.link:
        from pathprogrammer.ipc import CalibratorClient
        link = CalibratorClient(args.link)
    root = tk.Tk()
    App(root, link)
    root.mainloop()
