"""A titled box that folds open and shut with a click on its header (remembered per laptop)."""
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QFrame, QSizePolicy, QToolButton, QVBoxLayout


class CollapsibleBox(QFrame):
    def __init__(self, title, content, expanded=False, settings=None, key=None, parent=None):
        """content: the widget shown when open. key: QSettings key that remembers open/shut."""
        super().__init__(parent)
        self.setObjectName("collapsible")
        self.settings, self.key = settings, key
        if settings is not None and key:
            expanded = settings.value(key, expanded, type=bool)
        self.header = QToolButton()
        self.header.setObjectName("collapsibleHeader")
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.content = content
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.header)
        lay.addWidget(content)
        self.header.toggled.connect(self.set_expanded)
        self.header.setChecked(expanded)
        self.set_expanded(expanded)

    def is_expanded(self):
        return self.header.isChecked()

    def set_expanded(self, on):
        if self.header.isChecked() != on:
            self.header.setChecked(on)          # comes back here through toggled
            return
        self.header.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.content.setVisible(on)
        pol = self.sizePolicy()
        pol.setVerticalPolicy(QSizePolicy.Preferred if on else QSizePolicy.Fixed)
        self.setSizePolicy(pol)
        if self.settings is not None and self.key:
            self.settings.setValue(self.key, on)
