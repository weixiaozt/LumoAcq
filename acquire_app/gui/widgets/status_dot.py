from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QWidget

from acquire_app.gui import theme


class StatusDot(QWidget):
    """状态指示色点。通过 set_state('idle'|'ok'|'busy'|'error') 切换颜色。"""

    _COLORS = {
        "idle": theme.TEXT_DIM,
        "ok": theme.SUCCESS,
        "busy": theme.WARNING,
        "error": theme.DANGER,
        "active": theme.ACCENT,
    }

    def __init__(self, state: str = "idle", parent=None):
        super().__init__(parent)
        self._state = state
        self.setFixedSize(10, 10)

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(self._COLORS.get(self._state, theme.TEXT_DIM))
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawEllipse(1, 1, 8, 8)
