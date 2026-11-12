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
    assert s.work_area.usable == ((0.0, 250.0), (60.0, 160.0))
    assert (s.fiducials.inset, s.fiducials.height) == (10.0, 5.0)
    assert (s.forces.contact_g, s.forces.max_force_g, s.forces.guard_trip_g) == (5.0, 450.0, 500.0)
    assert set(s.unmeasured()) == {"work_area", "sensor", "camera"}


def test_config_modules_use_the_settings():
    import gantry_controls
    from pathprogrammer import config as pc
    from slicing import config as sc
    s = ms.SETTINGS
    assert sc.USABLE_AREA == s.work_area.usable and sc.CELL_PITCH == s.raster.cell_pitch
    assert sc.FIDUCIAL_BASE_D == s.fiducials.base_diameter and sc.PRINTER_MODEL == s.printer.model
    assert pc.MACHINE_XY_BOUNDS == s.work_area.machine and pc.GUARD_TRIP_G == s.forces.guard_trip_g
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
    ("forces", "max_force_g", 600.0, "contact_g < max_force_g < guard_trip_g"),
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
    text = ms.DEFAULT_PATH.read_text(encoding="utf-8").replace("offset_x = 0.0", "offset_x = 42.5")
    other.write_text(text, encoding="utf-8")
    code = "import machine_settings as m; print(m.SETTINGS.sensor.offset_x)"
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
