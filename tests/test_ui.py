import pytest
from PySide6.QtCore import QPointF, QSettings, Qt

from pathprogrammer import config
from pathprogrammer import program as pm
from pathprogrammer.ui.main_window import MainWindow

MODEL = config.PROJECT_DIR / "model.3mf"
pytestmark = pytest.mark.skipif(not MODEL.exists(), reason="model.3mf not present")


@pytest.fixture
def win(qtbot, tmp_path):
    w = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    qtbot.addWidget(w)
    w.settings.setValue("save_folder", str(tmp_path))
    assert w.open_model(MODEL)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_table_and_scene_stay_in_sync(win):
    win.add_point_at(5, 5)
    win.add_point_at(20, 6)
    win.add_point_at(30, 7)
    assert len(win.view.markers) == 3 and len(win.view.arrows) == 2
    win.points.move(0, 2)
    assert [m.pos().x() for m in win.view.markers] == [20, 30, 5]
    win.points.remove(1)
    assert len(win.view.markers) == 2


def test_click_adds_point_at_face_coords(win, qtbot):
    vp = win.view.mapFromScene(QPointF(10.0, 4.0))
    qtbot.mouseClick(win.view.viewport(), Qt.LeftButton, pos=vp)
    assert win.points.rowCount() == 1
    p = win.points.points[0]
    assert abs(p.x - 10) < 0.5 and abs(p.y - 4) < 0.5


def test_save_load_roundtrip(win, tmp_path):
    win.add_point_at(5, 5)
    f = tmp_path / "p.json"
    pm.save(win.collect_program(), f)
    win.points.clear()
    win.load_program(f, ask=False)
    assert win.points.rowCount() == 1


def test_delete_badge_and_key(win, qtbot):
    for x in (5, 20, 30):
        win.add_point_at(x, 5)
    win.table.select_row(1)
    m = win.view.markers[1]
    assert m.isSelected() and m.delete_badge.isVisible()
    # the marker ignores zoom, so its children sit at fixed pixel offsets from it
    badge_pos = win.view.mapFromScene(m.scenePos()) + m.delete_badge.pos().toPoint()
    qtbot.mouseClick(win.view.viewport(), Qt.LeftButton, pos=badge_pos)
    qtbot.waitUntil(lambda: win.points.rowCount() == 2)
    assert [p.x for p in win.points.points] == [5, 30]       # badge click did not add a point
    win.table.select_row(0)
    win.view.setFocus()
    qtbot.keyClick(win.view, Qt.Key_Delete)
    qtbot.waitUntil(lambda: win.points.rowCount() == 1)
    assert win.points.points[0].x == 30


def test_template_sets_new_points(win):
    t = win.table
    t.tpl_type.setCurrentIndex(t.tpl_type.findData("depth"))
    assert t.tpl_value.value() == 0.1
    t.tpl_value.setValue(0.05)
    t.tpl_speed.setValue(1.2)
    win.add_point_at(5, 5)
    p = win.points.points[0]
    assert (p.mode.value, p.value, p.speed) == ("depth", 0.05, 1.2)
    t.tpl_type.setCurrentIndex(t.tpl_type.findData("force"))
    assert t.tpl_value.value() == 50.0
    t.tpl_type.setCurrentIndex(t.tpl_type.findData("depth"))
    assert t.tpl_value.value() == 0.05          # remembered per type


def test_single_click_replaces_placeholder(win, qtbot):
    x, _ = win.left.face_fields["back_left"]
    assert x.text() == "--"
    qtbot.mouseClick(x.lineEdit(), Qt.LeftButton)
    qtbot.wait(20)
    qtbot.keyClicks(x, "12")
    qtbot.keyClick(x, Qt.Key_Return)
    assert x.value() == 12


def test_selected_row_shows_run_state(win):
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QStyleOptionViewItem
    win.add_point_at(1, 1)
    win.points.set_state(0, "ok")
    win.table.select_row(0)
    idx = win.points.index(0, 1)
    opt = QStyleOptionViewItem()
    win.table.view.itemDelegate().initStyleOption(opt, idx)
    assert opt.palette.color(QPalette.Highlight) == win.points.data(idx, Qt.BackgroundRole).darker(115)


def test_edits_clear_run_states(win):
    win.add_point_at(5, 5)
    win.add_point_at(10, 10)
    win.points.set_state(0, "ok")
    win.points.set_state(1, "ok")
    win.points.remove(1)
    assert win.points.states == {}
    win.points.set_state(0, "ok")
    win.points.clear()
    win.add_point_at(1, 1)
    assert win.points.states == {}
