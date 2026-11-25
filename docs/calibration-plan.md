# Calibration plan: from printed plate to calibrated sensor

**Goal.** After printing and swapping from the print head to the sensor, you click **Calibrate**. The sensor then finds the three printed cones F1, F2 and F3 by itself and determines where the plate lies (X/Y) and how high and how tilted the bed is (Z). It checks the result and only then allows measuring.

This plan builds on the design report (*Plan van aanpak, Tactile Sensing Robot*). From the morphological chart, "print calibration points and measure them before the real measurements" was chosen. The report also requires that the sensor ends up in the same place after taking it off and putting it back, that there is as little play as possible and that as much of the bed as possible stays usable.

All numbers that depend on the setup are in **[`machine_settings.toml`](../machine_settings.toml)**: the offset from nozzle to sensor, the reach with the stops, the camera scale, the cones and the force limits. The code reads them from there. See the [last chapter](#the-settings-file).

---

## What already exists

**Since 16-11-2026:** the **Start calibration** button (path programmer, *Calibration* box) opens a window that asks only one thing at a time per cone: *Go to cone N* (the head drives to the spot the sensor offset from `machine_settings.toml` predicts; first the bed to a safe height, then X/Y), then a slow jogger (5 to 0.05 mm) to put the sensor exactly on the tip, and *Send position of cone N*. After cone 3, *Calibration successful! … Position known* follows if the fit is right; the measured sensor offset is then saved (step A1). Since then there are also **Only cone 2** (shift only) and **Home X and Y only** (re-use the saved calibration). Z (B2/B4) and automatic XY (B3b/c) are still to come.


| Part | Where |
|---|---|
| Plate with F1 (front left), F2 (front right) and F3 (back left); the manifest knows their bed coordinates and tip height (5 mm) | `slicing/`, `plates/<set>/plate_NN.json` |
| Open the plate in the path programmer; the face points of F1–F3 are filled in from the manifest | *Open plate in programmer* |
| Send the machine position per point from the calibrator (actual position from the metrics) | `GUI.py`, F1/F2/F3 buttons |
| XY fit over 3 points (least squares), residual per point, check for points on one line | `pathprogrammer/calibration.py` |
| Touching with the load cell: fast approach, back off, slow search again, contact Z | `touch.py` |
| Print status through PrusaLink: we know when the print is done | `prusalink.py`, Printer box |

What is missing: the Z calibration (bed plane), finding the points automatically, the camera image, acceptance criteria that **block** measuring, saving the calibration per plate and a drift check.

---

## Overview: three layers

| Layer | How often | What |
|---|---|---|
| **A. Setup** | Once, and after another mount, another polymer or other stops | Nozzle–sensor offset, camera scale, load cell scale, reach |
| **B. Plate** | After every swap, for every plate: the **Calibrate** button | Find F1–F3, XY transformation, Z plane, check, save |
| **C. Drift** | While measuring | Touch one point again now and then; pause on a deviation |

---

## A. Calibrating the setup (rarely)

The results go into `machine_settings.toml`. Set `measured = true` there, and the software stops warning.

### A1. Nozzle → sensor offset (`[sensor] offset_x/y/z`)

This offset is **not a requirement** for a good plate calibration: the F1–F3 fit takes any shift into account by itself. It is needed, though, to:
- **predict** where the cones are, so the sensor can drive there by itself;
- determine the **reach** of the sensor. That is not the same as the reach of the nozzle;
- know the right **height**. The sensor sits lower or higher than the nozzle.

The measurement comes for free from step B. The nozzle printed the cones at their bed coordinates (from the manifest). If the sensor finds F1 at machine position (X, Y), the offset is `(X − F1.x, Y − F1.y)`. The offset in Z is `contact Z on the tip − 5 mm`. The software logs this offset with every calibration. After a few plates, you put the average in the settings file. The first time, you jog to F1 by hand, see B3a.

### A2. Camera scale (`[camera] mm_per_px`, `centre_px`)

- **Scale**: press the sensor lightly on a cone tip, shift 1.00 mm in X and then 1.00 mm in Y and press again. The shift of the fringe centre in pixels gives the mm per pixel and whether the image is rotated.
- **Centre**: the pixel where the contact point is when the sensor is straight above the tip. You find it with B3c (tactile search) or by approaching symmetrically.
- **Redo** after changing the polymer or adjusting the lens. The twist cap with the PMMA rim or the conical centring (see the report, VIII-A.2) should make this repeatable. B5 checks that.

### A3. Load cell scale

Already exists: *Weight calibration…* in `GUI.py` fits the points; the result goes into `calibration.json` by hand.

### A4. Reach with the stops (`[work_area] machine_x/y`)

Jog with the sensor mounted to the four extremes and note M114. The software then refuses measuring points outside this reach.

---

## B. Calibration per plate: the **Calibrate** button

### Procedure for the user

```
Print done (PrusaLink: FINISHED)
  -> "Swap to the sensor": instructions; wait until the bed has cooled down
  -> user confirms the swap
  -> [Calibrate]   progress per point: F1, F2, F3 (+ check point)
  -> result: residuals, tilt, nozzle-sensor offset, green/orange/red
  -> [Accept] or [Again]
  -> measuring is allowed
```

### B0. Checks beforehand (calibration blocks on red)

- The printer is done and no longer busy: PrusaLink gives `FINISHED`, `IDLE` or `STOPPED`.
- Fresh load cell data is coming in. Look at the age of the last sample, not only at the average (see the task plan, §1 "watchdog").
- The camera gives an image. That is only needed from B3b on.
- A manifest is loaded and the plate is open.
- The bed has cooled below a limit, for example 35 °C, so the polymer does not heat up.
- A warning appears if `machine_settings.toml` still contains `measured = false`.
- **Never `G28` with the sensor mounted.** Z homing uses the nozzle's load cell. If the sensor hangs lower, it hits the bed first. So the machine coordinates from after the print stay the basis.

### B1. Safe height

First lower the bed (higher Z) until above the highest cone plus the sensor's protrusion, and only then drive in XY.

### B2. To each cone and measuring the height

- **Predicted position** = bed coordinate from the manifest + nozzle–sensor offset (A1). After the first plate, use the previous calibration, which is more accurate.
- **Touch the tip lightly** with `touch()` and a low target force, for example 10–20 g. Use the slow search speed. That way the polymer is not damaged and the tip does not deform.
- **Keep the contact Z** per point. It is needed for the Z plane.

### B3. Determining the XY of the tip

There are three methods. Build them in this order; the later ones do not make the earlier ones unnecessary.

| | Method | Accuracy | Needs |
|---|---|---|---|
| a | **By hand**: jog with a live camera image and a crosshair, then *Send position* | Depends on the user, ±0.1 mm | Camera image in the calibrator (currently off in `GUI.py`) |
| b | **Camera, automatic**: press lightly on the tip. The fringe pattern is concentric around the contact point. Compute the centroid in pixels, convert to mm (A2), correct and repeat until the deviation is less than 0.05 mm (at most 3 times) | ±0.05 mm expected | A2 and image processing |
| c | **Tactile search, without camera**: measure the contact Z on a 3×3 grid around the predicted spot (step 0.5 mm). The tip is where the contact Z is highest; fit a paraboloid | ±0.1 mm expected, but about 9 touches per point | Only the load cell |

Method c is a good **fallback** and an independent **check** of b.

### B4. Fit

- **XY**: the existing similarity transformation (shift, rotation, scale), from bed or face to machine.
- **Z**: a plane through the three contact heights of the tips. A tip is 5 mm above the bed. That gives the bed height at every (x, y), and so the **expected contact height of every part** = plane + part height from the manifest. That goes to `touch()` as `expect_z`, so the fast approach can stop close to the part.

### B5. Acceptance

The criteria are proposals; decide on them together. They go into `[calibration]` in the settings file.

| Check | Green | Red (blocks measuring) | Why |
|---|---|---|---|
| Residual per point | < 0.2 mm | > 0.5 mm | Wrong point or bad measurement |
| Scale \|s − 1\| | < 0.3 % | > 2 % | The printer and the plate must be at scale 1 |
| Rotation | < 1° | > 3° | The plate cannot really be rotated: points swapped? |
| Distances F1–F2 (230 mm) and F1–F3 (80 mm) | < 0.2 mm | > 0.5 mm | Direct check against the geometry |
| Bed tilt over the plate | < 0.3 mm | > 1 mm | Plate not flat or sensor tilted |
| Change in nozzle–sensor offset compared to last time | < 0.3 mm | > 1 mm | Sensor mounted differently? (repeatability, task plan §9) |

Now a residual that is too large only gives a **warning**. The task plan asks for **refusing** (§3), so red should block measuring.

### B6. Check on a point that is not in the fit

The task plan asks for "measuring positions that were not used in the calibration" (§3). Proposal: a **fourth cone F4 at the back right**. It is only for checking and does not go into the fit. The software predicts where F4 is, measures it and logs the error. That is the most honest number for the accuracy of the calibration. F4 costs almost nothing: an extra cone in `slicing/` and an extra field in the manifest.

### B7. Saving

Save `plates/<set>/plate_NN_calibration.json` next to the plate, with:
- time, mount, polymer or sensor configuration, method used (a/b/c);
- per point: predicted and measured XY and Z, number of attempts, residual;
- the transformation (a, b), the Z plane, the nozzle–sensor offset and the error on the check point;
- a copy of the `machine_settings.toml` values used.

Every measurement in the run log refers to this file. Then you can always trace a dataset back to the calibration it was made with.

---

## C. Drift while measuring

The report names as options: calibrating on a deviation, per time unit or after X measurements. Combine them:
- After every **N measurements** or **T minutes**, touch F1 again, only contact Z and the camera centroid. That takes a few seconds.
- Deviation larger than the threshold (for example 0.1 mm in Z or 0.2 mm in XY): **pause** and suggest calibrating again.
- Set N, T and the thresholds in `[calibration]`.

---

## Accuracy (estimate, still to be measured)

| Source | Size | Remark |
|---|---|---|
| Printer positioning | ~0.01 mm | The report states 0.01 mm |
| Mount deflection | 3–6 µm at 20 N | FEM from the report, negligible |
| FDM cone tip | 0.1–0.2 mm | A printed tip is rounded; the centroid can still be found well |
| Contact detection Z | ~0.01–0.05 mm | At the slow search speed; the load cell delay determines this |
| Camera centroid | a few pixels | Depends on A2 |
| Swap repeatability | unknown | Task plan §9: measure it |

**Proposed target:** XY better than 0.2 mm and Z better than 0.05 mm on the check point F4.

---

## Recommendations for the cones (`[fiducials]`)

- **Give the tip a rounded point** (a sphere with a radius of about 1 mm) instead of a sharp point. A sharp point can cut into the polymer and gives an irregular fringe pattern. A small sphere gives a Hertz contact with **concentric fringe rings**: easy to centre with the camera (B3b), and for the tactile search (B3c) it also gives a smooth top.
- Keep them **5 mm high**, higher than most test parts, so the sensor housing touches nothing else.
- **Touch them lightly** (10–20 g): the calibration must not deform the cones.

---

## Build order

1. **Done:** `machine_settings.toml` and `machine_settings.py`. The existing config reads from them.
2. **Logic without hardware**, with tests:
   - Z plane from three contact heights;
   - `expect_z` per part;
   - the acceptance criteria of B5 (red blocks);
   - the saving of B7.
3. **Calibrate button in the path programmer**, semi-automatic at first: Z touched automatically (B2), XY by hand (B3a). Plus the checks of B0, including the watch on fresh load cell data.
4. **Camera:**
   - image in the calibrator (IDS on Windows, the macOS backend on the Mac);
   - computing the fringe centroid;
   - the routine for the camera scale (A2).
5. **Automatic XY** with the camera (B3b), with tactile search (B3c) as fallback and check.
6. **Fourth cone F4** in `slicing/` (B6).
7. **Drift check** while measuring (C).

## Open questions for the team

- **Which mount** will it be: side mount or fan mount V2? That determines the nozzle–sensor offset and the reach.
- **Does the camera axis coincide with the contact point?** The camera looks through the twist cap, so probably yes. Otherwise a camera–contact point offset is needed too.
- **After the print, the end code switches off the X/Y motors** (`M84 X Y E`). The head can then shift during the swap. The calibration handles a fixed shift, but a shifted head can also run into the stops. Options: remove `M84 X Y` from the end code, or home only X/Y after the swap. The latter is only allowed if the firmware does that safely with the stops.
- **How firmly may the polymer touch the cone tip?** That determines the calibration force.

---

## The settings file

**Yes, one file is a good idea**, with a clear boundary:

| Belongs in it | Does not belong in it |
|---|---|
| Values that depend on the **physical setup** and that the team changes on purpose: nozzle–sensor offset, reach with the stops, cones, raster, force limits, camera scale, acceptance limits | **Measurement results** per plate or session: those go into `plate_NN_calibration.json` and the run logs |
| | **Settings in the code** that nobody needs to change (tuning of `touch.py`, regexes) |
| | **Passwords** (PrusaLink): those stay local in the app settings |

**Why TOML?**
- It allows comments, it reads like a form, and changes are easy to see in git.
- Python reads it without an extra library (`tomllib`).
- The file is checked at start-up. A typo, a missing value or something that does not fit (a cone that does not fit in the border, force limits in the wrong order) gives a clear error message with section and key right away. You do not get strange behaviour halfway through a measurement.

**Another setup** (for example a second printer or the fan mount) uses its own file through `PRINTERPROJECT_SETTINGS=path/to/other.toml`.
