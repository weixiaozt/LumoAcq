"""根据自身宽度自动省略 (ellide) 的 QLabel。

QLabel 默认会把完整 text 的像素宽度报告给父 layout, 长文本就把父容器撑大。
这里: 在 paint / resize 时用当前宽度计算省略文本, 且 sizePolicy 横向 Ignored,
不让父 layout 因为 text 长度调整布局。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent=None, mode=Qt.ElideMiddle):
        super().__init__(text, parent)
        self._full_text = text
        self._mode = mode
        # 不让 label 用 text 的全长度去影响 layout
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def setText(self, text: str) -> None:  # type: ignore[override]
        self._full_text = text or ""
        self._apply_elide()

    def fullText(self) -> str:
        return self._full_text

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        fm = self.fontMetrics()
        w = max(0, self.width() - 2)
        elided = fm.elidedText(self._full_text, self._mode, w)
        super().setText(elided)
