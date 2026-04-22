"""单次采集任务 (one-shot): 单帧 或 N 帧平均, 完成后落盘。

线程模型: QObject + moveToThread。UI 层用法:
    thread = QThread()
    worker = CaptureWorker(request)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from acquire_app.camera.base import CameraBase
from acquire_app.config import APP_NAME, APP_VERSION
from acquire_app.core.averager import FrameAverager
from acquire_app.core.full_scale import full_scale_for
from acquire_app.core.image_stats import compute_stats
from acquire_app.core.metadata import (
    AcquisitionDiagnostics,
    AppInfo,
    CaptureMetadata,
    CameraInfo as MetaCameraInfo,
    EnvironmentInfo,
    ROI,
)
from acquire_app.core.naming import build_filename
from acquire_app.core.tiff_writer import write_capture
from acquire_app.logger import logger


@dataclass
class CaptureRequest:
    camera: CameraBase
    mode: str                    # "S" 单帧 / "A" N 帧平均
    prefix: str
    label: str
    out_dir: Path
    n_frames: int = 1
    environment: EnvironmentInfo = field(default_factory=EnvironmentInfo)
    grab_timeout_ms: int = 3000
    write_preview: bool = True
    camera_model: str = ""
    camera_serial: str = ""
    owns_stream: bool = True     # False: 调用方已开流 (如预览 worker), 采集后不 stop
    apply_dark: bool = False
    apply_flat: bool = False


class CaptureWorker(QObject):
    started = Signal()
    progress = Signal(int, int)            # current, total
    frame_captured = Signal(np.ndarray)    # 原始帧, UI 可选订阅做预览
    finished = Signal(dict)                # {paths, metadata, duration_ms}
    failed = Signal(str)

    def __init__(self, request: CaptureRequest, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._req = request
        self._stop_requested = False

    @Slot()
    def cancel(self) -> None:
        self._stop_requested = True

    @Slot()
    def run(self) -> None:
        req = self._req
        try:
            self._validate()
        except Exception as e:
            logger.exception("参数校验失败")
            self.failed.emit(str(e))
            return

        self.started.emit()
        start_ts = datetime.now()

        try:
            image = self._acquire()
            if image is None:
                # 中止: _acquire 已 emit failed
                return
        except TimeoutError as e:
            logger.warning(f"采集超时: {e}")
            self.failed.emit(f"采集超时: {e}")
            return
        except Exception as e:
            logger.exception("采集异常")
            self.failed.emit(f"采集异常: {e}")
            return

        end_ts = datetime.now()

        # 暗场 / 平场校准 (可选)
        image = self._maybe_apply_calibration(image)

        try:
            result = self._persist(image, start_ts, end_ts)
        except FileExistsError as e:
            logger.warning(f"文件已存在: {e}")
            self.failed.emit(f"文件冲突: {e}")
            return
        except Exception as e:
            logger.exception("落盘异常")
            self.failed.emit(f"落盘异常: {e}")
            return

        self.finished.emit(result)

    # ── 内部 ──

    def _validate(self) -> None:
        r = self._req
        if r.mode not in ("S", "A"):
            raise ValueError(f"未知模式: {r.mode!r}")
        if r.mode == "A" and r.n_frames < 1:
            raise ValueError(f"平均模式 n_frames 必须 >= 1, 实得 {r.n_frames}")
        if r.mode == "S" and r.n_frames != 1:
            raise ValueError(f"单帧模式 n_frames 必须 = 1, 实得 {r.n_frames}")

    def _acquire(self) -> np.ndarray | None:
        """采集并返回最终图像。同时填充 self._diag / self._first_frame 供 _persist 使用。

        §5.3: 任何掉帧立即中止, 不保存。
        """
        r = self._req
        cam = r.camera

        self._diag = AcquisitionDiagnostics()
        self._first_frame: np.ndarray | None = None

        if r.owns_stream:
            cam.start_stream()
        else:
            # 流由调用方 (预览) 持有。预览 pause 时相机仍在产帧, DataStream
            # 缓冲会溢出并丢旧帧, 导致采集到的前几帧 frame_id 不连续。
            # 这里强制 stop+start 清空残留帧, 保证采集序列干净。
            self._flush_stream(cam)
        try:
            if r.mode == "S":
                self.progress.emit(0, 1)
                if self._stop_requested:
                    self.failed.emit("用户中止")
                    return None
                self._maybe_software_trigger(cam)
                frame = cam.grab_one(timeout_ms=r.grab_timeout_ms)
                self._record_frame(frame)
                self._first_frame = frame.image
                self.frame_captured.emit(frame.image)
                self.progress.emit(1, 1)
                self._finalize_diagnostics(None)
                return frame.image

            # 平均模式
            averager = FrameAverager(r.n_frames)
            self.progress.emit(0, r.n_frames)
            prev_id: int | None = None
            for i in range(r.n_frames):
                if self._stop_requested:
                    self.failed.emit(
                        f"用户中止 (已采 {i}/{r.n_frames} 帧, 不落盘)"
                    )
                    return None
                self._maybe_software_trigger(cam)
                frame = cam.grab_one(timeout_ms=r.grab_timeout_ms)

                # 掉帧检查: frame_id 必须严格 +1 (首帧除外)
                if prev_id is not None and frame.frame_id != prev_id + 1:
                    gap = frame.frame_id - prev_id - 1
                    self.failed.emit(
                        f"掉帧: 第 {i + 1} 帧 frame_id={frame.frame_id} "
                        f"前一帧={prev_id}, 缺失 {gap} 帧, 已中止 (不落盘)"
                    )
                    return None
                prev_id = frame.frame_id

                self._record_frame(frame)
                if self._first_frame is None:
                    self._first_frame = frame.image
                averager.add(frame.image)
                self.frame_captured.emit(frame.image)
                self.progress.emit(i + 1, r.n_frames)

            mean_image = averager.result()
            self._finalize_diagnostics(mean_image)
            return mean_image
        finally:
            if r.owns_stream:
                try:
                    cam.stop_stream()
                except Exception:
                    logger.warning("stop_stream 失败, 已忽略")

    def _flush_stream(self, cam) -> None:
        """stop + start 清空 DataStream 缓冲里的陈旧帧。"""
        try:
            cam.stop_stream()
        except Exception as e:
            logger.warning(f"flush_stream stop 失败: {e}")
        try:
            cam.start_stream()
        except Exception as e:
            logger.warning(f"flush_stream start 失败: {e}")

    def _maybe_apply_calibration(self, image: np.ndarray) -> np.ndarray:
        r = self._req
        if not (r.apply_dark or r.apply_flat):
            return image
        from acquire_app.core.calibration import (
            apply_correction, load_dark, load_flat,
        )
        dark, _ = load_dark() if r.apply_dark else (None, None)
        flat, _ = load_flat() if r.apply_flat else (None, None)
        corrected = apply_correction(
            raw=image,
            dark=dark,
            flat=flat,
            apply_dark=r.apply_dark,
            apply_flat=r.apply_flat,
        )
        # 校准后图像统一为 float32; 单帧模式也要切换 dtype
        if r.mode == "S":
            # 改成 A 模式的 dtype 路径: float32 + mode 仍按请求 (S)
            # _check_dtype 对 S 要求 uint16, 所以校准后要路由到 A 式写盘
            # 简化: 凡是做了校准就把 mode 当作"已修正单帧" — 但保留 S 标签,
            # 直接让 dtype 成 float32 并改 mode 为 "A" 风格的写盘.
            # 折中方案: 把 corrected.astype 成和原图一样的 dtype (uint16), 但可能负数裁剪
            # 选稳: 只要 apply_* 任一为真, 就把 mode 改为 "A" 以保留 float32 精度
            r.mode = "A"
            if r.n_frames < 1:
                r.n_frames = 1
        return corrected

    def _maybe_software_trigger(self, cam) -> None:
        try:
            if cam.get_capture_trigger() == "software":
                cam.send_trigger_software()
        except Exception as e:
            logger.warning(f"软触发发送失败: {e}")

    def _record_frame(self, frame) -> None:
        d = self._diag
        d.frame_ids.append(int(frame.frame_id))
        d.exposure_observed_us.append(float(frame.exposure_us))
        d.gain_observed_db.append(float(frame.gain_db))

    def _finalize_diagnostics(self, mean_image: np.ndarray | None) -> None:
        """汇总一致性 + 噪声降低因子。"""
        d = self._diag

        # 一致性
        exp_set = set(round(v, 3) for v in d.exposure_observed_us)
        gain_set = set(round(v, 4) for v in d.gain_observed_db)
        d.exposure_consistent = len(exp_set) <= 1
        d.gain_consistent = len(gain_set) <= 1

        # dropped_frames: 合法数据上应永远 0 (掉帧已提前中止); 保底再算一次
        ids = d.frame_ids
        dropped = 0
        for i in range(1, len(ids)):
            dropped += max(0, ids[i] - ids[i - 1] - 1)
        d.dropped_frames = dropped

        # 噪声降低因子
        if mean_image is not None and self._first_frame is not None:
            std_first = float(np.std(self._first_frame.astype(np.float64)))
            std_mean = float(np.std(mean_image.astype(np.float64)))
            if std_mean > 1e-9:
                d.noise_reduction_factor = std_first / std_mean
            else:
                d.noise_reduction_factor = None
            d.expected_noise_factor = float(np.sqrt(self._req.n_frames))

    def _persist(
        self,
        image: np.ndarray,
        start_ts: datetime,
        end_ts: datetime,
    ) -> dict:
        r = self._req
        cam = r.camera

        pixel_format = cam.get_pixel_format()
        full_scale = full_scale_for(pixel_format)

        md = CaptureMetadata(app=AppInfo(name=APP_NAME, version=APP_VERSION))
        md.camera = MetaCameraInfo(
            model=r.camera_model,
            serial=r.camera_serial,
            sdk_version="",
        )
        md.acquisition.mode = r.mode
        md.acquisition.n_frames = r.n_frames
        md.acquisition.pixel_format = pixel_format
        md.acquisition.exposure_us = cam.get_exposure_us()
        md.acquisition.gain_db = cam.get_gain_db()
        md.acquisition.binning = cam.get_binning()
        roi = cam.get_roi()
        md.acquisition.roi = ROI(
            offset_x=roi[0], offset_y=roi[1], width=roi[2], height=roi[3]
        )
        md.acquisition.black_level = cam.get_black_level()
        md.acquisition.diagnostics = self._diag
        md.acquisition.dropped_frames = self._diag.dropped_frames

        md.environment = r.environment
        md.capture.prefix = r.prefix
        md.capture.label = r.label

        md.stamp_timing(start_ts, end_ts)
        md.set_image_stats(compute_stats(image, full_scale=float(full_scale)))

        fname = build_filename(
            prefix=r.prefix,
            label=r.label,
            mode=r.mode,
            exposure_us=md.acquisition.exposure_us,
            gain_db=md.acquisition.gain_db,
            n_frames=r.n_frames if r.mode == "A" else None,
            timestamp=start_ts,
        )
        stem = Path(fname).stem

        paths = write_capture(
            out_dir=r.out_dir,
            image=image,
            metadata=md,
            stem=stem,
            write_preview=r.write_preview,
        )

        return {
            "paths": {k: str(v) for k, v in paths.items()},
            "metadata": md.to_dict(),
            "duration_ms": md.timing.duration_ms,
            "stem": stem,
        }
