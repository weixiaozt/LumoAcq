"""分组平均采集实时监视: 当前帧 + 滚动波形 + 亮/暗结果图切换.

跟锁相监视窗 (LockInMonitorDialog) 形态相近, 差异:
- 没有"光源频率"
- 结果区不是单张差分图, 而是亮 / 暗 两张, 用按钮切换
- 统计表底部多一行 (亮图 + 暗图 各一行)

订阅源 (在 main_window 里 connect):
- worker.frame_captured(np.ndarray, int)             → update_frame
- worker.means_updated(list, list)                   → update_waveform
- worker.result_ready(np.ndarray, np.ndarray)        → show_result
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


class GroupedAverageMonitorDialog(QDialog):
    def __init__(self, total_frames: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"分组平均采集监视  ·  N={total_frames}")
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
        self._bright_image: Optional[np.ndarray] = None
        self._dark_image: Optional[np.ndarray] = None
        self._has_result = False

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
        self._plot.addLegend(offset=(10, 5))
        rv.addWidget(self._plot, 1)

        self._bright_scatter = pg.ScatterPlotItem(
            size=10, brush=pg.mkBrush(theme.SUCCESS), pen=None, name="亮", hoverable=True
        )
        self._dark_scatter = pg.ScatterPlotItem(
            size=10, brush=pg.mkBrush(theme.TEXT_DIM), pen=None, name="暗", hoverable=True
        )
        self._pending_scatter = pg.ScatterPlotItem(
            size=10, brush=pg.mkBrush(theme.WARNING), pen=None,
            name="过渡/待定", hoverable=True,
        )
        self._line = pg.PlotDataItem(pen=pg.mkPen(theme.BORDER, width=1))
        self._threshold_line = pg.InfiniteLine(
            angle=0, pen=pg.mkPen(theme.TEXT_DIM, style=Qt.DashLine)
        )

        self._plot.addItem(self._line)
        self._plot.addItem(self._bright_scatter)
        self._plot.addItem(self._dark_scatter)
        self._plot.addItem(self._pending_scatter)
        self._plot.addItem(self._threshold_line)

        for scatter in (self._bright_scatter, self._dark_scatter, self._pending_scatter):
            scatter.sigClicked.connect(self._on_scatter_clicked)

        # ── 统计表 ──
        self._group_cells: dict[str, list[QLabel]] = {}
        self._bright_result_cells: list[QLabel] = []
        self._dark_result_cells: list[QLabel] = []
        rv.addWidget(self._build_stats_panel())

        # ── 按钮区 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._show_bright_btn = QPushButton("显示亮图")
        self._show_bright_btn.setProperty("kind", "primary")
        self._show_bright_btn.setEnabled(False)
        self._show_bright_btn.clicked.connect(self._show_bright_image)
        btn_row.addWidget(self._show_bright_btn)

        self._show_dark_btn = QPushButton("显示暗图")
        self._show_dark_btn.setEnabled(False)
        self._show_dark_btn.clicked.connect(self._show_dark_image)
        btn_row.addWidget(self._show_dark_btn)

        self._close_btn = QPushButton("关闭")
        self._close_btn.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(self._close_btn)
        rv.addLayout(btn_row)

        outer.addWidget(right)

    # ── 槽: 来自 GroupedAverageWorker ──

    @Slot(np.ndarray, int)
    def update_frame(self, image: np.ndarray, frame_index: int) -> None:
        if 0 <= frame_index < self._total:
            self._frame_cache[frame_index] = _downscale(image, _CACHE_MAX_SIDE)
        # 出结果之前一直跟实时帧
        if not self._has_result:
            self._view.setImage(image.T, autoRange=False, autoLevels=True)
            self._view_label.setText(
                f"实时  ·  帧 {frame_index + 1}/{self._total}  ·  均值 {float(image.mean()):.1f}"
            )

    @Slot(list, list)
    def update_waveform(self, frame_means: list, is_bright: list) -> None:
        if not frame_means:
            return

        x = np.arange(len(frame_means))
        y = np.asarray(frame_means, dtype=float)
        self._line.setData(x, y)

        def _spots(indices):
            return [{"pos": (int(i), float(y[i])), "data": int(i)} for i in indices]

        bright_idx = [i for i, b in enumerate(is_bright) if b is True]
        dark_idx = [i for i, b in enumerate(is_bright) if b is False]
        pending_idx = [i for i, b in enumerate(is_bright) if b is None]

        self._bright_scatter.setData(spots=_spots(bright_idx))
        self._dark_scatter.setData(spots=_spots(dark_idx))
        self._pending_scatter.setData(spots=_spots(pending_idx))

        # 阈值线 = 亮暗两类均值的中点
        if bright_idx and dark_idx:
            b_avg = float(y[bright_idx].mean())
            d_avg = float(y[dark_idx].mean())
            self._threshold_line.setPos((b_avg + d_avg) / 2.0)

        self._update_group_stats(y, bright_idx, dark_idx, pending_idx)

    @Slot(np.ndarray, np.ndarray)
    def show_result(self, bright_image: np.ndarray, dark_image: np.ndarray) -> None:
        self._bright_image = np.asarray(bright_image)
        self._dark_image = np.asarray(dark_image)
        self._has_result = True
        self._show_bright_btn.setEnabled(True)
        self._show_dark_btn.setEnabled(True)
        self._update_result_stats(self._bright_image, self._dark_image)
        # 默认先显示亮图
        self._show_bright_image()

    @Slot(str)
    def mark_done(self, message: str) -> None:
        # 由 main_window 在 cleanup 时调; 默认不覆盖结果显示
        if not self._has_result:
            self._view_label.setText(message)

    # ── 内部: 统计表 ──

    def _build_stats_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # --- 表 1: 分组统计 ---
        grid1 = QGridLayout()
        grid1.setHorizontalSpacing(12)
        grid1.setVerticalSpacing(2)
        grid1.setContentsMargins(0, 0, 0, 0)

        headers = ["", "帧数", "均值 min", "均值 mean", "均值 max"]
        for col, text in enumerate(headers):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 8.5pt;")
            if col > 0:
                lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid1.addWidget(lbl, 0, col)

        group_defs = [
            ("亮", theme.SUCCESS),
            ("暗", theme.TEXT_MUTED),
            ("过渡(剔除)", theme.WARNING),
        ]
        for row, (name, color) in enumerate(group_defs, start=1):
            name_lbl = QLabel(name)
            name_lbl.setStyleSheet(f"color: {color}; font-weight: 600;")
            grid1.addWidget(name_lbl, row, 0)

            cells: list[QLabel] = []
            for col in range(1, 5):
                cell = QLabel("—")
                cell.setProperty("mono", True)
                cell.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                cell.setStyleSheet(f"font-family: {theme.FONT_MONO};")
                grid1.addWidget(cell, row, col)
                cells.append(cell)
            self._group_cells[name] = cells

        grid1.setColumnStretch(0, 0)
        for c in range(1, 5):
            grid1.setColumnStretch(c, 1)
        layout.addLayout(grid1)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {theme.BORDER}; max-height: 1px;")
        layout.addWidget(sep)

        # --- 表 2: 亮/暗结果统计 (两行) ---
        grid2 = QGridLayout()
        grid2.setHorizontalSpacing(12)
        grid2.setVerticalSpacing(2)
        grid2.setContentsMargins(0, 0, 0, 0)

        headers2 = ["", "min", "mean", "max", "std"]
        for col, text in enumerate(headers2):
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 8.5pt;")
            if col > 0:
                lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid2.addWidget(lbl, 0, col)

        for row, (label_text, color, cells_target) in enumerate(
            [
                ("亮图", theme.SUCCESS, "_bright_result_cells"),
                ("暗图", theme.TEXT_MUTED, "_dark_result_cells"),
            ],
            start=1,
        ):
            name = QLabel(label_text)
            name.setStyleSheet(f"color: {color}; font-weight: 600;")
            grid2.addWidget(name, row, 0)
            target_list: list[QLabel] = getattr(self, cells_target)
            for col in range(1, 5):
                cell = QLabel("—")
                cell.setProperty("mono", True)
                cell.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                cell.setStyleSheet(f"font-family: {theme.FONT_MONO};")
                grid2.addWidget(cell, row, col)
                target_list.append(cell)

        grid2.setColumnStretch(0, 0)
        for c in range(1, 5):
            grid2.setColumnStretch(c, 1)
        layout.addLayout(grid2)

        return frame

    def _update_group_stats(
        self,
        y: np.ndarray,
        bright_idx: list[int],
        dark_idx: list[int],
        pending_idx: list[int],
    ) -> None:
        groups = {
            "亮": bright_idx,
            "暗": dark_idx,
            "过渡(剔除)": pending_idx,
        }
        for name, idx in groups.items():
            cells = self._group_cells.get(name)
            if cells is None:
                continue
            if not idx:
                cells[0].setText("0")
                for c in cells[1:]:
                    c.setText("—")
                continue
            vals = y[idx]
            cells[0].setText(str(len(idx)))
            cells[1].setText(f"{float(vals.min()):.1f}")
            cells[2].setText(f"{float(vals.mean()):.1f}")
            cells[3].setText(f"{float(vals.max()):.1f}")

    def _update_result_stats(self, bright: np.ndarray, dark: np.ndarray) -> None:
        for img, cells in (
            (bright, self._bright_result_cells),
            (dark, self._dark_result_cells),
        ):
            arr = np.asarray(img, dtype=np.float64)
            stats = [
                f"{float(arr.min()):.1f}",
                f"{float(arr.mean()):.1f}",
                f"{float(arr.max()):.1f}",
                f"{float(arr.std()):.1f}",
            ]
            for cell, text in zip(cells, stats):
                cell.setText(text)

    # ── 内部: 视图切换 ──

    def _show_bright_image(self) -> None:
        if self._bright_image is None:
            return
        self._view.setImage(self._bright_image.T, autoRange=True, autoLevels=True)
        arr = self._bright_image
        self._view_label.setText(
            f"亮图 (float32)  ·  min {float(arr.min()):.1f}  "
            f"mean {float(arr.mean()):.2f}  max {float(arr.max()):.1f}"
        )

    def _show_dark_image(self) -> None:
        if self._dark_image is None:
            return
        self._view.setImage(self._dark_image.T, autoRange=True, autoLevels=True)
        arr = self._dark_image
        self._view_label.setText(
            f"暗图 (float32)  ·  min {float(arr.min()):.1f}  "
            f"mean {float(arr.mean()):.2f}  max {float(arr.max()):.1f}"
        )

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


def _downscale(image: np.ndarray, max_side: int) -> np.ndarray:
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
