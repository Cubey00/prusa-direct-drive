# Core One homing: how it works, and options for changing it

## Part 1: How homing works

**This Core One homes without any endstop switches, and nothing was changed to work this out.** X and Y use motor stall detection against the mechanical limits, then a refinement that snaps the position to the motors' electrical phase. Z uses the nozzle loadcell touching the bed. This comes from reading the code for the `COREONE` / `XBUDDY` build configuration in `build/coreone_release_boot`; I didn't run anything on a printer.

## G28 sequence
This is the sequence for a plain `G28` ([G28.cpp:386](lib/Marlin/Marlin/src/gcode/calibrate/G28.cpp#L386)):

1. **Prepare:** it turns off phase stepping, pressure advance and bed leveling, and sets XY acceleration to 1250 mm/s².
2. **Raise Z:** it raises to Z = 4 mm if lower, never moving down. If Z isn't homed, this is a stall-guarded move at 19 mm/s.
3. **Home Y, then X:** X and Y are always homed together, and the printer moves to Y = 15 just before homing X.
4. **Refine XY:** this is the precise phase-grid step described below.
5. **Home Z:** the nozzle moves to X240 Y10 and probes the bed.
6. **Detect the sheet:** it probes at X245 Y0 and makes up to 3 attempts before warning you.
7. **Finish:** it moves to Z = 2 mm and restores the earlier settings.

## XY: coarse homing
The home corner is X max, Y min, which sets the position to X = 252, Y = −19 ([motion.cpp:1212](lib/Marlin/Marlin/src/module/motion.cpp#L1212)). Each axis does this:

1. Move 0.5 mm away from the corner (10 mm on retries).
2. Run at 70 mm/s until a motor stall stops the move.
3. Retreat 10 mm, then run in again at 70 mm/s.
4. Accept if the two stops agree within ±1 mm, with up to 10 attempts before a homing error.
5. Set the position and back off 2 mm.

Stall detection is only armed above about 56 mm/s, so the head needs some run-up before a stall can register ([tmc_util.cpp:1112](lib/Marlin/Marlin/src/feature/tmc_util.cpp#L1112)). This stage only marks the axis as `imprecise`.

## XY: precise refinement
A stall point alone isn't very repeatable, so `corexy_home_refine` uses it only to work out which electrical cycle each motor is on ([homing_corexy.cpp:1192](lib/Marlin/Marlin/src/module/prusa/homing_corexy.cpp#L1192)):

1. **Align:** move about 5 mm from the corner on both axes and align both motors to an exact phase angle (45°).
2. **Measure:** hold motor B at 900 mA and drive only motor A at 650 mA and 70 mm/s, in both directions. One direction stalls against the X limit and the other against the Y limit.
3. **Convert:** the two step counts give the distance to the corner in electrical cycles for each motor.
4. **Round:** subtract the calibrated fractional origin from EEPROM and round to whole cycles.
5. **Validate:** repeat from a second point. If the results disagree, re-home and retry, up to 6 times.
6. **Set the position:** it is calculated from those whole cycle counts, so stall noise smaller than half a cycle has no effect.

One cycle is 32 microsteps, which is about 0.16 mm in both X and Y per motor at 100 steps/mm.

**Calibration:** this runs if no calibration is stored, if `G28 C` forces it, if retries run out, or if at least 6 of the last 10 homes were unstable.
- **Stall sensitivity:** it tries values −6 to −4 and stores the best.
- **Grid origin:** it measures 5 grid points over 2–10 passes and fits the origin.
- **Prompt:** by default it asks first, and proceeds if there is no answer within 60 s.
- **Related commands:** the Homing Calibration menu item sets the ask/always/never behaviour, `G28 D` disallows calibration, and `M9202` clears the stored origin but not the sensitivity.

## Z: loadcell homing
Z home is Z = 0, and the Z motor never needs to stall to set it ([motion.cpp:836](lib/Marlin/Marlin/src/module/motion.cpp#L836)):

1. **First touch:** descend at 19 mm/s to first contact, then rise 2 mm.
2. **Two slow touches:** descend at 70 mm/min (about 1.17 mm/s) twice, rising 2 mm between them.
3. **Before each descent:** wait 300 ms, then zero (tare) the loadcell.
4. **Trigger:** the filtered load reaches the −40 threshold, in the loadcell's scaled units (presumably grams).
5. **Check:** the two slow touches must agree within ±0.1 mm, with up to 10 attempts.

A Z motor stall is also OR-ed into the same "Z min" signal ([hwio_pindef.h:573](src/common/hwio_pindef.h#L573)). It should only be able to fire on the fast descent, because the threshold of 400 is out of reach at the slow speed (my calculation, assuming a 12 MHz driver clock).

The sheet check probes down to Z = −1, and no contact by then counts as "no sheet". This implies the bed is more than 1 mm lower at that spot without a sheet, but that's my inference from the code.

## Hardware it depends on

| Hardware | Connection | Role |
|---|---|---|
| X and Y steppers with TMC2130 drivers | SPI, step and dir (X: PD7/PD6, Y: PD5/PD4) | Stall detection and reading the motor phase (`MSCNT`) |
| X and Y `DIAG` lines | PG9, PE13 | Act as the "endstops": min and max for an axis share one pin |
| Mechanical limits of the X and Y travel | None | The hard stops that cause the stall |
| CoreXY belts and pulleys | None | Tension and friction affect stall points and the fitted origin (my inference) |
| Z stepper with TMC2130 driver | `DIAG` on PB4 | Stall detection for upward moves and at the ceiling |
| Loadcell and HX717 ADC | PE7 data, PG1 clock, PE3 soft interrupt | Bed contact at 320 samples/s; the ADC also serves the filament sensor, but is dedicated to the loadcell while probing |
| Nozzle and print sheet | None | The contact surfaces, so hanging cold filament can cause false sheet failures (why the check retries) |
| EEPROM config store | None | Grid origin, stall sensitivity and instability history |

## What this means in practice
- **Loadcell faults:** a tare under load is a fatal error, and missing or stale samples stop the probe, which is then retried if the loadcell recovers.
- **Mechanical changes:** drag or belt changes can shift the stall points. Moderate shifts should show up as unstable homes and eventually trigger recalibration.
- **Whole-cycle shifts:** a stall shift of more than half a cycle could round to a neighbouring cycle, moving home by about 0.16 mm per cycle in X and Y. The second measurement point only catches this if the two points disagree.

## What I couldn't confirm
- **Which `DIAG` line stops a move:** on a pure X or Y move, stall detection is armed on both drivers, and the code appears to let either one stop it. An A-only measurement would use only `X_DIAG`. This depends on how the stepper's "axis moving" flags are set, which I didn't trace to the end.
- **Motor type:** 100 steps/mm at 8 microsteps suggests 400-step motors with a 16-tooth pulley, but a config comment says "200 step". The firmware doesn't settle it, though the cycle logic works either way.
- **Belt option:** I didn't check whether the 1.5GT belt setting (101.587 steps/mm) applies to your printer.
- **Your uncommitted `M410` change:** it doesn't alter the homing sequence, but it keeps the homed state after a quick stop even though steps may have been skipped. Later moves could then be offset until the next real home. I didn't trace an `M410` sent during a `G28`.

---

## Part 2: Feasibility of changing the home corner / stops

**All of your options are feasible. I'd extend the stops with rigid spacers and leave the home corner where it is.** A different corner is also realistic, and Prusa's own firmware has a precedent for exactly your problem. I've only read the code, so none of this is tested on a printer.

## The key constraint
Moving the home corner only fixes the collision during homing. The attachment can still hit that wall during normal travel, so that side needs a reduced limit whichever option you choose. **The print-area loss is therefore the same with any option.**

Nozzle travel versus the 250 × 220 mm bed shows how much room you have to give up before losing print area:

| Side | Nozzle limit | Spare travel beyond the bed |
|---|---|---|
| Right (home side) | X = 252 | 2 mm |
| Left | X = −2 | 2 mm |
| Front (home side) | Y = −19 | 19 mm |
| Back | Y = 221 | 1 mm |

So the front can give up to 19 mm without losing bed, but anything you lose on the other sides quickly cuts into the print area. Check the slicer's purge line first, because it may use the area in front of the bed.

## Option comparison

| | Spacers on the stops | Home in another corner | Swap one axis only |
|---|---|---|---|
| **Firmware work** | Small: new limits and probe points | Moderate: directions, limits, self-test, probe points | Moderate, as for another corner |
| **Calibration path** | Same as stock | Same motor measured (A) | Measures motor B instead, a path used by XL and iX but not stock Core One |
| **Attachment protected from the wall** | Yes, physically | Only by software limits on the old side | Only by software limits on the unstopped sides |
| **Main risk** | Spacer stiffness and creep | Unknown stall behaviour at the new walls | Same, plus the untested path on this machine |

## Option 1: spacers on the rail ends
Home position comes from the travel limits (`X_HOME_POS` is `X_MAX_POS`, for example), so most dependent values follow automatically. That includes park points and self-test lengths.

**Firmware changes:**
- Set `X_MAX_POS` and/or `Y_MIN_POS` to the new measured stop positions.
- Keep X = 0 and Y = 0 at the bed origin, so only the limits change.
- If usable travel falls short of the bed, reduce `X_BED_SIZE` / `Y_BED_SIZE` too. The mesh grid is derived from them, and its points must stay reachable.
- Move the Z homing and sheet-check points if needed (see the trap below).

**Mechanical requirements:**
- **Stiffness:** precise homing depends on a repeatable stall at 70 mm/s. A soft spacer makes that stall less repeatable. Creep in a warm chamber could also shift the calibrated origin over time, so I'd avoid plain printed plastic at first.
- **Keep the gantry square:** if the Y stop acts on the gantry at both rails, use matching spacers, because uneven ones could twist it.
- **Contact point:** the spacer has to stop the carriage or gantry, never the attachment.
- **Recalibrate:** clear the old origin with `M9202`, then run `G28 C`, which also redoes the sensitivity calibration.

The new stop coordinate also has to be measured accurately, because any error directly offsets prints on the bed.

## Option 2: another corner
Prusa has already done this on the Core One L with an INDX head. Its config says homing is inverted because the print fan "is bumping to the motor" on the right ([Configuration_COREONEL.h:807](include/marlin/Configuration_COREONEL.h#L807)). Precise homing is also used with all four direction combinations across the printer range, though not all on this frame.

**Firmware changes:**
- Change `X_HOME_DIR` / `Y_HOME_DIR`.
- Measure new `X_MIN_POS` / `Y_MAX_POS` values. The current ones are only soft limits, and the INDX values are for different hardware, so neither can be reused as stop positions.
- Flip the self-test X direction, which is hardcoded by `has_indx` ([selftest_COREONE.cpp:55](src/common/selftest/selftest_COREONE.cpp#L55)), and check the Y test too.
- Review `HOMING_PREEMPTIVE_MOVE_Y`, which currently moves to Y = 15 before homing X.
- Possibly retune the stall sensitivity of −2, which is what coarse homing uses.

**What only testing can answer:** the left and back stops may not give a clean, repeatable stall, and cables may interfere at the back. Refinement also needs a clean stall on both walls from about 5 mm away.

**Remaining exposure on the old side:** only software limits protect the attachment there. Self-test may run the axis toward the wall, which I didn't confirm. Power-panic parking also deliberately widens the soft limits and can drive X at 200 mm/s toward an end ([power_panic.cpp:723](src/common/power_panic.cpp#L723)). A spacer is what covers this, which is why I'd add one on any side the attachment overhangs.

## Traps with either option
- **Z homing can be silently skipped:** if the probe point is out of reach, `home_z_safely` returns success without probing ([G28.cpp:213-233](lib/Marlin/Marlin/src/gcode/calibrate/G28.cpp#L213-L233)), and the sheet check does the same. With stock points, this happens once the X limit drops below 245 for the sheet check (a 7 mm loss) or 240 for Z homing (12 mm).
- **New sheet-check point:** the INDX build uses X0 Y0 with Z homing at X10 Y10. It may also work on your bed, but the sheet check depends on the bed geometry at that spot, so test with and without a sheet.
- **Out-of-range moves may be clamped, not rejected:** motion limits are applied to moves, so a G-code file sliced for the full bed could print distorted instead of failing. I'd update the slicer's bed shape to match.
- **Added weight:** the attachment changes the resonance, stall behaviour and possibly the loadcell. If it sits on the part the loadcell senses, cable drag or vibration could cause false Z triggers. Redo input shaper and phase stepping calibration.

## Options I'd avoid
- **Using the attachment as the stop:** it takes the impact on every home, and its mount becomes part of your positioning accuracy.
- **Adding physical switches:** the refinement is built around motor stalls and phase, so you'd be replacing the core of the homing system.

## What decides this
- **Which sides does it overhang, and by how much?** Measure from where the carriage currently stops. If it only overhangs the front by up to 19 mm, you lose no bed and this becomes nearly trivial. Overhang on the right loses print width beyond about 2 mm, plus the probe-point change from 7 mm.
- **Is it mounted on the part the loadcell senses, and what does it weigh?** This could affect Z homing, which is a bigger issue than the XY stops.

With those numbers I can work out the exact limits, the remaining print area and the list of config changes.
