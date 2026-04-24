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


# 预览图降采样阈值. 像素总数 > 阈值 → worker 线程整数抽样缩小后再 emit,
# 避免 12MP+ 大传感器把 24MB/帧 × 30Hz 的数据塞进 Qt 信号队列压垮 UI.
# 采集/保存路径不走预览, 仍用相机原始分辨率, 不受影响.
#   • Daheng SWIR 5.3 MP (2592×2056) → 不触发 (显示原分辨率)
#   • Hikvision MV-CT120G 12.3 MP (4096×3000) → 触发, 抽样到约 2048×1500
_PREVIEW_MAX_PIXELS = 6_000_000


def _downsample_for_preview(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    if h * w <= _PREVIEW_MAX_PIXELS:
        return img
    import math
    step = max(2, math.ceil(math.sqrt(h * w / _PREVIEW_MAX_PIXELS)))
    # .copy() 让原始大图可以被 GC, 否则 view 持有整块内存
    return img[::step, ::step].copy()


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
        # 连续错误计数 + 自动恢复: USB/网络抖动 → CALLORDER 等非致命错, 重启 stream 续跑
        consecutive_errors = 0
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
            # 超时放宽到 1000ms (官方 sample 用值). 300ms 曝光 + 300ms 超时会在
            # 帧到达边界上 race, MVS SDK 偶发返回 CALLORDER 而不是 NODATA.
            try:
                frame = self._cam.grab_one(timeout_ms=1000)
                consecutive_errors = 0
            except TimeoutError:
                continue
            except Exception as e:
                if not self._running:
                    break
                consecutive_errors += 1
                msg = str(e)
                logger.warning(f"预览 grab 异常 (#{consecutive_errors}): {e}")

                # 策略: 前 10 次非致命错只 sleep 重试; 第 3 次起尝试重启 stream 恢复;
                # 超过 30 次仍失败才放弃.
                if consecutive_errors >= 3 and consecutive_errors % 3 == 0:
                    try:
                        logger.info("预览尝试重启 stream 恢复")
                        self._cam.stop_stream()
                        time.sleep(0.1)
                        self._cam.start_stream()
                        time.sleep(0.1)
                    except Exception as re:
                        logger.warning(f"重启 stream 失败: {re}")

                if consecutive_errors >= 30:
                    self.error.emit(f"预览连续 {consecutive_errors} 次失败: {msg}")
                    break
                time.sleep(0.05)
                continue

            next_deadline = time.monotonic() + self._interval
            img = _downsample_for_preview(frame.image)
            try:
                self.frame_ready.emit(img, frame.frame_id)
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
