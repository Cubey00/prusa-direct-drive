import pytest
from PySide6.QtCore import QSettings

from fake_prusalink import API_KEY, FakePrinter
from prusalink import PrusaLink, PrusaLinkError, fmt_duration, safe_name


@pytest.fixture
def printer():
    p = FakePrinter()
    yield p
    p.close()


@pytest.fixture
def gcode(tmp_path):
    f = tmp_path / "testset" / "plate_01.bgcode"
    f.parent.mkdir()
    f.write_bytes(b"GCDE" + bytes(range(256)) * 10)
    return f


def test_status_when_idle(printer):
    st = PrusaLink(printer.host, api_key=API_KEY).status()
    assert st.state == "IDLE" and st.can_start and not st.active and st.job_id is None


def test_upload_prints_and_status_follows(printer, gcode):
    link = PrusaLink(printer.host, api_key=API_KEY)
    remote = link.upload(gcode, "testset_plate_01.bgcode")
    assert remote == "/usb/testset_plate_01.bgcode"
    path, headers = printer.uploads[0]
    assert printer.files[path] == gcode.read_bytes()
    assert headers["Print-After-Upload"] == "?1" and headers["Overwrite"] == "?1"
    printer.job.update(progress=42.4, time_remaining=300, time_printing=200)
    st = link.status()
    assert (st.state, st.job_id, st.progress, st.file) == ("PRINTING", 7, 42.4, "testset_plate_01.bgcode")


def test_upload_refused_while_printing(printer, gcode):
    printer.state = "PRINTING"
    with pytest.raises(PrusaLinkError, match="PRINTING"):
        PrusaLink(printer.host, api_key=API_KEY).upload(gcode)
    assert printer.uploads == []


def test_pause_resume_stop(printer, gcode):
    link = PrusaLink(printer.host, api_key=API_KEY)
    link.upload(gcode)
    link.pause(7)
    assert printer.state == "PAUSED"
    link.resume(7)
    assert printer.state == "PRINTING"
    link.stop(7)
    assert printer.state == "STOPPED" and printer.job is None


def test_wrong_key_and_unreachable_give_clear_errors(printer):
    with pytest.raises(PrusaLinkError, match="refused the login"):
        PrusaLink(printer.host, api_key="wrong").status()
    with pytest.raises(PrusaLinkError, match="Cannot reach"):
        PrusaLink("127.0.0.1:1", api_key=API_KEY).status()
    with pytest.raises(PrusaLinkError, match="No printer address"):
        PrusaLink("  ")


def test_helpers():
    assert safe_name("my plate (1).bgcode") == "my_plate_1.bgcode"
    assert safe_name("testset_plate_01.bgcode") == "testset_plate_01.bgcode"
    assert fmt_duration(3725) == "1h02m" and fmt_duration(65) == "1m05s" and fmt_duration(None) == "--"


# ---------------- UI ----------------

@pytest.fixture
def box(qtbot, tmp_path, printer, gcode, monkeypatch):
    from pathprogrammer.ui import printer_box
    from pathprogrammer.ui.printer_box import PrinterBox
    monkeypatch.setattr(printer_box, "POLL_MS", 100)
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    settings.setValue("printer/host", printer.host)
    settings.setValue("printer/api_key", API_KEY)
    b = PrinterBox(lambda: (gcode, "testset plate 1"), settings)
    qtbot.addWidget(b)
    b.show()
    yield b
    b.shutdown()


def test_box_prints_plate_and_tracks_progress(box, qtbot, printer, monkeypatch):
    from pathprogrammer.ui import printer_box
    qtbot.waitUntil(lambda: box.status is not None and box.status.state == "IDLE", timeout=5000)
    assert box.print_btn.isEnabled() and not box.stop_btn.isEnabled()
    asked = []
    monkeypatch.setattr(printer_box.QMessageBox, "question",
                        lambda *a: asked.append(a[2]) or printer_box.QMessageBox.Yes)
    box.print_btn.click()
    assert "testset plate 1" in asked[0]
    qtbot.waitUntil(lambda: bool(printer.uploads), timeout=5000)
    assert printer.uploads[0][0] == "/usb/testset_plate_01.bgcode"
    printer.job.update(progress=55.0, time_remaining=125, time_printing=150)
    qtbot.waitUntil(lambda: box.status is not None and box.status.state == "PRINTING"
                    and box.progress.value() == 55, timeout=5000)
    assert "PRINTING" in box.state_label.text() and "2m05s left" in box.detail_label.text()
    assert not box.print_btn.isEnabled() and box.stop_btn.isEnabled() and box.pause_btn.text() == "Pause"

    printer.state = "FINISHED"
    qtbot.waitUntil(lambda: "Print finished" in box.state_label.text(), timeout=5000)
    qtbot.wait(300)                                   # later polls keep the note
    assert "Print finished" in box.state_label.text() and box.print_btn.isEnabled()


def test_box_stop_asks_first(box, qtbot, printer, gcode, monkeypatch):
    from pathprogrammer.ui import printer_box
    PrusaLink(printer.host, api_key=API_KEY).upload(gcode)
    qtbot.waitUntil(lambda: box.status is not None and box.status.state == "PRINTING", timeout=5000)
    monkeypatch.setattr(printer_box.QMessageBox, "question", lambda *a: printer_box.QMessageBox.No)
    box.stop_btn.click()
    qtbot.wait(300)
    assert printer.state == "PRINTING"
    monkeypatch.setattr(printer_box.QMessageBox, "question", lambda *a: printer_box.QMessageBox.Yes)
    box.stop_btn.click()
    qtbot.waitUntil(lambda: printer.state == "STOPPED", timeout=5000)


def test_box_shows_unreachable_printer(qtbot, tmp_path):
    from pathprogrammer.ui.printer_box import PrinterBox
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    settings.setValue("printer/host", "127.0.0.1:1")
    b = PrinterBox(lambda: (None, ""), settings)
    qtbot.addWidget(b)
    qtbot.waitUntil(lambda: "not reachable" in b.state_label.text(), timeout=5000)
    assert not b.print_btn.isEnabled()
    b.shutdown()


def test_box_without_printer_set(qtbot, tmp_path):
    from pathprogrammer.ui.printer_box import PrinterBox
    b = PrinterBox(lambda: (None, ""), QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    qtbot.addWidget(b)
    assert "No printer set" in b.state_label.text() and not b.print_btn.isEnabled()
