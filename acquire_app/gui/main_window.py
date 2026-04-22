from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, QThread, QTimer, QSettings
from PySide6.QtWidgets import (
    QFrame,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QApplication,
)

import logging

from acquire_app.config import APP_NAME, APP_VERSION, DEFAULT_PIXEL_FORMAT
from acquire_app.camera.base import CameraBase, CameraInfo
from acquire_app.camera.factory import create_camera
from acquire_app.core.capture_worker import CaptureRequest, CaptureWorker
from acquire_app.core.grouped_average_worker import (
    GroupedAverageRequest,
    GroupedAverageWorker,
)
from acquire_app.core.lockin_worker import LockInRequest, LockInWorker
from acquire_app.core.preview_worker import PreviewWorker
from acquire_app.core.calibration_worker import CalibrationRequest, CalibrationWorker
from acquire_app.gui.dialogs.lockin_monitor import LockInMonitorDialog
from acquire_app.gui.dialogs.calibration_dialog import CalibrationDialog
from acquire_app.gui import theme
from acquire_app.gui.widgets.log_panel import LogPanel, QtLogHandler
from acquire_app.gui.widgets.status_dot import StatusDot
from acquire_app.gui.panels.connection_panel import ConnectionPanel, DeviceEntry
from acquire_app.gui.panels.param_panel import ParamPanel
from acquire_app.gui.panels.preview_panel import PreviewPanel
from acquire_app.gui.panels.capture_panel import CapturePanel
from acquire_app.gui.panels.env_panel import EnvPanel
from acquire_app.gui.panels.calibration_panel import CalibrationPanel
from acquire_app.logger import logger


