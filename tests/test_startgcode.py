"""Faster start of a print: a few bed probe points and no wait for the bed frame."""
from dataclasses import replace

import pytest

import machine_settings
from slicing import startgcode
from slicing.slicer import read_profile
from slicing import config as cfg


@pytest.fixture
def start():
    return read_profile(cfg.SLICER_PROFILE)["start_gcode"]


def g29_lines(value):
    return [l for l in value.split("\\n") if l.startswith("G29")]


def test_probe_grid_matches_the_firmware():
    xs, ys = startgcode.probe_grid()
    assert len(xs) == len(ys) == 7
    assert xs[0] == pytest.approx(11.8) and xs[-1] == pytest.approx(188.2)
    assert ys[0] == pytest.approx(51.7) and ys[-1] == pytest.approx(208.3)


def test_default_settings_probe_five_points_around_the_plate(start):
    printing = machine_settings.load().printing
    new, notes = startgcode.adapt(start, printing.bed_probe_points, printing.wait_for_bed_frame)
    probes = [l for l in g29_lines(new) if l.startswith("G29 P1 X") and "purge" not in l]
    assert len(probes) == 5 and notes == ["bed probing: 5 points", "no wait for the bed frame"]
    assert " C " not in probes[0] and all(" C " in l for l in probes[1:])   # first clears the old mesh
    assert startgcode.PROBE_LINE not in new.split("\\n")
    assert "G29 G ; absorb heat" not in new and "G29 P3.13" in new and "G29 A" in new
    # every box holds exactly one grid point
    xs, ys = startgcode.probe_grid()
    for l in probes:
        x, y, w = (float(l.split(k)[1].split()[0]) for k in (" X", " Y", " W"))
        assert sum(x <= gx <= x + w for gx in xs) == 1 and sum(y <= gy <= y + w for gy in ys) == 1


def test_empty_settings_keep_prusas_start(start):
    new, _ = startgcode.adapt(start, (), True)
    assert new == start


def test_ini_is_written_only_when_something_changes(start, tmp_path):
    printing = machine_settings.load().printing
    path, _ = startgcode.write_start_ini({"start_gcode": start}, tmp_path, printing)
    assert path.name == "start_gcode.ini"
    assert read_profile(path)["start_gcode"].count("G29 P1 X") == 6     # 5 + the purge-place one
    assert startgcode.write_start_ini({"start_gcode": "M17"}, tmp_path / "x", printing)[0] is None   # nothing to adapt


def test_probe_point_out_of_reach_is_refused(tmp_path, monkeypatch):
    text = machine_settings.DEFAULT_PATH.read_text(encoding="utf-8")
    bad = tmp_path / "s.toml"
    bad.write_text(text.replace("[100, 117]]", "[100, 230]]"), encoding="utf-8")
    with pytest.raises(machine_settings.SettingsError, match="bed probe point"):
        machine_settings.load(bad)


def test_purge_line_moves_where_the_nozzle_can_reach(start):
    new, notes = startgcode.adapt(start, (), True, (80.0, 47.0))
    assert notes == ["purge line X80 -> X30 at Y47"]
    lines = new.split("\\n")
    i0, i1 = lines.index(startgcode.PURGE_START), lines.index(startgcode.PURGE_END)
    moves = [l for l in lines[i0:i1] if l.startswith(("G0", "G1"))]
    xs = [float(t[1:]) for l in moves for t in l.split(";")[0].split() if t.startswith("X")]
    assert xs == [80, 66, 56, 46, 36, 33, 30]              # Prusa's 249 -> 199, shifted
    assert all("Y-2.5" not in l for l in lines) and "G0 X80 Y47 Z15 F4800" in new
    assert "G29 P9 X39 Y47 W32 H4" in lines                # nozzle cleaning moved along
    assert "G29 P1 X20 Y43 W70 H12 C ; probe near purge place" in lines
    tx, ty = machine_settings.load().printer.travel
    assert all(tx[0] <= x <= tx[1] for x in xs) and ty[0] <= 47 <= ty[1]


def test_default_settings_move_the_purge(start):
    printing = machine_settings.load().printing
    _, notes = startgcode.adapt(start, printing.bed_probe_points, printing.wait_for_bed_frame,
                                (printing.purge_start_x, printing.purge_y))
    assert notes[0].startswith("purge line X")


@pytest.mark.parametrize("change, match", [
    ("purge_y = 47.0", "purge_y = 54.0"),          # too close to the usable area
    ("purge_y = 47.0", "purge_y = 40.0"),          # outside the nozzle's reach
    ("purge_start_x = 80.0", "purge_start_x = 230.0"),
    ("purge_start_x = 80.0", "purge_start_x = 40.0"),   # line would start left of X -2
])
def test_purge_out_of_reach_is_refused(tmp_path, change, match):
    text = machine_settings.DEFAULT_PATH.read_text(encoding="utf-8")
    assert change in text
    bad = tmp_path / "s.toml"
    bad.write_text(text.replace(change, match), encoding="utf-8")
    with pytest.raises(machine_settings.SettingsError, match="purge"):
        machine_settings.load(bad)


def test_slicer_prints_slower_with_skirt_and_brim():
    o = cfg.SLICER_OVERRIDES
    assert "--external-perimeter-speed=40" in o and "--max-print-speed=100" in o
    assert "--skirts=1" in o and "--brim-width=2" in o and "--support-material=0" in o
