"""校准对话框: 把 CalibrationPanel 放进独立窗口, 通过菜单打开。

非模态 — 可以开着它的同时操作主界面。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout

from acquire_app.gui import theme
from acquire_app.gui.panels.calibration_panel import CalibrationPanel


class CalibrationDialog(QDialog):
    def __init__(self, panel: CalibrationPanel, parent=None):
        super().__init__(parent)
        self.setWindowTitle("暗场 / 平场校准")
        self.setModal(False)
        self.resize(460, 420)
        self.setStyleSheet(theme.QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.addWidget(panel)
        outer.addStretch()

        # 允许 Esc 关闭
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
