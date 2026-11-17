"""Right panel: ordered point list with type/value/speed editors, drag to reorder."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QComboBox, QDoubleSpinBox, QHBoxLayout,
                               QHeaderView, QLabel, QPushButton, QStyledItemDelegate, QTableView,
                               QVBoxLayout, QWidget)

from .. import config
from ..program import TouchMode, TouchPoint
from .left_panel import SelectAllSpinBox
from .point_model import COL_IMG, COL_SPEED, COL_TYPE, COL_VALUE, COL_X, COL_Y

EDIT_TRIGGERS = (QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                 | QAbstractItemView.SelectedClicked)


class PointDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        state = index.data(Qt.BackgroundRole)
        if state is not None:            # keep the run state visible on the selected row
            option.palette.setColor(QPalette.Highlight, state.darker(115))

    def createEditor(self, parent, option, index):
        c = index.column()
        p = index.model().points[index.row()]
        if c == COL_TYPE:
            box = QComboBox(parent)
            box.addItem("Mass (g)", TouchMode.FORCE.value)
            box.addItem("Depth (mm)", TouchMode.DEPTH.value)
            return box
        spin = QDoubleSpinBox(parent)
        spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        spin.setDecimals(2)
        if c in (COL_X, COL_Y):
            spin.setRange(-1e4, 1e4)
        elif c == COL_VALUE:
            spin.setRange(*p.mode.value_range)
            spin.setSuffix(f" {p.mode.unit}")
        elif c == COL_SPEED:
            spin.setRange(*config.TOUCH_SPEED_RANGE)
            spin.setSuffix(" mm/s")
        elif c == COL_IMG:
            spin.setRange(0.0, p.value)
            spin.setSuffix(f" {p.mode.unit}")
            spin.setSpecialValueText("off")
        return spin

    def setEditorData(self, editor, index):
        v = index.data(Qt.EditRole)
        if isinstance(editor, QComboBox):
            editor.setCurrentIndex(editor.findData(v))
        else:
            editor.setValue(float(v))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData() if isinstance(editor, QComboBox) else editor.value())


class ReorderTableView(QTableView):
    """Drag a row to a new place; the move goes through PointTableModel.move so every view follows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropOverwriteMode(False)

    def dropEvent(self, e):
        if e.source() is not self:
            e.ignore()
            return
        rows = self.selectionModel().selectedRows()
        if not rows:
            e.ignore()
            return
        src = rows[0].row()
        pos = e.position().toPoint()
        idx = self.indexAt(pos)
        if idx.isValid():
            rect = self.visualRect(idx)
            before = idx.row() + (1 if pos.y() > rect.center().y() else 0)
        else:
            before = self.model().rowCount()
        dest = before - 1 if src < before else before
        if self.model().move(src, dest):
            self.selectRow(dest)
        e.setDropAction(Qt.IgnoreAction)       # stop the view from removing the "dragged-out" row
        e.accept()


