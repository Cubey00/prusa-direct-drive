from PySide6.QtCore import Qt

from pathprogrammer.program import TouchMode, TouchPoint
from pathprogrammer.ui.point_model import COL_IMG, COL_TYPE, COL_VALUE, PointTableModel
from pathprogrammer.ui.point_table import PointTable


def test_image_step_column_clamps(qtbot):
    m = PointTableModel([])
    m.add(1, 2, TouchPoint(0, 0, TouchMode.FORCE, 50, 0.5, 5.0))
    assert m.data(m.index(0, COL_IMG)) == "5 g"
    m.setData(m.index(0, COL_IMG), 80.0)
    assert m.points[0].image_step == 50                      # at most the target value
    m.setData(m.index(0, COL_VALUE), 20.0)
    assert m.points[0].image_step == 20                      # follows a lowered value
    m.setData(m.index(0, COL_IMG), 0.0)
    assert m.data(m.index(0, COL_IMG)) == "off"
    m.setData(m.index(0, COL_TYPE), "depth")
    assert m.points[0].mode is TouchMode.DEPTH and m.points[0].image_step <= m.points[0].value


def test_template_carries_image_step(qtbot):
    m = PointTableModel([])
    t = PointTable(m)
    qtbot.addWidget(t)
    t.tpl_image.setValue(10.0)
    assert t.template().image_step == 10.0
    row = m.add(3, 4, t.template())
    assert m.points[row].image_step == 10.0
