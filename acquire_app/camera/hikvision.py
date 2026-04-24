"""海康 MVS 相机封装 (MvCameraControl).

- 枚举同时开启 USB3V + GigE, 后续接入 GigE 相机零改动。
- 特性节点对齐 GenICam SFNC, 和 Daheng 大体同名。
- 像素格式只暴露 unpacked Mono 系列 (Mono8/10/12/16), 不暴露 Packed 变体。
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

from acquire_app.camera.base import (
    CameraBase,
    CameraBusyError,
    CameraConnectError,
    CameraInfo,
    Frame,
)
from acquire_app.logger import logger


# MVS SDK Python 绑定 (MvImport 目录) 路径探测:
#   1. 环境变量 LUMOACQ_MVS_PATH
#   2. 常见安装位置
# 目录下应当有 MvCameraControl_class.py

_CANDIDATE_MVS_PATHS = [
    r"C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport",
    r"C:\Program Files\MVS\Development\Samples\Python\MvImport",
    r"D:\Program Files (x86)\MVS\Development\Samples\Python\MvImport",
    r"D:\Program Files\MVS\Development\Samples\Python\MvImport",
]


def _resolve_mvs_path() -> Optional[str]:
    env_path = os.environ.get("LUMOACQ_MVS_PATH")
    if env_path:
        if Path(env_path, "MvCameraControl_class.py").is_file():
            return env_path
        logger.warning(
            f"LUMOACQ_MVS_PATH={env_path} 下未发现 MvCameraControl_class.py, 已忽略"
        )

    for cand in _CANDIDATE_MVS_PATHS:
        if Path(cand, "MvCameraControl_class.py").is_file():
            return cand
    return None


_MVS_PATH = _resolve_mvs_path()
if _MVS_PATH and _MVS_PATH not in sys.path:
    sys.path.insert(0, _MVS_PATH)


# Python 3.8+ 在 Windows 下不再自动搜索 PATH 上的 DLL 目录, 需要显式 add_dll_directory.
# MVS 安装器把运行时 DLL (MvCameraControl.dll) 放在 Common Files\MVS\Runtime 下。
_MVS_RUNTIME_CANDIDATES = [
    r"C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64",
    r"C:\Program Files\Common Files\MVS\Runtime\Win64_x64",
    r"C:\Program Files (x86)\Common Files\MVS\Runtime\Win32_i86",
]
if sys.platform.startswith("win") and hasattr(os, "add_dll_directory"):
    for rt in _MVS_RUNTIME_CANDIDATES:
        if Path(rt, "MvCameraControl.dll").is_file():
            try:
                os.add_dll_directory(rt)
            except Exception as _e:
                logger.warning(f"add_dll_directory({rt}) 失败: {_e}")
            break


_MVS_AVAILABLE = False
_MVS_IMPORT_ERROR: Optional[BaseException] = None

if _MVS_PATH is None:
    _MVS_IMPORT_ERROR = RuntimeError(
        "未检测到海康 MVS SDK 安装路径\n\n"
        "已尝试的位置:\n"
        + "\n".join(f"  • {p}" for p in _CANDIDATE_MVS_PATHS)
        + "\n\n解决方法 (任选其一):\n"
          "  1. 把 MVS 装到上述任一默认位置\n"
          "  2. 设置环境变量 LUMOACQ_MVS_PATH 指向 "
          "Development\\Samples\\Python\\MvImport"
    )
    logger.warning("海康 MVS SDK 未找到, 海康相机不可用 (仍可用 dummy / 其他品牌)")
else:
    try:
        from MvCameraControl_class import MvCamera  # type: ignore  # noqa: E402
        import MvCameraControl_class as _mvsdk  # type: ignore  # noqa: E402

        # MVS SDK 需要显式全局初始化, 否则断开重连后 StartGrabbing 不生效
        # → GetImageBuffer 返回 MV_E_CALLORDER (0x80000003). 见官方样例 BasicDemo.
        try:
            _init_ret = MvCamera.MV_CC_Initialize()
            if _init_ret != 0:
                logger.warning(
                    f"MV_CC_Initialize 返回非零 ret=0x{_init_ret & 0xFFFFFFFF:08x}"
                )
            import atexit
            atexit.register(lambda: MvCamera.MV_CC_Finalize())
        except Exception as _init_err:
            logger.warning(f"MV_CC_Initialize 调用失败 (忽略): {_init_err}")

        _MVS_AVAILABLE = True
        logger.info(f"MVS SDK 路径: {_MVS_PATH}")
    except Exception as _e:
        _MVS_IMPORT_ERROR = _e
        logger.warning(f"MVS SDK 导入失败: {_e}")


def _require_mvs() -> None:
    if not _MVS_AVAILABLE:
        raise RuntimeError(
            f"MVS SDK 不可用 (期望路径: {_MVS_PATH}) — "
            f"原始异常: {_MVS_IMPORT_ERROR!r}"
        )


def _check(ret: int, op: str) -> None:
    if ret != 0:
        raise RuntimeError(f"{op} 失败, ret=0x{ret & 0xFFFFFFFF:08x}")


def _translate_connect_error(exc: BaseException) -> RuntimeError:
    """把 MVS 原始异常翻译成用户友好异常, 保留原始消息在 __cause__。"""
    msg = str(exc)
    lowered = msg.lower()

    # 0x80000006 MV_E_RESOURCE — 被其他程序独占
    # 0x800000FE 常见 U3V 独占冲突
    busy_markers = ("0x80000006", "access", "occupied", "in use", "exclusive")
    if any(m in lowered for m in busy_markers):
        return CameraBusyError(
            "相机已被其他程序占用\n\n"
            "可能的占用方:\n"
            "  • 海康 MVS 客户端\n"
            "  • 先前未正常退出的 LumoAcq 进程\n"
            "  • 其他使用该相机的软件\n\n"
            "请关闭占用程序后重新扫描并连接。"
        )

    # 0x8000001A MV_E_NORESPONSE — 设备无响应
    unreachable_markers = ("0x8000001a", "no response", "timeout", "unreachable")
    if any(m in lowered for m in unreachable_markers):
        return CameraConnectError(
            "无法连接到相机 (设备无响应)\n\n"
            "请检查: 电源 / USB 或网线 / 网卡是否同网段 / 防火墙。"
        )

    return CameraConnectError(f"相机连接失败: {msg}")


# 暴露给上层的 unpacked Mono 格式 -> (PixelType 常量名, numpy dtype, 是否 promote)
# 运行时才能拿到常量值, 所以字符串化延后取。
_UNPACKED_MONO_FORMATS = {
    "Mono8":  ("PixelType_Gvsp_Mono8",  np.uint8,  True),
    "Mono10": ("PixelType_Gvsp_Mono10", np.uint16, False),
    "Mono12": ("PixelType_Gvsp_Mono12", np.uint16, False),
    "Mono16": ("PixelType_Gvsp_Mono16", np.uint16, False),
}


def _decoding_char(barray) -> str:
    """把 SDK 的定长 c_ubyte 数组 (GBK 编码字符) 解成 Python str."""
    encode_type = sys.getfilesystemencoding()
    raw = bytes(b for b in barray if b != 0)
    for enc in ("utf-8", "gbk", encode_type):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode(errors="replace")


class HikvisionCamera(CameraBase):
    """海康 MVS 相机封装 (USB3V + GigE 共用同一实现)."""

    def __init__(self) -> None:
        _require_mvs()
        self._cam: Optional["MvCamera"] = None
        self._handle_open = False
        self._streaming = False
        self._stream_lock = threading.Lock()
        self._current_info: Optional[CameraInfo] = None
        # 扫描结果缓存 (pDeviceInfo 是 SDK 内部内存, 必须保持 list 引用有效)
        self._last_devlist = None

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    @staticmethod
    def scan() -> list[CameraInfo]:
        _require_mvs()
        dev_list = _mvsdk.MV_CC_DEVICE_INFO_LIST()
        tlayer = _mvsdk.MV_USB_DEVICE | _mvsdk.MV_GIGE_DEVICE
        ret = MvCamera.MV_CC_EnumDevices(tlayer, dev_list)
        if ret != 0:
            logger.warning(f"MV_CC_EnumDevices 失败 ret=0x{ret & 0xFFFFFFFF:08x}")
            return []
        results: list[CameraInfo] = []
        for i in range(dev_list.nDeviceNum):
            dev_info = ctypes.cast(
                dev_list.pDeviceInfo[i],
                ctypes.POINTER(_mvsdk.MV_CC_DEVICE_INFO),
            ).contents
            if dev_info.nTLayerType == _mvsdk.MV_GIGE_DEVICE:
                g = dev_info.SpecialInfo.stGigEInfo
                model = _decoding_char(g.chModelName)
                serial = _decoding_char(g.chSerialNumber)
                ip_u32 = g.nCurrentIp
                ip = (
                    f"{(ip_u32 >> 24) & 0xFF}."
                    f"{(ip_u32 >> 16) & 0xFF}."
                    f"{(ip_u32 >> 8) & 0xFF}."
                    f"{ip_u32 & 0xFF}"
                )
                results.append(CameraInfo(
                    index=i, vendor="Hikvision", model=model,
                    serial=serial, ip=ip,
                ))
            elif dev_info.nTLayerType == _mvsdk.MV_USB_DEVICE:
                u = dev_info.SpecialInfo.stUsb3VInfo
                model = _decoding_char(u.chModelName)
                serial = _decoding_char(u.chSerialNumber)
                results.append(CameraInfo(
                    index=i, vendor="Hikvision", model=model,
                    serial=serial, ip="",
                ))
            # CameraLink / GenTL / 线阵暂不暴露
        return results

    def connect(self, index: int = 0) -> None:
        # 必须在这次 connect 上下文内重做枚举, 否则 pDeviceInfo 指针可能失效
        dev_list = _mvsdk.MV_CC_DEVICE_INFO_LIST()
        tlayer = _mvsdk.MV_USB_DEVICE | _mvsdk.MV_GIGE_DEVICE
        ret = MvCamera.MV_CC_EnumDevices(tlayer, dev_list)
        if ret != 0:
            raise CameraConnectError(
                f"枚举设备失败, ret=0x{ret & 0xFFFFFFFF:08x}"
            )
        if index >= dev_list.nDeviceNum:
            raise CameraConnectError(
                f"设备索引越界: index={index}, 可用数={dev_list.nDeviceNum}"
            )
        self._last_devlist = dev_list  # 保活
        dev_info = ctypes.cast(
            dev_list.pDeviceInfo[index],
            ctypes.POINTER(_mvsdk.MV_CC_DEVICE_INFO),
        ).contents

        cam = MvCamera()
        try:
            _check(cam.MV_CC_CreateHandle(dev_info), "MV_CC_CreateHandle")
            _check(
                cam.MV_CC_OpenDevice(_mvsdk.MV_ACCESS_Exclusive, 0),
                "MV_CC_OpenDevice",
            )
        except Exception as e:
            try:
                cam.MV_CC_DestroyHandle()
            except Exception:
                pass
            raise _translate_connect_error(e) from e

        # GigE 推荐设最优包长
        if dev_info.nTLayerType == _mvsdk.MV_GIGE_DEVICE:
            try:
                packet_size = cam.MV_CC_GetOptimalPacketSize()
                if packet_size > 0:
                    cam.MV_CC_SetIntValue("GevSCPSPacketSize", packet_size)
            except Exception as e:
                logger.warning(f"设置最优包长失败 (忽略): {e}")

        self._cam = cam
        self._handle_open = True

        # 加载默认参数组, 清掉遗留触发配置
        try:
            cam.MV_CC_SetEnumValueByString("UserSetSelector", "Default")
            cam.MV_CC_SetCommandValue("UserSetLoad")
        except Exception:
            pass

        # 显式把几个自动模式关掉, 避免厂商默认 UserSet 带进来的惊喜:
        # 部分海康型号 (如 MV-CT120G) 出厂默认开着 DigitalGainEnable,
        # 会让画面看起来比 MVS 客户端默认状态亮很多 (等效额外 19dB 数字增益).
        # 我们只暴露曝光 + 模拟增益作为亮度控制, 数字增益/自动曝光/自动增益统一关掉.
        for key, value in [
            ("TriggerMode", "Off"),
            ("ExposureAuto", "Off"),
            ("GainAuto", "Off"),
        ]:
            try:
                cam.MV_CC_SetEnumValueByString(key, value)
            except Exception as e:
                logger.warning(f"{key}={value} 设置失败 (忽略): {e}")
        for key in ("DigitalGainEnable",):
            try:
                ret = cam.MV_CC_SetBoolValue(key, False)
                if ret != 0:
                    logger.warning(
                        f"{key}=False 失败 ret=0x{ret & 0xFFFFFFFF:08x} (部分型号无此节点, 可忽略)"
                    )
            except Exception as e:
                logger.warning(f"{key}=False 异常 (忽略): {e}")

        logger.info(f"已连接海康相机 index={index}")

    def disconnect(self) -> None:
        if self._cam is None:
            return
        if self._streaming:
            try:
                self.stop_stream()
            except Exception as e:
                logger.warning(f"断开时停流失败: {e}")
            finally:
                self._streaming = False
        try:
            if self._handle_open:
                self._cam.MV_CC_CloseDevice()
        except Exception as e:
            logger.warning(f"CloseDevice 失败 (可能是热拔插): {e}")
        try:
            self._cam.MV_CC_DestroyHandle()
        except Exception as e:
            logger.warning(f"DestroyHandle 失败: {e}")
        self._cam = None
        self._handle_open = False
        self._last_devlist = None
        logger.info("海康相机已断开")

    def start_stream(self) -> None:
        with self._stream_lock:
            if self._streaming or self._cam is None:
                return
            _check(self._cam.MV_CC_StartGrabbing(), "MV_CC_StartGrabbing")
            self._streaming = True
            logger.debug("采集流已开启")

    def stop_stream(self) -> None:
        with self._stream_lock:
            if not self._streaming or self._cam is None:
                return
            try:
                self._cam.MV_CC_StopGrabbing()
            finally:
                self._streaming = False
            logger.debug("采集流已关闭")

    def grab_one(self, timeout_ms: int = 2000) -> Frame:
        assert self._cam is not None, "未连接相机"
        out = _mvsdk.MV_FRAME_OUT()
        ctypes.memset(ctypes.byref(out), 0, ctypes.sizeof(out))
        ret = self._cam.MV_CC_GetImageBuffer(out, int(timeout_ms))
        if ret != 0:
            if (ret & 0xFFFFFFFF) == 0x80000007:  # MV_E_NODATA
                raise TimeoutError(f"grab_one 超时 ({timeout_ms} ms)")
            raise RuntimeError(
                f"MV_CC_GetImageBuffer 失败 ret=0x{ret & 0xFFFFFFFF:08x}"
            )
        try:
            info = out.stFrameInfo
            nW, nH, nLen = int(info.nWidth), int(info.nHeight), int(info.nFrameLen)
            pixel_type = int(info.enPixelType)
            frame_id = int(info.nFrameNum)

            image = self._raw_to_ndarray(out.pBufAddr, nLen, nW, nH, pixel_type)
        finally:
            # 无论成功与否都必须 Free, 否则内部缓冲很快耗尽
            self._cam.MV_CC_FreeImageBuffer(out)

        return Frame(
            image=image,
            frame_id=frame_id,
            timestamp_ns=time.time_ns(),
            pixel_format=self.get_pixel_format(),
            exposure_us=self.get_exposure_us(),
            gain_db=self.get_gain_db(),
        )

    @staticmethod
    def _raw_to_ndarray(
        p_buf, n_len: int, width: int, height: int, pixel_type: int
    ) -> np.ndarray:
        """把 SDK 缓冲拷贝为 uint16 numpy 图。调用方应在返回后立刻 FreeImageBuffer."""
        raw = ctypes.string_at(p_buf, n_len)  # 复制到 Python 端, 随后可安全释放
        if pixel_type == _mvsdk.PixelType_Gvsp_Mono8:
            return np.frombuffer(raw, dtype=np.uint8).reshape(height, width).astype(np.uint16)
        if pixel_type in (
            _mvsdk.PixelType_Gvsp_Mono10,
            _mvsdk.PixelType_Gvsp_Mono12,
            _mvsdk.PixelType_Gvsp_Mono16,
        ):
            return np.frombuffer(raw, dtype=np.uint16).reshape(height, width).copy()
        raise RuntimeError(
            f"暂不支持的像素格式 0x{pixel_type & 0xFFFFFFFF:08x} "
            "(请使用 Mono8/10/12/16 unpacked)"
        )

    # ── 参数 getter/setter ────────────────────────────────────────────────────

    def _get_float(self, key: str) -> float:
        v = _mvsdk.MVCC_FLOATVALUE()
        ctypes.memset(ctypes.byref(v), 0, ctypes.sizeof(v))
        _check(self._cam.MV_CC_GetFloatValue(key, v), f"GetFloatValue({key})")
        return float(v.fCurValue)

    def _set_float(self, key: str, value: float) -> None:
        _check(self._cam.MV_CC_SetFloatValue(key, float(value)), f"SetFloatValue({key})")

    def _get_int(self, key: str) -> int:
        v = _mvsdk.MVCC_INTVALUE()
        ctypes.memset(ctypes.byref(v), 0, ctypes.sizeof(v))
        _check(self._cam.MV_CC_GetIntValueEx(key, v), f"GetIntValueEx({key})")
        return int(v.nCurValue)

    def _set_int(self, key: str, value: int) -> None:
        _check(
            self._cam.MV_CC_SetIntValueEx(key, int(value)),
            f"SetIntValueEx({key})",
        )

    def _get_enum_symbolic(self, key: str) -> str:
        v = _mvsdk.MVCC_ENUMVALUE()
        ctypes.memset(ctypes.byref(v), 0, ctypes.sizeof(v))
        _check(self._cam.MV_CC_GetEnumValue(key, v), f"GetEnumValue({key})")
        cur = int(v.nCurValue)
        entry = _mvsdk.MVCC_ENUMENTRY()
        ctypes.memset(ctypes.byref(entry), 0, ctypes.sizeof(entry))
        entry.nValue = cur
        try:
            if self._cam.MV_CC_GetEnumEntrySymbolic(key, entry) == 0:
                raw = bytes(entry.chSymbolic).split(b"\x00", 1)[0]
                return raw.decode("utf-8", errors="replace")
        except Exception:
            pass
        return f"0x{cur:08x}"

    def _set_enum_symbolic(self, key: str, symbol: str) -> None:
        _check(
            self._cam.MV_CC_SetEnumValueByString(key, symbol),
            f"SetEnumValueByString({key}={symbol})",
        )

    def _list_enum_symbols(self, key: str) -> Optional[list[str]]:
        try:
            info = _mvsdk.MVCC_ENUMVALUE()
            ctypes.memset(ctypes.byref(info), 0, ctypes.sizeof(info))
            ret = self._cam.MV_CC_GetEnumValue(key, info)
            if ret != 0:
                return None
            entry = _mvsdk.MVCC_ENUMENTRY()
            out: list[str] = []
            for i in range(int(info.nSupportedNum)):
                val = int(info.nSupportValue[i])
                ctypes.memset(ctypes.byref(entry), 0, ctypes.sizeof(entry))
                entry.nValue = val
                if self._cam.MV_CC_GetEnumEntrySymbolic(key, entry) == 0:
                    raw = bytes(entry.chSymbolic).split(b"\x00", 1)[0]
                    out.append(raw.decode("utf-8", errors="replace"))
            return out or None
        except Exception:
            return None

    # 基础参数

    def get_exposure_us(self) -> float:
        return self._get_float("ExposureTime")

    def set_exposure_us(self, value: float) -> None:
        self._set_float("ExposureTime", value)

    def get_gain_db(self) -> float:
        return self._get_float("Gain")

    def set_gain_db(self, value: float) -> None:
        self._set_float("Gain", value)

    def get_frame_rate_hz(self) -> float:
        try:
            return self._get_float("AcquisitionFrameRate")
        except Exception:
            return 0.0

    def set_frame_rate_hz(self, value: float) -> None:
        try:
            # 海康一般需要先开启 AcquisitionFrameRateEnable
            self._cam.MV_CC_SetBoolValue("AcquisitionFrameRateEnable", True)
            self._set_float("AcquisitionFrameRate", value)
        except Exception as e:
            logger.warning(f"set_frame_rate_hz 失败: {e}")

    def get_pixel_format(self) -> str:
        return self._get_enum_symbolic("PixelFormat")

    def set_pixel_format(self, fmt: str) -> None:
        was_streaming = self._streaming
        if was_streaming:
            self.stop_stream()
        try:
            self._set_enum_symbolic("PixelFormat", fmt)
        finally:
            if was_streaming:
                self.start_stream()

    def get_binning(self) -> int:
        try:
            return self._get_int("BinningHorizontal")
        except Exception:
            return 1

    def set_binning(self, value: int) -> None:
        was_streaming = self._streaming
        if was_streaming:
            self.stop_stream()
        try:
            self._set_int("BinningHorizontal", value)
            try:
                self._set_int("BinningVertical", value)
            except Exception:
                pass
        finally:
            if was_streaming:
                self.start_stream()

    def get_roi(self) -> tuple[int, int, int, int]:
        return (
            self._get_int("OffsetX"),
            self._get_int("OffsetY"),
            self._get_int("Width"),
            self._get_int("Height"),
        )

    def set_roi(self, offset_x: int, offset_y: int, width: int, height: int) -> None:
        was_streaming = self._streaming
        if was_streaming:
            self.stop_stream()
        try:
            self._set_int("OffsetX", 0)
            self._set_int("OffsetY", 0)
            self._set_int("Width", int(width))
            self._set_int("Height", int(height))
            self._set_int("OffsetX", int(offset_x))
            self._set_int("OffsetY", int(offset_y))
        finally:
            if was_streaming:
                self.start_stream()

    def get_black_level(self) -> Optional[float]:
        try:
            return self._get_float("BlackLevel")
        except Exception:
            try:
                return float(self._get_int("BlackLevel"))
            except Exception:
                return None

    def set_black_level(self, value: float) -> None:
        try:
            self._cam.MV_CC_SetBoolValue("BlackLevelEnable", True)
        except Exception:
            pass
        try:
            self._set_float("BlackLevel", value)
        except Exception:
            try:
                self._set_int("BlackLevel", int(round(value)))
            except Exception as e:
                logger.warning(f"set_black_level 失败: {e}")

    def get_trigger_mode(self) -> str:
        sym = self._get_enum_symbolic("TriggerMode")
        # 海康返回 "Off" / "On"
        return sym

    def set_trigger_mode(self, mode: str) -> None:
        self._set_enum_symbolic("TriggerMode", mode)

    def get_trigger_source(self) -> str:
        try:
            return self._get_enum_symbolic("TriggerSource")
        except Exception:
            return ""

    def set_trigger_source(self, source: str) -> None:
        self._set_enum_symbolic("TriggerSource", source)

    def send_trigger_software(self) -> None:
        _check(
            self._cam.MV_CC_SetCommandValue("TriggerSoftware"),
            "TriggerSoftware",
        )

    def get_temperature(self) -> Optional[float]:
        # 海康常见节点名: DeviceTemperature (部分老固件写 DeviceTemp)
        for key in ("DeviceTemperature", "DeviceTemp"):
            try:
                return self._get_float(key)
            except Exception:
                continue
        return None

    # ── 能力查询 ──

    def list_pixel_formats(self) -> Optional[list[str]]:
        all_syms = self._list_enum_symbols("PixelFormat")
        if not all_syms:
            return None
        # 只暴露我们支持的 unpacked Mono 列表
        return [s for s in all_syms if s in _UNPACKED_MONO_FORMATS] or None

    def get_exposure_range(self) -> Optional[tuple[float, float]]:
        try:
            v = _mvsdk.MVCC_FLOATVALUE()
            ctypes.memset(ctypes.byref(v), 0, ctypes.sizeof(v))
            if self._cam.MV_CC_GetFloatValue("ExposureTime", v) == 0:
                return float(v.fMin), float(v.fMax)
        except Exception:
            pass
        return None

    def get_gain_range(self) -> Optional[tuple[float, float]]:
        try:
            v = _mvsdk.MVCC_FLOATVALUE()
            ctypes.memset(ctypes.byref(v), 0, ctypes.sizeof(v))
            if self._cam.MV_CC_GetFloatValue("Gain", v) == 0:
                return float(v.fMin), float(v.fMax)
        except Exception:
            pass
        return None

    def get_current_frame_rate_hz(self) -> Optional[float]:
        # 海康通常暴露 ResultingFrameRate
        for key in ("ResultingFrameRate", "CurrentAcquisitionFrameRate"):
            try:
                return self._get_float(key)
            except Exception:
                continue
        return None

    # ── 传感器模式 (海康 SensorMode 节点) ──

    def list_sensor_modes(self) -> Optional[list[str]]:
        return self._list_enum_symbols("SensorMode")

    def get_sensor_mode(self) -> Optional[str]:
        try:
            return self._get_enum_symbolic("SensorMode")
        except Exception:
            return None

    def set_sensor_mode(self, mode: str) -> None:
        was_streaming = self._streaming
        if was_streaming:
            self.stop_stream()
        try:
            self._set_enum_symbolic("SensorMode", mode)
        finally:
            if was_streaming:
                self.start_stream()
