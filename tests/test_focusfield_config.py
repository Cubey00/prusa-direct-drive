from dataclasses import replace
import math
import tomllib

import pytest

from focusfield.config import Recipe, load_recipe, simulation_fixture
from machine_settings import DEFAULT_PATH, SETTINGS, SettingsError, parse


def test_recipe_and_z_wd_sign():
    recipe, fixture = load_recipe(), simulation_fixture()
    assert len(recipe.distances()) == 21
    assert recipe.distances()[0] == 19.5 and recipe.distances()[-1] == 20.5
    assert fixture.z_for_wd(20.05) == pytest.approx(100.05)
    assert fixture.wd_for_z(100.05) == pytest.approx(20.05)
    recipe.validate_fixture(fixture, SETTINGS.printer.z_max)
    recipe = replace(recipe, reverse_alternate=True)
    assert recipe.schedule(fixture)[21][1] == 20.5
    assert recipe.schedule(fixture)[-1][1] == 19.5


@pytest.mark.parametrize("kw", [dict(step_mm=0), dict(step_mm=0.03), dict(step_mm=math.nan),
    dict(wd_end_mm=math.inf), dict(wd_start_mm=21), dict(frames_per_position=True),
    dict(repetitions=0), dict(discard_frames=0), dict(settle_s=-1), dict(exposure_us=0),
    dict(fixed_frequency_cpp=0.3), dict(pixel_format="BayerRG12g24IDS")])
def test_invalid_recipes(kw):
    with pytest.raises(ValueError):
        replace(Recipe(), **kw)


def test_unmeasured_bounds_and_clearance_are_rejected():
    with pytest.raises(ValueError, match="disabled or unmeasured"):
        Recipe().validate_fixture(SETTINGS.focusfield, SETTINGS.printer.z_max)
    fixture = replace(simulation_fixture(), safe_z_min_mm=100)
    with pytest.raises(ValueError, match="bounds"):
        Recipe().validate_fixture(fixture, SETTINGS.printer.z_max)
    with pytest.raises(ValueError, match="clearance"):
        Recipe().validate_fixture(replace(simulation_fixture(), minimum_wd_mm=19.5), 273)


def test_optional_profile_backwards_compatibility_and_invalid_settings():
    data = tomllib.loads(DEFAULT_PATH.read_text())
    del data["focusfield"]
    assert not parse(data).focusfield.enabled
    data = tomllib.loads(DEFAULT_PATH.read_text())
    data["focusfield"]["enabled"] = True
    with pytest.raises(SettingsError, match="enabling requires"):
        parse(data)
    data["focusfield"]["enabled"] = False
    data["focusfield"]["reference_wd_mm"] = math.nan
    with pytest.raises(SettingsError, match="finite"):
        parse(data)
