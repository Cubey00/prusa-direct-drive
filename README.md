# Touch Path Programmer (prusa-direct-drive)

Python software that uses a **Prusa Core One** as a precise 3-axis gantry for materials research.
It prints test parts, finds the printed plate again with a **tactile sensor** mounted on the print
head, touches every part with a known force via the printer's **load cell**, and takes a photo with
an **IDS camera** at every indentation step.

Everything is driven from one desktop program, the **Touch Path Programmer**. This README explains
all of it: installing, printing, calibrating, measuring, the other tools, settings, safety and
troubleshooting. Deeper background is in the [`docs/`](docs) folder (linked where relevant).

---

## Contents

1. [What you need](#1-what-you-need)
2. [First-time setup (Windows)](#2-first-time-setup-windows)
3. [Starting the program](#3-starting-the-program)
4. [The main window](#4-the-main-window)
5. [Workflow: from parts to measurements](#5-workflow-from-parts-to-measurements)
6. [The other tabs](#6-the-other-tabs)
7. [Settings](#7-settings)
8. [Safety](#8-safety)
9. [Troubleshooting](#9-troubleshooting)
10. [Files the program writes](#10-files-the-program-writes)
11. [For developers](#11-for-developers)
12. [More documentation](#12-more-documentation)

---

## 1. What you need

| Item | Notes |
|---|---|
| Prusa **Core One** with the **team firmware** | Separate repository `Cubey00/prusa-direct-drive-firmware` (6.10.x). It contains the immediate M410 stop and the travel limits of the spacer blocks. Standard Prusa firmware does **not** work for moving, calibrating and touching. |
| Spacer blocks on the rail ends | They stop the head early: the nozzle reaches X −2..201 and Y 42..221. The software keeps everything inside that. |
| Tactile sensor on the head bracket | Mounted after printing, taken off before printing. |
| IDS camera (USB 3) + 5 V LED lighting | The lighting is switched on by hand; the software does not control it. |
| A Windows laptop (macOS also works, see [docs/macos-setup.md](docs/macos-setup.md)) | |
| A **USB cable** printer → laptop | Moving, calibrating and touching go over USB. |
| A **network cable** printer → laptop | Printing (PrusaLink) and the force data go over Ethernet. The TU wall sockets do **not** work. |

---

## 2. First-time setup (Windows)

Do this once per laptop. The full step-by-step version with screenshots-in-words is in
[How to start.md](How%20to%20start.md).

1. **Python 3.12 (64-bit)** from [python.org](https://www.python.org/downloads/). Tick
   **"Add python.exe to PATH"** in the installer.
2. **Git** from [git-scm.com](https://git-scm.com/download/win), then get the code:
   ```bash
   git clone https://github.com/Cubey00/prusa-direct-drive.git
   cd prusa-direct-drive
   git checkout development-bep-groep
   ```
   Later updates: `git pull` in that folder. Never put the firmware repository inside this folder
   or the folder above it.
3. **Python packages** (again after every `git pull` that changes `requirements.txt`):
   ```bash
   python -m pip install -r requirements.txt
   ```
4. **PrusaSlicer 2.9 or newer** in its default place (`C:\Program Files\Prusa3D\PrusaSlicer`).
   Older versions do not know the Core One. Somewhere else? Set `SLICER_EXE` in `slicing/config.py`.
5. **IDS peak SDK** (camera driver) from [ids-imaging.com](https://www.ids-imaging.com), installed as
   administrator with the USB3 Vision driver, then **restart the laptop**. Connect the camera to a
   blue USB 3 port. (Skip this if you don't need the camera.)
6. **Network cable and fixed address**: plug the cable from the printer into the laptop and run
   [`printer_network/laptop_setup.ps1`](printer_network/laptop_setup.ps1) **as administrator** once.
   It gives the laptop `192.168.50.1` (no gateway, so internet stays on Wi-Fi) and opens UDP port
   8514 for the force data. The printer is already at `192.168.50.2`. Undo with `laptop_undo.ps1`.
   Details, macOS and troubleshooting: [docs/connecting-the-printer.md](docs/connecting-the-printer.md).
7. **Shortcut**: `python tools/make_shortcut.py` puts **Touch Path Programmer** on the desktop.
8. **PrusaLink password**: start the program, click **Printer settings...** in the
   *Printer (PrusaLink)* box, enter the password from the printer (*Settings > Network > PrusaLink*;
   user `maker`), **Test connection**, **Save**. It is stored on this laptop only.

**Check:** *Printer (PrusaLink)* shows **● Connected**, *Photo control* shows a live image,
**Mini test set** ends with "All plates sliced", and **Start calibration** opens its window
without a printer error.

---

## 3. Starting the program

Double-click **Touch Path Programmer** on the desktop, or from the project folder:

```bash
python -m pathprogrammer
```

The program reopens your last import and plate by itself. It talks to the printer through a
hidden helper process (the *calibrator*, `GUI.py --link ... --hidden`) that starts when needed and
closes with the program. **Only one program may use the printer at a time**: close the Touch Path
Programmer before using another tool on the printer, and after `git pull` restart it completely.

---

## 4. The main window

Three tabs:

| Tab | For |
|---|---|
| **Print, calibration & touch** | The daily work: plates, printing, calibration, measuring, photos. |
| **Focus-Field MTF** | Optical tests of the camera: focus over the field, MTF/SFR. See [section 6](#6-the-other-tabs). |
| **Positioning Repeatability** | Move sequences with manual dial-gauge readings. See [section 6](#6-the-other-tabs). |

On the first tab:

- **Left column**: **Print plates** (import, filament, plates), **Printer (PrusaLink)** (print,
  status), **Calibration** (sensor offset, **Start calibration**).
- **Centre**: **Photo control** (live camera image, **Take photo**, exposure, gain), a small model
  preview, and the folded **Model control** (rotate the model, add touch points by clicking).
- **Right column** (scrolls): the folded points list, **Measure** (the measuring buttons and
  settings), the live force graph, and the run controls with the red **STOP**.

---

## 5. Workflow: from parts to measurements

### 5.1 Make print plates

1. In **Print plates**, choose the **filament** that is loaded in the printer. The printer refuses
   G-code made for another filament type.
2. Click **Dataset...** and choose a folder with `.stl` / `.3mf` parts (STL in mm), or
   **Test set** (30 shapes) or **Mini test set** (a 5 mm square and a 5 mm dome).
3. The program checks every part and lays them out on plates:
   - a fixed raster of **8 × 5 = 40 cells** of 20 mm per plate, filled row by row from the front left;
   - three **cones F1 (front left), F2 (front right), F3 (back left)** on every plate, used to find
     the plate again;
   - parts are **rejected** when the footprint is over 15 × 15 mm, the height over 50 mm, or they
     need support (overhangs steeper than 45°). The reasons are shown and kept in `summary.json`.
   - parts are taken in **file-name order**: number them (`001_…`, `002_…`) to control the order.
4. It slices every plate right away into `plates/<dataset folder name>/`. 100 parts → 3 plates.
5. Select a plate in the list: it opens as the model. **Show in 3D** opens its G-code in the viewer.

**After a software update that changes printing, import again**: settings like the purge line and
speeds are baked into the G-code when slicing; old plates keep the old behaviour.

What the slicing adds on top of Prusa's profile (because of the spacer blocks and the small parts):

- the **purge line** is moved to where the nozzle can reach (X 80 → 30 at Y 47; Prusa's own spot at
  the far front right is behind the spacer blocks, so the nozzle would start empty);
- slower printing for small parts (walls 40/60 mm/s, small perimeters 30, infill 100, max
  100 mm/s, gentle accelerations), **1 skirt loop** to prime the nozzle and a **2 mm brim**;
- quicker start: only 5 bed probe points, and no wait for the bed frame to warm up.

### 5.2 Print

1. **Take the sensor off** the print head.
2. Select the plate and click the green **Print this plate**. The G-code goes over the network and
   starts; the box shows state, progress, time left and temperatures, with pause / resume / stop.
3. Stay with the printer until the first layer is down (purge line first, then the skirt).

### 5.3 Calibrate (find the plate with the sensor)

Put the sensor back on the head and click the blue **Start calibration**. Choose one of three
modes (the choice is remembered on this laptop):

| Mode | When | What it does |
|---|---|---|
| **All 3 cones** | First time, or after moving cones / mount | Measures F1, F2, F3: shift, rotation and scale of the plate. Most accurate. |
| **Only cone 2** | Normal per-plate calibration | Measures F2 (front right) only: shift; F1/F3 follow from their distance to F2. |
| **Home X and Y only** | The calibration is still right (same plate, printer not switched off) | Lowers the bed, homes X and Y, then **Go to point 1** goes straight to measuring. |

Per cone, one step at a time:

1. **Go to cone N**: the bed lowers to a safe height, then the head drives to the cone. The first
   cone homes X and Y first. **Z is never homed** with the sensor on (the nozzle would push the
   sensor into the bed); the printer keeps its Z from printing. With a saved position it stops
   1 cm above the cone.
2. Jog with the buttons (step 5 / 1 / 0.5 / 0.1 / 0.05 mm) until the centre of the sensor is
   exactly on the cone tip. **Bed up** is refused once the sensor feels more than 100 g.
3. **Send position of cone N**.

After the last cone the program fits the plate. **Calibration successful! … Position known** saves
the head position on the cone(s) and the measured **sensor offset** (nozzle → sensor) in
`machine_settings.toml`. The next calibration drives straight to the saved spot. The offset is
shown above **Start calibration**; **Adjust...** changes it by hand (only if you are sure).

### 5.4 Measure

The **Measure** box (right column):

| Control | What it does |
|---|---|
| **Do whole measuring procedure** | Everything automatically, for **every part on the plate** (see below). |
| Max force / Down per step / Steps | Settings of the procedure (default 300 g, max 1000 g; 0.1 mm; 10 steps). |
| Hover height / Fast approach / Slow last part / Slow speed | How the sensor approaches a part (default 3 mm above the top, 2 mm/s, last 1.5 mm at 0.1 mm/s). Less hover height = faster. |
| **Hover N mm above point 1** | Drive (at a safe height) to the first part and stop just above it. |
| **Go to next point** | Up to the safe height, to the next part, just above it. |
| **Move very slowly until the sensor touches the object** | First touch: this is indentation 0. |
| **Go 0.1 mm down** | Press 0.1 mm deeper, stops at 300 g. |

All settings are remembered per laptop.

**Do whole measuring procedure** does, in one go:

1. Bed to a safe height, **home X and Y** (the saved calibration is used).
2. For every part of the plate, in order (parts the sensor can't reach are skipped):
   - to just above the part; very slowly up until it touches (indentation 0, **photo**);
   - the set number of steps deeper, a **photo after every step** with the bed standing still;
   - reaching the max force ends that part (with a photo at that point); a part that is never
     touched (e.g. it didn't print) is skipped;
   - up to the safe height and on to the next part.
3. At the end the sensor goes back up above the last part. **STOP** ends it at once.

Each run gets **one folder**: `photos/<date_start time>/` (e.g. `photos/2026-11-25_15-24-13/`) with:

- the photos, named `photo_<object>_d<indentation>mm_F<force>g_<date>_<time>.tif`;
- `measurements.csv`: file, time, object, indentation (mm), force (g) per photo;
- `procedure.json`: the settings, the result per part, and per photo the file, time, point,
  object, step, indentation, force, head X/Y/Z, contact Z and the part's bed position. It is
  rewritten after every photo, so it is complete even when a run stops halfway.

**How the touch is detected**: the load cell drifts, so contact is a quick **rise** of the force
(10 g above its level of 1–2 s earlier) that **lasts 0.2 s**; after stopping, the force is checked
again 0.3 s later, and if it has gone it was a false alarm and the bed goes on. In the fast,
known-free part of the approach only a real push (100 g) stops it.

### 5.5 Photos

**Photo control** shows the live camera image. **Take photo** saves a TIFF in `photos/` (or the
folder chosen with **Folder...**). **Exposure** (ms) and **Gain** are below the image and remembered.
After a first touch in Measure, a photo is named with object, indentation and force and logged in
`measurements.csv`.

### 5.6 Touch programs (Execute program)

Open **Model control** and **Points in ascending order**, click on the model to place touch points,
set per point force or depth (and photos per step), then **Execute program** (with the simulator or
the printer). Each run gets its own folder in the **Runs folder** (`runs/` by default) with
`run_log.json`, `images.json`, the images and a height map. The calibrator must be closed for this.

---

## 6. The other tabs

### Focus-Field MTF

Measures how sharp the camera image is across the field while the bed moves the target through
focus (apparent best-focus surface and system SFR/MTF). It works fully in **simulation** first.
For real hardware: generate and print the chart, check it with **Check chart**, tick the physical
safety checks, then **Run scan**; results are under **Results**. The same analysis runs without the
GUI: `python -m focusfield simulate --quick`, `python -m focusfield analyze <run folder>`.
Chart: `python generate_focus_chart.py --output focus_field_chart.svg --field-width 60 --field-height 45`.
Read [docs/focusfield.md](docs/focusfield.md) before using it on hardware.

### Positioning Repeatability

Tests how repeatable the gantry positions: build a sequence of **Move**, **Home X/Y** and **Measure**
steps, run it for a number of cycles, and type the dial-gauge readings when it pauses at a
**Measure** step. Sequences can be saved and reopened; every run writes a CSV
(`repeatability_<date_time>.csv`) with the plan and all readings.

---

## 7. Settings

### `machine_settings.toml` (the setup, shared through git)

The **one place** for everything that depends on the physical setup. It is checked when the program
starts; a wrong value gives a clear error with section and key. Change values here, not in code.

| Section | Holds |
|---|---|
| `[prusalink]` | Printer address and user (the password stays in the app). |
| `[printing]` | Filament, bed probe points, purge line position (`purge_start_x`, `purge_y`). |
| `[printer]`, `[work_area]` | Bed size, travel limits, usable area, Z max. |
| `[raster]`, `[fiducials]` | Cell pitch and margins; cone size and positions. |
| `[sensor]` | Nozzle → sensor offset (measured by the calibration), hover clearance, size. |
| `[forces]` | Contact detection, force limits (a touch aborts above 1100 g), force guard (trip 1200 g, drop ≤ 30 mm). |
| `[calibration]` | Tolerances and the saved cone positions (`cone_F1..F3`). |
| `[camera]`, `[focusfield]` | Camera scale and Focus-Field defaults. |

A calibration writes the new cone position and offset into this file. Commit it if your team
should use it too; a `git pull` can bring in someone else's calibration.

### Per laptop (not in git)

Remembered automatically: the PrusaLink login, filament choice, calibration mode, Measure settings,
camera exposure/gain, photo / runs folders, and which boxes are folded open.

### Load cell scale

`calibration.json` holds the load cell scale (grams = 0.54982 × tared reading, a 50-point fit).
Redo it with *Weight calibration…* in the visual calibrator (`python GUI.py`) after changing the
load cell, the sensor mount or the firmware.

---

## 8. Safety

- **STOP** (red, in the run controls) stops every move at once (M410).
- **Never home Z (G28) with the sensor mounted.** Z homing probes the bed with the nozzle; the
  sensor hangs lower and would be pushed into the bed. The software only ever homes X and Y.
- **If the printer restarts** (power, crash, USB loss) it forgets its Z height and calls the
  current bed height "Z 0": every height is then wrong, and the sensor stops far above (or runs
  into) the parts. Fix: take the sensor off, let the printer home all axes, put the sensor back.
- **Force guard**: above 1200 g the bed drops by at most 30 mm, and it drops again only after a new
  move (so drift or a hand on the sensor can't walk the bed to the bottom).
- **Bed up** is refused in the calibration jogger once the sensor feels more than 100 g.
- Only **one program** may own the printer and the camera; a refusal names the program and PID
  that holds it.

---

## 9. Troubleshooting

| What you see | Cause and fix |
|---|---|
| *No Prusa printer found* | USB cable loose or printer off. Two Prusas connected: set `PORT_OVERRIDE` in `gantry_controls.py`. |
| *printer is owned by another application process (PID …)* | Another program (often a second Touch Path Programmer) holds the printer. Close it, or end that PID in Task Manager. |
| *Waiting to …: the printer is busy with …* | Normal for a few seconds. If it says *setting up the force data (M334)*: confirm the metrics address on the printer's screen. |
| *The USB connection to the printer was lost* | The printer restarted or the cable moved. It reconnects on the next command; after a printer restart also see "Z lost" below. |
| Sensor stops far above the parts / Z values strange | The printer restarted and lost Z. Take the sensor off, home all axes on the printer, put it back. |
| The procedure stops in the air ("touched" nothing) | A false contact: see the false-alarm check; raise `[forces] contact_hold_s` (e.g. 0.3–0.4). |
| No force data / empty force graph | Firewall rule for UDP 8514 missing (run `laptop_setup.ps1` again), or confirm the metrics address on the printer's screen. |
| *Printer not reachable* (PrusaLink) | Network cable, fixed address; `ping 192.168.50.2` must answer. |
| No purge line / poor prints | Plates sliced before the fix: import the dataset again. Check the filament and the nozzle type (the profile assumes a 0.4 HF nozzle). |
| Program slow to start | Should be ~2 s now even with 40-part plates; if not, update with `git pull`. |
| *IDS peak SDK (camera driver) is not installed* | Install the SDK (setup step 5) and restart the laptop. |
| *No module named …* after `git pull` | `python -m pip install -r requirements.txt` |
| Anything hangs and you want to know why | Open `%TEMP%\prusa-direct-drive-serial.log`: every command to and reply from the printer, with time and what the program was doing. |

---

## 10. Files the program writes

| Where | What |
|---|---|
| `plates/<dataset>/` | `plate_NN.3mf`, `plate_NN.json` (positions of parts and cones), G-code, slicer logs, `summary.json`. |
| `photos/` | Single photos (**Take photo**). |
| `photos/<date_start time>/` | One measuring procedure: photos, `measurements.csv`, `procedure.json`. |
| `runs/<run name>/` | Touch programs: `run_log.json`, `images.json`, images, height map. |
| `runs/repeatability/`, repeatability CSVs | Positioning Repeatability sequences and logs. |
| `logs/runs/focusfield/` | Focus-Field scans and analyses. |
| `%TEMP%\prusa-direct-drive-serial.log` | Every serial line to and from the printer (kept below 5 MB). |
| `machine_settings.toml` | Updated by a successful calibration. |

`plates/`, `photos/`, `runs/` and the test sets are not in git.

---

## 11. For developers

- Tests: `python -m pytest` from the project folder (pytest + pytest-qt; simulated printer and
  camera). Close the Touch Path Programmer first, or the printer/camera tests find the devices in use.
- Layout of the code: `pathprogrammer/` (PySide6 app; `ui/` holds the widgets), `slicing/` (plates
  and slicing), `touch.py` (force touching), `gantry_controls.py` (USB serial link),
  `strain_gauge.py` (force data over UDP), `GUI.py` (the calibrator / visual tool), `focusfield/`
  (optics), `machine_settings.py` (settings loader). A detailed map with design decisions is in
  [CLAUDE.md](CLAUDE.md).
- Team branch: `development-bep-groep`. The firmware lives in its own repository; never clone it
  inside this folder.

---

## 12. More documentation

| Document | About |
|---|---|
| [How to start.md](How%20to%20start.md) | First-time setup, step by step. |
| [docs/connecting-the-printer.md](docs/connecting-the-printer.md) | Network cable, fixed addresses, Windows and macOS, troubleshooting. |
| [docs/calibration-plan.md](docs/calibration-plan.md) | Background and plan of the calibration (layers, accuracy, next steps). |
| [docs/focusfield.md](docs/focusfield.md) | Focus-Field MTF: setup, measurement, limits. |
| [docs/focusfield-review.md](docs/focusfield-review.md) | Review notes of the Focus-Field implementation. |
| [docs/macos-setup.md](docs/macos-setup.md) | Running on macOS (camera backend, printer). |
| [printer_network/README.md](printer_network/README.md) | The network scripts and printer settings file. |
