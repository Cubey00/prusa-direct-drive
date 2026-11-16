"""Filament presets straight from PrusaSlicer's own profile bundle (PrusaResearch.ini), so another
filament can be chosen without exporting a new config from the PrusaSlicer GUI.

A preset in the bundle inherits from others ("inherits = A; B": A first, then B, then its own
keys). resolve() flattens that into one set of filament settings; write_filament_ini() writes it
as a small config that is loaded after the printer profile (--load profile --load filament).
"""
import os
import re
from pathlib import Path

from . import config as cfg

# keys that only steer the GUI's preset lists, not the print
_SKIP_KEYS = {"inherits", "renamed_from", "compatible_printers", "compatible_printers_condition",
              "compatible_prints", "compatible_prints_condition"}


class FilamentError(Exception):
    pass


def bundle_paths():
    """Where PrusaSlicer keeps PrusaResearch.ini: the updated copy in the user's data folder first,
    then the one shipped with the program."""
    paths = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "PrusaSlicer" / "vendor" / "PrusaResearch.ini")
    paths.append(Path.home() / "Library" / "Application Support" / "PrusaSlicer" / "vendor" / "PrusaResearch.ini")
    for exe in cfg.SLICER_SEARCH:
        paths.append(exe.parent / "resources" / "profiles" / "PrusaResearch.ini")      # Windows
        paths.append(exe.parent.parent / "Resources" / "profiles" / "PrusaResearch.ini")  # macOS app
    return paths


def find_bundle():
    for p in bundle_paths():
        if p.exists():
            return p
    raise FilamentError("PrusaSlicer's profile bundle (PrusaResearch.ini) was not found; "
                        "install PrusaSlicer 2.9 or newer and start it once.")


def parse_bundle(path):
    """{"filament": {name: {key: value}}, ...} for every [type:name] section."""
    sections, current = {}, None
    for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("[") and line.rstrip().endswith("]"):
            head = line.strip()[1:-1]
            kind, _, name = head.partition(":")
            current = sections.setdefault(kind.strip(), {}).setdefault(name.strip(), {}) if name else None
        elif current is not None and "=" in line and not line.lstrip().startswith(("#", ";")):
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip()
    return sections


class Bundle:
    def __init__(self, path=None):
        self.path = Path(path) if path else find_bundle()
        self.filaments = parse_bundle(self.path).get("filament", {})

    def resolve(self, name, _seen=()):
        if name not in self.filaments:
            raise FilamentError(f"Filament preset {name!r} is not in {self.path.name}")
        if name in _seen:
            raise FilamentError(f"Inheritance loop at {name!r}")
        own = self.filaments[name]
        values = {}
        for parent in (p.strip() for p in own.get("inherits", "").split(";")):
            if parent:
                values.update(self.resolve(parent, _seen + (name,)))
        values.update(own)
        return values

    def names(self, suffix):
        """Real (not *abstract*) presets ending in `suffix`, e.g. '@COREONE HF0.4'."""
        return sorted(n for n in self.filaments if n.endswith(suffix) and not n.startswith("*"))


def printer_suffix(profile):
    """'@COREONE HF0.4' from the printer profile's printer_settings_id and nozzle."""
    printer_id = profile.get("printer_settings_id", "")
    filament_id = profile.get("filament_settings_id", "").strip('"')
    if "@" in filament_id:
        return filament_id[filament_id.index("@"):]
    m = re.search(r"CORE One (HF)?\s*(\d\.\d)", printer_id)
    if not m:
        raise FilamentError(f"Cannot tell the nozzle from printer_settings_id {printer_id!r}")
    return f"@COREONE {'HF' if m.group(1) else ''}{m.group(2)}"


def write_filament_ini(bundle, name, path):
    values = {k: v for k, v in bundle.resolve(name).items() if k not in _SKIP_KEYS}
    values["filament_settings_id"] = f'"{name}"'
    path = Path(path)
    path.write_text(f"# filament preset {name} resolved from {bundle.path}\n"
                    + "".join(f"{k} = {v}\n" for k, v in sorted(values.items())), encoding="utf-8")
    return path
