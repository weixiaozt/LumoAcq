"""连续预览 worker: 后台线程循环 grab, 按 PREVIEW_FPS 限速发帧到 UI。

与 CaptureWorker 协调:
- CaptureWorker 在采集前应调用 preview.pause(), 结束后 resume()
- pause 后 worker 不 grab 也不碰相机, 直到 resume
"""
from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from acquire_app.camera.base import CameraBase
from acquire_app.config import PREVIEW_FPS
from acquire_app.logger import logger


class PreviewWorker(QObject):
    frame_ready = Signal(np.ndarray, int)  # (image, frame_id)
    capture_fps_measured = Signal(float)   # 基于 frame_id + 时间戳实测的相机产帧率
    stopped = Signal()
    error = Signal(str)

    def __init__(self, camera: CameraBase, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cam = camera
        self._running = False
        self._paused = False
        self._interval = 1.0 / max(1.0, float(PREVIEW_FPS))
        # 实测采集帧率: 用滑动窗口记录最近 N 帧的 (frame_id, ts)
        self._fps_history: list[tuple[int, float]] = []
        self._fps_window = 30

    @Slot()
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._paused = False
        try:
            self._cam.start_stream()
        except Exception as e:
            logger.exception("预览 start_stream 失败")
            self.error.emit(f"预览启动失败: {e}")
            self._running = False
            return

        # gxipy 的 stream_on 返回后 acquisition buffer 尚未就绪, 给 SDK 一点时间
        time.sleep(0.1)

        next_deadline = time.monotonic()
        while self._running:
            if self._paused:
                time.sleep(0.02)
                continue

            now = time.monotonic()
            if now < next_deadline:
                time.sleep(max(0.0, next_deadline - now))

            # 触发模式下预览不自动发触发 (那会让预览跟自由运行没区别)
            # - free: 相机连续出帧, 正常 grab
            # - software: 不发软触发; grab 会超时 (直到有 CaptureWorker 发触发)
            # - hardware: grab 等外部触发来; 没信号就持续超时
            try:
                frame = self._cam.grab_one(timeout_ms=300)
            except TimeoutError:
                continue
            except Exception as e:
                if not self._running:
                    break
                # 切 PixelFormat / Binning / 主动 stop_stream 会让 SDK 短暂抛这类错
                msg = str(e)
                transient_markers = (
                    "NewBufferEvent", "don't", "stream", "acquisition",
                )
                if any(m in msg for m in transient_markers) or \
                   any(m in msg.lower() for m in ("don't", "stream", "acquisition")):
                    time.sleep(0.05)
                    continue
                logger.warning(f"预览 grab 异常: {e}")
                self.error.emit(str(e))
                break

            next_deadline = time.monotonic() + self._interval
            try:
                self.frame_ready.emit(frame.image, frame.frame_id)
            except Exception as e:
                logger.warning(f"预览 emit 失败: {e}")

            # 实测相机帧率 (用 frame_id 差 / 时间差, 不依赖 SDK 回读值)
            now_ts = time.monotonic()
            self._fps_history.append((int(frame.frame_id), now_ts))
            if len(self._fps_history) > self._fps_window:
                self._fps_history.pop(0)
            if len(self._fps_history) >= 2:
                (id0, t0), (id1, t1) = self._fps_history[0], self._fps_history[-1]
                if t1 > t0 and id1 > id0:
                    measured = (id1 - id0) / (t1 - t0)
                    self.capture_fps_measured.emit(float(measured))

        try:
            self._cam.stop_stream()
        except Exception:
            pass
        self._running = False
        self.stopped.emit()

    @Slot()
    def stop(self) -> None:
        self._running = False
        # 主动打断 grab_one 的阻塞: 停流, 让下一次 grab 立刻抛异常/超时
        try:
            self._cam.stop_stream()
        except Exception:
            pass

    @Slot()
    def pause(self) -> None:
        self._paused = True

    @Slot()
    def resume(self) -> None:
        self._paused = False