class PointTable(QWidget):
    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        title = QLabel("Points in ascending order")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        top.addWidget(title)
        top.addStretch()
        self.add_btn = QPushButton("Add point")
        top.addWidget(self.add_btn)
        lay.addLayout(top)

        # --- template for new points ---
        tpl = QHBoxLayout()
        tpl.addWidget(QLabel("New points:"))
        self.tpl_type = QComboBox()
        self.tpl_type.addItem("Mass (M)", TouchMode.FORCE.value)
        self.tpl_type.addItem("Depth (d)", TouchMode.DEPTH.value)
        self.tpl_value = SelectAllSpinBox()
        self.tpl_speed = SelectAllSpinBox()
        for s in (self.tpl_value, self.tpl_speed):
            s.setButtonSymbols(QAbstractSpinBox.NoButtons)
            s.setDecimals(2)
        self.tpl_speed.setRange(*config.TOUCH_SPEED_RANGE)
        self.tpl_speed.setSuffix(" mm/s")
        self.tpl_speed.setValue(config.TOUCH_SPEED)
        self.tpl_image = SelectAllSpinBox()
        self.tpl_image.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.tpl_image.setDecimals(2)
        self.tpl_image.setSpecialValueText("no images")
        self._tpl_images = dict((TouchMode(k), v) for k, v in config.IMAGE_STEP_DEFAULT.items())
        self.tpl_image.valueChanged.connect(lambda v: self._tpl_images.__setitem__(self._tpl_mode(), v))
        self._tpl_values = {TouchMode.FORCE: 50.0, TouchMode.DEPTH: 2.0}   # remembered per type
        self.tpl_type.currentIndexChanged.connect(self._tpl_type_changed)
        self.tpl_value.valueChanged.connect(lambda v: self._tpl_values.__setitem__(self._tpl_mode(), v))
        self._tpl_type_changed()
        for w in (self.tpl_type, self.tpl_value, self.tpl_speed, QLabel("image every"), self.tpl_image):
            tpl.addWidget(w)
        lay.addLayout(tpl)

        self.view = ReorderTableView()
        self.view.setModel(model)
        self.view.setItemDelegate(PointDelegate(self.view))
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.verticalHeader().hide()
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.view.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.view.setColumnWidth(0, 32)
        self.view.setEditTriggers(EDIT_TRIGGERS)
        lay.addWidget(self.view)
        hint = QLabel("Drag rows to reorder. Delete removes the selected point.")
        hint.setStyleSheet("color: gray;")
        lay.addWidget(hint)

        row = QHBoxLayout()
        self.up_btn, self.down_btn, self.del_btn = QPushButton("Move up"), QPushButton("Move down"), QPushButton("Delete")
        for b in (self.up_btn, self.down_btn, self.del_btn):
            row.addWidget(b)
        lay.addLayout(row)
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(+1))
        self.del_btn.clicked.connect(self.delete_selected)
        self.del_shortcut = QShortcut(QKeySequence.Delete, self.view, self.delete_selected,
                                      context=Qt.WidgetShortcut)
        self._editable = True

    def _tpl_mode(self):
        return TouchMode(self.tpl_type.currentData())

    def _tpl_type_changed(self):
        mode = self._tpl_mode()
        value = self._tpl_values[mode]
        self.tpl_value.blockSignals(True)
        self.tpl_value.setRange(*mode.value_range)
        self.tpl_value.setSuffix(f" {mode.unit}")
        self.tpl_value.setValue(value)
        self.tpl_value.blockSignals(False)
        self.tpl_image.blockSignals(True)
        self.tpl_image.setRange(0.0, mode.value_range[1])
        self.tpl_image.setSuffix(f" {mode.unit}")
        self.tpl_image.setValue(self._tpl_images[mode])
        self.tpl_image.blockSignals(False)

    def template(self):
        """TouchPoint carrying the type/value/speed for new points (x, y unused)."""
        return TouchPoint(0, 0, self._tpl_mode(), self.tpl_value.value(), self.tpl_speed.value(),
                          self.tpl_image.value())

    def selection_model(self):
        return self.view.selectionModel()

    def current_row(self):
        rows = self.view.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def select_row(self, row):
        self.view.selectRow(row)

    def _move(self, d):
        r = self.current_row()
        if r is not None and self.model.move(r, r + d):
            self.select_row(r + d)

    def delete_row(self, r):
        if not self._editable or not (0 <= r < self.model.rowCount()):
            return
        self.model.remove(r)
        if self.model.rowCount():
            self.select_row(min(r, self.model.rowCount() - 1))

    def delete_selected(self):
        r = self.current_row()
        if r is not None:
            self.delete_row(r)

    def set_editable(self, on):
        self._editable = on
        for b in (self.add_btn, self.up_btn, self.down_btn, self.del_btn):
            b.setEnabled(on)
        self.view.setEditTriggers(EDIT_TRIGGERS if on else QAbstractItemView.NoEditTriggers)
        self.view.setDragEnabled(on)
