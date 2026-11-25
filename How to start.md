# How to start

Step by step: what you need to do the **first time** on a laptop to use the Touch Path Programmer.
After that, you simply start it with the shortcut on your desktop.

This guide assumes **Windows**. For a Mac, see [docs/macos-setup.md](docs/macos-setup.md).

| Step | What | How often |
|---|---|---|
| 1 | Install Python | once per laptop |
| 2 | Git and getting the code | once per laptop |
| 3 | Install the Python packages | once, and after a new `requirements.txt` |
| 4 | Install PrusaSlicer | once per laptop |
| 5 | Install the IDS peak SDK (camera driver) | once per laptop |
| 6 | Network cable and fixed address | once per laptop |
| 7 | Create the shortcut | once per laptop |
| 8 | First start: PrusaLink password | once per laptop |
| 9 | Check that everything works | a quick look every time |

---

## Step 1: install Python

1. Download **Python 3.12** (64-bit) from [python.org/downloads](https://www.python.org/downloads/).
2. On the first screen of the installer, tick **"Add python.exe to PATH"**.
3. Click **Install Now**.
4. Check it: open a command prompt (Windows key, type `cmd`) and type:

   ```bash
   python --version
   ```

   You should see `Python 3.12.x`.

## Step 2: Git and getting the code

1. Install **Git** from [git-scm.com](https://git-scm.com/download/win). The default settings are
   fine.
2. Get the code and switch to the team branch:

   ```bash
   git clone https://github.com/Cubey00/prusa-direct-drive.git
   ```

   ```bash
   cd prusa-direct-drive
   ```

   ```bash
   git checkout development-bep-groep
   ```

3. To get the newest version later, run this in that folder:

   ```bash
   git pull
   ```

> **Note:** do **not** put the firmware repository (`prusa-direct-drive-firmware`) in this folder or
> in the folder above it. It belongs somewhere separate.

## Step 3: install the Python packages

In the `prusa-direct-drive` folder:

```bash
python -m pip install -r requirements.txt
```

This installs everything the program uses: PySide6 (the windows), numpy, trimesh, pyserial (USB to
the printer), requests (PrusaLink), tifffile (photos) and the camera's Python packages, `ids_peak`
and `ids_peak_ipl`.

Do you later get a message like *"No module named …"*? Then run this command again: a package has
probably been added.

## Step 4: install PrusaSlicer

The program uses PrusaSlicer to slice the print plates and to show G-code in 3D.

1. Download **PrusaSlicer 2.9 or newer** from
   [prusa3d.com/page/prusaslicer_424](https://www.prusa3d.com/page/prusaslicer_424/). Older versions
   do not know the Core One.
2. Install it in the default location (`C:\Program Files\Prusa3D\PrusaSlicer`). The program looks
   for it there by itself.

Is PrusaSlicer somewhere else? Then put its path in `slicing/config.py` at `SLICER_EXE`.

## Step 5: install the IDS peak SDK (camera driver)

The Python packages from step 3 talk to the camera through the **IDS peak SDK**. It contains the
USB3 driver and has to be installed separately. Without the SDK the camera panel says:
*"IDS peak SDK (camera driver) is not installed on this computer"*.

1. Go to [ids-imaging.com](https://www.ids-imaging.com) → **Downloads**, choose your camera and
   download **IDS peak** for Windows (64-bit). Preferably choose the same series as the Python
   package (`ids_peak` 1.17).
2. Run the installer **as administrator** and leave the **USB3 Vision driver** ticked.
3. **Restart the laptop.** The installer sets the environment variable `GENICAM_GENTL64_PATH`; it
   is only seen everywhere after a restart.
4. Connect the camera to a **USB 3 port** (blue, or marked "SS").
5. If you like, check it with **IDS peak Cockpit**, which is installed with the SDK: the camera
   should be in its list.

If you do not need a camera, you can skip this step. The rest of the program works without it.

## Step 6: network cable and fixed address (for PrusaLink and the force data)

Printing from the program (PrusaLink) and the load cell's force data go over a **network cable
straight from the printer to the laptop**. The network socket in the wall at the TU does not work
for this.

The full procedure is in [docs/connecting-the-printer.md](docs/connecting-the-printer.md). In
short:

1. Connect a network cable from the printer to your laptop (if needed through a USB-to-Ethernet
   adapter).
2. Give your laptop the fixed address `192.168.50.1` once. To do so, run
   [printer_network/laptop_setup.ps1](printer_network/laptop_setup.ps1) **as administrator**. The
   script also adds the firewall rule for the force data (UDP 8514). Undo it with
   `laptop_undo.ps1`.
3. The printer is already at `192.168.50.2`. If that is no longer the case after a reset, follow
   step 1 of [docs/connecting-the-printer.md](docs/connecting-the-printer.md).
4. Also connect the printer's **USB cable** to the laptop. The program controls the printer over it
   (moving, calibrating, touching). The program finds the printer by itself. If it does not, it
   says *"No Prusa printer found"*: check that the cable is plugged in properly and the printer is
   on.

Your laptop keeps using Wi-Fi (eduroam) for the internet.

> **Firmware:** the program works with the **team firmware** (Core One, 6.10.100, with among other
> things the modified M410 stop and the limits of the spacer blocks). On a printer with standard
> Prusa firmware, moving, calibrating and touching do not work properly.

## Step 7: create the shortcut

In the `prusa-direct-drive` folder, create the shortcut once:

```bash
python tools/make_shortcut.py
```

There is now a **"Touch Path Programmer"** shortcut (with the blue logo) on your desktop and in the
project folder. Double-clicking it starts the program, without a black command window.

You can also start it without the shortcut, from the project folder:

```bash
python -m pathprogrammer
```

## Step 8: first start and setting up PrusaLink

1. Start the program with the shortcut.
2. In the **Printer (PrusaLink)** box, click **Printer settings...**
3. Enter the **password**. You find it on the printer under **Settings > Network > PrusaLink**; the
   user is `maker` and the address `192.168.50.2` is already filled in. If the printer has an
   **API key** instead of a password, enter that.
4. Click **Test connection**. You should see *"Connected: printer is …"*. Then click **Save**.

The password is only stored on this laptop, not in the repository. From now on the program connects
by itself as soon as the printer is connected. The top left of the printer box then shows
**● Connected**.

## Step 9: check that everything works

| Where | What you should see | If not |
|---|---|---|
| Printer (PrusaLink) | **● Connected** and the printer status | Cable, fixed address (step 6), password (step 8) |
| Photo control | Live camera image, **Take photo** is blue | SDK (step 5), camera on USB 3, laptop restarted |
| Print plates | After **Mini test set**: "All plates sliced" | PrusaSlicer 2.9+ (step 4) |
| Calibration | With **Start calibration**: the steps in the calibration window, no error about the printer | USB cable to the printer, printer on |

---

## After that: how to use the program

1. **Make plates.** In **Print plates**, choose the filament that is in the printer and click
   **Dataset...** (your own parts) or **Mini test set** (two small test parts). The program
   divides the parts over plates and slices them. The selected plate is loaded as the model right
   away.
2. **Print.** Take the sensor off the print head. Then click the green **Print this plate** button
   and stay with the printer until the first layer is down.
3. **Calibrate.** Put the sensor back on the head and click the blue **Start calibration** button.
   Choose **All 3 cones**, **Only cone 2** or (when the calibration is already right) **Home X and
   Y only**, and follow the steps: per cone, first **Go to cone N**, then put the sensor exactly on
   the tip with the jog buttons, and then **Send position of cone N**. Above the button you see the
   offset between the nozzle and the sensor head. Only change it if you are sure it is wrong; the
   calibration measures it itself.
4. **Measure.** In **Measure**, **Hover 1 cm above point 1** puts the sensor above the first part,
   **Move very slowly until the sensor touches the object** makes the first touch, **Go 0.1 mm
   down** presses deeper in steps, and **Go to next point** moves on to the next part.
   **Do whole measuring procedure** does it all in one go: home X and Y, then for every part on
   the plate: go above it, first touch, then the set number of steps (each **Down per step** deeper, stopping at **Max force**),
   with a photo at every step. Each run gets its own folder named after the date and start time,
   with all photos, `measurements.csv` and `procedure.json` (object, indentation and force per
   photo). STOP ends it at once.
5. **Photos.** **Photo control** always shows the live camera image. **Take photo** saves a TIFF
   in the `photos/` folder. With **Folder...** you choose another folder. Below the image you set
   the **exposure** (how long the camera collects light per image) and the **gain**. While a first
   touch exists, the photo name contains the object, the indentation and the force, and a line is
   added to `measurements.csv`. The LED lighting (5 V) is separate from the program: switch it on
   yourself.
6. **Touching.** Open **Points in ascending order** and place points on the model. Then start with
   **Execute program**. The red **STOP** always works.

## Common problems

- **"No Prusa printer found"**: the USB cable is not plugged in properly, or the printer is off.
  With two Prusas connected, choose the port with `PORT_OVERRIDE` in `gantry_controls.py`.
- **Printer not reachable (PrusaLink)**: check the network cable and the fixed address. In a
  command prompt, `ping 192.168.50.2` should get an answer.
- **No force data**: the firewall rule for UDP 8514 is missing; run `laptop_setup.ps1` again as
  administrator. If the printer asks on its screen to confirm a metrics address, confirm it.
- **"Bed up" is refused during calibration**: the sensor feels more than 100 g, so it is already
  pressing on the cone. Lower the bed a little. If it happens while the sensor is not touching
  anything, the load cell has drifted.
- **The program does not start after a `git pull`**: run
  `python -m pip install -r requirements.txt` again.
- **Settings of the setup** (work area, cones, force limits, sensor offset) are all in
  `machine_settings.toml`. Change them there, nowhere else.
