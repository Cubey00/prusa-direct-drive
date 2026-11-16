import pytest

from slicing import pipeline
from slicing.filaments import Bundle, FilamentError, printer_suffix, write_filament_ini
from slicing.slicer import SlicerError, read_profile

BUNDLE = """\
[vendor]
name = Prusa Research

[filament:*common*]
filament_type = PLA
temperature = 200
bed_temperature = 60
compatible_printers_condition = nozzle_diameter[0]==0.4

[filament:*PET*]
inherits = *common*
filament_type = PETG
temperature = 240

[filament:*HFC1*]
bed_temperature = 70
fan = 50

[filament:Generic PETG @COREONE HF0.4]
inherits = *PET*; *HFC1*
bed_temperature = 85

[filament:Generic PLA @COREONE HF0.4]
inherits = *common*; *HFC1*

[filament:Generic PLA @MK4]
inherits = *common*

[filament:Loop A @COREONE HF0.4]
inherits = Loop B @COREONE HF0.4

[filament:Loop B @COREONE HF0.4]
inherits = Loop A @COREONE HF0.4

[printer:Prusa CORE One HF0.4 nozzle]
nozzle_diameter = 0.4
"""


@pytest.fixture
def bundle(tmp_path):
    path = tmp_path / "PrusaResearch.ini"
    path.write_text(BUNDLE, encoding="utf-8")
    return Bundle(path)


def test_inheritance_left_to_right_then_own_keys(bundle):
    petg = bundle.resolve("Generic PETG @COREONE HF0.4")
    assert petg["filament_type"] == "PETG" and petg["temperature"] == "240"
    assert petg["fan"] == "50" and petg["bed_temperature"] == "85"   # own key beats *HFC1*
    pla = bundle.resolve("Generic PLA @COREONE HF0.4")
    assert (pla["filament_type"], pla["temperature"], pla["bed_temperature"]) == ("PLA", "200", "70")


def test_names_for_printer_skip_abstract_and_other_printers(bundle):
    names = bundle.names("@COREONE HF0.4")
    assert "Generic PETG @COREONE HF0.4" in names and "Generic PLA @COREONE HF0.4" in names
    assert "Generic PLA @MK4" not in names and not any(n.startswith("*") for n in names)


def test_unknown_and_looping_presets_raise(bundle):
    with pytest.raises(FilamentError, match="not in"):
        bundle.resolve("Nope @COREONE HF0.4")
    with pytest.raises(FilamentError, match="loop"):
        bundle.resolve("Loop A @COREONE HF0.4")


def test_printer_suffix_from_profile():
    assert printer_suffix({"filament_settings_id": '"Generic PLA @COREONE HF0.4"'}) == "@COREONE HF0.4"
    assert printer_suffix({"printer_settings_id": "Prusa CORE One HF0.4 nozzle"}) == "@COREONE HF0.4"
    assert printer_suffix({"printer_settings_id": "Prusa CORE One 0.6 nozzle"}) == "@COREONE 0.6"


def test_written_ini_is_a_loadable_filament_config(bundle, tmp_path):
    path = write_filament_ini(bundle, "Generic PETG @COREONE HF0.4", tmp_path / "filament.ini")
    values = read_profile(path)
    assert values["filament_type"] == "PETG" and values["temperature"] == "240"
    assert values["filament_settings_id"] == '"Generic PETG @COREONE HF0.4"'
    assert "inherits" not in values and "compatible_printers_condition" not in values


def test_pipeline_refuses_filament_for_another_printer(monkeypatch, bundle, tmp_path):
    monkeypatch.setattr(pipeline, "Bundle", lambda: bundle)
    profile = {"filament_settings_id": '"Generic PLA @COREONE HF0.4"'}
    assert pipeline.prepare_filament(None, profile, tmp_path) is None
    with pytest.raises(SlicerError, match="not for this printer"):
        pipeline.prepare_filament("Generic PLA @MK4", profile, tmp_path)
    ini = pipeline.prepare_filament("Generic PETG @COREONE HF0.4", profile, tmp_path)
    assert ini == tmp_path / "filament.ini" and ini.exists()