class MainWindow(QMainWindow):
    """主窗口, 兼任相机实例的持有者与信号中枢。"""

    camera_ready = Signal(object)         # CameraBase
    camera_disconnected = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  ·  SWIR 采集工作台  ·  v{APP_VERSION}")
        self.resize(1600, 1000)

        self._camera: Optional[CameraBase] = None
        self._camera_info: Optional[CameraInfo] = None
        self._preview_thread: Optional[QThread] = None
        self._preview_worker: Optional[PreviewWorker] = None
        self._capture_thread: Optional[QThread] = None
        self._capture_worker: Optional[CaptureWorker] = None
        self._lockin_thread: Optional[QThread] = None
        self._lockin_worker: Optional[LockInWorker] = None
        self._lockin_monitor: Optional[LockInMonitorDialog] = None
        self._grouped_avg_thread: Optional[QThread] = None
        self._grouped_avg_worker: Optional[GroupedAverageWorker] = None
        self._calib_thread: Optional[QThread] = None
        self._calib_worker: Optional[CalibrationWorker] = None

        self._temp_timer = QTimer(self)
        self._temp_timer.setInterval(2000)
        self._temp_timer.timeout.connect(self._refresh_temperature)

        self._connection_panel = ConnectionPanel()
        self._param_panel = ParamPanel()
        self._capture_panel = CapturePanel()
        self._env_panel = EnvPanel()
        # 校准面板常驻实例 (持有状态), 但不放侧栏, 通过 工具菜单 打开独立对话框
        self._calib_panel = CalibrationPanel()
        self._calib_dialog: Optional[CalibrationDialog] = None

        left = self._build_side([self._connection_panel, self._param_panel])
        center = self._build_center()
        right = self._build_side([self._capture_panel, self._env_panel])

        # 固定左右栏宽度, 避免面板内长文本把列撑开
        left.setFixedWidth(340)
        right.setFixedWidth(340)

        root = QSplitter(Qt.Horizontal)
        root.setChildrenCollapsible(False)
        root.addWidget(left)
        root.addWidget(center)
        root.addWidget(right)
        root.setStretchFactor(0, 0)
        root.setStretchFactor(1, 1)
        root.setStretchFactor(2, 0)
        root.setSizes([340, 920, 340])

        self._log_panel = LogPanel()

        wrap = QWidget()
        wrap_layout = QVBoxLayout(wrap)
        wrap_layout.setContentsMargins(10, 10, 10, 6)
        wrap_layout.setSpacing(8)
        wrap_layout.addWidget(root, 1)
        wrap_layout.addWidget(self._log_panel)
        self.setCentralWidget(wrap)

        self._build_status_bar()
        self._build_menu_bar()
        self.setStyleSheet(theme.QSS)

        self._install_log_handler()

        self._connection_panel.scan_requested.connect(self._on_scan)
        self._connection_panel.connect_requested.connect(self._on_connect)
        self._connection_panel.disconnect_requested.connect(self._on_disconnect)

        self.camera_ready.connect(self._param_panel.attach_camera)
        self.camera_disconnected.connect(self._param_panel.detach_camera)

        self.camera_ready.connect(self._start_preview)
        self.camera_disconnected.connect(self._stop_preview)

        self._param_panel.pixel_format_changed.connect(self._preview.set_pixel_format)

        self.camera_ready.connect(lambda _: self._capture_panel.set_camera_connected(True))
        self.camera_disconnected.connect(lambda: self._capture_panel.set_camera_connected(False))
        self._capture_panel.single_capture_requested.connect(self._on_capture_start)
        self._capture_panel.average_capture_requested.connect(self._on_capture_start)
        self._capture_panel.grouped_average_capture_requested.connect(
            self._on_grouped_average_start
        )
        self._capture_panel.lockin_capture_requested.connect(self._on_lockin_start)
        self._capture_panel.software_trigger_requested.connect(self._on_software_trigger)
        self._capture_panel.capture_stop_requested.connect(self._on_capture_stop)

        self._param_panel.trigger_mode_changed.connect(self._capture_panel.set_trigger_mode)

        self.camera_ready.connect(lambda _: self._calib_panel.set_camera_connected(True))
        self.camera_disconnected.connect(lambda: self._calib_panel.set_camera_connected(False))
        self._calib_panel.capture_dark_requested.connect(
            lambda n: self._on_calibration_start("dark", n)
        )
        self._calib_panel.capture_flat_requested.connect(
            lambda n: self._on_calibration_start("flat", n)
        )

        self._settings = QSettings("LumoAcq", APP_NAME)
        self._restore_settings()

        self._log_panel.append(f"{APP_NAME} v{APP_VERSION} 启动就绪", "ok")

    # ── 日志 ──

    def _install_log_handler(self) -> None:
        handler = QtLogHandler(self._log_panel)
        handler.setLevel(logging.INFO)
        logger.addHandler(handler)
        self._log_handler = handler

    # ── 生命周期 ──

    def closeEvent(self, event) -> None:
        for w in (self._capture_worker, self._lockin_worker, self._grouped_avg_worker):
            if w is not None:
                try:
                    w.cancel()
                except Exception:
                    pass
        for t in (self._capture_thread, self._lockin_thread, self._grouped_avg_thread):
            if t is not None:
                try:
                    t.wait(2000)
                except Exception:
                    pass
        self._stop_preview()
        if self._camera is not None:
            try:
                self._camera.disconnect()
            except Exception as e:
                logger.warning(f"关闭时断开相机失败: {e}")
        self._save_settings()
        super().closeEvent(event)

    # ── 设置持久化 ──

    def _restore_settings(self) -> None:
        s = self._settings
        geom = s.value("window/geometry")
        if geom is not None:
            self.restoreGeometry(geom)
        state = {
            "prefix": s.value("capture/prefix"),
            "label": s.value("capture/label"),
            "out_dir": s.value("capture/out_dir"),
            "n_avg": s.value("capture/n_avg"),
            "lk_n": s.value("capture/lk_n"),
            "lk_freq": s.value("capture/lk_freq"),
        }
        # 过滤 None 避免把空值写回控件
        state = {k: v for k, v in state.items() if v not in (None, "")}
        self._capture_panel.apply_state(state)

    def _save_settings(self) -> None:
        s = self._settings
        s.setValue("window/geometry", self.saveGeometry())
        st = self._capture_panel.get_state()
        for k, v in st.items():
            s.setValue(f"capture/{k}", v)
        s.sync()

    # ── 预览 ──

    def _start_preview(self, camera: CameraBase) -> None:
        if self._preview_thread is not None:
            return
        try:
            fmt = camera.get_pixel_format()
            self._preview.set_pixel_format(fmt)
        except Exception:
            pass

        # 注意: 不把 deleteLater 连到 thread.finished, 否则 _stop_preview
        # 再访问 self._preview_thread 会撞上 "C++ object already deleted".
        thread = QThread(self)
        worker = PreviewWorker(camera)
        worker.moveToThread(thread)
        thread.started.connect(worker.start)
        worker.frame_ready.connect(self._on_preview_frame)
        worker.capture_fps_measured.connect(self._preview.set_capture_fps)
        worker.error.connect(self._on_preview_error)
        worker.stopped.connect(thread.quit)

        self._preview_thread = thread
        self._preview_worker = worker
        thread.start()
        self._status_msg.setText("预览运行中")
        self._refresh_temperature()
        self._temp_timer.start()

    def _stop_preview(self) -> None:
        self._temp_timer.stop()
        worker = self._preview_worker
        thread = self._preview_thread
        self._preview_worker = None
        self._preview_thread = None

        if worker is not None:
            try:
                worker.stop()                  # _running=False + cam.stop_stream()
            except RuntimeError:
                pass

        if thread is not None:
            try:
                thread.quit()                  # 让 exec() 立刻退出, 不等 stopped 信号
                if not thread.wait(2000):
                    logger.warning("预览线程停止超时")
            except RuntimeError:
                pass
            try:
                thread.deleteLater()
            except RuntimeError:
                pass
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

        self._preview.clear()

    def _on_preview_frame(self, image, frame_id: int) -> None:
        self._preview.set_frame(image)
        self._status_fps.setText(f"#{frame_id}")

    def _on_preview_error(self, msg: str) -> None:
        logger.warning(f"预览错误: {msg}")
        self._status_msg.setText(f"预览错误: {msg}")
        self._status_dot.set_state("error")

    # ── 采集 ──

    def _on_capture_start(self, payload: dict) -> None:
        if self._camera is None:
            self._log_panel.append("相机未连接, 无法采集", "warn")
            return
        if (
            self._capture_worker is not None
            or self._lockin_worker is not None
            or self._grouped_avg_worker is not None
        ):
            self._log_panel.append("上一次采集尚未结束", "warn")
            return

        # 按模式分流到 single/ 或 average/ 子目录
        root = Path(payload["out_dir"])
        subdir = "single" if payload["mode"] == "S" else "average"
        out_dir = root / subdir

        if self._preview_worker is not None:
            self._preview_worker.pause()
        self._param_panel.set_locked(True)
        self._connection_panel.set_locked(True)
        self._capture_panel.set_busy(True)
        self._capture_panel.reset_progress()

        info = self._camera_info
        model = info.model if info else ""
        serial = info.serial if info else ""

        request = CaptureRequest(
            camera=self._camera,
            mode=payload["mode"],
            n_frames=payload["n_frames"],
            prefix=payload["prefix"],
            label=payload["label"],
            out_dir=out_dir,
            environment=self._env_panel.get_environment(),
            camera_model=model,
            camera_serial=serial,
            owns_stream=False,   # 预览 worker 持有流, 采集借用
            apply_dark=self._calib_panel.apply_dark(),
            apply_flat=self._calib_panel.apply_flat(),
        )

        thread = QThread(self)
        worker = CaptureWorker(request)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._capture_panel.set_progress)
        worker.finished.connect(self._on_capture_finished)
        worker.failed.connect(self._on_capture_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)

        self._capture_thread = thread
        self._capture_worker = worker
        thread.start()

        mode_text = "单帧" if payload["mode"] == "S" else f"平均 N={payload['n_frames']}"
        self._log_panel.append(
            f"开始采集 [{mode_text}]  {payload['prefix']}_{payload['label']}  → {out_dir}",
            "info",
        )
        self._status_msg.setText("采集进行中")
        self._status_dot.set_state("busy")

    def _on_software_trigger(self) -> None:
        if self._camera is None:
            self._log_panel.append("相机未连接, 无法软触发", "warn")
            return
        try:
            self._camera.send_trigger_software()
            self._log_panel.append("已发送一次软触发", "info")
        except Exception as e:
            self._log_panel.append(f"软触发失败: {e}", "error")

    def _on_capture_stop(self) -> None:
        if self._capture_worker is not None:
            self._capture_worker.cancel()
            self._log_panel.append("已请求中止采集…", "warn")
        if self._lockin_worker is not None:
            self._lockin_worker.cancel()
            self._log_panel.append("已请求中止锁相采集…", "warn")
        if self._grouped_avg_worker is not None:
            self._grouped_avg_worker.cancel()
            self._log_panel.append("已请求中止分组平均采集…", "warn")
        if self._calib_worker is not None:
            self._calib_worker.cancel()
            self._log_panel.append("已请求中止校准采集…", "warn")

    # ── 锁相 ──

    def _on_lockin_start(self, payload: dict) -> None:
        if self._camera is None:
            self._log_panel.append("相机未连接, 无法锁相采集", "warn")
            return
        if (
            self._lockin_worker is not None
            or self._capture_worker is not None
            or self._grouped_avg_worker is not None
        ):
            self._log_panel.append("上一次采集尚未结束", "warn")
            return

        # 独立目录: <root>/lockin/
        root = Path(payload["out_dir"])
        out_dir = root / "lockin"

        if self._preview_worker is not None:
            self._preview_worker.pause()
        self._param_panel.set_locked(True)
        self._connection_panel.set_locked(True)
        self._capture_panel.set_busy(True)
        self._capture_panel.reset_progress()

        info = self._camera_info
        model = info.model if info else ""
        serial = info.serial if info else ""
        total = int(payload["n_frames"])
        freq = float(payload.get("light_freq_hz", 5.0))

        request = LockInRequest(
            camera=self._camera,
            prefix=payload["prefix"],
            label=payload["label"],
            out_dir=out_dir,
            total_frames=total,
            light_freq_hz=freq,
            sync_mode="free",
            environment=self._env_panel.get_environment(),
            camera_model=model,
            camera_serial=serial,
            owns_stream=False,
            apply_dark=self._calib_panel.apply_dark(),
        )

        # 监视窗: 实时帧 + 波形
        monitor = LockInMonitorDialog(total_frames=total, light_freq_hz=freq, parent=self)
        monitor.show()
        self._lockin_monitor = monitor

        thread = QThread(self)
        worker = LockInWorker(request)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._capture_panel.set_progress)
        worker.frame_captured.connect(monitor.update_frame)
        worker.means_updated.connect(monitor.update_waveform)
        worker.result_ready.connect(monitor.show_result)
        worker.finished.connect(self._on_lockin_finished)
        worker.failed.connect(self._on_lockin_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)

        self._lockin_thread = thread
        self._lockin_worker = worker
        thread.start()

        self._log_panel.append(
            f"开始锁相 [N={total}, 光源 {freq} Hz]  {payload['prefix']}_{payload['label']}"
            f"  → {out_dir}",
            "info",
        )
        self._status_msg.setText("锁相采集中")
        self._status_dot.set_state("busy")

    def _on_lockin_finished(self, result: dict) -> None:
        stem = result.get("stem", "?")
        duration = result.get("duration_ms", 0.0)
        paths = result.get("paths", {})
        tiff_path = paths.get("tiff", "")
        json_path = paths.get("json", "")
        meta = result.get("metadata", {}) or {}
        lk = meta.get("lockin") or {}

        self._cleanup_lockin()

        if tiff_path:
            self._capture_panel.set_last_capture(
                "L", Path(tiff_path), Path(json_path) if json_path else None
            )

        contrast = lk.get("contrast_ratio", 0.0)
        sync_mode = lk.get("sync_mode", "free")
        self._log_panel.append(
            f"锁相完成: {stem}  ({duration:.0f} ms)  → {tiff_path}", "ok"
        )
        if sync_mode == "free":
            n_total = int(lk.get("total_frames", 0))
            n_bright = int(lk.get("n_bright", 0))
            n_dark = int(lk.get("n_dark", 0))
            n_trans = int(lk.get("n_transition", 0))
            trans_ids = lk.get("transition_frames", []) or []
            trans_preview = (
                ",".join(str(i) for i in trans_ids[:10])
                + (f",… (+{len(trans_ids) - 10})" if len(trans_ids) > 10 else "")
            )
            trans_frac = n_trans / max(1, n_total)
            self._log_panel.append(
                f"  自由运行 · 调制深度 {contrast*100:.1f}% · "
                f"亮 {n_bright} / 暗 {n_dark} / 过渡剔除 {n_trans}"
                + (f" [帧 {trans_preview}]" if trans_ids else ""),
                "info" if trans_frac < 0.3 else "warn",
            )
            if trans_frac >= 0.3:
                self._log_panel.append(
                    "  警告: 过渡帧占比偏高, 检查光源切换速度或增加 N",
                    "warn",
                )
        else:
            aliased = int(lk.get("aliased_pairs", 0))
            pairs = int(lk.get("n_pairs", 1))
            self._log_panel.append(
                f"  硬件同步 · 调制深度 {contrast*100:.1f}% · 混叠对 {aliased}/{pairs}",
                "info" if aliased / max(1, pairs) < 0.3 else "warn",
            )
            if aliased / max(1, pairs) >= 0.3:
                self._log_panel.append(
                    "  警告: 过多混叠对, 建议把相机帧率设为 2 × 光源频率",
                    "warn",
                )
        self._status_msg.setText("锁相完成")
        self._status_dot.set_state("ok")

    def _on_lockin_failed(self, message: str) -> None:
        self._cleanup_lockin()
        self._log_panel.append(f"锁相失败: {message}", "error")
        self._status_msg.setText("锁相失败")
        self._status_dot.set_state("error")

    def _cleanup_lockin(self) -> None:
        thread = self._lockin_thread
        worker = self._lockin_worker
        self._lockin_thread = None
        self._lockin_worker = None

        if thread is not None:
            try:
                thread.wait(3000)
            except RuntimeError:
                pass
            try:
                thread.deleteLater()
            except RuntimeError:
                pass
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

        if self._lockin_monitor is not None:
            try:
                self._lockin_monitor.mark_done("采集结束 — 可关闭此窗口")
            except RuntimeError:
                pass
            self._lockin_monitor = None   # 非模态, 留给用户自行关

        self._param_panel.set_locked(False)
        self._connection_panel.set_locked(False)
        self._capture_panel.set_busy(False)
        if self._preview_worker is not None:
            self._preview_worker.resume()

    def _on_capture_finished(self, result: dict) -> None:
        stem = result.get("stem", "?")
        duration = result.get("duration_ms", 0.0)
        paths = result.get("paths", {})
        tiff_path = paths.get("tiff", "")
        json_path = paths.get("json", "")

        # 诊断日志
        meta = result.get("metadata", {})
        acq = meta.get("acquisition", {})
        diag = acq.get("diagnostics", {}) or {}
        nrf = diag.get("noise_reduction_factor")
        exp_factor = diag.get("expected_noise_factor")

        self._cleanup_capture()

        mode = (acq.get("mode") or "S").upper()
        if tiff_path:
            self._capture_panel.set_last_capture(
                mode, Path(tiff_path), Path(json_path) if json_path else None
            )

        self._log_panel.append(
            f"采集完成: {stem}  ({duration:.0f} ms)  → {tiff_path}", "ok"
        )
        if nrf is not None and exp_factor is not None:
            self._log_panel.append(
                f"  噪声降低因子 {nrf:.2f} (期望 ≈ √N = {exp_factor:.2f})",
                "info",
            )
        if diag.get("dropped_frames", 0) > 0:
            self._log_panel.append(
                f"  警告: 掉帧 {diag['dropped_frames']} 帧", "warn"
            )
        self._status_msg.setText("采集完成")
        self._status_dot.set_state("ok")

    def _on_capture_failed(self, message: str) -> None:
        self._cleanup_capture()
        self._log_panel.append(f"采集失败: {message}", "error")
        self._status_msg.setText("采集失败")
        self._status_dot.set_state("error")

    def _cleanup_capture(self) -> None:
        thread = self._capture_thread
        worker = self._capture_worker
        self._capture_thread = None
        self._capture_worker = None

        if thread is not None:
            try:
                thread.wait(3000)
            except RuntimeError:
                pass
            try:
                thread.deleteLater()
            except RuntimeError:
                pass
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

        self._param_panel.set_locked(False)
        self._connection_panel.set_locked(False)
        self._capture_panel.set_busy(False)
        if self._preview_worker is not None:
            self._preview_worker.resume()

    # ── 分组平均 (EL 双图) ──

    def _on_grouped_average_start(self, payload: dict) -> None:
        if self._camera is None:
            self._log_panel.append("相机未连接, 无法分组平均采集", "warn")
            return
        if (
            self._capture_worker is not None
            or self._lockin_worker is not None
            or self._grouped_avg_worker is not None
        ):
            self._log_panel.append("上一次采集尚未结束", "warn")
            return

        # 跟 N 帧平均同目录
        root = Path(payload["out_dir"])
        out_dir = root / "average"

        if self._preview_worker is not None:
            self._preview_worker.pause()
        self._param_panel.set_locked(True)
        self._connection_panel.set_locked(True)
        self._capture_panel.set_busy(True)
        self._capture_panel.reset_progress()

        info = self._camera_info
        model = info.model if info else ""
        serial = info.serial if info else ""
        total = int(payload["n_frames"])

        request = GroupedAverageRequest(
            camera=self._camera,
            prefix=payload["prefix"],
            label=payload["label"],
            out_dir=out_dir,
            total_frames=total,
            environment=self._env_panel.get_environment(),
            camera_model=model,
            camera_serial=serial,
            owns_stream=False,
        )

        thread = QThread(self)
        worker = GroupedAverageWorker(request)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._capture_panel.set_progress)
        worker.finished.connect(self._on_grouped_average_finished)
        worker.failed.connect(self._on_grouped_average_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)

        self._grouped_avg_thread = thread
        self._grouped_avg_worker = worker
        thread.start()

        self._log_panel.append(
            f"开始分组平均 [N={total}]  {payload['prefix']}_{payload['label']}  → {out_dir}",
            "info",
        )
        self._status_msg.setText("分组平均采集中")
        self._status_dot.set_state("busy")

    def _on_grouped_average_finished(self, result: dict) -> None:
        bright = result.get("bright") or {}
        dark = result.get("dark") or {}
        stats = result.get("stats") or {}
        duration = result.get("duration_ms", 0.0)

        bright_tiff = (bright.get("paths") or {}).get("tiff", "")
        dark_tiff = (dark.get("paths") or {}).get("tiff", "")
        bright_json = (bright.get("paths") or {}).get("json", "")

        self._cleanup_grouped_average()

        # 最近采集条: 挂亮图 (用户要看两张时自行去目录)
        if bright_tiff:
            self._capture_panel.set_last_capture(
                "A", Path(bright_tiff),
                Path(bright_json) if bright_json else None,
            )

        n_b = int(stats.get("n_bright", 0))
        n_d = int(stats.get("n_dark", 0))
        n_t = int(stats.get("n_transition", 0))
        trans_ids = stats.get("transition_frames", []) or []
        trans_preview = (
            ",".join(str(i) for i in trans_ids[:10])
            + (f",… (+{len(trans_ids) - 10})" if len(trans_ids) > 10 else "")
        )

        self._log_panel.append(
            f"分组平均完成 ({duration:.0f} ms)", "ok"
        )
        self._log_panel.append(f"  亮: {bright_tiff}", "info")
        self._log_panel.append(f"  暗: {dark_tiff}", "info")
        self._log_panel.append(
            f"  亮 {n_b} / 暗 {n_d} / 过渡剔除 {n_t}"
            + (f" [帧 {trans_preview}]" if trans_ids else ""),
            "info",
        )
        self._status_msg.setText("分组平均完成")
        self._status_dot.set_state("ok")

    def _on_grouped_average_failed(self, message: str) -> None:
        self._cleanup_grouped_average()
        self._log_panel.append(f"分组平均失败: {message}", "error")
        self._status_msg.setText("分组平均失败")
        self._status_dot.set_state("error")

    def _cleanup_grouped_average(self) -> None:
        thread = self._grouped_avg_thread
        worker = self._grouped_avg_worker
        self._grouped_avg_thread = None
        self._grouped_avg_worker = None

        if thread is not None:
            try:
                thread.wait(3000)
            except RuntimeError:
                pass
            try:
                thread.deleteLater()
            except RuntimeError:
                pass
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

        self._param_panel.set_locked(False)
        self._connection_panel.set_locked(False)
        self._capture_panel.set_busy(False)
        if self._preview_worker is not None:
            self._preview_worker.resume()

    # ── 校准 ──

    def _on_calibration_start(self, kind: str, n_frames: int) -> None:
        if self._camera is None:
            self._log_panel.append("相机未连接, 无法校准", "warn")
            return
        if (
            self._capture_worker is not None
            or self._lockin_worker is not None
            or self._grouped_avg_worker is not None
            or self._calib_worker is not None
        ):
            self._log_panel.append("上一次采集尚未结束", "warn")
            return

        if self._preview_worker is not None:
            self._preview_worker.pause()
        self._param_panel.set_locked(True)
        self._connection_panel.set_locked(True)
        self._capture_panel.set_busy(True)
        self._calib_panel.set_busy(True)
        self._capture_panel.reset_progress()

        request = CalibrationRequest(
            kind=kind,
            camera=self._camera,
            n_frames=n_frames,
            owns_stream=False,
        )
        thread = QThread(self)
        worker = CalibrationWorker(request)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._capture_panel.set_progress)
        worker.finished.connect(self._on_calib_finished)
        worker.failed.connect(self._on_calib_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)

        self._calib_thread = thread
        self._calib_worker = worker
        thread.start()

        label = "暗场" if kind == "dark" else "平场"
        self._log_panel.append(f"开始采集{label}参考帧 (N={n_frames})", "info")
        self._status_msg.setText(f"采集{label}参考中")
        self._status_dot.set_state("busy")

    def _on_calib_finished(self, kind: str) -> None:
        label = "暗场" if kind == "dark" else "平场"
        self._cleanup_calib()
        self._calib_panel.refresh_status()
        self._log_panel.append(f"{label}参考帧已保存", "ok")
        self._status_msg.setText(f"{label}校准完成")
        self._status_dot.set_state("ok")

    def _on_calib_failed(self, message: str) -> None:
        self._cleanup_calib()
        self._log_panel.append(f"校准失败: {message}", "error")
        self._status_msg.setText("校准失败")
        self._status_dot.set_state("error")

    def _cleanup_calib(self) -> None:
        thread = self._calib_thread
        worker = self._calib_worker
        self._calib_thread = None
        self._calib_worker = None
        if thread is not None:
            try:
                thread.wait(3000)
                thread.deleteLater()
            except RuntimeError:
                pass
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
        self._param_panel.set_locked(False)
        self._connection_panel.set_locked(False)
        self._capture_panel.set_busy(False)
        self._calib_panel.set_busy(False)
        if self._preview_worker is not None:
            self._preview_worker.resume()

    def _refresh_temperature(self) -> None:
        if self._camera is None:
            return
        try:
            t = self._camera.get_temperature()
        except Exception:
            t = None
        self._preview.set_temperature(t)

        try:
            cap_fps = self._camera.get_current_frame_rate_hz()
        except Exception:
            cap_fps = None
        self._preview.set_capture_fps(cap_fps)

        self._param_panel.refresh_temperature()

    # ── 相机槽 ──

    def _on_scan(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            entries = self._scan_all_roles()
        except Exception as e:
            logger.exception("扫描失败")
            self._connection_panel.set_error(f"扫描失败: {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        self._connection_panel.set_devices(entries)
        self._status_msg.setText(f"扫描到 {len(entries)} 台设备" if entries else "未发现设备")
        self._status_dot.set_state("ok" if entries else "error")

    def _on_connect(self, role: str, index: int) -> None:
        if self._camera is not None:
            logger.warning("连接请求时已有活动相机, 先断开")
            self._teardown_camera()

        self._connection_panel.set_connecting(True)
        self._status_dot.set_state("busy")
        self._status_msg.setText(f"连接 {role}#{index}…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()

        try:
            cam = create_camera(role)
            cam.connect(index)
            self._camera = cam

            # 默认切到高位宽格式 (真机出厂常为 Mono8)
            try:
                cam.set_pixel_format(DEFAULT_PIXEL_FORMAT)
            except Exception as e:
                logger.warning(f"切换 PixelFormat 到 {DEFAULT_PIXEL_FORMAT} 失败: {e}")

            info = self._probe_info(cam, role, index)
            self._camera_info = info

            info_text = f"{info.model}  SN:{info.serial}"
            self._connection_panel.set_connected(info_text)

            # 连接后, 同步触发模式到 capture panel (影响"软触发"按钮启用状态)
            try:
                self._capture_panel.set_trigger_mode(cam.get_capture_trigger())
            except Exception:
                pass
            self._status_cam.setText(f"相机: {info.model}")
            self._status_dot.set_state("ok")
            self._status_msg.setText("已连接")
            self.camera_ready.emit(cam)

        except Exception as e:
            logger.exception("连接失败")
            self._teardown_camera()
            self._connection_panel.set_disconnected("连接失败")

            # 侧栏显示首行, 详细信息走弹窗的"详细信息"
            first_line = str(e).splitlines()[0] if str(e) else "连接失败"
            self._connection_panel.set_error(first_line)
            self._status_dot.set_state("error")
            self._status_msg.setText("连接失败")

            box = QMessageBox(self)
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("连接失败")
            box.setText(str(e))
            cause = e.__cause__ or e.__context__
            if cause is not None:
                box.setDetailedText(f"{type(cause).__name__}: {cause}")
            box.exec()
        finally:
            QApplication.restoreOverrideCursor()

    def _on_disconnect(self) -> None:
        # 顺序要先停预览, 否则 worker 在已关闭相机上 grab 会报 "m_hNewBufferEvent NULL"
        self._stop_preview()
        self.camera_disconnected.emit()
        self._teardown_camera()
        self._connection_panel.set_disconnected("已断开")
        self._status_cam.setText("相机: 未连接")
        self._status_fps.setText("FPS: —")
        self._status_dot.set_state("idle")
        self._status_msg.setText("就绪")

    def _teardown_camera(self) -> None:
        if self._camera is not None:
            try:
                self._camera.disconnect()
            except Exception as e:
                logger.warning(f"断开相机失败: {e}")
        self._camera = None
        self._camera_info = None

    # ── 辅助 ──

    @staticmethod
    def _scan_all_roles() -> list[DeviceEntry]:
        entries: list[DeviceEntry] = []

        # 真机: 枚举可能触发 SDK; 失败只记日志不挂整个扫描
        try:
            from acquire_app.camera.daheng import DahengCamera
            for info in DahengCamera.scan():
                entries.append(DeviceEntry(
                    role="daheng",
                    index=info.index,
                    display=f"[真机] {info.model}  SN:{info.serial}  {info.ip}",
                ))
        except Exception as e:
            logger.warning(f"daheng 扫描失败: {e}")

        # Dummy 始终可用, 便于离线开发
        try:
            from acquire_app.camera.dummy import DummyCamera
            for info in DummyCamera.scan():
                entries.append(DeviceEntry(
                    role="dummy",
                    index=info.index,
                    display=f"[虚拟] {info.model}",
                ))
        except Exception as e:
            logger.warning(f"dummy 扫描失败: {e}")

        return entries

    @staticmethod
    def _probe_info(cam: CameraBase, role: str, index: int) -> CameraInfo:
        """从已连接相机回推 CameraInfo (扫描时的 info 对象难直接透传)。"""
        try:
            infos = type(cam).scan()
            for info in infos:
                if info.index == index:
                    return info
        except Exception:
            pass
        return CameraInfo(index=index, vendor=role, model="Unknown", serial="")

    # ── 布局 ──

    def _build_side(self, cards) -> QWidget:
        """把一组卡片放进可滚动侧栏, 防止卡片总高超窗口时底部被裁。"""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        for c in cards:
            layout.addWidget(c)
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: transparent; border: none; }}"
        )
        return scroll

    def _build_center(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self._preview = PreviewPanel()
        layout.addWidget(self._preview, 1)
        return box

    def _build_menu_bar(self) -> None:
        mb = self.menuBar()
        tools = mb.addMenu("工具")
        act_calib = tools.addAction("暗场 / 平场校准…")
        act_calib.triggered.connect(self._show_calibration_dialog)

    def _show_calibration_dialog(self) -> None:
        if self._calib_dialog is None:
            self._calib_dialog = CalibrationDialog(self._calib_panel, parent=self)
        self._calib_dialog.show()
        self._calib_dialog.raise_()
        self._calib_dialog.activateWindow()

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)

        left = QWidget()
        left_l = QHBoxLayout(left)
        left_l.setContentsMargins(4, 0, 0, 0)
        left_l.setSpacing(8)
        self._status_dot = StatusDot("ok")
        self._status_msg = QLabel("就绪")
        left_l.addWidget(self._status_dot)
        left_l.addWidget(self._status_msg)
        sb.addWidget(left)

        self._status_cam = QLabel("相机: 未连接")
        self._status_cam.setProperty("muted", True)
        sb.addWidget(self._status_cam)

        self._status_fps = QLabel("FPS: —")
        self._status_fps.setProperty("mono", True)
        self._status_fps.setProperty("muted", True)
        sb.addPermanentWidget(self._status_fps)

        self._status_ver = QLabel(f"v{APP_VERSION}")
        self._status_ver.setProperty("muted", True)
        sb.addPermanentWidget(self._status_ver)
