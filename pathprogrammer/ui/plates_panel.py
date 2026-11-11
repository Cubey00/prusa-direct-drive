"""Print plates: import a dataset (or the built-in test set), lay it out on plates and slice them.
The work runs in a QThread (slicing takes a while); the plates are shown from their manifests."""
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QSettings, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QGroupBox, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QToolTip, QWidget)

from slicing import pipeline
from slicing.config import PROJECT_DIR
from slicing.slicer import SlicerError, open_in_viewer
from slicing.testset import write_testset

TESTSET_DIR = PROJECT_DIR / "testset"
PLATES_DIR = PROJECT_DIR / "plates"


class PlateWorker(QObject):
    status = Signal(str)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, dataset, out_dir, make_testset=False):
        super().__init__()
        self.dataset, self.out_dir, self.make_testset = Path(dataset), Path(out_dir), make_testset

    def run(self):
        try:
            if self.make_testset:
                n = len(write_testset(self.dataset))
                self.status.emit(f"Wrote {n} test parts to {self.dataset}")
            self.done.emit(pipeline.run(self.dataset, self.out_dir, on_status=self.status.emit))
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


class PlatePreview(QWidget):
    """Top view of one plate in bed coordinates: door (front, low Y) at the bottom."""
    BLOCKED = QColor("#e2e2e2")
    PART = QColor("#2d7be5")
    FIDUCIAL = QColor("#d33a2c")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manifest = None
        self.setMinimumHeight(220)
        self.setMouseTracking(True)

    def set_manifest(self, manifest):
        self.manifest = manifest
        self.update()

    def _frame(self):
        """(scale px/mm, left px, bottom px) fitting the bed in the widget."""
        bw, bh = self.manifest["bed_size"]
        m = 18
        s = min((self.width() - 2 * m) / bw, (self.height() - 2 * m) / bh)
        left = (self.width() - bw * s) / 2
        bottom = (self.height() + bh * s) / 2
        return s, left, bottom

    def _to_px(self, x, y):
        s, left, bottom = self._frame()
        return QPointF(left + x * s, bottom - y * s)

    def _rect(self, x0, y0, x1, y1):
        return QRectF(self._to_px(x0, y1), self._to_px(x1, y0))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        if not self.manifest:
            p.setPen(QColor("gray"))
            p.drawText(self.rect(), Qt.AlignCenter, "No plate yet")
            return
        m = self.manifest
        bw, bh = m["bed_size"]
        (ax0, ax1), (ay0, ay1) = m.get("usable_area", [[0, bw], [0, bh]])
        p.setPen(Qt.NoPen)
        p.setBrush(self.BLOCKED)
        p.drawRect(self._rect(0, 0, bw, ay0))
        p.drawRect(self._rect(0, ay1, bw, bh))
        p.drawRect(self._rect(0, ay0, ax0, ay1))
        p.drawRect(self._rect(ax1, ay0, bw, ay1))
        p.setPen(QPen(QColor("#555555"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(self._rect(0, 0, bw, bh))
        p.setPen(QColor("gray"))
        door = self._to_px(bw / 2, 0)
        p.drawText(QRectF(door.x() - 60, door.y() + 2, 120, 14), Qt.AlignCenter, "door")

        p.setPen(Qt.NoPen)
        p.setBrush(self.PART)
        for part in m["parts"]:
            w, d = part["size"][:2]
            p.drawRect(self._rect(part["x"] - w / 2, part["y"] - d / 2, part["x"] + w / 2, part["y"] + d / 2))
        r = m.get("fiducial_shape", {}).get("base_d", 8) / 2
        s, _, _ = self._frame()
        for f in m["fiducials"]:
            c = self._to_px(f["x"], f["y"])
            p.setPen(Qt.NoPen)
            p.setBrush(self.FIDUCIAL)
            p.drawEllipse(c, r * s, r * s)
            p.setPen(self.FIDUCIAL)
            p.drawText(QRectF(c.x() - 20, c.y() - r * s - 15, 40, 14), Qt.AlignCenter, f["name"])

    def item_at(self, pos):
        """Part id or fiducial name under a widget position, else None."""
        if not self.manifest:
            return None
        s, left, bottom = self._frame()
        x, y = (pos.x() - left) / s, (bottom - pos.y()) / s
        for part in self.manifest["parts"]:
            w, d = part["size"][:2]
            if abs(x - part["x"]) <= max(w / 2, 2) and abs(y - part["y"]) <= max(d / 2, 2):
                return f"{part['id']}  (X{part['x']:g} Y{part['y']:g})"
        for f in self.manifest["fiducials"]:
            if (x - f["x"]) ** 2 + (y - f["y"]) ** 2 <= 16:
                return f"{f['name']}  (X{f['x']:g} Y{f['y']:g})"
        return None

    def mouseMoveEvent(self, e):
        text = self.item_at(e.position())
        if text:
            QToolTip.showText(e.globalPosition().toPoint(), text, self)
        else:
            QToolTip.hideText()


class PlatesPanel(QGroupBox):
    def __init__(self, settings=None, parent=None):
        super().__init__("Print plates", parent)
        self.settings = settings or QSettings("TUDelft", "pathprogrammer")
        self.thread = self.worker = None
        self.result = None

        self.dataset_btn = QPushButton("Import dataset...")
        self.dataset_btn.setToolTip("Folder of .3mf and .stl parts: lay them out on plates and slice them")
        self.testset_btn = QPushButton("Import test set")
        self.testset_btn.setToolTip(f"Write the 30 simple test parts to {TESTSET_DIR.name}/, "
                                    "lay them out and slice them")
        self.status = QLabel("Import a dataset or the test set.")
        self.status.setWordWrap(True)
        self.plate_list = QListWidget()
        self.plate_list.setMaximumHeight(90)
        self.preview = PlatePreview()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        self.log.setPlaceholderText("Import log")
        self.view3d_btn = QPushButton("Show in 3D")
        self.view3d_btn.setToolTip("Open the selected plate's G-code in PrusaSlicer's 3D G-code viewer")
        self.view3d_btn.setEnabled(False)
        self.open_btn = QPushButton("Open plates folder")
        self.open_btn.setEnabled(False)

        g = QGridLayout(self)
        g.addWidget(self.dataset_btn, 0, 0)
        g.addWidget(self.testset_btn, 0, 1)
        g.addWidget(self.status, 1, 0, 1, 2)
        g.addWidget(self.plate_list, 2, 0, 1, 2)
        g.addWidget(self.preview, 3, 0, 1, 2)
        g.addWidget(self.log, 4, 0, 1, 2)
        g.addWidget(self.view3d_btn, 5, 0)
        g.addWidget(self.open_btn, 5, 1)

        self.dataset_btn.clicked.connect(self.import_dataset_dialog)
        self.testset_btn.clicked.connect(self.import_testset)
        self.plate_list.currentRowChanged.connect(self._show_plate)
        self.open_btn.clicked.connect(self._open_folder)
        self.view3d_btn.clicked.connect(self.show_in_3d)

    # ---------------- import ----------------
    def import_dataset_dialog(self):
        start = self.settings.value("dataset_folder", str(PROJECT_DIR))
        folder = QFileDialog.getExistingDirectory(self, "Select dataset folder (.3mf / .stl files)", start)
        if folder:
            self.settings.setValue("dataset_folder", folder)
            self.import_dataset(folder)

    def import_dataset(self, folder):
        folder = Path(folder)
        return self._start(folder, PLATES_DIR / folder.name, make_testset=False)

    def import_testset(self):
        return self._start(TESTSET_DIR, PLATES_DIR / TESTSET_DIR.name, make_testset=True)

    def _start(self, dataset, out_dir, make_testset):
        if self.thread is not None:
            return False
        self.log.clear()
        self.plate_list.clear()
        self.preview.set_manifest(None)
        self.result = None
        self.view3d_btn.setEnabled(False)
        self.status.setText(f"Importing {dataset.name} ...")
        self._set_busy(True)
        self.thread = QThread(self)
        self.worker = PlateWorker(dataset, out_dir, make_testset)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.status.connect(self.log.appendPlainText)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.thread.start()
        return True

    def _finish_thread(self):
        self.thread.quit()
        self.thread.wait()
        self.thread = self.worker = None
        self._set_busy(False)

    def _on_done(self, result):
        self._finish_thread()
        self.result = result
        for m in result["manifests"]:
            gcode = m.get("gcode") or "not sliced"
            item = QListWidgetItem(f"Plate {m['plate']}: {len(m['parts'])} parts - {gcode}")
            self.plate_list.addItem(item)
        if self.plate_list.count():
            self.plate_list.setCurrentRow(0)
        self.open_btn.setEnabled(True)
        text = (f"{result['parts']} parts on {len(result['plates'])} plate(s), "
                f"{len(result['rejected'])} rejected.")
        if result["slice_error"]:
            text += f"\nNot sliced: {result['slice_error']}"
            self.status.setStyleSheet("color: #b36b00;")
        elif result["slice_failed"]:
            text += f"\nSlicing failed for {', '.join(result['slice_failed'])}; see the log."
            self.status.setStyleSheet("color: #c00000;")
        else:
            text += " All plates sliced."
            self.status.setStyleSheet("color: #1b7a1b;")
        self.status.setText(text)

    def _on_failed(self, message):
        self._finish_thread()
        self.status.setStyleSheet("color: #c00000;")
        self.status.setText("Import failed.")
        self.log.appendPlainText(message)
        QMessageBox.warning(self, "Import", message)

    def _set_busy(self, busy):
        self.dataset_btn.setEnabled(not busy)
        self.testset_btn.setEnabled(not busy)
        if busy:
            self.status.setStyleSheet("")

    def _show_plate(self, row):
        if self.result and 0 <= row < len(self.result["manifests"]):
            m = self.result["manifests"][row]
            self.preview.set_manifest(m)
            self.view3d_btn.setEnabled(bool(m.get("gcode")))

    def selected_gcode(self):
        row = self.plate_list.currentRow()
        if not self.result or not 0 <= row < len(self.result["manifests"]):
            return None
        name = self.result["manifests"][row].get("gcode")
        return Path(self.result["out"]) / name if name else None

    def show_in_3d(self):
        gcode = self.selected_gcode()
        if gcode is None:
            return
        try:
            open_in_viewer(gcode)
        except (SlicerError, OSError) as e:
            QMessageBox.warning(self, "Show in 3D", str(e))

    def _open_folder(self):
        if self.result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.result["out"]))

    def is_busy(self):
        return self.thread is not None

    def shutdown(self):
        """Wait for a running import (called when the window closes)."""
        if self.thread is not None:
            self.thread.quit()
            self.thread.wait()
