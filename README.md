# prusa-direct-drive
A python program to directly control the gantry of a PRUSA Core One printer for the purposes of materials research.
Parts of this program depend on custom firmware for the Core One which I have not uploaded yet.

Start the integrated desktop application with `python -m pathprogrammer`.
The **Focus-Field MTF** tab adds optical chart generation, preview, simulated or guarded Z scans,
and offline analysis alongside the existing printing, plates, calibration and touch tools.
See [Focus-Field setup and measurement limits](docs/focusfield.md) before using physical hardware.

Generate the supplied multi-region optical target with:

```sh
python generate_focus_chart.py --output focus_field_chart.svg --field-width 60 --field-height 45
```

This creates both the vector SVG and its physical-coordinate JSON layout. Measure the actual field
of view and print scale; the default 60 × 45 mm field is a starting example.

Install `requirements.txt` and run `python -m pytest` for regression and simulated hardware tests.

For the macOS camera backend and setup, see [macOS compatibility](docs/macos-setup.md).
