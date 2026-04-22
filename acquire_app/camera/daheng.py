from __future__ import annotations

import os
import sys
import threading
import time
import types
from pathlib import Path
from typing import Optional

import numpy as np

from acquire_app.camera.base import CameraBase, CameraInfo, Frame
from acquire_app.logger import logger


# gxipy 路径探测策略 (按优先级):
#   1. 环境变量 LUMOACQ_GXIPY_PATH — 用户显式指定
#   2. 几个常见安装位置 — 自动探测
# 每个候选路径下应当有 gxipy/ 目录 (含 __init__.py)

_CANDIDATE_GXIPY_PATHS = [
    r"C:\Program Files\Daheng Imaging\GalaxySDK\Development\Samples\Python",
    r"C:\Program Files (x86)\Daheng Imaging\GalaxySDK\Development\Samples\Python",
    r"D:\Program Files\Daheng Imaging\GalaxySDK\Development\Samples\Python",
    r"D:\Daheng Imaging\GalaxySDK\Development\Samples\Python",
]


def _resolve_gxipy_path() -> Optional[str]:
    env_path = os.environ.get("LUMOACQ_GXIPY_PATH")
    if env_path:
        if Path(env_path, "gxipy").is_dir():
            return env_path
        logger.warning(f"LUMOACQ_GXIPY_PATH={env_path} 下未发现 gxipy 目录, 已忽略")

    for cand in _CANDIDATE_GXIPY_PATHS:
        if Path(cand, "gxipy").is_dir():
            return cand
    return None


_GXIPY_PATH = _resolve_gxipy_path()
if _GXIPY_PATH and _GXIPY_PATH not in sys.path:
    sys.path.insert(0, _GXIPY_PATH)

# numpy 2.0 起移除了 numpy.compat, 而 gxipy DeviceManager.py 仍 import long.
# 注入最小垫片, 避免触碰供应商源码也不降级 numpy.
if "numpy.compat" not in sys.modules:
    _compat = types.ModuleType("numpy.compat")
    _compat.long = int
    sys.modules["numpy.compat"] = _compat
    np.compat = _compat  # type: ignore[attr-defined]

if _GXIPY_PATH is None:
    _GX_AVAILABLE = False
    _GX_IMPORT_ERROR: Optional[BaseException] = RuntimeError(
        "未检测到 Daheng Galaxy SDK 安装路径\n\n"
        "已尝试的位置:\n"
        + "\n".join(f"  • {p}" for p in _CANDIDATE_GXIPY_PATHS)
        + "\n\n解决方法 (任选其一):\n"
          "  1. 把 Galaxy SDK 装到上述任一默认位置\n"
          "  2. 设置环境变量 LUMOACQ_GXIPY_PATH 指向安装目录下的 "
          "Development\\Samples\\Python"
    )
    logger.warning("Daheng Galaxy SDK 未找到, 真机相机不可用 (仍可用 dummy)")
else:
    try:
        import gxipy as gx
        _GX_AVAILABLE = True
        _GX_IMPORT_ERROR = None
        logger.info(f"gxipy 路径: {_GXIPY_PATH}")
    except Exception as _e:
        _GX_AVAILABLE = False
        _GX_IMPORT_ERROR = _e
        logger.warning(f"gxipy 导入失败: {_e}")


class CameraBusyError(RuntimeError):
    """相机已被其他进程占用。"""


class CameraConnectError(RuntimeError):
    """相机连接失败 (网络/设备不可达等)。"""


def _translate_connect_error(exc: BaseException) -> RuntimeError:
    """把大恒 SDK 原始异常翻译成对用户友好的异常。原始消息保留在 __cause__。"""
    msg = str(exc)
    lowered = msg.lower()

    busy_markers = ("access denied", "-1005", "ccp register", "device is denied")
    if any(m in lowered for m in busy_markers):
        return CameraBusyError(
            "相机已被其他程序占用\n\n"
            "可能的占用方:\n"
            "  • 大恒 Galaxy Viewer / MVS 客户端\n"
            "  • 先前未正常退出的 LumoAcq 进程\n"
            "  • 其他使用 GigE Vision 的软件\n\n"
            "请关闭占用程序后重新扫描并连接。"
        )

    unreachable_markers = ("timeout", "-1003", "unreachable", "no device")
    if any(m in lowered for m in unreachable_markers):
        return CameraConnectError(
            "无法连接到相机 (网络不可达或设备未响应)\n\n"
            "请检查: 电源 / 网线 / 网卡与相机是否同网段 / 防火墙。"
        )

    return CameraConnectError(f"相机连接失败: {msg}")


