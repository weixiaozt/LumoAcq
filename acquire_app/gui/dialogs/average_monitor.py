"""N 帧平均采集实时监视: 当前帧 + 滚动均值曲线 + 历史帧回看.

简化版 (相对 LockInMonitorDialog):
- 不分类亮/暗 (单色散点)
- 没有结果切换 (采完即落盘, 看文件即可)
- 没有阈值线 / 分组统计

订阅源 (在 main_window 里 connect):
- worker.frame_captured(np.ndarray)  → on_frame
- worker.finished / failed 后 main_window 调 mark_done
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from acquire_app.gui import theme


_CACHE_MAX_SIDE = 800   # 每帧缓存降采样到的最大边长, 控制内存


class AverageMonitorDialog(QDialog):
    def __init__(self, total_frames: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"N 帧平均采集监视  ·  N={total_frames}")
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
        self.resize(1200, 700)
        self.setModal(False)
        self.setStyleSheet(theme.QSS)

        self._total = int(total_frames)
        self._frame_cache: list[Optional[np.ndarray]] = [None] * self._total
        self._frame_means: list[float] = []
        self._frame_idx = 0
        self._done = False

        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        # ── 左: 图像 ──
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(6)

        pg.setConfigOption("background", theme.BG)
        pg.setConfigOption("foreground", theme.TEXT)
        self._view = pg.ImageView()
        self._view.ui.roiBtn.hide()
        self._view.ui.menuBtn.hide()
        self._view.ui.histogram.hide()
        self._view.getView().setBackgroundColor(theme.BG)
        left_l.addWidget(self._view, 1)

        self._view_label = QLabel("等待第一帧…")
        self._view_label.setProperty("mono", True)
        left_l.addWidget(self._view_label)

        outer.addWidget(left, 1)

        # ── 右: 波形 + 控制 ──
        right = QWidget()
        right.setFixedWidth(560)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)

        hint = QLabel("点击波形上的点可查看该帧原图")
        hint.setProperty("muted", True)
        rv.addWidget(hint)

        self._plot = pg.PlotWidget()
        self._plot.setBackground(theme.BG)
        self._plot.showGrid(x=False, y=True, alpha=0.3)
        self._plot.setLabel("bottom", "帧序号")
        self._plot.setLabel("left", "帧均值 (ADU)")
        rv.addWidget(self._plot, 1)

        self._line = pg.PlotDataItem(pen=pg.mkPen(theme.BORDER, width=1))
        self._scatter = pg.ScatterPlotItem(
            size=8, brush=pg.mkBrush(theme.ACCENT), pen=None, hoverable=True
        )
        self._plot.addItem(self._line)
        self._plot.addItem(self._scatter)
        self._scatter.sigClicked.connect(self._on_scatter_clicked)

        # 统计表
        self._stats_cells: list[QLabel] = []
        rv.addWidget(self._build_stats_panel())

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addStretch()
        self._close_btn = QPushButton("关闭")
        self._close_btn.clicked.connect(self.close)
        btn_row.addWidget(self._close_btn)
        rv.addLayout(btn_row)

        outer.addWidget(right)

    # ── 槽: 来自 CaptureWorker.frame_captured ──

    @Slot(np.ndarray)
    def on_frame(self, image: np.ndarray) -> None:
        idx = self._frame_idx
        self._frame_idx += 1

        if 0 <= idx < self._total:
            self._frame_cache[idx] = _downscale(image, _CACHE_MAX_SIDE)
        self._frame_means.append(float(image.mean()))

        # 实时模式刷新主视图 (mark_done 后不再刷, 留给用户看历史)
        if not self._done:
            self._view.setImage(image.T, autoRange=False, autoLevels=True)
            self._view_label.setText(
                f"实时  ·  帧 {idx + 1}/{self._total}  ·  均值 {float(image.mean()):.1f}"
            )

        self._update_scatter()
        self._update_stats()

    @Slot(str)
    def mark_done(self, message: str) -> None:
        self._done = True
        self._view_label.setText(message)

    # ── 内部 ──

    def _update_scatter(self) -> None:
        if not self._frame_means:
            return
        x = np.arange(len(self._frame_means))
        y = np.asarray(self._frame_means, dtype=float)
        self._line.setData(x, y)
        spots = [{"pos": (int(i), float(y[i])), "data": int(i)} for i in range(len(y))]
        self._scatter.setData(spots=spots)

    def _on_scatter_clicked(self, plot, points, *args) -> None:
        if not points:
            return
        idx = int(round(points[0].pos().x()))
        if 0 <= idx < self._total:
            cached = self._frame_cache[idx]
            if cached is None:
                self._view_label.setText(f"帧 {idx + 1}  ·  未缓存")
                return
            self._view.setImage(cached.T, autoRange=True, autoLevels=True)
            self._view_label.setText(
                f"查看  ·  帧 {idx + 1}  ·  均值 {float(cached.mean()):.1f}"
                f"  (显示为 1/{_get_step(cached)} 缩略图)"
            )

    def _build_stats_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        grid.setContentsMargins(0, 0, 0, 0)

        headers = ["", "已采", "均值 min", "均值 mean", "均值 max"]
        for col, text in enumerate(headers):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 8.5pt;")
            if col > 0:
                lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(lbl, 0, col)

        name = QLabel("帧均值")
        name.setStyleSheet(f"color: {theme.ACCENT}; font-weight: 600;")
        grid.addWidget(name, 1, 0)
        for col in range(1, 5):
            cell = QLabel("—")
            cell.setProperty("mono", True)
            cell.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cell.setStyleSheet(f"font-family: {theme.FONT_MONO};")
            grid.addWidget(cell, 1, col)
            self._stats_cells.append(cell)

        grid.setColumnStretch(0, 0)
        for c in range(1, 5):
            grid.setColumnStretch(c, 1)

        layout.addLayout(grid)
        return frame

    def _update_stats(self) -> None:
        if not self._frame_means or not self._stats_cells:
            return
        y = np.asarray(self._frame_means, dtype=float)
        self._stats_cells[0].setText(f"{len(y)}/{self._total}")
        self._stats_cells[1].setText(f"{float(y.min()):.1f}")
        self._stats_cells[2].setText(f"{float(y.mean()):.1f}")
        self._stats_cells[3].setText(f"{float(y.max()):.1f}")


def _downscale(image: np.ndarray, max_side: int) -> np.ndarray:
    """等比降采样到长边 ≤ max_side. 整数 step slicing, 对 uint16/float32 都安全."""
    h, w = image.shape[:2]
    long_side = max(h, w)
    if long_side <= max_side:
        return image.copy()
    step = max(1, long_side // max_side)
    return image[::step, ::step].copy()


def _get_step(cached: np.ndarray) -> int:
    max_side = max(cached.shape[:2])
    if max_side >= 800:
        return 1
    return max(1, 2592 // max_side)
