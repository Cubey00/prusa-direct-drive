# Prusa Direct Drive — Development To-Do List

**Project:** Tactile Sensing Robot — Prusa Core One  
**Repository:** [Cubey00/prusa-direct-drive](https://github.com/Cubey00/prusa-direct-drive)

**Priority:** P0 = critical/blocking · P1 = required · P2 = optional improvement

---

## 1. Printer safety and force monitoring — P0

- [ ] **Fix emergency-stop and emergency-retraction logic**
  - Review `emergency_drop()` in `gantry_controls.py`.
  - Ensure emergency movements stop when force has been released.
  - Verify STOP functionality during every movement phase.
  - Test emergency behavior on the physical printer.

- [ ] **Implement a force telemetry watchdog**
  - Detect missing or stale UDP load-cell measurements.
  - Stop indentation when force data becomes unreliable.
  - Ensure force limits remain active during all touch operations.
  - Reject measurements with invalid force data.

- [ ] **Verify displacement and force measurement accuracy**
  - Check the accuracy of commanded versus achieved Z displacement.
  - Verify load-cell calibration, noise and response delay.
  - Determine a suitable force safety limit.
  - Confirm that indentation measurements remain consistent across repeated touches.

**Completion:** Indentation is performed reliably, with functioning force monitoring and emergency protection.

## 2. Printer firmware and usable build plate — P0

- [ ] **Determine the reduced working area**
  - Measure the maximum reachable X/Y positions with the sensor installed.
  - Account for the sensor assembly's dimensions and required clearance.
  - Define the restricted build-plate dimensions.
  - Determine whether printing and sensing require different movement limits.

- [ ] **Modify firmware homing**
  - Identify movements in the original homing sequence that become unsafe with the sensor installed.
  - Modify the sequence for the reduced movement range.
  - Verify homing behavior with the relevant tool configurations.
  - Preserve the custom firmware's immediate `M410` functionality.
  - Test homing from multiple initial positions.

- [ ] **Update software motion limits**
  - Update `pathprogrammer/config.py`.
  - Enforce the reduced X/Y movement boundaries.
  - Validate measurement positions against the new limits.
  - Ensure safe clearance during moves between objects.

**Completion:** Printer homing and movement are safe with the sensor installed, and software and firmware use consistent limits.

## 3. Three-point calibration and coordinate systems — P0

- [ ] **Replace two-point calibration with three-point calibration**
  - Update `pathprogrammer/calibration.py`.
  - Update the calibration GUI to accept three points.
  - Update communication with the visual calibrator.
  - Require three non-collinear calibration points.
  - Calculate the coordinate transformation using all three points.
  - Calculate calibration residuals and reject unacceptable errors.
  - Update program serialization and automated tests.

- [ ] **Implement separate nozzle and sensor coordinate systems**
  - Define nozzle-to-sensor X/Y/Z offsets.
  - Store and apply the sensor coordinate transformation.
  - Convert printed-object coordinates into sensor measurement coordinates.
  - Allow sensor offsets to be recalibrated.
  - Apply the reduced movement boundaries after transformation.

- [ ] **Validate coordinate accuracy**
  - Use a reference object with known geometry.
  - Measure positions not used during calibration.
  - Quantify positional errors.
  - Repeat after removing and reinstalling the sensor.

**Completion:** The sensor can accurately reach locations defined relative to the printed object.

## 4. Manual sensor transition — P1

- [ ] **Implement a manual tool-change waiting stage**
  - Move the printer to a safe parking position after printing.
  - Wait for safe cooldown conditions.
  - Display instructions for exchanging the printhead and sensor.
  - Pause the program until the user confirms completion.
  - Support printer shutdown and reconnection if required.

- [ ] **Verify the system after exchanging tools**
  - Confirm the sensor is installed.
  - Restore or recalibrate the appropriate coordinate system.
  - Verify safe printer positioning.
  - Verify the load cell and camera are available.
  - Prevent measurement from starting when required checks fail.

**Completion:** Printing can be followed by a manual sensor change without losing the coordinate reference or requiring a full restart of the experiment.

## 5. STL support and measurement planning — P1

- [ ] **Support STL files**
  - Extend `pathprogrammer/model_io.py` to accept `.stl`.
  - Update the file selection dialog.
  - Support model rotation and visualization.
  - Add explicit unit handling for STL files.
  - Verify touch-point positioning with STL geometry.
  - Add automated tests for both STL and 3MF.

- [ ] **Improve measurement-point planning**
  - Check whether selected points are geometrically valid.
  - Validate sensor reachability and clearance.
  - Calculate measurement positions using the calibrated sensor offsets.
  - Allow individual measurement points to specify indentation depths.
  - Support measurement plans for multiple printed objects.

**Completion:** STL and 3MF objects can be loaded and used to generate valid indentation-based measurement programs.

## 6. Indentation-based touch control — P1

- [ ] **Make indentation-based touching the default**
  - Set depth mode as the default in `pathprogrammer/program.py` and the UI.
  - Keep force-controlled touching as an optional alternative.
  - Allow each point to specify indentation depth and speed.
  - Preserve force monitoring during both modes.

- [ ] **Improve the indentation procedure**
  - Detect initial contact with the object.
  - Establish the contact Z position.
  - Move to the requested indentation depth.
  - Record the achieved displacement and resulting force.
  - Support a configurable holding period at the target indentation.
  - Retract safely before moving to the next point.

- [ ] **Validate indentation accuracy**
  - Compare requested and achieved indentation.
  - Repeat measurements at multiple depths and speeds.
  - Determine contact-detection uncertainty.
  - Measure force variation at a fixed indentation.
  - Investigate effects of polymer deformation, relaxation and hysteresis.

**Completion:** Depth-controlled indentation is accurate and repeatable, with corresponding force data recorded throughout each measurement.

## 7. Camera support and integration — P1

- [ ] **Maintain the existing IDS camera backend for Windows and Linux**
  - Preserve `ids_camera.py` and its IDS SDK integration.
  - Verify the IDS camera's operation on both platforms.
  - Ensure subsequent camera changes do not break existing functionality.
  - Keep full-resolution capture and configurable exposure/gain.

- [ ] **Support a separate macOS camera backend**
  - Continue the planned [`scientific-camera-capture`](https://github.com/stefberends/scientific-camera-capture) integration.
  - Implement a platform-independent camera interface.
  - Select the appropriate camera backend without changing measurement logic.
  - Keep camera driver dependencies optional where possible.
  - Test macOS camera functionality independently.

- [ ] **Integrate camera acquisition into the path programmer**
  - Connect camera capture to the indentation procedure.
  - Support image capture at the target indentation.
  - Store indentation depth, measured force and image identifiers together.
  - Support multiple images per measurement point.
  - Record camera settings and capture timestamps.

**Completion:** The measurement procedure can capture and save images with their corresponding indentation and force data, while retaining IDS support on Windows/Linux and separate macOS compatibility.

## 8. Moving versus stationary image acquisition — P1

- [ ] **Test stationary image acquisition**
  - Reach the requested indentation depth.
  - Stop movement and allow the system to settle.
  - Capture images while holding indentation.
  - Record force changes during the holding period.
  - Measure image sharpness and fringe visibility.

- [ ] **Test image acquisition during movement**
  - Capture images while moving at controlled indentation speeds.
  - Record the actual Z position during acquisition.
  - Record force throughout movement.
  - Evaluate motion blur, image distortion and force variation.
  - Assess timing uncertainty between exposure, position and force.

- [ ] **Compare both approaches**
  - Compare image quality.
  - Compare accuracy of corresponding displacement and force labels.
  - Compare measurement duration and throughput.
  - Test different exposure times, movement speeds and indentation depths.
  - Determine whether a hybrid approach is beneficial.

- [ ] **Implement the selected method**
  - Define acceptable image-quality and measurement-error thresholds.
  - Configure the selected acquisition procedure in the path programmer.
  - Verify reliability across repeated measurements.

**Completion:** The team has experimentally determined whether imaging should occur during movement or at standstill, with reliable displacement and force labels in either case.

## 9. Sensor-change repeatability — P1

- [ ] **Design a repeatability test**
  - Select a known reference object.
  - Establish baseline position and indentation measurements.
  - Define an adequate number of removal/reinstallation cycles.
  - Agree on acceptable positional and measurement errors.

- [ ] **Measure repeatability**
  - Repeatedly remove and reinstall the sensor.
  - Measure deviations in X, Y and Z.
  - Compare measured forces at identical indentation depths.
  - Check image alignment and focus stability.
  - Evaluate whether the mounting mechanism introduces systematic errors.

- [ ] **Determine calibration requirements**
  - Compare measurements before and after recalibration.
  - Determine whether calibration is needed after every exchange.
  - Define acceptable tool-offset drift.
  - Add the necessary verification steps to the tool-change workflow.

**Completion:** Reinstallation accuracy is quantified and the minimum required recalibration procedure is known.

## 10. Experiment automation and dataset generation — P1

- [ ] **Integrate the complete print-to-measurement procedure**
  - Select and identify the specimen model.
  - Record the printed object's position and orientation.
  - Detect or confirm completion of printing.
  - Pause for the manual tool exchange.
  - Apply the sensor coordinate transformation.
  - Execute the indentation measurement program.
  - Acquire images at the selected measurement conditions.

- [ ] **Implement structured experiment data storage**
  - Assign experiment and specimen identifiers.
  - Store sensor and calibration configurations.
  - Record requested and achieved indentation depth.
  - Record measured force and force traces.
  - Associate each image with its correct measurement point.
  - Record failures, interrupted measurements and unusable images.

- [ ] **Perform an end-to-end validation experiment**
  - Print a known test object.
  - Manually exchange the toolhead for the sensor.
  - Calibrate the sensor coordinates.
  - Perform a series of indentation measurements.
  - Acquire labeled images.
  - Verify all stored positions, indentation depths, forces and images.
  - Repeat the experiment to check reproducibility.

**Completion:** The system reliably generates labeled tactile datasets from printed objects.

---

## Final acceptance checklist

- [ ] Safe emergency stopping and force telemetry watchdog.
- [ ] Modified firmware homing verified.
- [ ] Reduced usable build-plate area enforced.
- [ ] Three-point calibration operational.
- [ ] Nozzle and sensor coordinate systems calibrated.
- [ ] Manual sensor-transition waiting stage operational.
- [ ] STL and 3MF support operational.
- [ ] Indentation-based touching is the primary measurement mode.
- [ ] Achieved indentation and force measurements validated.
- [ ] IDS camera support preserved for Windows and Linux.
- [ ] Separate macOS camera integration implemented and validated on target hardware.
- [ ] Images linked to corresponding indentation and force data.
- [ ] Moving versus stationary image acquisition evaluated.
- [ ] Sensor-change repeatability measured.
- [ ] Complete print-to-measurement procedure validated.
- [ ] Reliable and correctly labeled dataset generated.

**Scope:** Tactile sensing hardware integration, printer control and automated data acquisition. Machine-learning model training remains outside the scope.
