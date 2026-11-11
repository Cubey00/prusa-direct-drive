"""Centre panel: face image with numbered point markers, sequence arrows and calibration X markers.
Scene units are mm, origin at the face's back-left corner, scene y = face y (toward the front)."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView, QStyle)

FACE_BG = QColor(255, 220, 80)
SEL_PAD = 3             # px between a marker and its selection outline


class PointMarker(QGraphicsRectItem):
    def __init__(self, view, row):
        super().__init__()
        self.view, self.row = view, row
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
                      | QGraphicsItem.ItemIgnoresTransformations | QGraphicsItem.ItemSendsGeometryChanges)
        self.setRect(-12, -12, 24, 24)          # pixels, since transformations are ignored
        self.setBrush(QBrush(QColor(25, 25, 25)))
        self.setPen(QPen(Qt.white, 1))
        self.setZValue(10)
        self.label = QGraphicsSimpleTextItem(str(row + 1), self)
        self.label.setBrush(Qt.white)
        f = QFont()
        f.setBold(True)
        self.label.setFont(f)
        br = self.label.boundingRect()
        self.label.setPos(-br.width() / 2, -br.height() / 2)
        self.setCursor(Qt.OpenHandCursor)
        self.delete_badge = DeleteBadge(self)

    def boundingRect(self):
        return self.rect().adjusted(-SEL_PAD - 2, -SEL_PAD - 2, SEL_PAD + 2, SEL_PAD + 2)   # includes the outline

    def set_highlight(self, state):
        color = {"running": QColor(230, 120, 0), "ok": QColor(30, 130, 40),
                 "failed": QColor(190, 20, 20)}.get(state, QColor(25, 25, 25))
        self.setBrush(QBrush(color))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and self.view.face_rect is not None:
            r = self.view.face_rect                 # keep markers on the face
            return QPointF(min(max(value.x(), r.left()), r.right()), min(max(value.y(), r.top()), r.bottom()))
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.view.marker_moving(self)
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.delete_badge.setVisible(bool(value) and self.view._editable)
            if value:
                self.view.pointSelected.emit(self.row)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        self.view.marker_dropped(self)

    def paint(self, painter, option, widget=None):
        if self.isSelected():
            painter.setPen(QPen(QColor(0, 150, 255), 3))
            painter.drawRect(self.rect().adjusted(-SEL_PAD, -SEL_PAD, SEL_PAD, SEL_PAD))
        option.state &= ~QStyle.State_Selected          # no default dashed selection box
        super().paint(painter, option, widget)


class DeleteBadge(QGraphicsEllipseItem):
    """Red cross at the top-right of the selected marker; click deletes the point."""

    def __init__(self, marker):
        r = 8
        super().__init__(-r, -r, 2 * r, 2 * r, marker)
        self.marker = marker
        self.setPos(marker.rect().right() + 4, marker.rect().top() - 4)
        self.setBrush(QColor(200, 30, 30))
        self.setPen(QPen(Qt.white, 1.5))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Delete point")
        self.setZValue(1)
        self.setVisible(False)

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        painter.setPen(QPen(Qt.white, 2))
        painter.drawLine(QPointF(-3.5, -3.5), QPointF(3.5, 3.5))
        painter.drawLine(QPointF(-3.5, 3.5), QPointF(3.5, -3.5))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.marker.view.deleteRequested.emit(self.marker.row)
            e.accept()


class CalMarker(QGraphicsPathItem):
    def __init__(self, text):
        path = QPainterPath()
        for a, b in (((-6, -6), (6, 6)), ((-6, 6), (6, -6))):
            path.moveTo(*a)
            path.lineTo(*b)
        super().__init__(path)
        self.setPen(QPen(Qt.black, 1.5))
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        self.setZValue(20)
        self.text = QGraphicsSimpleTextItem(text, self)
        self.text.setBrush(Qt.black)
        br = self.text.boundingRect()
        self.text.setPos(-br.width() / 2, -br.height() - 8)      # centred above the cross


class FaceView(QGraphicsView):
    faceClicked = Signal(float, float)      # face mm, empty spot clicked
    pointMoved = Signal(int, float, float)  # row, face mm (on release)
    pointSelected = Signal(int)
    deleteRequested = Signal(int)           # row: delete badge clicked or Delete key

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor(235, 235, 235))
        self.face_rect = None
        self.pixmap_item = None
        self.bg_item = None
        self.markers, self.arrows = [], []
        self.cal_markers = {}
        self.states = {}
        self._pan = None
        self._editable = True
        model.pointsChanged.connect(self.rebuild_points)

    # --- face image ---
    def set_face(self, face_image):
        self.scene_.clear()
        self.markers, self.arrows, self.cal_markers = [], [], {}
        if face_image is None:
            self.face_rect = None
            return
        w, h = face_image.width_mm, face_image.height_mm
        self.face_rect = QRectF(0, 0, w, h)
        self.bg_item = self.scene_.addRect(self.face_rect, QPen(Qt.NoPen), QBrush(FACE_BG))
        a = face_image.rgba
        img = QImage(a.data, a.shape[1], a.shape[0], a.strides[0], QImage.Format_RGBA8888).copy()
        self.pixmap_item = self.scene_.addPixmap(QPixmap.fromImage(img))
        self.pixmap_item.setScale(w / a.shape[1])
        self.pixmap_item.setTransformationMode(Qt.SmoothTransformation)
        self.scene_.addRect(self.face_rect, QPen(QColor(60, 60, 60), 0))
        margin = max(w, h) * 0.15
        self.scene_.setSceneRect(self.face_rect.adjusted(-margin, -margin, margin, margin))
        self.fit()
        self.rebuild_points()

    def fit(self):
        if self.face_rect is not None:
            self.fitInView(self.scene_.sceneRect(), Qt.KeepAspectRatio)

    # --- overlay ---
    def rebuild_points(self):
        for it in self.markers + self.arrows:
            self.scene_.removeItem(it)
        self.markers, self.arrows = [], []
        if self.face_rect is None:
            return
        selected = None
        sm = getattr(self, "_selection_model", None)
        if sm is not None and sm.selectedRows():
            selected = sm.selectedRows()[0].row()
        for row, p in enumerate(self.model.points):
            m = PointMarker(self, row)
            m.setFlag(QGraphicsItem.ItemIsMovable, self._editable)
            m.setPos(p.x, p.y)
            m.set_highlight(self.model.states.get(row))
            self.scene_.addItem(m)
            if row == selected:
                m.setSelected(True)
            self.markers.append(m)
        self._rebuild_arrows()

    def _rebuild_arrows(self):
        for it in self.arrows:
            self.scene_.removeItem(it)
        self.arrows = []
        px = 1.0 / max(self.transform().m11(), 1e-6)       # mm per screen pixel
        pen = QPen(QColor(30, 30, 30), 0)
        pen.setWidthF(2 * px)
        for a, b in zip(self.markers, self.markers[1:]):
            p1, p2 = a.pos(), b.pos()
            dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
            d = math.hypot(dx, dy)
            gap = 15 * px                                 # stop short of the 24 px markers
            if d <= 2 * gap:
                continue
            ux, uy = dx / d, dy / d
            s = QPointF(p1.x() + ux * gap, p1.y() + uy * gap)
            e = QPointF(p2.x() - ux * gap, p2.y() - uy * gap)
            path = QPainterPath(s)
            path.lineTo(e)
            hl = 9 * px
            head = QPolygonF([e, QPointF(e.x() - ux * hl - uy * hl * 0.5, e.y() - uy * hl + ux * hl * 0.5),
                              QPointF(e.x() - ux * hl + uy * hl * 0.5, e.y() - uy * hl - ux * hl * 0.5)])
            path.addPolygon(head)
            item = self.scene_.addPath(path, pen, QBrush(QColor(30, 30, 30)))
            item.setZValue(5)
            self.arrows.append(item)

    def set_cal_point(self, name, xy):
        old = self.cal_markers.pop(name, None)
        if old is not None:
            self.scene_.removeItem(old)
        if xy is None or self.face_rect is None:
            return
        m = CalMarker(name)
        m.setPos(*xy)
        self.scene_.addItem(m)
        self.cal_markers[name] = m

    def set_selection_model(self, sm):
        self._selection_model = sm
        sm.selectionChanged.connect(self._sync_selection)

    def _sync_selection(self, *_):
        rows = {i.row() for i in self._selection_model.selectedRows()}
        for m in self.markers:
            m.setSelected(m.row in rows)

    def set_editable(self, on):
        self._editable = on
        for m in self.markers:
            m.setFlag(QGraphicsItem.ItemIsMovable, on)

    # --- marker callbacks ---
    def marker_moving(self, marker):
        self._rebuild_arrows()

    def marker_dropped(self, marker):
        p = marker.pos()
        pt = self.model.points[marker.row] if marker.row < len(self.model.points) else None
        if pt is not None and (abs(pt.x - p.x()) > 1e-6 or abs(pt.y - p.y()) > 1e-6):
            self.pointMoved.emit(marker.row, p.x(), p.y())

    # --- mouse: click to add, wheel zoom, middle-drag pan ---
    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._pan = e.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        if e.button() == Qt.LeftButton and self._editable and self.face_rect is not None:
            item = self.itemAt(e.position().toPoint())
            if not isinstance(item, (PointMarker, DeleteBadge, QGraphicsSimpleTextItem)):
                sp = self.mapToScene(e.position().toPoint())
                if self.face_rect.contains(sp):
                    self.faceClicked.emit(sp.x(), sp.y())
                    return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton and self._pan is not None:
            self._pan = None
            self.unsetCursor()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self._editable:
            rows = sorted((m.row for m in self.markers if m.isSelected()), reverse=True)
            for r in rows:
                self.deleteRequested.emit(r)
            if rows:
                return
        super().keyPressEvent(e)

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)
        self._rebuild_arrows()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.fit()
        self._rebuild_arrows()
