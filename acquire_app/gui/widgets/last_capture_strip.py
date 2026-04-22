"""一条"最近采集"展示条: 文件名 + 预览 / 打开目录。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from acquire_app.gui import theme
from acquire_app.gui.dialogs.capture_preview import CapturePreviewDialog
from acquire_app.gui.widgets.elided_label import ElidedLabel
from acquire_app.logger import logger


class LastCaptureStrip(QFrame):
    def __init__(self, label: str = "最近", parent=None):
        super().__init__(parent)
        self.setObjectName("LastCaptureStrip")
        self.setStyleSheet(
            f"QFrame#LastCaptureStrip {{ background-color: {theme.INPUT}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 4px; }}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)

        tag = QLabel(label)
        tag.setProperty("muted", True)
        tag.setFixedWidth(28)
        row.addWidget(tag)

        self._name = ElidedLabel("—")
        self._name.setProperty("mono", True)
        row.addWidget(self._name, 1)

        self._preview_btn = QPushButton("预览")
        self._preview_btn.setFixedWidth(54)
        self._preview_btn.setEnabled(False)
        self._preview_btn.clicked.connect(self._on_preview)
        row.addWidget(self._preview_btn)

        self._open_btn = QPushButton("目录")
        self._open_btn.setFixedWidth(54)
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._on_open_dir)
        row.addWidget(self._open_btn)

        self._tiff: Optional[Path] = None
        self._json: Optional[Path] = None
        self._dir: Optional[Path] = None

    def set_path(self, tiff: Path, json: Path | None) -> None:
        self._tiff = Path(tiff)
        self._json = Path(json) if json else None
        self._dir = self._tiff.parent

        # ElidedLabel 会根据自身实际宽度自动省略; 直接丢完整文件名即可
        self._name.setText(self._tiff.name)
        self._name.setToolTip(str(self._tiff))
        self._preview_btn.setEnabled(True)
        self._open_btn.setEnabled(True)

    def clear(self) -> None:
        self._tiff = None
        self._json = None
        self._dir = None
        self._name.setText("—")
        self._name.setToolTip("")
        self._preview_btn.setEnabled(False)
        self._open_btn.setEnabled(False)

    # ── 槽 ──

    def _on_preview(self) -> None:
        if self._tiff is None or not self._tiff.exists():
            logger.warning("文件不存在, 无法预览")
            return
        dlg = CapturePreviewDialog(self._tiff, self._json, self)
        dlg.show()

    def _on_open_dir(self) -> None:
        if self._dir is None or not self._dir.exists():
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(self._dir))
            elif sys.platform == "darwin":
                os.system(f"open '{self._dir}'")
            else:
                os.system(f"xdg-open '{self._dir}'")
        except Exception as e:
            logger.warning(f"打开目录失败: {e}")
