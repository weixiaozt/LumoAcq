"""锁相采集 worker: 独立线程, 独立信号, 独立落盘路径。

跟 CaptureWorker 解耦: 共用相机 + 写盘 + 元数据, 但采集循环与算法独立。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from acquire_app.camera.base import CameraBase
from acquire_app.config import APP_NAME, APP_VERSION
from acquire_app.core.full_scale import full_scale_for
from acquire_app.core.image_stats import compute_stats
from acquire_app.core.lockin import FreeRunLockIn, PairwiseLockIn
from acquire_app.core.metadata import (
    AppInfo,
    CameraInfo as MetaCameraInfo,
    CaptureMetadata,
    EnvironmentInfo,
    LockInInfo,
    ROI,
)
from acquire_app.core.naming import build_filename
from acquire_app.core.tiff_writer import write_capture
from acquire_app.logger import logger


@dataclass
class LockInRequest:
    camera: CameraBase
    prefix: str
    label: str
    out_dir: Path
    total_frames: int                 # free 模式: >=4; hardware 模式: 偶数 >=2
    light_freq_hz: float = 5.0
    sync_mode: str = "free"           # "free" (自由运行, median+MAD) | "hardware" (硬件同步, 相邻配对)
    reject_z: float = 2.5             # 仅 free 模式: 过渡帧剔除 z 阈值
    environment: EnvironmentInfo = field(default_factory=EnvironmentInfo)
    camera_model: str = ""
    camera_serial: str = ""
    grab_timeout_ms: int = 3000
    write_preview: bool = True
    owns_stream: bool = False
    apply_dark: bool = False          # 锁相一般无需扣暗 (做差已抵消), 保留选项


class LockInWorker(QObject):
    started = Signal()
    progress = Signal(int, int)                              # current, total
    frame_captured = Signal(np.ndarray, int)                  # image, frame_index
    means_updated = Signal(list, list)                        # frame_means, is_bright (每对结束发一次)
    result_ready = Signal(np.ndarray)                         # 锁相幅度图, 落盘前发给 UI
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, request: LockInRequest, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._req = request
        self._stop_requested = False

    @Slot()
    def cancel(self) -> None:
        self._stop_requested = True

    @Slot()
    def run(self) -> None:
        r = self._req
        try:
            self._validate()
        except Exception as e:
            logger.exception("锁相参数校验失败")
            self.failed.emit(str(e))
            return

        self.started.emit()
        start_ts = datetime.now()

        try:
            mean_diff_image, diag = self._acquire()
            if mean_diff_image is None:
                return
        except TimeoutError as e:
            logger.warning(f"锁相采集超时: {e}")
            self.failed.emit(f"采集超时: {e}")
            return
        except Exception as e:
            logger.exception("锁相采集异常")
            self.failed.emit(f"采集异常: {e}")
            return

        # 锁相做差本就抵消直流背景; 如果用户显式勾选扣暗场, 额外再扣一次 (极少用)
        if self._req.apply_dark:
            from acquire_app.core.calibration import apply_correction, load_dark
            dark, _ = load_dark()
            mean_diff_image = apply_correction(
                mean_diff_image,
                dark=dark, flat=None,
                apply_dark=True, apply_flat=False,
            )

        # 落盘前推给 UI, 让监视窗可以立刻展示最终锁相图
        self.result_ready.emit(mean_diff_image)

        end_ts = datetime.now()

        try:
            result = self._persist(mean_diff_image, diag, start_ts, end_ts)
        except FileExistsError as e:
            self.failed.emit(f"文件冲突: {e}")
            return
        except Exception as e:
            logger.exception("锁相落盘异常")
            self.failed.emit(f"落盘异常: {e}")
            return

        self.finished.emit(result)

    # ── 内部 ──

    def _validate(self) -> None:
        r = self._req
        if r.sync_mode == "hardware":
            if r.total_frames < 2 or r.total_frames % 2 != 0:
                raise ValueError(
                    f"硬件同步锁相 total_frames 必须是 >= 2 的偶数, 实得 {r.total_frames}"
                )
        elif r.sync_mode == "free":
            if r.total_frames < 4:
                raise ValueError(
                    f"自由运行锁相 total_frames 必须 >= 4, 实得 {r.total_frames}"
                )
        else:
            raise ValueError(f"未知 sync_mode: {r.sync_mode!r}")
        if not (0.1 <= r.light_freq_hz <= 200.0):
            raise ValueError(f"光源频率超出 [0.1, 200] Hz: {r.light_freq_hz}")

    def _acquire(self):
        r = self._req
        cam = r.camera

        if r.owns_stream:
            cam.start_stream()
        else:
            # 刷掉预览留下的陈旧缓冲
            self._flush_stream(cam)

        averager = (
            PairwiseLockIn(r.total_frames)
            if r.sync_mode == "hardware"
            else FreeRunLockIn(r.total_frames, reject_z=r.reject_z)
        )
        prev_id: int | None = None
        try:
            self.progress.emit(0, r.total_frames)
            for i in range(r.total_frames):
                if self._stop_requested:
                    self.failed.emit(
                        f"用户中止 (已采 {i}/{r.total_frames} 帧, 不落盘)"
                    )
                    return None, None

                try:
                    if cam.get_capture_trigger() == "software":
                        cam.send_trigger_software()
                except Exception as e:
                    logger.warning(f"软触发发送失败: {e}")
                frame = cam.grab_one(timeout_ms=r.grab_timeout_ms)
                if prev_id is not None and frame.frame_id != prev_id + 1:
                    gap = frame.frame_id - prev_id - 1
                    self.failed.emit(
                        f"掉帧: 第 {i + 1} 帧 frame_id={frame.frame_id} "
                        f"前一帧={prev_id}, 缺失 {gap} 帧, 已中止 (不落盘)"
                    )
                    return None, None
                prev_id = frame.frame_id

                averager.add(frame.image)
                self.frame_captured.emit(frame.image, i)
                # 每对结束 (或至少每 2 帧) 推送一次波形数据. free 模式此时 labels 全 None
                if (i + 1) % 2 == 0:
                    self.means_updated.emit(averager.frame_means, averager.is_bright)
                self.progress.emit(i + 1, r.total_frames)

            result = averager.result()
            diag = averager.diagnostics()
            # 分类完成后再推一次, 让 UI 把过渡帧 (None) 和确定的亮/暗 (True/False) 区分开
            self.means_updated.emit(averager.frame_means, averager.is_bright)
            return result, diag
        finally:
            if r.owns_stream:
                try:
                    cam.stop_stream()
                except Exception:
                    logger.warning("stop_stream 失败, 已忽略")

    def _flush_stream(self, cam) -> None:
        try:
            cam.stop_stream()
        except Exception as e:
            logger.warning(f"flush_stream stop 失败: {e}")
        try:
            cam.start_stream()
        except Exception as e:
            logger.warning(f"flush_stream start 失败: {e}")

    def _persist(
        self,
        image: np.ndarray,
        diag: dict,
        start_ts: datetime,
        end_ts: datetime,
    ) -> dict:
        r = self._req
        cam = r.camera

        pixel_format = cam.get_pixel_format()
        full_scale = full_scale_for(pixel_format)

        md = CaptureMetadata(app=AppInfo(name=APP_NAME, version=APP_VERSION))
        md.camera = MetaCameraInfo(
            model=r.camera_model, serial=r.camera_serial, sdk_version=""
        )
        md.acquisition.mode = "L"
        md.acquisition.n_frames = r.total_frames
        md.acquisition.pixel_format = pixel_format
        md.acquisition.exposure_us = cam.get_exposure_us()
        md.acquisition.gain_db = cam.get_gain_db()
        md.acquisition.binning = cam.get_binning()
        roi = cam.get_roi()
        md.acquisition.roi = ROI(
            offset_x=roi[0], offset_y=roi[1], width=roi[2], height=roi[3]
        )
        md.acquisition.black_level = cam.get_black_level()

        md.environment = r.environment
        md.capture.prefix = r.prefix
        md.capture.label = r.label

        md.stamp_timing(start_ts, end_ts)
        # 锁相图统计跟普通图意义不同 (可能有负值), 但 full_scale 基础上仍给参考直方图
        md.set_image_stats(
            compute_stats(image.astype(np.float32), full_scale=float(full_scale))
        )

        md.lockin = LockInInfo(
            total_frames=r.total_frames,
            light_freq_hz=r.light_freq_hz,
            sync_mode=r.sync_mode,
            has_phase=False,
            reject_z=float(diag.get("reject_z", 0.0)),
            n_bright=int(diag.get("n_bright", 0)),
            n_dark=int(diag.get("n_dark", 0)),
            n_transition=int(diag.get("n_transition", 0)),
            transition_frames=list(diag.get("transition_frames", [])),
            n_pairs=int(diag.get("pairs", 0)),
            aliased_pairs=int(diag.get("aliased_pairs", 0)),
            bright_mean_avg=float(diag.get("bright_mean_avg", 0.0)),
            dark_mean_avg=float(diag.get("dark_mean_avg", 0.0)),
            contrast_ratio=float(diag.get("contrast_ratio", 0.0)),
            frame_means=list(diag.get("frame_means", [])),
            is_bright=list(diag.get("is_bright", [])),
        )

        fname = build_filename(
            prefix=r.prefix,
            label=r.label,
            mode="L",
            exposure_us=md.acquisition.exposure_us,
            gain_db=md.acquisition.gain_db,
            n_frames=r.total_frames,
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
