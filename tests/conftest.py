from dataclasses import asdict, replace
import json

import pytest

from focusfield.calibration import WDReference
from focusfield.config import simulation_fixture


@pytest.fixture
def verified_optical_fixture(tmp_path):
    """Synthetic attestation for hardware stubs; no physical calibration is claimed."""
    record = WDReference('SIMULATED', 'SIMULATED', 100, 20, 100.1, 20.1, 0.01,
                         'TEST lens plane', 'TEST patterned surface', 'TEST independent ruler',
                         'TEST', '2026-11-17T00:00:00+00:00', 'SIMULATED').validate()
    path = tmp_path / 'test-wd-reference.json'
    path.write_text(json.dumps(asdict(record)), encoding='utf-8')
    return replace(simulation_fixture(), wd_reference_record=str(path))
