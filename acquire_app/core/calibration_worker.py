"""校准参考帧采集 worker (N 帧平均 → float32 → 保存到 calibration/)。

跟 CaptureWorker 独立, 不走 images/ 路径, 输出固定为 dark_ref / flat_ref。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from acquire_app.camera.base import CameraBase
from acquire_app.core.averager import FrameAverager
from acquire_app.core.calibration import (
    make_ref_meta,
    save_dark,
    save_flat,
)
from acquire_app.logger import logger


@dataclass
class CalibrationRequest:
    kind: str                    # "dark" | "flat"
    camera: CameraBase
    n_frames: int = 16
    grab_timeout_ms: int = 3000
    owns_stream: bool = False


class CalibrationWorker(QObject):
    started = Signal()
    progress = Signal(int, int)
    finished = Signal(str)       # kind
    failed = Signal(str)

    def __init__(self, request: CalibrationRequest, parent: QObject | None = None) -> None:
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
            if r.kind not in ("dark", "flat"):
                raise ValueError(f"未知校准类型: {r.kind!r}")
            if r.n_frames < 1:
                raise ValueError(f"n_frames 必须 >= 1, 实得 {r.n_frames}")
        except Exception as e:
            self.failed.emit(str(e))
            return

        self.started.emit()
        cam = r.camera

        # 共享流时先 flush 陈旧缓冲
        if r.owns_stream:
            try:
                cam.start_stream()
            except Exception as e:
                self.failed.emit(f"start_stream 失败: {e}")
                return
        else:
            try:
                cam.stop_stream()
                cam.start_stream()
            except Exception as e:
                logger.warning(f"flush stream 失败: {e}")

        averager = FrameAverager(r.n_frames)
        prev_id: int | None = None
        try:
            self.progress.emit(0, r.n_frames)
            for i in range(r.n_frames):
                if self._stop_requested:
                    self.failed.emit(f"用户中止 (已采 {i}/{r.n_frames} 帧, 不保存)")
                    return

                try:
                    if cam.get_capture_trigger() == "software":
                        cam.send_trigger_software()
                except Exception:
                    pass

                frame = cam.grab_one(timeout_ms=r.grab_timeout_ms)
                if prev_id is not None and frame.frame_id != prev_id + 1:
                    gap = frame.frame_id - prev_id - 1
                    self.failed.emit(
                        f"校准掉帧: 缺失 {gap} 帧, 已中止 (不保存)"
                    )
                    return
                prev_id = frame.frame_id
                averager.add(frame.image)
                self.progress.emit(i + 1, r.n_frames)

            mean_image = averager.result()   # float32
        except Exception as e:
            logger.exception("校准采集异常")
            self.failed.emit(f"采集异常: {e}")
            return
        finally:
            if r.owns_stream:
                try:
                    cam.stop_stream()
                except Exception:
                    pass

        try:
            meta = make_ref_meta(
                kind=r.kind,
                image=mean_image,
                pixel_format=cam.get_pixel_format(),
                exposure_us=cam.get_exposure_us(),
                gain_db=cam.get_gain_db(),
                n_frames=r.n_frames,
            )
            if r.kind == "dark":
                save_dark(mean_image, meta)
            else:
                save_flat(mean_image, meta)
        except Exception as e:
            logger.exception("校准落盘异常")
            self.failed.emit(f"落盘异常: {e}")
            return

        self.finished.emit(r.kind)
