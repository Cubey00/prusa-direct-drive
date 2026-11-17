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
    same = replace(printing, bed_probe_points=(), wait_for_bed_frame=True)
    assert startgcode.write_start_ini({"start_gcode": start}, tmp_path / "x", same) == (None, [])


def test_probe_point_out_of_reach_is_refused(tmp_path, monkeypatch):
    text = machine_settings.DEFAULT_PATH.read_text(encoding="utf-8")
    bad = tmp_path / "s.toml"
    bad.write_text(text.replace("[100, 117]]", "[100, 230]]"), encoding="utf-8")
    with pytest.raises(machine_settings.SettingsError, match="bed probe point"):
        machine_settings.load(bad)
