"""Live strain gauge plot: recent force in grams, a solid line at the next point's max force and a dotted
line at the contact threshold. Polls `source()` -> list of grams (or None when no gauge is open)."""
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class ForceGraph(QWidget):
    def __init__(self, parent=None, interval_ms=100):
        super().__init__(parent)
        self.setMinimumHeight(160)
        self.source = lambda: None
        self.samples = None
        self.max_g = None           # next point's force target, None for depth points / no point
        self.threshold_g = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(interval_ms)

    def set_source(self, source):
        self.source = source or (lambda: None)
        self._poll()

    def set_lines(self, max_g, threshold_g):
        if (max_g, threshold_g) != (self.max_g, self.threshold_g):
            self.max_g, self.threshold_g = max_g, threshold_g
            self.update()

    def _poll(self):
        try:
            s = self.source()
        except Exception:
            s = None
        if s or self.samples:
            self.samples = s
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        left, top, right, bottom = 45, 18, self.width() - 8, self.height() - 8
        w, h = max(right - left, 1), max(bottom - top, 1)
        p.setPen(QColor("black"))
        p.drawText(left, 13, "Strain gauge (g)")

        vals = list(self.samples or [])
        refs = [v for v in (self.max_g, self.threshold_g) if v is not None]
        lo = min([0.0] + vals + [-v for v in refs if v < 0])
        hi = max([10.0] + vals + [v * 1.1 for v in refs])
        span = hi - lo or 1.0

        def y(v):
            return bottom - (v - lo) / span * h

        p.setPen(QColor("#ccc"))
        p.drawRect(left, top, w, h)
        p.setPen(QColor("gray"))
        for v in (lo, 0.0, hi) if lo < 0 else (lo, hi):
            p.drawText(2, int(y(v)) + 4, f"{v:.0f}")

        if self.threshold_g is not None:
            p.setPen(QPen(QColor("#1f5fbf"), 1.5, Qt.DotLine))
            p.drawLine(QPointF(left, y(self.threshold_g)), QPointF(right, y(self.threshold_g)))
        if self.max_g is not None:
            p.setPen(QPen(QColor("#c00000"), 1.5))
            p.drawLine(QPointF(left, y(self.max_g)), QPointF(right, y(self.max_g)))
            p.drawText(right - 90, int(y(self.max_g)) - 3, f"max {self.max_g:g} g")

        if len(vals) > 1:
            path = QPainterPath(QPointF(left, y(vals[0])))
            for i, v in enumerate(vals[1:], 1):
                path.lineTo(left + i / (len(vals) - 1) * w, y(v))
            p.setPen(QPen(QColor("#b36b00"), 1.5))
            p.drawPath(path)
            p.setPen(QColor("black"))
            p.drawText(left + 4, top + 14, f"{vals[-1]:.1f} g")
        else:
            p.setPen(QColor("gray"))
            p.drawText(self.rect().adjusted(left, top, 0, 0), Qt.AlignCenter, "No gauge data (runs only)")
