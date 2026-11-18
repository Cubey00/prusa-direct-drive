"""Makes a "Touch Path Programmer" shortcut (with the logo) on this computer's desktop and in the
project folder: double-click it to start the app, no PowerShell or console window.

Windows: python tools/make_shortcut.py   (once per laptop; uses the Python it is run with)
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "assets" / "icon.ico"
NAME = "Touch Path Programmer.lnk"


def desktop():
    """The real desktop folder (also when OneDrive moved it)."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return Path(out)


def make(link):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
        "$s.TargetPath = $env:TARGET; $s.Arguments = '-m pathprogrammer';"
        "$s.WorkingDirectory = $env:ROOT; $s.IconLocation = $env:ICON;"
        "$s.Description = 'Touch path programmer: print plates, calibrate, touch and photograph';"
        "$s.Save()")
    env = {"LNK": str(link), "TARGET": str(pythonw), "ROOT": str(ROOT), "ICON": f"{ICON},0"}
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   env={**__import__("os").environ, **env})
    return link


def main():
    if sys.platform != "win32":
        sys.exit("Only for Windows; on macOS start it with: python -m pathprogrammer")
    if not ICON.exists():
        sys.exit("assets/icon.ico is missing: run python tools/make_icon.py first")
    for folder in (desktop(), ROOT):
        print("made", make(folder / NAME))


if __name__ == "__main__":
    main()
