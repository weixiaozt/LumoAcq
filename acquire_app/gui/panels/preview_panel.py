"""实时预览面板 M7.

功能:
- pyqtgraph ImageView 显示实时图
- 顶部工具条: 拉伸模式 / 直方图线性-对数 / 饱和暗部实时比例
- 鼠标悬停显示 (x, y, 原始值)
- 对外: set_frame(image) 由 MainWindow 在主线程调用 (worker 线程发信号, MainWindow 转发)
"""
from __future__ import annotations

import time
from collections import deque
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QPointF, Signal, QEvent
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from acquire_app.core.full_scale import full_scale_for
from acquire_app.gui import theme
from acquire_app.gui.widgets.card import Card


_STRETCH_LABELS = {
    "auto_p2p98": "自动 p2–p98",
    "auto_minmax": "自动 min–max",
    "manual": "手动",
}


class PreviewPanel(Card):
    stretch_mode_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__("实时预览", subtitle="M7 · pyqtgraph", parent=parent)

        pg.setConfigOption("background", theme.BG)
        pg.setConfigOption("foreground", theme.TEXT)
        pg.setConfigOption("antialias", True)

        toolbar = self._build_toolbar()
        self.add_widget(toolbar)

        self.image_view = pg.ImageView()
        self.image_view.ui.roiBtn.hide()
        self.image_view.ui.menuBtn.hide()
        view = self.image_view.getView()
        view.setBackgroundColor(theme.BG)

        self._histogram = self.image_view.getHistogramWidget()
        self._histogram.setBackground(theme.BG)
        self._histogram.gradient.loadPreset("grey")

        self.add_widget(self.image_view)
        self.add_widget(self._build_info_bar())

        self._stretch_mode = "auto_p2p98"
        self._pixel_format = "Mono12"
        self._full_scale = full_scale_for(self._pixel_format)
        self._last_image: Optional[np.ndarray] = None
        self._frame_count = 0
        self._frame_times: deque[float] = deque(maxlen=30)

        self._show_test_pattern()

        view.scene().sigMouseMoved.connect(self._on_mouse_moved)
        # 双击图像 → 适应窗口
        self.image_view.installEventFilter(self)

    # ── 工具条 ──

    def _build_toolbar(self) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        row.addWidget(QLabel("拉伸"))
        self._stretch_combo = QComboBox()
        for key, label in _STRETCH_LABELS.items():
            self._stretch_combo.addItem(label, userData=key)
        self._stretch_combo.currentIndexChanged.connect(self._on_stretch_changed)
        row.addWidget(self._stretch_combo)

        row.addWidget(QLabel("直方图"))
        self._hist_combo = QComboBox()
        self._hist_combo.addItem("线性", userData=False)
        self._hist_combo.addItem("对数", userData=True)
        self._hist_combo.currentIndexChanged.connect(self._on_hist_scale_changed)
        row.addWidget(self._hist_combo)

        self._fit_btn = QPushButton("适应窗口")
        self._fit_btn.setFixedWidth(76)
        self._fit_btn.setToolTip("把图像还原铺满视图 (双击图像同等效果)")
        self._fit_btn.clicked.connect(self.fit_view)
        row.addWidget(self._fit_btn)

        row.addStretch()

        self._sat_label = QLabel("饱和 —")
        self._sat_label.setProperty("mono", True)
        self._dark_label = QLabel("暗部 —")
        self._dark_label.setProperty("mono", True)
        self._dark_label.setProperty("muted", True)
        row.addWidget(self._sat_label)
        row.addWidget(self._dark_label)

        return box

    def _build_info_bar(self) -> QWidget:
        box = QFrame()
        box.setObjectName("PreviewInfo")
        box.setStyleSheet(
            f"QFrame#PreviewInfo {{ background-color: {theme.CARD}; "
            f"border: 1px solid {theme.BORDER}; border-radius: 4px; }}"
        )
        row = QHBoxLayout(box)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(16)

        self._info_cap_fps = QLabel("采集帧率 —")
        self._info_cap_fps.setProperty("mono", True)
        self._info_cap_fps.setToolTip(
            "相机实测采集速率 (基于 frame_id + 时间戳, 受曝光/读出/带宽限制)"
        )

        self._info_disp_fps = QLabel("实时帧率 —")
        self._info_disp_fps.setProperty("mono", True)
        self._info_disp_fps.setToolTip("UI 预览刷新速率, 上限见 config.PREVIEW_FPS")

        self._info_frames = QLabel("帧数 0")
        self._info_frames.setProperty("mono", True)

        self._info_shape = QLabel("分辨率 —")
        self._info_shape.setProperty("mono", True)

        self._info_pixel = QLabel("(x=—, y=—) = —")
        self._info_pixel.setProperty("mono", True)

        self._info_temp = QLabel("温度 —")
        self._info_temp.setProperty("mono", True)

        for w in (self._info_cap_fps, self._info_disp_fps, self._info_frames, self._info_shape):
            row.addWidget(w)
        row.addSpacing(12)
        row.addWidget(self._info_pixel, 1)
        row.addWidget(self._info_temp)
        return box

    # ── 外部 API ──

    def fit_view(self) -> None:
        """把图像铺满视图 (auto range), 不改灰度映射。"""
        try:
            self.image_view.getView().autoRange()
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if obj is self.image_view and event.type() == QEvent.MouseButtonDblClick:
            self.fit_view()
            return True
        return super().eventFilter(obj, event)

    def set_pixel_format(self, fmt: str) -> None:
        try:
            self._full_scale = full_scale_for(fmt)
            self._pixel_format = fmt
        except ValueError:
            pass

    def set_frame(self, image: np.ndarray) -> None:
        """主线程接收的帧更新, 由 MainWindow 转发 worker 的 frame_ready 信号。"""
        # 首帧 或 帧形状发生变化 (测试图 → 相机帧 / ROI 切换) 时自动铺满,
        # 之后保持用户的缩放/平移状态, 不打扰交互.
        should_fit = (
            self._last_image is None
            or self._last_image.shape != image.shape
        )
        self._last_image = image
        levels = self._compute_levels(image)
        self.image_view.setImage(
            image.T,
            autoRange=should_fit,
            autoLevels=False,
            levels=levels,
        )
        self._update_saturation(image)

        # 显示 FPS 滚动窗口
        now = time.monotonic()
        self._frame_times.append(now)
        self._frame_count += 1
        if len(self._frame_times) >= 2:
            dt = self._frame_times[-1] - self._frame_times[0]
            fps = (len(self._frame_times) - 1) / dt if dt > 0 else 0.0
            self._info_disp_fps.setText(f"实时帧率 {fps:5.1f}")
        self._info_frames.setText(f"帧数 {self._frame_count}")
        h, w = image.shape[-2:]
        self._info_shape.setText(f"分辨率 {w}×{h}  {image.dtype}")

    def set_temperature(self, value: float | None) -> None:
        self._info_temp.setText(f"温度 {value:.1f}°C" if value is not None else "温度 —")

    def set_capture_fps(self, value: float | None) -> None:
        if value is None or value <= 0:
            self._info_cap_fps.setText("采集帧率 —")
        else:
            self._info_cap_fps.setText(f"采集帧率 {value:5.1f}")

    def clear(self) -> None:
        self._last_image = None
        self._frame_count = 0
        self._frame_times.clear()
        self._info_cap_fps.setText("采集帧率 —")
        self._info_disp_fps.setText("实时帧率 —")
        self._info_frames.setText("帧数 0")
        self._info_shape.setText("分辨率 —")
        self._info_pixel.setText("(x=—, y=—) = —")
        self._info_temp.setText("温度 —")
        self._sat_label.setText("饱和 —")
        self._dark_label.setText("暗部 —")
        self._show_test_pattern()

    # ── 拉伸 ──

    def _compute_levels(self, image: np.ndarray) -> tuple[float, float] | None:
        mode = self._stretch_mode
        if mode == "manual":
            return None   # 使用直方图滑块当前值
        arr = image
        if mode == "auto_minmax":
            lo = float(arr.min())
            hi = float(arr.max())
        else:  # auto_p2p98
            lo, hi = np.percentile(arr, [2.0, 98.0])
            lo = float(lo)
            hi = float(hi)
        if hi <= lo:
            hi = lo + 1.0
        return (lo, hi)

    def _on_stretch_changed(self) -> None:
        data = self._stretch_combo.currentData()
        if isinstance(data, str):
            self._stretch_mode = data
            self.stretch_mode_changed.emit(data)
            if self._last_image is not None:
                self.set_frame(self._last_image)

    def _on_hist_scale_changed(self) -> None:
        is_log = bool(self._hist_combo.currentData())
        plot = self._histogram.plot
        vb = plot.getViewBox() if hasattr(plot, "getViewBox") else None
        # pyqtgraph PlotCurveItem 在 HistogramLUTItem: setLogMode 在 ViewBox/PlotItem 上都有
        try:
            self._histogram.plot.setLogMode(False, is_log)
        except Exception:
            pass

    # ── 饱和 / 暗部 ──

    def _update_saturation(self, image: np.ndarray) -> None:
        full = self._full_scale
        sat = float((image >= 0.99 * full).mean())
        dark = float((image <= 0.01 * full).mean())
        self._sat_label.setText(f"饱和 {sat * 100:5.2f}%")
        self._dark_label.setText(f"暗部 {dark * 100:5.2f}%")

        danger = sat > 0.001  # > 0.1%
        self._sat_label.setStyleSheet(
            f"color: {theme.DANGER}; font-weight: 600;" if danger else ""
        )

    # ── 悬停 ──

    def _on_mouse_moved(self, scene_pos: QPointF) -> None:
        if self._last_image is None:
            return
        view = self.image_view.getView()
        try:
            view_pos = view.mapSceneToView(scene_pos)
        except Exception:
            return
        # image_view.setImage(image.T) 所以视图坐标 x↔列, y↔行; 但 x 原本是 W, y 是 H
        x = int(view_pos.x())
        y = int(view_pos.y())
        h, w = self._last_image.shape[-2:]
        if 0 <= x < w and 0 <= y < h:
            val = self._last_image[y, x]
            val_str = f"{val:.1f}" if np.issubdtype(self._last_image.dtype, np.floating) else f"{int(val)}"
            self._info_pixel.setText(f"(x={x:>4}, y={y:>4}) = {val_str}")
        else:
            self._info_pixel.setText("(x=—, y=—) = —  (超出范围)")

    # ── 占位 ──

    def _show_test_pattern(self) -> None:
        h, w = 640, 800
        yy, xx = np.meshgrid(
            np.linspace(-1, 1, h, dtype=np.float32),
            np.linspace(-1, 1, w, dtype=np.float32),
            indexing="ij",
        )
        r = np.sqrt(xx * xx + yy * yy)
        img = np.clip(1.0 - r * 0.6, 0.0, 1.0)
        img += 0.05 * np.sin(12 * xx) * np.cos(12 * yy)
        img = np.clip(img, 0.0, 1.0)
        img = (img * 4095).astype(np.uint16)
        self.image_view.setImage(img.T, autoRange=True, autoLevels=True)
