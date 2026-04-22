"""参数面板 M6: UI 接相机, 修改控件 → 实时生效。

实时生效: Exposure / Gain / FrameRate (底层无需 stop-stream)
需要 stop→set→start: PixelFormat / Binning (set_* 内部已封装, 会保持预览状态)
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QDoubleSpinBox,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QComboBox,
)

from PySide6.QtCore import Qt

from acquire_app.camera.base import CameraBase
from acquire_app.config import (
    DEFAULT_EXPOSURE_US,
    DEFAULT_GAIN_DB,
    DEFAULT_PIXEL_FORMAT,
    DEFAULT_FRAME_RATE_HZ,
    DEFAULT_BINNING,
)
from acquire_app.core.full_scale import supported_formats
from acquire_app.core.presets import (
    CameraPreset,
    apply_preset,
    capture_preset,
    delete_preset,
    load_presets,
    save_presets,
    upsert_preset,
)
from acquire_app.gui import theme
from acquire_app.gui.widgets.card import Card
from acquire_app.logger import logger


def _fmt_int(v: float) -> str:
    """大数字缩写: 1_000_000 → 1M; 10_000 → 10k"""
    n = int(v)
    if n >= 1_000_000 and n % 1_000_000 == 0:
        return f"{n // 1_000_000}M"
    if n >= 1_000 and n % 1_000 == 0:
        return f"{n // 1_000}k"
    return str(n)


def _fmt_duration_us(us: float) -> str:
    """把微秒换算成带单位的友好字符串: 21 → '21 μs'; 1_000_000 → '1 s'; 15000 → '15 ms'"""
    u = float(us)
    if u >= 1_000_000 and u % 1_000_000 == 0:
        return f"{u / 1_000_000:.0f} s"
    if u >= 1_000 and u % 1_000 == 0:
        return f"{u / 1_000:.0f} ms"
    return f"{int(u)} μs"


class ParamPanel(Card):
    pixel_format_changed = Signal(str)   # 供预览/采集协调用 (可能需要重建累加器)
    binning_changed = Signal(int)
    trigger_mode_changed = Signal(str)   # "free" | "software" | "hardware"

    def __init__(self, parent=None):
        super().__init__("采集参数", subtitle="M6", parent=parent)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self._exposure = QDoubleSpinBox()
        self._exposure.setRange(1.0, 10_000_000.0)
        self._exposure.setDecimals(0)
        self._exposure.setSuffix(" us")
        self._exposure.setValue(DEFAULT_EXPOSURE_US)
        self._exposure.setProperty("mono", True)
        self._exposure.setKeyboardTracking(False)

        self._gain = QDoubleSpinBox()
        self._gain.setRange(0.0, 48.0)
        self._gain.setDecimals(1)
        self._gain.setSuffix(" dB")
        self._gain.setValue(DEFAULT_GAIN_DB)
        self._gain.setKeyboardTracking(False)

        self._pixel = QComboBox()
        self._pixel.addItems(supported_formats())
        self._pixel.setCurrentText(DEFAULT_PIXEL_FORMAT)

        self._fps = QDoubleSpinBox()
        self._fps.setRange(0.1, 500.0)
        self._fps.setDecimals(1)
        self._fps.setSuffix(" Hz")
        self._fps.setValue(DEFAULT_FRAME_RATE_HZ)
        self._fps.setKeyboardTracking(False)

        self._binning = QSpinBox()
        self._binning.setRange(1, 4)
        self._binning.setValue(DEFAULT_BINNING)
        self._binning.setKeyboardTracking(False)

        self._trigger = QComboBox()
        self._trigger.addItem("自由运行", userData="free")
        self._trigger.addItem("软触发", userData="software")
        self._trigger.addItem("硬触发 (Line0)", userData="hardware")
        self._trigger.setToolTip(
            "自由运行: 相机持续出帧\n"
            "软触发: 每帧由软件发起; 预览会自动发软触发\n"
            "硬触发: 等外部信号, 适合和频闪光源同步"
        )

        self._temperature = QLabel("—")
        self._temperature.setProperty("mono", True)
        self._temperature.setProperty("muted", True)

        self._exposure_label = QLabel("曝光")
        self._gain_label = QLabel("增益")
        self._fps_label = QLabel("帧率")
        self._binning_label = QLabel("Binning")

        form.addRow(self._exposure_label, self._exposure)
        form.addRow(self._gain_label, self._gain)
        form.addRow(QLabel("像素格式"), self._pixel)
        form.addRow(self._fps_label, self._fps)
        form.addRow(self._binning_label, self._binning)
        form.addRow(QLabel("触发模式"), self._trigger)
        form.addRow(QLabel("温度"), self._temperature)

        self.add_layout(form)

        # 预设区: 分隔线 + 下拉 + 按钮
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {theme.BORDER}; max-height: 1px;")
        self.add_widget(sep)

        preset_title = QLabel("预设")
        preset_title.setObjectName("CardSubtitle")
        self.add_widget(preset_title)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(120)
        preset_row.addWidget(self._preset_combo, 1)
        self._preset_save_btn = QPushButton("保存")
        self._preset_save_btn.setFixedWidth(54)
        self._preset_save_btn.clicked.connect(self._on_preset_save)
        self._preset_load_btn = QPushButton("加载")
        self._preset_load_btn.setFixedWidth(54)
        self._preset_load_btn.clicked.connect(self._on_preset_load)
        self._preset_delete_btn = QPushButton("删除")
        self._preset_delete_btn.setFixedWidth(54)
        self._preset_delete_btn.clicked.connect(self._on_preset_delete)
        preset_row.addWidget(self._preset_save_btn)
        preset_row.addWidget(self._preset_load_btn)
        preset_row.addWidget(self._preset_delete_btn)
        self.add_layout(preset_row)

        self._presets: list[CameraPreset] = load_presets()
        self._refresh_preset_combo()

        self._camera: Optional[CameraBase] = None
        self._set_enabled(False)

        self._exposure.editingFinished.connect(self._push_exposure)
        self._gain.editingFinished.connect(self._push_gain)
        self._fps.editingFinished.connect(self._push_fps)
        self._pixel.currentTextChanged.connect(self._push_pixel_format)
        self._binning.valueChanged.connect(self._push_binning)
        self._trigger.currentIndexChanged.connect(self._push_trigger_mode)

    # ── 对外 ──

    def attach_camera(self, camera: CameraBase) -> None:
        self._camera = camera
        self._pull_from_camera()
        self._set_enabled(True)

    def detach_camera(self) -> None:
        self._camera = None
        self._set_enabled(False)
        self._temperature.setText("—")

    def refresh_temperature(self) -> None:
        """供 M10 状态栏定时器调用。"""
        if self._camera is None:
            return
        try:
            t = self._camera.get_temperature()
        except Exception:
            return
        self._temperature.setText(f"{t:.1f} °C" if t is not None else "不支持")

    # ── 内部: 读相机 ──

    def _pull_from_camera(self) -> None:
        cam = self._camera
        assert cam is not None
        widgets = [self._exposure, self._gain, self._pixel, self._fps, self._binning, self._trigger]
        for w in widgets:
            w.blockSignals(True)
        try:
            # 曝光/增益: 先应用相机真实范围, 再回填当前值
            try:
                rng = cam.get_exposure_range()
                if rng is not None:
                    self._exposure.setRange(rng[0], rng[1])
                    self._exposure_label.setText(
                        f"曝光  <span style='color:{theme.TEXT_DIM}'>"
                        f"{_fmt_duration_us(rng[0])}~{_fmt_duration_us(rng[1])}</span>"
                    )
                    self._exposure_label.setTextFormat(Qt.RichText)
                    self._exposure.setToolTip(
                        f"曝光时间 (微秒, μs)\n范围: {int(rng[0])} ~ {int(rng[1])} us\n"
                        f"= {_fmt_duration_us(rng[0])} ~ {_fmt_duration_us(rng[1])}"
                    )
                self._exposure.setValue(float(cam.get_exposure_us()))
            except Exception as e:
                logger.warning(f"读曝光失败: {e}")

            try:
                rng = cam.get_gain_range()
                if rng is not None:
                    self._gain.setRange(rng[0], rng[1])
                    self._gain_label.setText(
                        f"增益  <span style='color:{theme.TEXT_DIM}'>"
                        f"{rng[0]:.0f}~{rng[1]:.0f} dB</span>"
                    )
                    self._gain_label.setTextFormat(Qt.RichText)
                self._gain.setValue(float(cam.get_gain_db()))
            except Exception as e:
                logger.warning(f"读增益失败: {e}")

            # PixelFormat: 先用相机支持列表重填 combo, 再选中当前值
            try:
                formats = cam.list_pixel_formats()
                if not formats:
                    formats = supported_formats()
                # 只保留我们能算 full_scale 的 Mono*, 过滤 Packed 之类暂不支持
                formats = [f for f in formats if f in supported_formats()]
                self._pixel.clear()
                self._pixel.addItems(formats)
                current = cam.get_pixel_format()
                if current in formats:
                    self._pixel.setCurrentText(current)
            except Exception as e:
                logger.warning(f"读像素格式失败: {e}")

            fps_max = self._fps.maximum()
            self._fps_label.setText(
                f"帧率  <span style='color:{theme.TEXT_DIM}'>"
                f"0.1~{fps_max:.0f} 帧/秒</span>"
            )
            self._fps_label.setTextFormat(Qt.RichText)
            self._fps.setToolTip(
                "帧率 (每秒采集的帧数, 单位 Hz = 帧/秒)\n"
                "例: 15 Hz 表示每秒采 15 帧"
            )
            try:
                fps = cam.get_frame_rate_hz()
                if fps > 0:
                    self._fps.setValue(float(fps))
            except Exception as e:
                logger.warning(f"读帧率失败: {e}")

            self._binning_label.setText(
                f"Binning  <span style='color:{theme.TEXT_DIM}'>1~4</span>"
            )
            self._binning_label.setTextFormat(Qt.RichText)
            try:
                self._binning.setValue(int(cam.get_binning()))
            except Exception as e:
                logger.warning(f"读 Binning 失败: {e}")

            try:
                trig = cam.get_capture_trigger()
                for i in range(self._trigger.count()):
                    if self._trigger.itemData(i) == trig:
                        self._trigger.setCurrentIndex(i)
                        break
            except Exception as e:
                logger.warning(f"读触发模式失败: {e}")
        finally:
            for w in widgets:
                w.blockSignals(False)

        self.refresh_temperature()

    # ── 内部: 推相机 ──

    def _push_exposure(self) -> None:
        if self._camera is None:
            return
        try:
            self._camera.set_exposure_us(self._exposure.value())
        except Exception as e:
            logger.warning(f"设置曝光失败: {e}")

    def _push_gain(self) -> None:
        if self._camera is None:
            return
        try:
            self._camera.set_gain_db(self._gain.value())
        except Exception as e:
            logger.warning(f"设置增益失败: {e}")

    def _push_fps(self) -> None:
        if self._camera is None:
            return
        try:
            self._camera.set_frame_rate_hz(self._fps.value())
        except Exception as e:
            logger.warning(f"设置帧率失败: {e}")

    def _push_pixel_format(self, fmt: str) -> None:
        if self._camera is None:
            return
        try:
            self._camera.set_pixel_format(fmt)
            self.pixel_format_changed.emit(fmt)
        except Exception as e:
            logger.warning(f"设置像素格式失败: {e}")
            # 回滚显示
            self._pixel.blockSignals(True)
            try:
                self._pixel.setCurrentText(self._camera.get_pixel_format())
            finally:
                self._pixel.blockSignals(False)

    def _push_binning(self, value: int) -> None:
        if self._camera is None:
            return
        try:
            self._camera.set_binning(value)
            self.binning_changed.emit(value)
        except Exception as e:
            logger.warning(f"设置 Binning 失败: {e}")

    def _push_trigger_mode(self) -> None:
        if self._camera is None:
            return
        mode = self._trigger.currentData()
        if not isinstance(mode, str):
            return
        try:
            self._camera.set_capture_trigger(mode)
            self.trigger_mode_changed.emit(mode)
        except Exception as e:
            logger.warning(f"设置触发模式失败: {e}")
            # 回滚到相机实际状态
            try:
                actual = self._camera.get_capture_trigger()
                self._trigger.blockSignals(True)
                for i in range(self._trigger.count()):
                    if self._trigger.itemData(i) == actual:
                        self._trigger.setCurrentIndex(i)
                        break
                self._trigger.blockSignals(False)
            except Exception:
                pass

    # ── 其他 ──

    def _set_enabled(self, enabled: bool) -> None:
        for w in (self._exposure, self._gain, self._pixel, self._fps, self._binning, self._trigger):
            w.setEnabled(enabled)
        # 预设: 保存/加载/删除只有连上相机且不在采集中才有意义
        for b in (self._preset_save_btn, self._preset_load_btn, self._preset_delete_btn):
            b.setEnabled(enabled)

    # ── 预设 ──

    def _refresh_preset_combo(self) -> None:
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        if not self._presets:
            self._preset_combo.addItem("(无预设)")
            self._preset_combo.setEnabled(False)
        else:
            self._preset_combo.setEnabled(True)
            for p in self._presets:
                self._preset_combo.addItem(p.name, userData=p)
        self._preset_combo.blockSignals(False)

    def _on_preset_save(self) -> None:
        if self._camera is None:
            return
        name, ok = QInputDialog.getText(
            self, "保存预设", "预设名称 (同名将覆盖):"
        )
        if not ok or not name.strip():
            return
        preset = capture_preset(name.strip(), self._camera)
        self._presets = upsert_preset(self._presets, preset)
        save_presets(self._presets)
        self._refresh_preset_combo()
        # 选中刚存的
        for i in range(self._preset_combo.count()):
            if self._preset_combo.itemText(i) == name.strip():
                self._preset_combo.setCurrentIndex(i)
                break
        logger.info(f"保存预设: {name}")

    def _on_preset_load(self) -> None:
        if self._camera is None:
            return
        data = self._preset_combo.currentData()
        if not isinstance(data, CameraPreset):
            return
        errors = apply_preset(data, self._camera)
        self._pull_from_camera()
        if errors:
            QMessageBox.warning(
                self,
                "预设部分加载失败",
                "以下项未能应用:\n  " + "\n  ".join(errors),
            )
        else:
            logger.info(f"加载预设: {data.name}")

    def _on_preset_delete(self) -> None:
        data = self._preset_combo.currentData()
        if not isinstance(data, CameraPreset):
            return
        reply = QMessageBox.question(
            self,
            "删除预设",
            f"确认删除预设 “{data.name}” ?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._presets = delete_preset(self._presets, data.name)
        save_presets(self._presets)
        self._refresh_preset_combo()
        logger.info(f"删除预设: {data.name}")

    # 供 M8 采集时冻结参数使用
    def set_locked(self, locked: bool) -> None:
        self._set_enabled(not locked and self._camera is not None)
