"""Operator-entered optical setup records; these dialogs never access hardware."""
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QLabel, QLineEdit, QMessageBox)

from focusfield.calibration import WDReference
from focusfield.setup import ScoutRecipe, suggest_scout


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


def scout_recipe_dialog(parent, fixture, nominal_wd_mm, *, physical):
    suggested = suggest_scout(fixture, nominal_wd_mm)
    dialog = QDialog(parent)
    dialog.setWindowTitle("Review focus scout")
    form = QFormLayout(dialog)
    notice = QLabel(f"Requested working distance {nominal_wd_mm:g} mm. "
                    "The scout proposes a fine scan for separate review.")
    notice.setWordWrap(True)
    form.addRow(notice)
    defaults = dict(z_start_mm=suggested.z_start_mm, z_end_mm=suggested.z_end_mm,
                    step_mm=suggested.step_mm, fine_step_mm=suggested.fine_step_mm)
    fields = {}
    for key, value in defaults.items():
        widget = QDoubleSpinBox()
        widget.setRange(0.001, 1000)
        widget.setDecimals(3)
        widget.setValue(value)
        fields[key] = widget
        form.addRow(key.replace("_", " ").capitalize(), widget)
    widen = QCheckBox("Allow one extension to the full verified interval")
    form.addRow(widen)
    motion = QLabel()
    motion.setWordWrap(True)
    form.addRow(motion)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    approve = buttons.button(QDialogButtonBox.Ok)
    approve.setText("Approve supervised scout" if physical else "Run simulated scout")
    approve.setAutoDefault(False)
    buttons.button(QDialogButtonBox.Cancel).setDefault(True)
    form.addRow(buttons)
    result = []

    def scout_plan():
        return ScoutRecipe(**{k: w.value() for k, w in fields.items()},
                           allow_widen=widen.isChecked()).validate(fixture)

    def update_motion(*_):
        try:
            recipe = scout_plan()
            initial = recipe.positions(recipe.z_start_mm, recipe.z_end_mm)
            text = f"Z {initial[0]:g}–{initial[-1]:g} mm · {len(initial)} positions"
            if recipe.allow_widen:
                extra = recipe.positions(fixture.safe_z_min_mm, fixture.safe_z_max_mm)
                text += f" · Up to {len(set(initial + extra))} positions over Z {extra[0]:g}–{extra[-1]:g} mm"
            motion.setText(text)
            approve.setEnabled(True)
        except ValueError as exc:
            motion.setText(str(exc))
            approve.setEnabled(False)

    def accept():
        try:
            result.append(scout_plan())
            dialog.accept()
        except ValueError as exc:
            QMessageBox.warning(dialog, "Scout limits", str(exc))
    buttons.accepted.connect(accept)
    buttons.rejected.connect(dialog.reject)
    widen.toggled.connect(update_motion)
    for widget in fields.values():
        widget.valueChanged.connect(update_motion)
    update_motion()
    dialog.exec()
    return result[0] if result else None
