"""Operator-entered optical setup records; these dialogs never access hardware."""
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QLabel, QLineEdit, QMessageBox)

from focusfield.calibration import WDReference
from focusfield.setup import ScoutRecipe


def measured_wd_dialog(parent, fixture):
    dialog = QDialog(parent)
    dialog.setWindowTitle("Record independently measured working distance — no motion")
    form = QFormLayout(dialog)
    notice = QLabel("Manually read printer Z and independently measure from the defined lens plane to the patterned "
                    "surface at TWO supervised positions. This dialog never moves hardware and never enables motion.")
    notice.setWordWrap(True)
    form.addRow(notice)
    fields = {}
    text = {"fixture_id": fixture.fixture_id, "camera_serial": fixture.camera_serial,
            "lens_reference_plane": "", "patterned_surface_notes": "", "method": "", "operator": ""}
    for key, value in text.items():
        fields[key] = QLineEdit(value)
        form.addRow(key.replace("_", " ").capitalize(), fields[key])
    values = dict(reference_z_mm=fixture.reference_z_mm, reference_wd_mm=fixture.reference_wd_mm,
                  check_z_mm=fixture.reference_z_mm, check_wd_mm=fixture.reference_wd_mm,
                  uncertainty_mm=fixture.wd_uncertainty_mm)
    for key, value in values.items():
        widget = QDoubleSpinBox()
        widget.setRange(0, 1000)
        widget.setDecimals(4)
        widget.setValue(value)
        fields[key] = widget
        form.addRow(key.replace("_", " ").capitalize(), widget)
    buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    form.addRow(buttons)
    result = []
    def save():
        try:
            record = WDReference.measured(**{k: w.text() if isinstance(w, QLineEdit) else w.value()
                                            for k, w in fields.items()})
            result.append(record)
            dialog.accept()
        except ValueError as exc:
            QMessageBox.warning(dialog, "WD reference", str(exc))
    buttons.accepted.connect(save)
    buttons.rejected.connect(dialog.reject)
    dialog.exec()
    return result[0] if result else None


def scout_recipe_dialog(parent, fixture):
    dialog = QDialog(parent)
    dialog.setWindowTitle("Bounded focus scout — propose a fine scan")
    form = QFormLayout(dialog)
    notice = QLabel("Bounds come from measured mechanical clearance, never image scale. "
                    "The scout may widen once to the full verified Z envelope; a proposed fine scan never starts automatically.")
    notice.setWordWrap(True)
    form.addRow(notice)
    defaults = dict(z_start_mm=max(fixture.safe_z_min_mm, fixture.reference_z_mm-0.5),
                    z_end_mm=min(fixture.safe_z_max_mm, fixture.reference_z_mm+0.5),
                    step_mm=0.1, fine_step_mm=0.025)
    fields = {}
    for key, value in defaults.items():
        widget = QDoubleSpinBox()
        widget.setRange(0.001, 1000)
        widget.setDecimals(3)
        widget.setValue(value)
        fields[key] = widget
        form.addRow(key.replace("_", " ").capitalize(), widget)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    form.addRow(buttons)
    result = []
    def accept():
        try:
            recipe = ScoutRecipe(**{k: w.value() for k, w in fields.items()}).validate(fixture)
            result.append(recipe)
            dialog.accept()
        except ValueError as exc:
            QMessageBox.warning(dialog, "Scout limits", str(exc))
    buttons.accepted.connect(accept)
    buttons.rejected.connect(dialog.reject)
    dialog.exec()
    return result[0] if result else None
