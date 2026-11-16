# printer_network

Files for connecting the Core One to a laptop with a direct Ethernet cable (fixed addresses:
printer `192.168.50.2`, laptop `192.168.50.1`). The full step-by-step guide (Dutch, Windows and
macOS, troubleshooting) is [docs/printer-verbinden.md](../docs/printer-verbinden.md).

| File | What |
|---|---|
| `prusa_printer_settings.ini` | Put in the root of a USB stick; on the printer: Settings > System > Load Settings from File, then restart |
| `laptop_setup.ps1` | Windows, run as administrator: fixed address on the Ethernet port + firewall rule for the force data (UDP 8514) |
| `laptop_undo.ps1` | Windows, run as administrator: back to DHCP, firewall rule removed |
