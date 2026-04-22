"""锁相采集实时监视: 当前帧 + 滚动波形 + 结果图切换。

左侧 ImageView 有三种视图:
  - 实时: 每帧 update_frame 更新
  - 某帧: 用户点波形上的点查看该帧 (从内存缓存)
  - 结果: 采集结束后显示锁相幅度图
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


class LockInMonitorDialog(QDialog):
    def __init__(self, total_frames: int, light_freq_hz: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle(
            f"锁相采集监视  ·  N={total_frames}  ·  光源 {light_freq_hz} Hz"
        )
        # 默认 QDialog 没有最大化/最小化按钮, 这里放开以支持最大化/全屏
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
        self._result_image: Optional[np.ndarray] = None

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

        # ── 统计表 (分组帧均值 + 锁相结果) ──
        self._group_cells: dict[str, list[QLabel]] = {}
        self._result_cells: list[QLabel] = []
        rv.addWidget(self._build_stats_panel())

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._show_result_btn = QPushButton("显示锁相结果")
        self._show_result_btn.setProperty("kind", "primary")
        self._show_result_btn.setEnabled(False)
        self._show_result_btn.clicked.connect(self._show_result_image)
        btn_row.addWidget(self._show_result_btn)

        self._close_btn = QPushButton("关闭")
        self._close_btn.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(self._close_btn)
        rv.addLayout(btn_row)

        outer.addWidget(right)

    # ── 槽: 来自 LockInWorker ──

    @Slot(np.ndarray, int)
    def update_frame(self, image: np.ndarray, frame_index: int) -> None:
        # 入缓 (降采样, 控制内存)
        if 0 <= frame_index < self._total:
            self._frame_cache[frame_index] = _downscale(image, _CACHE_MAX_SIDE)
        # 采集中途仍自动刷新主视图 (实时模式)
        if self._result_image is None:
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
            return [{"pos": (i, y[i]), "data": int(i)} for i in indices]

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

    @Slot(np.ndarray)
    def show_result(self, result_image: np.ndarray) -> None:
        self._result_image = np.asarray(result_image)
        self._show_result_btn.setEnabled(True)
        self._update_result_stats(self._result_image)
        self._show_result_image()

    @Slot(str)
    def mark_done(self, message: str) -> None:
        # 保留接口, 交由 show_result 覆盖显示
        pass

    # ── 内部: 统计表 ──

    def _build_stats_panel(self) -> QFrame:
        """波形下方: 分组帧均值统计 + 锁相结果统计.

        分组统计 3 行 (亮/暗/过渡) × 5 列 (组名/帧数/min/mean/max).
        结果统计 1 行 (差分图) × 5 列 (标题/min/mean/max/std).
        值格式化到 1 位小数, 数字列用 mono 字体右对齐.
        """
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
            lbl.setProperty("muted", True)
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

        # --- 表 2: 锁相结果统计 ---
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

        name2 = QLabel("锁相结果")
        name2.setStyleSheet(f"color: {theme.ACCENT}; font-weight: 600;")
        grid2.addWidget(name2, 1, 0)
        for col in range(1, 5):
            cell = QLabel("—")
            cell.setProperty("mono", True)
            cell.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cell.setStyleSheet(f"font-family: {theme.FONT_MONO};")
            grid2.addWidget(cell, 1, col)
            self._result_cells.append(cell)

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
        """根据当前分组填写帧均值 min/mean/max."""
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

    def _update_result_stats(self, image: np.ndarray) -> None:
        """采完后填写锁相结果图的全局 min/mean/max/std."""
        arr = np.asarray(image, dtype=np.float64)
        stats = [
            f"{float(arr.min()):.1f}",
            f"{float(arr.mean()):.1f}",
            f"{float(arr.max()):.1f}",
            f"{float(arr.std()):.1f}",
        ]
        for cell, text in zip(self._result_cells, stats):
            cell.setText(text)

    # ── 内部: 视图切换 ──

    def _show_result_image(self) -> None:
        if self._result_image is None:
            return
        self._view.setImage(self._result_image.T, autoRange=True, autoLevels=True)
        stats = self._result_image
        self._view_label.setText(
            f"锁相结果 (float32)  ·  min {float(stats.min()):.1f}  "
            f"max {float(stats.max()):.1f}  mean {float(stats.mean()):.2f}"
        )

    def _on_scatter_clicked(self, plot, points, *args) -> None:
        if not points:
            return
        idx = int(round(points[0].pos().x()))
        if 0 <= idx < self._total:
            cached = self._frame_cache[idx]
            if cached is None:
                self._view_label.setText(f"帧 {idx}  ·  未缓存")
                return
            # 自适应窗口 + 自动 levels, 避免缩略图只占一角
            self._view.setImage(cached.T, autoRange=True, autoLevels=True)
            self._view_label.setText(
                f"查看  ·  帧 {idx}  ·  均值 {float(cached.mean()):.1f}"
                f"  (显示为 1/{_get_step(cached)} 缩略图)"
            )


def _downscale(image: np.ndarray, max_side: int) -> np.ndarray:
    """等比降采样到长边 ≤ max_side。整数 step slicing, 对 uint16/float32 都安全。"""
    h, w = image.shape[:2]
    long_side = max(h, w)
    if long_side <= max_side:
        return image.copy()
    step = max(1, long_side // max_side)
    return image[::step, ::step].copy()


def _get_step(cached: np.ndarray) -> int:
    # 给标签用: 粗略反推缩略倍数 (只是给用户做参考)
    # 原图宽 w ~ N * step; 这里简单按缓存大小反推
    max_side = max(cached.shape[:2])
    if max_side >= 800:
        return 1
    # 退一步按 2592 / max_side 取整 (真机典型)
    return max(1, 2592 // max_side)
