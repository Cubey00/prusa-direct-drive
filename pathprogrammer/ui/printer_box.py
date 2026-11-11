"""Printer box: send the selected plate to the printer over PrusaLink and follow the print.
Network calls run in short background threads; results come back as Qt signals."""
import threading
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QGroupBox,
                               QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout)

from prusalink import DEFAULT_USER, PrusaLink, PrusaLinkError, fmt_duration

POLL_MS = 2000              # while the printer answers
POLL_OFFLINE_MS = 10000     # after it didn't

STATE_COLORS = {"PRINTING": "#2d7be5", "PAUSED": "#b36b00", "FINISHED": "#1b7a1b", "STOPPED": "#b36b00",
                "ERROR": "#c00000", "ATTENTION": "#c00000", "BUSY": "#b36b00"}


def link_from_settings(settings):
    host = settings.value("printer/host", "")
    if not host:
        return None
    return PrusaLink(host, password=settings.value("printer/password", "") or None,
                     user=settings.value("printer/user", DEFAULT_USER) or DEFAULT_USER,
                     api_key=settings.value("printer/api_key", "") or None)


class PrinterDialog(QDialog):
    """Address and login of the printer's PrusaLink, with a connection test."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Printer (PrusaLink)")
        self.settings = settings
        self.host = QLineEdit(settings.value("printer/host", ""))
        self.host.setPlaceholderText("e.g. 192.168.1.42")
        self.user = QLineEdit(settings.value("printer/user", DEFAULT_USER))
        self.password = QLineEdit(settings.value("printer/password", ""))
        self.password.setEchoMode(QLineEdit.Password)
        self.api_key = QLineEdit(settings.value("printer/api_key", ""))
        self.api_key.setPlaceholderText("only if the printer shows an API key instead")
        self.api_key.setEchoMode(QLineEdit.Password)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        test = QPushButton("Test connection")
        test.clicked.connect(self.test)

        form = QFormLayout()
        form.addRow("Address (IP)", self.host)
        form.addRow("User", self.user)
        form.addRow("Password", self.password)
        form.addRow("API key", self.api_key)
        hint = QLabel("Shown on the printer under Settings > Network > PrusaLink. "
                      "Saved on this computer only (not in the repository).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(hint)
        lay.addWidget(test)
        lay.addWidget(self.result_label)
        lay.addWidget(buttons)

    def _link(self):
        return PrusaLink(self.host.text(), password=self.password.text() or None,
                         user=self.user.text() or DEFAULT_USER, api_key=self.api_key.text() or None)

    def test(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            st = self._link().status()
            self.result_label.setStyleSheet("color: #1b7a1b;")
            self.result_label.setText(f"Connected: printer is {st.state}")
        except PrusaLinkError as e:
            self.result_label.setStyleSheet("color: #c00000;")
            self.result_label.setText(str(e))
        finally:
            QApplication.restoreOverrideCursor()

    def save(self):
        for key, w in (("host", self.host), ("user", self.user), ("password", self.password),
                       ("api_key", self.api_key)):
            self.settings.setValue(f"printer/{key}", w.text().strip())
        self.accept()


class PrinterBox(QGroupBox):
    statusReady = Signal(object)    # PrintStatus, from a background thread
    pollFailed = Signal(str)
    actionDone = Signal(str)        # message after upload/stop/pause/resume
    actionFailed = Signal(str)

    def __init__(self, plate_source, settings=None, parent=None):
        """plate_source() -> (gcode Path or None, label) of the plate selected in the Print plates box."""
        super().__init__("Printer (PrusaLink)", parent)
        self.settings = settings or QSettings("TUDelft", "pathprogrammer")
        self.plate_source = plate_source
        self.status = None
        self._polling = self._busy = self._finished_note = False

        self.settings_btn = QPushButton("Printer settings...")
        self.print_btn = QPushButton("Print this plate")
        self.print_btn.setStyleSheet("font-weight: bold;")
        self.state_label = QLabel("")
        self.state_label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: gray;")
        self.pause_btn = QPushButton("Pause")
        self.stop_btn = QPushButton("Stop print")

        g = QGridLayout(self)
        g.addWidget(self.settings_btn, 0, 0)
        g.addWidget(self.print_btn, 0, 1)
        g.addWidget(self.state_label, 1, 0, 1, 2)
        g.addWidget(self.progress, 2, 0, 1, 2)
        g.addWidget(self.detail_label, 3, 0, 1, 2)
        g.addWidget(self.pause_btn, 4, 0)
        g.addWidget(self.stop_btn, 4, 1)

        self.settings_btn.clicked.connect(self.open_settings)
        self.print_btn.clicked.connect(self.print_plate)
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn.clicked.connect(self.stop_print)
        self.statusReady.connect(self._on_status)
        self.pollFailed.connect(self._on_poll_failed)
        self.actionDone.connect(self._on_action_done)
        self.actionFailed.connect(self._on_action_failed)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self._restart_polling()

    # ---------------- state ----------------
    def configured(self):
        return bool(self.settings.value("printer/host", ""))

    def _restart_polling(self):
        self.status = None
        if self.configured():
            self.state_label.setText(f"Connecting to {self.settings.value('printer/host')} ...")
            self.timer.start(POLL_MS)
            self.poll()
        else:
            self.timer.stop()
            self.state_label.setText("No printer set: use 'Printer settings...'")
        self.refresh()

    def refresh(self):
        """Enable the buttons that make sense now."""
        st, busy = self.status, self._busy
        gcode, _ = self.plate_source()
        self.print_btn.setEnabled(bool(gcode) and st is not None and st.can_start and not busy)
        self.print_btn.setToolTip("" if gcode else "Select a sliced plate in the Print plates box")
        has_job = st is not None and st.job_id is not None and st.state in ("PRINTING", "PAUSED")
        self.pause_btn.setEnabled(has_job and not busy)
        self.pause_btn.setText("Resume" if st is not None and st.state == "PAUSED" else "Pause")
        self.stop_btn.setEnabled(has_job and not busy)
        self.progress.setVisible(st is not None and st.progress is not None)

    # ---------------- polling ----------------
    def _run(self, work, ok_signal, fail_signal):
        def target():
            try:
                ok_signal.emit(work())
            except PrusaLinkError as e:
                fail_signal.emit(str(e))
            except Exception as e:                     # never let a thread die silently
                fail_signal.emit(f"{type(e).__name__}: {e}")
        threading.Thread(target=target, daemon=True).start()

    def poll(self):
        if self._polling or not self.configured():
            return
        self._polling = True
        try:
            link = link_from_settings(self.settings)
        except PrusaLinkError as e:
            self._polling = False
            self._on_poll_failed(str(e))
            return
        self._run(link.status, self.statusReady, self.pollFailed)

    def _on_status(self, st):
        self._polling = False
        was = self.status
        self.status = st
        if self.timer.interval() != POLL_MS:
            self.timer.setInterval(POLL_MS)
        text = f"Printer: <b>{st.state}</b>"
        if st.file:
            text += f" &nbsp; {st.file}"
        if was is not None and was.state == "PRINTING" and st.state == "FINISHED":
            self._finished_note = True
        elif st.state != "FINISHED":
            self._finished_note = False
        if self._finished_note:
            text += "<br>Print finished: take the plate off before the next one."
        self.state_label.setStyleSheet(f"color: {STATE_COLORS.get(st.state, '#1a1a1a')};")
        self.state_label.setText(text)
        if st.progress is not None:
            self.progress.setValue(int(round(st.progress)))
        parts = []
        if st.time_printing is not None:
            parts.append(f"printing {fmt_duration(st.time_printing)}")
        if st.time_remaining is not None and st.state in ("PRINTING", "PAUSED"):
            parts.append(f"about {fmt_duration(st.time_remaining)} left")
        if st.temp_nozzle is not None:
            parts.append(f"nozzle {st.temp_nozzle:.0f} C, bed {st.temp_bed or 0:.0f} C")
        self.detail_label.setText(", ".join(parts))
        self.refresh()

    def _on_poll_failed(self, message):
        self._polling = False
        self.status = None
        self.timer.setInterval(POLL_OFFLINE_MS)
        self.state_label.setStyleSheet("color: #c00000;")
        self.state_label.setText(f"Printer not reachable: {message}")
        self.detail_label.setText("")
        self.refresh()

    # ---------------- actions ----------------
    def _action(self, work, busy_text):
        try:
            link = link_from_settings(self.settings)
        except PrusaLinkError as e:
            QMessageBox.warning(self, "Printer", str(e))
            return
        self._busy = True
        self.state_label.setText(busy_text)
        self.refresh()
        self._run(lambda: work(link), self.actionDone, self.actionFailed)

    def print_plate(self):
        gcode, label = self.plate_source()
        if not gcode:
            return
        gcode = Path(gcode)
        msg = (f"Print {label} on the printer now?\n\n"
               "- The bed is empty and the sheet is clean.\n"
               "- The tactile sensor and its bracket are off the head.\n\n"
               "The start G-code still wipes, probes and purges in the front strip that the stops "
               "block (Y < 60) and parks at the back: stay at the printer during the start.")
        if QMessageBox.question(self, "Print this plate", msg) != QMessageBox.Yes:
            return
        remote_name = f"{gcode.parent.name}_{gcode.name}"
        self._action(lambda link: f"Uploaded {link.upload(gcode, remote_name)}; the printer starts it now.",
                     f"Uploading {gcode.name} ...")

    def toggle_pause(self):
        st = self.status
        if st is None or st.job_id is None:
            return
        if st.state == "PAUSED":
            self._action(lambda link: (link.resume(st.job_id), "Resumed")[1], "Resuming ...")
        else:
            self._action(lambda link: (link.pause(st.job_id), "Paused")[1], "Pausing ...")

    def stop_print(self):
        st = self.status
        if st is None or st.job_id is None:
            return
        if QMessageBox.question(self, "Stop print",
                                f"Stop the running print{f' ({st.file})' if st.file else ''}? "
                                "It cannot be resumed.") != QMessageBox.Yes:
            return
        self._action(lambda link: (link.stop(st.job_id), "Print stopped")[1], "Stopping ...")

    def _on_action_done(self, message):
        self._busy = False
        self.state_label.setStyleSheet("")
        self.state_label.setText(message)
        self.refresh()
        self.poll()

    def _on_action_failed(self, message):
        self._busy = False
        self.refresh()
        QMessageBox.warning(self, "Printer", message)
        self.poll()

    def open_settings(self):
        if PrinterDialog(self.settings, self).exec() == QDialog.Accepted:
            self._restart_polling()

    def shutdown(self):
        self.timer.stop()
