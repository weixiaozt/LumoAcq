"""分组平均采集 worker: 独立线程, 采 N 帧后按亮/暗分组, 剔除过渡帧, 落两张 float32 TIFF.

行为上是 N 帧平均的 EL 变体: 输出给 Halcon 等下游做 sub_image 相减得锁相图.
算法复用 FreeRunLockIn (lockin.py) 的分类与累加, 只是不取差分而取两组平均.

跟 CaptureWorker (单帧/N 帧平均) 解耦: 采集循环独立, 落盘路径独立.
跟 LockInWorker 也解耦: 不共享代码, 但核心算法复用同一个 FreeRunLockIn 类.
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
from acquire_app.core.lockin import FreeRunLockIn
from acquire_app.core.metadata import (
    AppInfo,
    CameraInfo as MetaCameraInfo,
    CaptureMetadata,
    EnvironmentInfo,
    GroupedAverageInfo,
    ROI,
)
from acquire_app.core.naming import build_filename
from acquire_app.core.tiff_writer import write_capture
from acquire_app.logger import logger


@dataclass
class GroupedAverageRequest:
    camera: CameraBase
    prefix: str
    label: str
    out_dir: Path
    total_frames: int                 # 原始采集帧数, 必须 >= 4
    reject_z: float = 2.5
    environment: EnvironmentInfo = field(default_factory=EnvironmentInfo)
    camera_model: str = ""
    camera_serial: str = ""
    grab_timeout_ms: int = 3000
    write_preview: bool = True
    owns_stream: bool = False


class GroupedAverageWorker(QObject):
    started = Signal()
    progress = Signal(int, int)                       # current, total
    frame_captured = Signal(np.ndarray, int)          # image, frame_index
    means_updated = Signal(list, list)                # frame_means, is_bright (采集中 is_bright 全 None)
    result_ready = Signal(np.ndarray, np.ndarray)     # bright_image, dark_image (落盘前)
    finished = Signal(dict)                           # {bright: {...}, dark: {...}, stats: {...}}
    failed = Signal(str)

    def __init__(
        self,
        request: GroupedAverageRequest,
        parent: QObject | None = None,
    ) -> None:
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
            logger.exception("分组平均参数校验失败")
            self.failed.emit(str(e))
            return

        self.started.emit()
        start_ts = datetime.now()

        try:
            bright_img, dark_img, diag = self._acquire()
            if bright_img is None:
                return
        except TimeoutError as e:
            logger.warning(f"分组平均采集超时: {e}")
            self.failed.emit(f"采集超时: {e}")
            return
        except Exception as e:
            logger.exception("分组平均采集异常")
            self.failed.emit(f"采集异常: {e}")
            return

        # 落盘前推给 UI, 让监视窗立刻能切换到亮/暗结果图
        self.result_ready.emit(bright_img, dark_img)

        end_ts = datetime.now()

        try:
            result = self._persist(bright_img, dark_img, diag, start_ts, end_ts)
        except FileExistsError as e:
            self.failed.emit(f"文件冲突: {e}")
            return
        except Exception as e:
            logger.exception("分组平均落盘异常")
            self.failed.emit(f"落盘异常: {e}")
            return

        self.finished.emit(result)

    # ── 内部 ──

    def _validate(self) -> None:
        r = self._req
        if r.total_frames < 4:
            raise ValueError(
                f"分组平均 total_frames 必须 >= 4, 实得 {r.total_frames}"
            )

    def _acquire(self):
        r = self._req
        cam = r.camera

        if r.owns_stream:
            cam.start_stream()
        else:
            self._flush_stream(cam)

        averager = FreeRunLockIn(r.total_frames, reject_z=r.reject_z)
        prev_id: int | None = None
        try:
            self.progress.emit(0, r.total_frames)
            for i in range(r.total_frames):
                if self._stop_requested:
                    self.failed.emit(
                        f"用户中止 (已采 {i}/{r.total_frames} 帧, 不落盘)"
                    )
                    return None, None, None

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
                    return None, None, None
                prev_id = frame.frame_id

                averager.add(frame.image)
                # 推给监视窗: 实时帧
                self.frame_captured.emit(frame.image, i)
                # 每 2 帧推一次帧均值曲线 (此时 is_bright 全 None, UI 端单色散点)
                if (i + 1) % 2 == 0:
                    self.means_updated.emit(
                        list(averager.frame_means), list(averager.is_bright)
                    )
                self.progress.emit(i + 1, r.total_frames)

            # 触发分类, 然后取两张分组平均图
            bright = averager.bright_image()
            dark = averager.dark_image()
            diag = averager.diagnostics()
            # 分类完成后再推一次, 让监视窗散点着色 (亮/暗/过渡)
            self.means_updated.emit(
                list(averager.frame_means), list(averager.is_bright)
            )
            return bright, dark, diag
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
        bright_img: np.ndarray,
        dark_img: np.ndarray,
        diag: dict,
        start_ts: datetime,
        end_ts: datetime,
    ) -> dict:
        r = self._req
        cam = r.camera

        pixel_format = cam.get_pixel_format()
        full_scale = full_scale_for(pixel_format)

        # 两张图共用大部分元数据, 单独写入 group / group_frame_count
        def build_meta(group: str, group_img: np.ndarray, group_count: int) -> CaptureMetadata:
            md = CaptureMetadata(app=AppInfo(name=APP_NAME, version=APP_VERSION))
            md.camera = MetaCameraInfo(
                model=r.camera_model, serial=r.camera_serial, sdk_version=""
            )
            md.acquisition.mode = "A"
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
            md.set_image_stats(
                compute_stats(group_img.astype(np.float32), full_scale=float(full_scale))
            )

            md.grouped_average = GroupedAverageInfo(
                group=group,
                group_frame_count=int(group_count),
                total_frames=r.total_frames,
                reject_z=float(diag.get("reject_z", 0.0)),
                n_bright=int(diag.get("n_bright", 0)),
                n_dark=int(diag.get("n_dark", 0)),
                n_transition=int(diag.get("n_transition", 0)),
                transition_frames=list(diag.get("transition_frames", [])),
                bright_mean_avg=float(diag.get("bright_mean_avg", 0.0)),
                dark_mean_avg=float(diag.get("dark_mean_avg", 0.0)),
                frame_means=list(diag.get("frame_means", [])),
                is_bright=list(diag.get("is_bright", [])),
            )
            return md

        md_bright = build_meta("bright", bright_img, int(diag.get("n_bright", 0)))
        md_dark = build_meta("dark", dark_img, int(diag.get("n_dark", 0)))

        name_bright = build_filename(
            prefix=r.prefix, label=r.label, mode="A",
            exposure_us=md_bright.acquisition.exposure_us,
            gain_db=md_bright.acquisition.gain_db,
            n_frames=r.total_frames, timestamp=start_ts, suffix="_bright",
        )
        name_dark = build_filename(
            prefix=r.prefix, label=r.label, mode="A",
            exposure_us=md_dark.acquisition.exposure_us,
            gain_db=md_dark.acquisition.gain_db,
            n_frames=r.total_frames, timestamp=start_ts, suffix="_dark",
        )
        stem_bright = Path(name_bright).stem
        stem_dark = Path(name_dark).stem

        paths_bright = write_capture(
            out_dir=r.out_dir, image=bright_img, metadata=md_bright,
            stem=stem_bright, write_preview=r.write_preview,
        )
        try:
            paths_dark = write_capture(
                out_dir=r.out_dir, image=dark_img, metadata=md_dark,
                stem=stem_dark, write_preview=r.write_preview,
            )
        except Exception:
            # bright 已经落盘, dark 失败就回滚 bright, 保持"成对落盘或都不落"
            for p in paths_bright.values():
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass
            raise

        duration_ms = (end_ts - start_ts).total_seconds() * 1000.0
        return {
            "bright": {
                "paths": {k: str(v) for k, v in paths_bright.items()},
                "metadata": md_bright.to_dict(),
                "stem": stem_bright,
            },
            "dark": {
                "paths": {k: str(v) for k, v in paths_dark.items()},
                "metadata": md_dark.to_dict(),
                "stem": stem_dark,
            },
            "duration_ms": duration_ms,
            "stats": {
                "n_bright": int(diag.get("n_bright", 0)),
                "n_dark": int(diag.get("n_dark", 0)),
                "n_transition": int(diag.get("n_transition", 0)),
                "transition_frames": list(diag.get("transition_frames", [])),
                "bright_mean_avg": float(diag.get("bright_mean_avg", 0.0)),
                "dark_mean_avg": float(diag.get("dark_mean_avg", 0.0)),
            },
        }
