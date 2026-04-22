from __future__ import annotations

import time
from typing import Optional

import numpy as np

from acquire_app.camera.base import CameraBase, CameraInfo, Frame

_SENSOR_H = 2048
_SENSOR_W = 2560

_BIT_DEPTH = {
    "Mono8": 8, "Mono10": 10, "Mono12": 12, "Mono16": 16,
}


class DummyCamera(CameraBase):
    """无硬件可用时的虚拟相机，产生逼真的合成帧供 UI 调试."""

    def __init__(self) -> None:
        self._streaming = False
        self._frame_id = 0
        self._rng = np.random.default_rng(42)

        self._exposure_us = 20000.0
        self._gain_db = 0.0
        self._frame_rate_hz = 15.0
        self._pixel_format = "Mono12"
        self._binning = 1
        self._roi = (0, 0, _SENSOR_W, _SENSOR_H)
        self._black_level = 20.0
        self._trigger_mode = "Off"

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    @staticmethod
    def scan() -> list[CameraInfo]:
        return [CameraInfo(
            index=0,
            vendor="Daheng (Dummy)",
            model="MER2-2560-2048M-IMX992-Dummy",
            serial="DUMMY-0001",
            ip="192.168.1.100",
        )]

    def connect(self, index: int = 0) -> None:
        pass

    def disconnect(self) -> None:
        if self._streaming:
            self.stop_stream()

    def start_stream(self) -> None:
        self._streaming = True

    def stop_stream(self) -> None:
        self._streaming = False

    def grab_one(self, timeout_ms: int = 2000) -> Frame:
        if not self._streaming:
            raise RuntimeError("流未开启，请先调用 start_stream()")

        # 模拟帧率延时
        time.sleep(1.0 / self._frame_rate_hz)

        _, _, w, h = self._roi
        w //= self._binning
        h //= self._binning

        bit = _BIT_DEPTH.get(self._pixel_format, 12)
        max_val = (1 << bit) - 1

        # 用曝光/增益简单映射亮度，模拟真实相机响应
        exposure_norm = np.clip(self._exposure_us / 20000.0, 0.01, 8.0)
        gain_linear = 10 ** (self._gain_db / 20.0)
        mean_adu = np.clip(self._black_level + 1000 * exposure_norm * gain_linear, 0, max_val)
        noise_std = np.clip(mean_adu * 0.1, 5, 200)

        # 合成图像：高斯噪声 + 简单梯度（模拟场景）
        grad_x = np.linspace(0, max_val * 0.3, w, dtype=np.float32)
        grad_y = np.linspace(0, max_val * 0.3, h, dtype=np.float32)
        scene = mean_adu + grad_x[None, :] * 0.5 + grad_y[:, None] * 0.5
        noise = self._rng.normal(0, noise_std, (h, w)).astype(np.float32)
        image = np.clip(scene + noise, 0, max_val).astype(np.uint16)

        self._frame_id += 1
        return Frame(
            image=image,
            frame_id=self._frame_id,
            timestamp_ns=time.time_ns(),
            pixel_format=self._pixel_format,
            exposure_us=self._exposure_us,
            gain_db=self._gain_db,
        )

    # ── 参数 getter/setter ────────────────────────────────────────────────────

    def get_exposure_us(self) -> float:
        return self._exposure_us

    def set_exposure_us(self, value: float) -> None:
        self._exposure_us = float(value)

    def get_gain_db(self) -> float:
        return self._gain_db

    def set_gain_db(self, value: float) -> None:
        self._gain_db = float(value)

    def get_frame_rate_hz(self) -> float:
        return self._frame_rate_hz

    def set_frame_rate_hz(self, value: float) -> None:
        self._frame_rate_hz = float(value)

    def get_pixel_format(self) -> str:
        return self._pixel_format

    def set_pixel_format(self, fmt: str) -> None:
        if fmt not in _BIT_DEPTH:
            raise ValueError(f"不支持的像素格式: {fmt}")
        self._pixel_format = fmt

    def get_binning(self) -> int:
        return self._binning

    def set_binning(self, value: int) -> None:
        if value not in (1, 2, 4):
            raise ValueError(f"Binning 只支持 1/2/4，收到: {value}")
        self._binning = value

    def get_roi(self) -> tuple[int, int, int, int]:
        return self._roi

    def set_roi(self, offset_x: int, offset_y: int, width: int, height: int) -> None:
        self._roi = (offset_x, offset_y, width, height)

    def get_black_level(self) -> Optional[float]:
        return self._black_level

    def set_black_level(self, value: float) -> None:
        self._black_level = float(value)

    def get_trigger_mode(self) -> str:
        return self._trigger_mode

    def set_trigger_mode(self, mode: str) -> None:
        if mode not in ("Off", "On"):
            raise ValueError(f"触发模式只支持 Off/On，收到: {mode}")
        self._trigger_mode = mode

    def get_trigger_source(self) -> str:
        return getattr(self, "_trigger_source", "Software")

    def set_trigger_source(self, source: str) -> None:
        self._trigger_source = source

    def send_trigger_software(self) -> None:
        # Dummy 无事可做, 直接放行 (下次 grab_one 照常返回合成帧)
        pass

    def get_temperature(self) -> Optional[float]:
        # 模拟随时间缓慢升温
        return round(25.0 + self._frame_id * 0.001, 1)

    def list_pixel_formats(self) -> list[str]:
        return list(_BIT_DEPTH.keys())

    def get_exposure_range(self) -> tuple[float, float]:
        return (1.0, 1_000_000.0)

    def get_gain_range(self) -> tuple[float, float]:
        return (0.0, 24.0)

    def get_current_frame_rate_hz(self) -> float:
        # 模拟: 不会超过曝光时间倒数
        max_by_exposure = 1_000_000.0 / max(1.0, self._exposure_us)
        return float(min(self._frame_rate_hz, max_by_exposure))
