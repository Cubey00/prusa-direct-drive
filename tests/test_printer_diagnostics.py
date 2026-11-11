"""Standalone diagnostics must not acquire hardware on import or send movement."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import gantry_controls as gc
import gauge_drift_reader as drift
import printer_command_test as diagnostic


def test_imports_do_not_open_hardware_or_start_experiments(tmp_path):
    script = """
from unittest.mock import patch
import sys
import gantry_controls as gc
import serial, socket, threading
import strain_gauge

with patch.object(gc, 'connect', side_effect=AssertionError('connect on import')), \
     patch.object(gc, 'enable_metrics', side_effect=AssertionError('metrics on import')), \
     patch.object(gc, 'send_gcode', side_effect=AssertionError('Gcode on import')), \
     patch.object(serial, 'Serial', side_effect=AssertionError('serial on import')), \
     patch.object(socket, 'socket', side_effect=AssertionError('socket on import')), \
     patch.object(threading.Thread, 'start', side_effect=AssertionError('thread on import')), \
     patch.object(strain_gauge, 'StrainGauge', side_effect=AssertionError('gauge on import')):
    import gauge_drift_reader
    import printer_command_test
    assert callable(gauge_drift_reader.main)
    assert callable(printer_command_test.main)
    assert 'matplotlib.pyplot' not in sys.modules
