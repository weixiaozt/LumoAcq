"""采集结果预览对话框: 读 TIFF + 侧边元数据摘要。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyqtgraph as pg
import tifffile
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from acquire_app.gui import theme


class CapturePreviewDialog(QDialog):
    """独立窗口, 不阻塞主界面预览。"""

    def __init__(self, tiff_path: Path, json_path: Path | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"采集结果预览 — {Path(tiff_path).name}")
        self.resize(1400, 900)
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setStyleSheet(theme.QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        outer = QHBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        root.addLayout(outer, 1)

        # 图像区
        self._view = pg.ImageView()
        self._view.ui.roiBtn.hide()
        self._view.ui.menuBtn.hide()
        self._view.getView().setBackgroundColor(theme.BG)
        hist = self._view.getHistogramWidget()
        hist.setBackground(theme.BG)
        hist.gradient.loadPreset("grey")
        outer.addWidget(self._view, 1)

        # 右侧: 摘要 + JSON 元数据
        right = QWidget()
        right.setFixedWidth(380)
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(8)

        self._summary = QLabel("加载中…")
        self._summary.setProperty("mono", True)
        self._summary.setWordWrap(True)
        self._summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right_l.addWidget(self._summary)

        right_l.addWidget(QLabel("元数据 (JSON)"))
        self._meta_text = QPlainTextEdit()
        self._meta_text.setReadOnly(True)
        self._meta_text.setStyleSheet(
            f"QPlainTextEdit {{ font-family: {theme.FONT_MONO}; font-size: 8.5pt; }}"
        )
        right_l.addWidget(self._meta_text, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        right_l.addLayout(btn_row)

        outer.addWidget(right)

        # 锁相波形 (仅锁相数据可见)
        self._wave_frame = QFrame()
        self._wave_frame.setObjectName("WaveFrame")
        self._wave_frame.setStyleSheet(
            f"QFrame#WaveFrame {{ background-color: {theme.CARD}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 6px; }}"
        )
        self._wave_frame.setFixedHeight(180)
        wl = QVBoxLayout(self._wave_frame)
        wl.setContentsMargins(10, 6, 10, 8)
        wl.setSpacing(4)
        wave_title = QLabel("锁相帧波形 (亮=绿, 暗=灰)")
        wave_title.setObjectName("CardTitle")
        wl.addWidget(wave_title)
        self._wave_plot = pg.PlotWidget()
        self._wave_plot.setBackground(theme.BG)
        self._wave_plot.showGrid(x=False, y=True, alpha=0.3)
        self._wave_plot.setLabel("bottom", "帧序号")
        self._wave_plot.setLabel("left", "帧均值 (ADU)")
        wl.addWidget(self._wave_plot, 1)
        self._wave_frame.setVisible(False)
        root.addWidget(self._wave_frame)

        self._load(Path(tiff_path), Path(json_path) if json_path else None)

    def _load(self, tiff_path: Path, json_path: Path | None) -> None:
        try:
            image = tifffile.imread(str(tiff_path))
        except Exception as e:
            self._summary.setText(f"<span style='color:{theme.DANGER}'>无法读取 TIFF: {e}</span>")
            return

        # pyqtgraph 的 setImage: 转置对齐 (H, W) → (W, H) 让 x=列
        self._view.setImage(image.T, autoRange=True, autoLevels=True)

        arr = image.astype(np.float32)
        summary_lines = [
            f"路径: {tiff_path.name}",
            f"形状: {image.shape[1]} × {image.shape[0]}",
            f"dtype: {image.dtype}",
            f"min / max: {arr.min():.2f} / {arr.max():.2f}",
            f"mean / std: {arr.mean():.2f} / {arr.std():.2f}",
        ]
        self._summary.setText("\n".join(summary_lines))

        if json_path and json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                self._meta_text.setPlainText(
                    json.dumps(data, ensure_ascii=False, indent=2)
                )
                self._maybe_draw_waveform(data)
            except Exception as e:
                self._meta_text.setPlainText(f"<无法解析 JSON: {e}>")
        else:
            self._meta_text.setPlainText("<无侧车 JSON>")

    def _maybe_draw_waveform(self, data: dict) -> None:
        lockin = data.get("lockin")
        if not lockin:
            return
        means = lockin.get("frame_means") or []
        is_bright = lockin.get("is_bright") or []
        if not means:
            return

        self._wave_frame.setVisible(True)
        x = np.arange(len(means))
        y = np.asarray(means, dtype=float)

        self._wave_plot.clear()
        self._wave_plot.plot(x, y, pen=pg.mkPen(theme.BORDER, width=1))

        bright_pts = [(i, y[i]) for i, b in enumerate(is_bright) if b is True]
        dark_pts = [(i, y[i]) for i, b in enumerate(is_bright) if b is False]

        if bright_pts:
            bs = pg.ScatterPlotItem(
                size=6, brush=pg.mkBrush(theme.SUCCESS), pen=None
            )
            bs.setData(pos=bright_pts)
            self._wave_plot.addItem(bs)
        if dark_pts:
            ds = pg.ScatterPlotItem(
                size=6, brush=pg.mkBrush(theme.TEXT_DIM), pen=None
            )
            ds.setData(pos=dark_pts)
            self._wave_plot.addItem(ds)
