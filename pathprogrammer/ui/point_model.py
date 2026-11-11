"""PointTableModel: the single shared model for the point table and the face view."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor

from .. import config
from ..program import TouchMode, TouchPoint

COLUMNS = ("#", "x (mm)", "y (mm)", "type", "value", "mm/s")
COL_X, COL_Y, COL_TYPE, COL_VALUE, COL_SPEED = 1, 2, 3, 4, 5

STATE_COLORS = {"running": QColor(255, 215, 80), "ok": QColor(170, 230, 170),
                "failed": QColor(240, 150, 150)}


class PointTableModel(QAbstractTableModel):
    pointsChanged = Signal()           # any change at all; the face view redraws on it

    def __init__(self, points=None, parent=None):
        super().__init__(parent)
        self.points = points if points is not None else []
        self.states = {}               # row -> "running" | "ok" | "failed"
        for sig in (self.dataChanged, self.rowsInserted, self.rowsRemoved, self.rowsMoved, self.modelReset):
            sig.connect(lambda *a: self.pointsChanged.emit())

    # --- Qt model API ---
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.points)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsDropEnabled              # drops between rows / below the last one
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled
        return f if index.column() == 0 else f | Qt.ItemIsEditable

    def supportedDropActions(self):
        return Qt.MoveAction

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        p, c = self.points[index.row()], index.column()
        if role == Qt.BackgroundRole and index.row() in self.states:
            return STATE_COLORS[self.states[index.row()]]
        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter
        if role == Qt.DisplayRole:
            return [index.row() + 1, f"{p.x:.2f}", f"{p.y:.2f}",
                    "Mass (g)" if p.mode is TouchMode.FORCE else "Depth (mm)",
                    f"{p.mode.label} = {p.value:g}{p.mode.unit}", f"{p.speed:g}"][c]
        if role == Qt.EditRole:
            return [index.row() + 1, p.x, p.y, p.mode.value, p.value, p.speed][c]
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False
        p, c = self.points[index.row()], index.column()
        try:
            if c == COL_X:
                p.x = float(value)
            elif c == COL_Y:
                p.y = float(value)
            elif c == COL_TYPE:
                mode = TouchMode(value)
                if mode is not p.mode:
                    p.mode = mode
                    lo, hi = mode.value_range
                    p.value = min(max(p.value, lo), hi)
            elif c == COL_VALUE:
                lo, hi = p.mode.value_range
                p.value = min(max(float(value), lo), hi)
            elif c == COL_SPEED:
                lo, hi = config.TOUCH_SPEED_RANGE
                p.speed = min(max(float(value), lo), hi)
            else:
                return False
        except (TypeError, ValueError):
            return False
        self.states.clear()            # results no longer describe the edited program
        self.dataChanged.emit(self.index(0, 0), self.index(len(self.points) - 1, len(COLUMNS) - 1))
        return True

    # --- editing helpers ---
    def set_xy(self, row, x, y):
        p = self.points[row]
        p.x, p.y = x, y
        self.states.clear()
        self.dataChanged.emit(self.index(0, 0), self.index(len(self.points) - 1, len(COLUMNS) - 1))

    def add(self, x, y, template=None):
        t = template or (self.points[-1] if self.points else TouchPoint(0, 0))
        row = len(self.points)
        self.states.clear()
        self.beginInsertRows(QModelIndex(), row, row)
        self.points.append(TouchPoint(x, y, t.mode, t.value, t.speed))
        self.endInsertRows()
        return row

    def remove(self, row):
        self.states.clear()
        self.beginRemoveRows(QModelIndex(), row, row)
        del self.points[row]
        self.endRemoveRows()

    def move(self, row, new_row):
        if not (0 <= new_row < len(self.points)) or new_row == row:
            return False
        dest = new_row + 1 if new_row > row else new_row      # Qt's destination is "insert before"
        self.states.clear()
        self.beginMoveRows(QModelIndex(), row, row, QModelIndex(), dest)
        self.points.insert(new_row, self.points.pop(row))
        self.endMoveRows()
        return True

    def set_points(self, points):
        self.beginResetModel()
        self.points[:] = points
        self.states.clear()
        self.endResetModel()

    def clear(self):
        self.set_points([])

    def set_state(self, row, state):
        if state is None:
            self.states.pop(row, None)
        else:
            self.states[row] = state
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1), [Qt.BackgroundRole])

    def clear_states(self):
        self.states.clear()
        if self.points:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.points) - 1, len(COLUMNS) - 1))
