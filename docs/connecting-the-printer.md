# Connecting the printer to a laptop (also at the TU)

This procedure is for everyone on the team who wants to use the Core One with **another laptop**:
printing through PrusaLink, controlling the printer and receiving the force data.

## Why a direct cable?

At the TU, the **network socket in the wall does not work** for this setup. It is on a different,
shielded network than eduroam, and the TU network does not let devices reach each other. Because of
that, PrusaLink and the **force data** (UDP, port 8514) do not work. So we connect the printer with
a **network cable straight to the laptop**, with fixed addresses:

| Device | Address |
|---|---|
| Printer | `192.168.50.2` |
| Laptop (Ethernet port) | `192.168.50.1` |
| Subnet mask | `255.255.255.0` |

Your laptop keeps **Wi-Fi (eduroam) for the internet**; the cable is only for the printer.

**Every laptop gets the same address `192.168.50.1`.** The printer remembers which address it sends
the force data to. Because every laptop uses the same address, nothing needs to change on the
printer when someone else connects. Only one laptop at a time can be on the cable, though.

## What you need

- **A network cable** from the printer to the laptop. A normal cable works; you do not need a
  crossover cable.
- **A network port on the laptop**, or a **USB-C or USB-to-Ethernet adapter**.
- **The USB cable** from the printer to the laptop, to control the printer.
- **Administrator rights** on the laptop, to set a fixed address once.
- **The repository** (branch `development-bep-groep`) with Python and
  `pip install -r requirements.txt`.
- **The PrusaLink password**. You find it on the printer under **Settings > Network > PrusaLink**;
  the user is `maker`.

---

## Step 1: printer (already done; only needed after a reset)

The printer is already fixed at `192.168.50.2`. Check this under
**Settings > Network > Ethernet (LAN)**. Only if something else is shown there (after a reset, a
firmware update or with another printer), do this:

1. Put [`printer_network/prusa_printer_settings.ini`](../printer_network/prusa_printer_settings.ini)
   in the **root folder** of a FAT32 USB stick. Keep the name exactly as it is.
2. Put the stick in the printer and choose **Settings > System > Load Settings from File**. Note:
   not the menu under *Network* or *Wi-Fi*, because that only loads Wi-Fi details.
3. **Restart the printer.** If there is a `.bbf` (firmware) on the stick and the printer asks
   whether to install it, choose **No**, unless you want that on purpose.
4. Check that **Ethernet (LAN)** now shows `192.168.50.2`.

The file looks like this:

```ini
[eth::ipv4]
type=static
addr=192.168.50.2
mask=255.255.255.0
gw=192.168.50.1

[network]
dns4=192.168.50.1
```

## Step 2: connect the cable

Plug the network cable from the printer into your laptop. **Not into the wall.**

## Step 3: fixed address on your laptop (once per laptop)

### Windows

Use the script [`printer_network/laptop_setup.ps1`](../printer_network/laptop_setup.ps1). It does
two things:
- it gives the Ethernet port the fixed address `192.168.50.1`, **without a gateway**, so your
  internet keeps going over Wi-Fi;
- it adds a **firewall rule** that lets the printer's force data in (inbound UDP 8514 only).

1. Open a normal **PowerShell** window in the project folder.
2. Paste this and press Enter. Change the path to where the repository is on your laptop:
   ```powershell
   Start-Process powershell -Verb RunAs -ArgumentList '-NoExit -ExecutionPolicy Bypass -File "C:\path\to\prusa-direct-drive\printer_network\laptop_setup.ps1"'
   ```
3. Click **Yes** when Windows asks. The new window ends with **"Done"** in green.

**Is the network port not called `Ethernet`**, for example *Ethernet 2* with a USB adapter? Look up
the name with `Get-NetAdapter` and change `$Adapter` at the top of the script.

**Undo:** run [`printer_network/laptop_undo.ps1`](../printer_network/laptop_undo.ps1) the same way.
Ethernet is then automatic again and the firewall rule is gone.

