"""全局日志栏: 底部固定高度, 只读, 彩色分级, 自动滚动。

支持两种来源:
- 组件直接调用 append(msg, level)
- Python logging → QtLogHandler 转信号 → 线程安全追加
"""
from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QFrame, QPlainTextEdit, QVBoxLayout, QHBoxLayout, QLabel, QPushButton

from acquire_app.gui import theme


_LEVEL_COLOR = {
    "debug": theme.TEXT_DIM,
    "info": theme.TEXT,
    "ok": theme.SUCCESS,
    "warn": theme.WARNING,
    "error": theme.DANGER,
}

_LEVEL_TAG = {
    "debug": "DEBUG",
    "info": "INFO ",
    "ok": "OK   ",
    "warn": "WARN ",
    "error": "ERROR",
}


class LogPanel(QFrame):
    _message_for_append = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LogPanel")
        self.setStyleSheet(
            f"QFrame#LogPanel {{ background-color: {theme.CARD}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )
        self.setFixedHeight(120)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 8)
        outer.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("日志")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        header.addStretch()
        self._clear_btn = QPushButton("清空")
        self._clear_btn.setFixedWidth(60)
        self._clear_btn.clicked.connect(self._on_clear)
        header.addWidget(self._clear_btn)
        outer.addLayout(header)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(500)
        self._text.setFrameShape(QFrame.NoFrame)
        self._text.setStyleSheet(
            f"QPlainTextEdit {{ background-color: transparent; "
            f"color: {theme.TEXT}; border: none; "
            f"font-family: {theme.FONT_MONO}; font-size: 8.5pt; }}"
        )
        outer.addWidget(self._text)

        self._message_for_append.connect(self._do_append, Qt.QueuedConnection)

    # ── 公共 API ──

    def append(self, message: str, level: str = "info") -> None:
        self._message_for_append.emit(message, level)

    @Slot()
    def _on_clear(self) -> None:
        self._text.clear()

    @Slot(str, str)
    def _do_append(self, message: str, level: str) -> None:
        color = _LEVEL_COLOR.get(level, theme.TEXT)
        tag = _LEVEL_TAG.get(level, level.upper())
        ts = datetime.now().strftime("%H:%M:%S")
        safe = _escape_html(message)
        html = (
            f"<span style='color:{theme.TEXT_DIM}'>{ts}</span> "
            f"<span style='color:{color}; font-weight:600'>{tag}</span> "
            f"<span>{safe}</span>"
        )
        self._text.appendHtml(html)
        self._text.moveCursor(QTextCursor.End)


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )


class QtLogHandler(logging.Handler):
    """把 Python logging 记录转发到 LogPanel (线程安全)。"""

    _LEVEL_MAP = {
        logging.DEBUG: "debug",
        logging.INFO: "info",
        logging.WARNING: "warn",
        logging.ERROR: "error",
        logging.CRITICAL: "error",
    }

    def __init__(self, panel: LogPanel) -> None:
        super().__init__()
        self._panel = panel

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = self._LEVEL_MAP.get(record.levelno, "info")
            self._panel.append(record.getMessage(), level)
        except Exception:
            pass
