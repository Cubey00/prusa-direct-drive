import copy
import subprocess
import sys
import tomllib

import pytest

import machine_settings as ms


@pytest.fixture
def data():
    with open(ms.DEFAULT_PATH, "rb") as f:
        return tomllib.load(f)


def test_default_file_loads_with_the_known_values():
    s = ms.load()
    assert s.printer.bed_size == (250.0, 220.0)
    assert (s.prusalink.host, s.prusalink.user) == ("192.168.50.2", "maker")
    assert s.printing.filament == "Generic PETG @COREONE HF0.4"
    assert s.work_area.usable == ((0.0, 200.0), (56.0, 179.0))
    assert s.printer.travel == ((-2.0, 201.0), (42.0, 221.0))
    assert len(s.sensor.offset) == 3            # the value itself is this laptop's last calibration
    assert s.work_area.probe_points == ((189.0, 52.0), (194.0, 44.0))
    assert (s.fiducials.inset, s.fiducials.height) == (10.0, 5.0)
    assert (s.forces.contact_g, s.forces.max_force_g, s.forces.guard_trip_g) == (5.0, 1100.0, 1200.0)
    assert set(s.unmeasured()) == {"sensor", "camera"}


def test_config_modules_use_the_settings():
    import gantry_controls
    from pathprogrammer import config as pc
    from slicing import config as sc
    s = ms.SETTINGS
    assert sc.USABLE_AREA == s.work_area.usable and sc.CELL_PITCH == s.raster.cell_pitch
    assert sc.FIDUCIAL_BASE_D == s.fiducials.base_diameter and sc.PRINTER_MODEL == s.printer.model
    assert pc.MACHINE_XY_BOUNDS == s.printer.travel and pc.GUARD_TRIP_G == s.forces.guard_trip_g
    assert pc.CAL_RESIDUAL_TOL == s.calibration.residual_tol
    assert gantry_controls.Z_MAX == s.printer.z_max


def test_missing_key_names_section_and_key(data):
    del data["sensor"]["offset_x"]
    with pytest.raises(ms.SettingsError, match=r"missing \[sensor\] offset_x"):
        ms.parse(data)


@pytest.mark.parametrize("section,key,value,msg", [
    ("sensor", "offset_x", "3", "must be a number"),
    ("sensor", "measured", 1, "true or false"),
    ("work_area", "usable_y", [60, 160, 3], "two numbers"),
    ("work_area", "usable_y", [60.0, 230.0], "inside the bed"),
    ("raster", "min_gap", 25.0, "larger than min_gap"),
    ("fiducials", "inset", 18.0, "do not fit"),
    ("forces", "max_force_g", 1300.0, "contact_g < max_force_g < guard_trip_g"),
    ("forces", "jog_stop_g", 1300.0, "contact_g < jog_stop_g < guard_trip_g"),
    ("work_area", "usable_y", [50.0, 179.0], "probes the bed"),
    ("work_area", "usable_y", [56.0, 200.0], "sensor can reach"),
    ("work_area", "usable_x", [0.0, 205.0], "nozzle can reach"),
    ("sensor", "offset_y", -60.0, "sensor can reach"),
    ("work_area", "probe_points", [[189.0]], "list of"),
])
def test_bad_values_are_refused(data, section, key, value, msg):
    bad = copy.deepcopy(data)
    bad[section][key] = value
    with pytest.raises(ms.SettingsError, match=msg):
        ms.parse(bad)


def test_measured_camera_needs_a_scale(data):
    data["camera"]["measured"] = True
    with pytest.raises(ms.SettingsError, match="mm_per_px"):
        ms.parse(data)
    data["camera"]["mm_per_px"] = 0.012
    assert ms.parse(data).camera.mm_per_px == 0.012


def test_env_var_selects_another_file(tmp_path):
    other = tmp_path / "other.toml"
    text = ms.DEFAULT_PATH.read_text(encoding="utf-8").replace("hover_clearance = 10.0", "hover_clearance = 42.5")
    other.write_text(text, encoding="utf-8")
    code = "import machine_settings as m; print(m.SETTINGS.sensor.hover_clearance)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         cwd=ms.DEFAULT_PATH.parent, env={**__import__("os").environ, ms.ENV_VAR: str(other)})
    assert out.stdout.strip() == "42.5"


def test_unreadable_file_gives_settings_error(tmp_path):
    broken = tmp_path / "broken.toml"
    broken.write_text("[printer\n", encoding="utf-8")
    with pytest.raises(ms.SettingsError, match="broken.toml"):
        ms.load(broken)
    with pytest.raises(ms.SettingsError, match="not found"):
        ms.load(tmp_path / "nope.toml")


def test_sensor_helpers_predict_head_position_and_travel_height():
    s = ms.Sensor(**{**ms.SETTINGS.sensor.__dict__, "offset_x": 0.0, "offset_y": -40.0, "offset_z": -80.0})
    assert s.head_xy(10.0, 66.0) == (10.0, 106.0)          # sensor 40 mm in front: head 40 mm back
    assert s.hover_z(5.0) == 5.0 + s.hover_clearance + 80.0  # sensor 80 mm below the nozzle


def test_save_values_keeps_comments_and_refuses_bad_values(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(ms.DEFAULT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    s = ms.save_values("sensor", {"offset_y": -38.5, "offset_z": -81.0, "measured": True}, path)
    assert s.sensor.offset == (0.0, -38.5, -81.0) and s.sensor.measured
    after = path.read_text(encoding="utf-8")
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert [b for _, b in changed] == ["offset_y = -38.5", "offset_z = -81.0", "measured = true"]
    with pytest.raises(ms.SettingsError, match="sensor can reach"):
        ms.save_values("sensor", {"offset_y": -60.0}, path)
    assert path.read_text(encoding="utf-8") == after       # nothing written
    with pytest.raises(ms.SettingsError, match="no \\[sensor\\] nope"):
        ms.save_values("sensor", {"nope": 1.0}, path)


def test_cone_positions_save_and_load(tmp_path):
    machine_settings = ms
    path = tmp_path / "s.toml"
    path.write_text(machine_settings.DEFAULT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    assert machine_settings.load(path).calibration.cone_position("F1") is None
    machine_settings.save_values("calibration", {"cone_F1": [10.4, 105.2, 85.25]}, path)
    s = machine_settings.load(path)
    assert s.calibration.cone_position("F1") == (10.4, 105.2, 85.25)
    assert "cone_F1 = [10.400, 105.200, 85.250]" in path.read_text(encoding="utf-8")
    with pytest.raises(machine_settings.SettingsError, match="cone_F2"):
        machine_settings.save_values("calibration", {"cone_F2": [10.0, 300.0, 85.0]}, path)