"""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path,
                            env=dict(os.environ, PYTHONPATH=str(root)),
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(tmp_path.iterdir())


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


@pytest.fixture
def drift_run(monkeypatch):
    gauge = Mock()
    gauge._thread = None
    gauge.can_tare.return_value = True
    gauge.tare.return_value = True
    gauge.recent.return_value = [3, 5]
    gauge.offset = 2
    constructor = Mock(return_value=gauge)
    monkeypatch.setattr(drift, "StrainGauge", constructor)
    enable = Mock()
    disconnect = Mock()
    analyze = Mock()
    monkeypatch.setattr(gc, "enable_metrics", enable)
    monkeypatch.setattr(gc, "disconnect", disconnect)
    monkeypatch.setattr(drift, "analyze_drift", analyze)
    clock = Clock()
    monkeypatch.setattr(drift.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(drift.time, "sleep", clock.sleep)
    return SimpleNamespace(gauge=gauge, constructor=constructor, enable=enable,
                           disconnect=disconnect, analyze=analyze)


def test_failed_metrics_initialization_aborts_before_listener(drift_run, monkeypatch, capsys):
    drift_run.enable.side_effect = gc.PrinterError("printer unavailable")
    log = Mock()
    monkeypatch.setattr(drift, "log_drift", log)
    assert drift.main([]) == 1
    drift_run.constructor.assert_not_called()
    log.assert_not_called()
    drift_run.analyze.assert_not_called()
    drift_run.disconnect.assert_called_once_with()
    assert "printer unavailable" in capsys.readouterr().err


def test_missing_udp_data_has_bounded_wait_and_cleanup(drift_run, capsys):
    drift_run.gauge.can_tare.return_value = False
    assert drift.main(["--startup-timeout", "0.25"]) == 1
    drift_run.gauge.start.assert_called_once_with()
    drift_run.gauge.tare.assert_not_called()
    drift_run.gauge.stop.assert_called_once_with()
    drift_run.disconnect.assert_called_once_with()
    drift_run.analyze.assert_not_called()
    assert "UDP data on port 8514" in capsys.readouterr().err


def test_failed_tare_aborts_measurement(drift_run, monkeypatch):
    drift_run.gauge.tare.return_value = False
    log = Mock()
    monkeypatch.setattr(drift, "log_drift", log)
    assert drift.main([]) == 1
    log.assert_not_called()
    drift_run.gauge.stop.assert_called_once_with()
    drift_run.disconnect.assert_called_once_with()


def test_drift_logs_existing_average_and_offset_then_cleans_before_analysis(drift_run, tmp_path):
    output = tmp_path / "logs" / "drift.json"
    thread = Mock()
    thread.is_alive.return_value = False
    drift_run.gauge._thread = thread
    def analyze(path):
        assert path == output
        drift_run.gauge.stop.assert_called_once_with()
        thread.join.assert_called_once_with(timeout=1)
        drift_run.disconnect.assert_called_once_with()
    drift_run.analyze.side_effect = analyze
    assert drift.main(["--duration", "0.4", "--interval", "0.1", "--output", str(output)]) == 0
    samples = json.loads(output.read_text())
    assert len(samples) == 4
    assert [row[0] for row in samples] == pytest.approx([0, 0.1, 0.2, 0.3])
    assert [row[1] for row in samples] == [2, 2, 2, 2]
    drift_run.analyze.assert_called_once_with(output)


@pytest.mark.parametrize("error", [OSError("disk full"), KeyboardInterrupt()])
def test_logging_failure_or_interrupt_still_cleans_resources(drift_run, monkeypatch, error):
    monkeypatch.setattr(drift, "log_drift", Mock(side_effect=error))
    if isinstance(error, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            drift.main([])
    else:
        assert drift.main([]) == 1
    drift_run.gauge.stop.assert_called_once_with()
    drift_run.disconnect.assert_called_once_with()
    drift_run.analyze.assert_not_called()


def test_listener_cleanup_failure_is_explicit(drift_run, tmp_path, capsys):
    thread = Mock()
    thread.is_alive.return_value = True
    drift_run.gauge._thread = thread
    assert drift.main(["--duration", "0.4", "--output", str(tmp_path / "drift.json")]) == 1
    drift_run.disconnect.assert_called_once_with()
    drift_run.analyze.assert_not_called()
    assert "listener did not stop" in capsys.readouterr().err


@pytest.mark.parametrize("flag,value", [("--duration", "0"), ("--interval", "nan"),
                                        ("--startup-timeout", "-1")])
def test_invalid_measurement_arguments_do_not_touch_hardware(drift_run, flag, value):
    with pytest.raises(SystemExit) as exc:
        drift.main([flag, value])
    assert exc.value.code == 2
    drift_run.enable.assert_not_called()
    drift_run.constructor.assert_not_called()


def test_existing_linear_quadratic_analysis_and_plot_are_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    show = Mock()
    monkeypatch.setattr(plt, "show", show)
    path = tmp_path / "drift.json"
    path.write_text(json.dumps([[0, 1], [1, 3], [2, 5], [3, 7]]))
    try:
        a, b = drift.analyze_drift(path)
        assert a == pytest.approx(2) and b == pytest.approx(1)
        assert len(plt.gca().lines) == 3  # raw, linear, quadratic
        show.assert_called_once_with()
    finally:
        plt.close("all")


@pytest.fixture
def transport(monkeypatch):
    connection = Mock()
    connection.is_open = True
    connection.readline.side_effect = [b"metrics configuration\n", b"ok\n"]
    connection.close.side_effect = lambda: setattr(connection, "is_open", False)
    factory = Mock(return_value=connection)
    ports = Mock(return_value=[])
    monkeypatch.setattr(gc.serial, "Serial", factory)
    monkeypatch.setattr(gc.list_ports, "comports", ports)
    monkeypatch.setattr(gc, "PORT_OVERRIDE", None)
    monkeypatch.setattr(gc, "_ser", None)
    monkeypatch.setattr(gc.time, "sleep", Mock())
    return SimpleNamespace(connection=connection, factory=factory, ports=ports)


@pytest.mark.parametrize("device", ["/dev/cu.usbmodem1234", "/dev/ttyACM0", "COM7"])
def test_diagnostic_uses_cross_platform_prusa_detection_and_only_m333(transport, device, capsys):
    transport.ports.return_value = [SimpleNamespace(device="other", vid=0x1234),
                                    SimpleNamespace(device=device, vid=gc.PRUSA_VID)]
    assert diagnostic.main([]) == 0
    transport.factory.assert_called_once_with(device, gc.BAUD, timeout=1)
    transport.connection.write.assert_called_once_with(b"M333\n")
    transport.connection.reset_input_buffer.assert_called_once_with()
    transport.connection.close.assert_called_once_with()
    assert gc._ser is None and gc.PORT_OVERRIDE is None
    assert "metrics configuration" in capsys.readouterr().out


@pytest.mark.parametrize("device", ["/dev/cu.custom", "/dev/ttyUSB0", "COM9"])
def test_manual_override_bypasses_vid_detection_and_restores_setting(transport, monkeypatch, device):
    monkeypatch.setattr(gc, "PORT_OVERRIDE", "previous-port")
    transport.ports.side_effect = AssertionError("explicit port must bypass discovery")
    assert diagnostic.main(["--port", device]) == 0
    transport.factory.assert_called_once_with(device, gc.BAUD, timeout=1)
    transport.connection.write.assert_called_once_with(b"M333\n")
    assert gc.PORT_OVERRIDE == "previous-port"


@pytest.mark.parametrize("devices", [[], ["/dev/cu.one", "/dev/cu.two"]])
def test_missing_or_ambiguous_printer_never_guesses_a_port(transport, devices):
    transport.ports.return_value = [SimpleNamespace(device=d, vid=gc.PRUSA_VID) for d in devices]
    assert diagnostic.main([]) == 1
    transport.factory.assert_not_called()
    transport.connection.write.assert_not_called()


def test_transport_failure_disconnects_and_restores_override(transport, monkeypatch, capsys):
    monkeypatch.setattr(gc, "PORT_OVERRIDE", "previous-port")
    transport.connection.write.side_effect = gc.serial.SerialException("USB disconnected")
    assert diagnostic.main(["--port", "/dev/cu.custom"]) == 1
    transport.connection.close.assert_called_once_with()
    assert gc.PORT_OVERRIDE == "previous-port" and gc._ser is None
    assert "USB disconnected" in capsys.readouterr().err