### macOS

1. **System Settings > Network**. Choose the Ethernet connection (or the USB adapter) and click
   **Details... > TCP/IP**.
2. Set **Configure IPv4** to **Manually** and fill in:
   - IP address: `192.168.50.1`
   - Subnet mask: `255.255.255.0`
   - Router: **leave empty**
3. Click **OK**. If the **firewall** is on (*System Settings > Network > Firewall*), allow incoming
   connections for **Python** when macOS asks. Otherwise the force data does not come in.

**Undo:** set **Configure IPv4** back to **Using DHCP**.

## Step 4: check

1. Ping the printer:
   ```bash
   ping 192.168.50.2
   ```
   You should get an answer. On macOS, stop with Ctrl+C.
2. Open **`http://192.168.50.2`** in your browser. The PrusaLink login page should appear.

## Step 5: the software

1. Start the path programmer in the project folder:
   ```bash
   python -m pathprogrammer
   ```
2. **PrusaLink (to print):** the address (`192.168.50.2`) and the user (`maker`) are already filled
   in; they come from [`machine_settings.toml`](../machine_settings.toml) under `[prusalink]`. You
   only need to enter the **password once**:
   - In the **Printer (PrusaLink)** box, click **Printer settings...**
   - Enter the PrusaLink password at **Password**. Leave *API key* empty.
   - Click **Test connection**; you should see *"Connected: printer is IDLE"*. Then click
     **Save**.

   The password is only stored on your laptop, not in the repository. If the printer has another
   address, change it in `machine_settings.toml`, or only for your laptop in *Printer settings...*.
3. **USB and force data (to calibrate and measure):**
   - Connect the USB cable and click **Start calibration** (it starts the calibrator, which talks
     to the printer).
   - The first time, or if the printer had another address, the printer asks **on its screen**
     whether the metrics may go to `192.168.50.1`. **Confirm this.**
   - When it works, the force graph moves.
   - Only one program can use the USB port at a time. **Close the calibration window** before you
     start *Execute program*.

---

## Troubleshooting

| What you see | Cause and solution |
|---|---|
| Ping: no answer | Is the cable in the laptop, and not in the wall? Is the laptop's address right (`ipconfig` or `ifconfig`)? Does the printer show `192.168.50.2`? |
| *Start this from a Terminal opened as administrator* | The window is not running as administrator. Use the command with `-Verb RunAs` from step 3 |
| *Cannot reach the printer* in the app | See the ping row. Use `192.168.50.2` as the address in the app |
| *PrusaLink refused the login* | The user must be `maker`; check the password on the printer |
| No force data (the graph stays empty) | The firewall blocks UDP 8514 (Windows: run `laptop_setup.ps1`; macOS: allow Python), or the question on the printer's screen was not confirmed |
| The software picks the wrong address for the force data | Fix it. Windows (PowerShell): `$env:PRINTER_METRICS_IP="192.168.50.1"; python -m pathprogrammer`. macOS: `PRINTER_METRICS_IP=192.168.50.1 python -m pathprogrammer` |
| *could not open port* / *Access denied* (USB) | Another program is already using the USB port: the calibrator, PrusaSlicer or a second instance. Close it |
| The printer shows `0.0.0.0` after loading the file | The file was not read properly. Check the name, that it is in the root folder and that you used the **System** menu, and restart the printer. Still not working: set the printer to DHCP and let Windows hand out the address with *Internet Connection Sharing* (the laptop address then becomes `192.168.137.1`) |

## Back to the normal network

- **Laptop:** run `laptop_undo.ps1` (Windows), or set IPv4 back to DHCP (macOS).
- **Printer:** set **Ethernet** under *Settings > Network* back to **DHCP**, or load a
  `prusa_printer_settings.ini` with `type=dhcp` under `[eth::ipv4]`.