def _require_gx() -> None:
    if not _GX_AVAILABLE:
        raise RuntimeError(
            f"gxipy 不可用 (期望路径: {_GXIPY_PATH}) — 原始异常: {_GX_IMPORT_ERROR!r}"
        )


class DahengCamera(CameraBase):
    """大恒 GigE SWIR 相机封装（gxipy）."""

    def __init__(self) -> None:
        _require_gx()
        self._dev_manager: gx.DeviceManager = gx.DeviceManager()
        self._cam = None
        self._feat = None          # remote_device_feature_control
        self._streaming = False
        self._stream_lock = threading.Lock()  # 保护 start/stop_stream 跨线程竞争
        self._current_info: Optional[CameraInfo] = None

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    @staticmethod
    def scan() -> list[CameraInfo]:
        _require_gx()
        dm = gx.DeviceManager()
        dev_num, dev_list = dm.update_all_device_list()
        results = []
        for i, d in enumerate(dev_list or []):
            results.append(CameraInfo(
                index=i,
                vendor=d.get("vendor_name", "Daheng"),
                model=d.get("model_name", "Unknown"),
                serial=d.get("sn", d.get("serial_number", "")),
                ip=d.get("ip", d.get("device_ip", "")),
            ))
        return results

    def connect(self, index: int = 0) -> None:
        try:
            self._cam = self._dev_manager.open_device_by_index(index + 1)  # gxipy 从 1 开始
        except Exception as e:
            raise _translate_connect_error(e) from e
        self._feat = self._cam.get_remote_device_feature_control()
        # 恢复默认参数组
        try:
            self._feat.get_enum_feature("UserSetSelector").set("Default")
            self._feat.get_command_feature("UserSetLoad").send_command()
        except Exception:
            pass
        # 保证自由运行, 防止遗留触发设置导致预览拿不到帧
        try:
            self._feat.get_enum_feature("TriggerMode").set("Off")
        except Exception as e:
            logger.warning(f"TriggerMode=Off 失败: {e}")
        logger.info(f"已连接相机 index={index}")

    def disconnect(self) -> None:
        if self._streaming:
            try:
                self.stop_stream()
            except Exception as e:
                logger.warning(f"断开时停流失败: {e}")
            finally:
                self._streaming = False
        if self._cam is not None:
            try:
                self._cam.close_device()
            except Exception as e:
                logger.warning(f"close_device 失败 (可能是热拔插): {e}")
            self._cam = None
            self._feat = None
        logger.info("相机已断开")

    def start_stream(self) -> None:
        with self._stream_lock:
            if self._streaming or self._cam is None:
                return
            self._cam.stream_on()
            self._streaming = True
            logger.debug("采集流已开启")

    def stop_stream(self) -> None:
        with self._stream_lock:
            if not self._streaming or self._cam is None:
                return
            try:
                self._cam.stream_off()
            finally:
                self._streaming = False
            logger.debug("采集流已关闭")

    def grab_one(self, timeout_ms: int = 2000) -> Frame:
        raw = self._cam.data_stream[0].get_image(timeout_ms)
        if raw is None:
            raise TimeoutError(f"grab_one 超时 ({timeout_ms} ms)")

        arr = raw.get_numpy_array()
        if arr is None:
            raise RuntimeError("get_numpy_array() 返回 None")

        # gxipy 返回的数组可能是 uint8 packed，确保 uint16
        image = arr.astype(np.uint16)

        return Frame(
            image=image,
            frame_id=int(raw.get_frame_id()),
            timestamp_ns=time.time_ns(),
            pixel_format=self.get_pixel_format(),
            exposure_us=self.get_exposure_us(),
            gain_db=self.get_gain_db(),
        )

    # ── 参数 getter/setter ────────────────────────────────────────────────────

    def get_exposure_us(self) -> float:
        return float(self._feat.get_float_feature("ExposureTime").get())

    def set_exposure_us(self, value: float) -> None:
        self._feat.get_float_feature("ExposureTime").set(float(value))

    def get_gain_db(self) -> float:
        return float(self._feat.get_float_feature("Gain").get())

    def set_gain_db(self, value: float) -> None:
        self._feat.get_float_feature("Gain").set(float(value))

    def get_frame_rate_hz(self) -> float:
        try:
            return float(self._feat.get_float_feature("AcquisitionFrameRate").get())
        except Exception:
            return 0.0

    def set_frame_rate_hz(self, value: float) -> None:
        try:
            self._feat.get_enum_feature("AcquisitionFrameRateMode").set("On")
            self._feat.get_float_feature("AcquisitionFrameRate").set(float(value))
        except Exception as e:
            logger.warning(f"set_frame_rate_hz 失败: {e}")

    def get_pixel_format(self) -> str:
        _, fmt_str = self._feat.get_enum_feature("PixelFormat").get()
        return fmt_str

    def set_pixel_format(self, fmt: str) -> None:
        was_streaming = self._streaming
        if was_streaming:
            self.stop_stream()
        try:
            self._feat.get_enum_feature("PixelFormat").set(fmt)
        finally:
            if was_streaming:
                self.start_stream()

    def get_binning(self) -> int:
        try:
            return int(self._feat.get_int_feature("BinningHorizontal").get())
        except Exception:
            return 1

    def set_binning(self, value: int) -> None:
        was_streaming = self._streaming
        if was_streaming:
            self.stop_stream()
        try:
            self._feat.get_int_feature("BinningHorizontal").set(int(value))
            self._feat.get_int_feature("BinningVertical").set(int(value))
        finally:
            if was_streaming:
                self.start_stream()

    def get_roi(self) -> tuple[int, int, int, int]:
        f = self._feat
        return (
            int(f.get_int_feature("OffsetX").get()),
            int(f.get_int_feature("OffsetY").get()),
            int(f.get_int_feature("Width").get()),
            int(f.get_int_feature("Height").get()),
        )

    def set_roi(self, offset_x: int, offset_y: int, width: int, height: int) -> None:
        was_streaming = self._streaming
        if was_streaming:
            self.stop_stream()
        try:
            f = self._feat
            # 先设偏移为 0 再改尺寸，避免越界
            f.get_int_feature("OffsetX").set(0)
            f.get_int_feature("OffsetY").set(0)
            f.get_int_feature("Width").set(int(width))
            f.get_int_feature("Height").set(int(height))
            f.get_int_feature("OffsetX").set(int(offset_x))
            f.get_int_feature("OffsetY").set(int(offset_y))
        finally:
            if was_streaming:
                self.start_stream()

    def get_black_level(self) -> Optional[float]:
        try:
            return float(self._feat.get_float_feature("BlackLevel").get())
        except Exception:
            return None

    def set_black_level(self, value: float) -> None:
        try:
            self._feat.get_float_feature("BlackLevel").set(float(value))
        except Exception as e:
            logger.warning(f"set_black_level 失败: {e}")

    def get_trigger_mode(self) -> str:
        _, mode = self._feat.get_enum_feature("TriggerMode").get()
        return mode

    def set_trigger_mode(self, mode: str) -> None:
        self._feat.get_enum_feature("TriggerMode").set(mode)

    def get_trigger_source(self) -> str:
        try:
            _, src = self._feat.get_enum_feature("TriggerSource").get()
            return src
        except Exception:
            return ""

    def set_trigger_source(self, source: str) -> None:
        self._feat.get_enum_feature("TriggerSource").set(source)

    def send_trigger_software(self) -> None:
        self._feat.get_command_feature("TriggerSoftware").send_command()

    def get_temperature(self) -> Optional[float]:
        try:
            return float(self._feat.get_float_feature("DeviceTemperature").get())
        except Exception:
            return None

    # ── 能力查询 ──

    def list_pixel_formats(self) -> Optional[list[str]]:
        try:
            entries = self._feat.get_enum_feature("PixelFormat").get_range()
        except Exception as e:
            logger.warning(f"读取 PixelFormat 范围失败: {e}")
            return None
        out: list[str] = []
        for item in entries or []:
            sym = item.get("symbolic") if isinstance(item, dict) else None
            if sym:
                out.append(sym)
        return out or None

    def get_exposure_range(self) -> Optional[tuple[float, float]]:
        try:
            info = self._feat.get_float_feature("ExposureTime").get_range()
            return float(info["min"]), float(info["max"])
        except Exception:
            return None

    def get_gain_range(self) -> Optional[tuple[float, float]]:
        try:
            info = self._feat.get_float_feature("Gain").get_range()
            return float(info["min"]), float(info["max"])
        except Exception:
            return None

    def get_current_frame_rate_hz(self) -> Optional[float]:
        for name in ("CurrentAcquisitionFrameRate", "ResultingFrameRate"):
            try:
                return float(self._feat.get_float_feature(name).get())
            except Exception:
                continue
        return None
