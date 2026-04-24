from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class CameraInfo:
    index: int
    vendor: str
    model: str
    serial: str
    ip: str = ""

    def __str__(self) -> str:
        return f"{self.model} | SN:{self.serial} | {self.ip}"


@dataclass
class Frame:
    image: np.ndarray        # uint16, shape (H, W)
    frame_id: int
    timestamp_ns: int = 0
    pixel_format: str = "Mono12"
    exposure_us: float = 0.0
    gain_db: float = 0.0

    @property
    def height(self) -> int:
        return self.image.shape[0]

    @property
    def width(self) -> int:
        return self.image.shape[1]


class CameraBusyError(RuntimeError):
    """相机已被其他进程占用。"""


class CameraConnectError(RuntimeError):
    """相机连接失败 (网络/设备不可达等)。"""


class CameraBase(ABC):
    """所有相机实现的抽象基类."""

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    @staticmethod
    @abstractmethod
    def scan() -> list[CameraInfo]:
        """枚举所有可用设备."""

    @abstractmethod
    def connect(index: int) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def start_stream(self) -> None: ...

    @abstractmethod
    def stop_stream(self) -> None: ...

    @abstractmethod
    def grab_one(self, timeout_ms: int = 2000) -> Frame: ...

    # ── 参数 getter/setter ────────────────────────────────────────────────────

    @abstractmethod
    def get_exposure_us(self) -> float: ...

    @abstractmethod
    def set_exposure_us(self, value: float) -> None: ...

    @abstractmethod
    def get_gain_db(self) -> float: ...

    @abstractmethod
    def set_gain_db(self, value: float) -> None: ...

    @abstractmethod
    def get_frame_rate_hz(self) -> float: ...

    @abstractmethod
    def set_frame_rate_hz(self, value: float) -> None: ...

    @abstractmethod
    def get_pixel_format(self) -> str: ...

    @abstractmethod
    def set_pixel_format(self, fmt: str) -> None:
        """需要调用方先 stop_stream / 再 start_stream."""

    @abstractmethod
    def get_binning(self) -> int: ...

    @abstractmethod
    def set_binning(self, value: int) -> None:
        """需要调用方先 stop_stream / 再 start_stream."""

    @abstractmethod
    def get_roi(self) -> tuple[int, int, int, int]:
        """返回 (offset_x, offset_y, width, height)."""

    @abstractmethod
    def set_roi(self, offset_x: int, offset_y: int, width: int, height: int) -> None:
        """需要调用方先 stop_stream / 再 start_stream."""

    @abstractmethod
    def get_black_level(self) -> Optional[float]: ...

    @abstractmethod
    def set_black_level(self, value: float) -> None: ...

    @abstractmethod
    def get_trigger_mode(self) -> str:
        """返回 'Off' 或 'On'."""

    @abstractmethod
    def set_trigger_mode(self, mode: str) -> None: ...

    def get_trigger_source(self) -> str:
        """'Software' / 'Line0' / 其他; 默认空串 = 未知。"""
        return ""

    def set_trigger_source(self, source: str) -> None:
        raise NotImplementedError("该相机未实现 set_trigger_source")

    def send_trigger_software(self) -> None:
        raise NotImplementedError("该相机未实现 send_trigger_software")

    # ── 高层触发封装 ──

    def get_capture_trigger(self) -> str:
        """'free' | 'software' | 'hardware'。"""
        try:
            mode = self.get_trigger_mode()
        except Exception:
            return "free"
        if str(mode).lower() == "off":
            return "free"
        try:
            src = (self.get_trigger_source() or "").lower()
        except Exception:
            src = ""
        if src == "software":
            return "software"
        return "hardware"

    def set_capture_trigger(self, mode: str) -> None:
        mode = mode.lower()
        if mode == "free":
            self.set_trigger_mode("Off")
            return
        if mode == "software":
            try:
                self.set_trigger_source("Software")
            except NotImplementedError:
                raise RuntimeError("当前相机不支持软触发")
            self.set_trigger_mode("On")
            return
        if mode == "hardware":
            try:
                self.set_trigger_source("Line0")
            except NotImplementedError:
                raise RuntimeError("当前相机不支持硬触发")
            self.set_trigger_mode("On")
            return
        raise ValueError(f"未知触发模式: {mode!r}")

    @abstractmethod
    def get_temperature(self) -> Optional[float]:
        """返回摄氏度，不支持时返回 None."""

    # ── 能力查询 (有默认实现, 子类可选择 override) ────────────────────────────

    def list_pixel_formats(self) -> Optional[list[str]]:
        """返回相机实际支持的 PixelFormat 列表。None 表示未知。"""
        return None

    def get_exposure_range(self) -> Optional[tuple[float, float]]:
        return None

    def get_gain_range(self) -> Optional[tuple[float, float]]:
        return None

    def get_current_frame_rate_hz(self) -> Optional[float]:
        """当前实际采集速率 (受曝光/读出/带宽限制)。不支持返回 None。"""
        return None

    # ── 传感器模式 (海康等相机的 SensorMode 节点, 不支持时返回 None) ──

    def list_sensor_modes(self) -> Optional[list[str]]:
        """返回支持的传感器模式 (如 '高灵敏度' / '高速度'). None = 不支持。"""
        return None

    def get_sensor_mode(self) -> Optional[str]:
        """当前传感器模式. None = 不支持或未知。"""
        return None

    def set_sensor_mode(self, mode: str) -> None:
        raise NotImplementedError("该相机未实现 set_sensor_mode")

    # ── 便利方法 ──────────────────────────────────────────────────────────────

    def restart_stream(self) -> None:
        """停流 → 调用方设参数 → 重新开流的辅助封装 (供子类内部使用)."""
        self.stop_stream()
        time.sleep(0.05)
        self.start_stream()
