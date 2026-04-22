"""M1 真机冒烟测试：scan / connect / 参数读写 / grab × 3 / 断开。

运行:
    D:/LumoAcq/.venv/Scripts/python.exe -m examples.smoke_daheng
"""
from __future__ import annotations

import sys
import traceback

import numpy as np

from acquire_app.camera.daheng import DahengCamera


def _section(title: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def _kv(key: str, value) -> None:
    print(f"  {key:<20} = {value}")


def main() -> int:
    _section("1. 枚举设备")
    infos = DahengCamera.scan()
    if not infos:
        print("  [失败] 未发现任何大恒设备")
        print("  检查: 相机通电 / 网线 / IP 在同网段 / 防火墙 / Galaxy Viewer 是否能看到")
        return 2

    for info in infos:
        print(f"  [{info.index}] {info}")

    _section("2. 连接第一台设备 (index=0)")
    cam = DahengCamera()
    try:
        cam.connect(index=0)
    except Exception as e:
        print(f"  [失败] connect: {e}")
        traceback.print_exc()
        return 3

    try:
        _section("3. 读取初始参数")
        try:
            _kv("pixel_format", cam.get_pixel_format())
        except Exception as e:
            _kv("pixel_format", f"<读取失败: {e}>")
        try:
            _kv("exposure_us", cam.get_exposure_us())
        except Exception as e:
            _kv("exposure_us", f"<读取失败: {e}>")
        try:
            _kv("gain_db", cam.get_gain_db())
        except Exception as e:
            _kv("gain_db", f"<读取失败: {e}>")
        try:
            _kv("frame_rate_hz", cam.get_frame_rate_hz())
        except Exception as e:
            _kv("frame_rate_hz", f"<读取失败: {e}>")
        try:
            _kv("binning", cam.get_binning())
        except Exception as e:
            _kv("binning", f"<读取失败: {e}>")
        try:
            _kv("roi", cam.get_roi())
        except Exception as e:
            _kv("roi", f"<读取失败: {e}>")
        try:
            _kv("black_level", cam.get_black_level())
        except Exception as e:
            _kv("black_level", f"<读取失败: {e}>")
        try:
            _kv("trigger_mode", cam.get_trigger_mode())
        except Exception as e:
            _kv("trigger_mode", f"<读取失败: {e}>")
        try:
            _kv("temperature_c", cam.get_temperature())
        except Exception as e:
            _kv("temperature_c", f"<读取失败: {e}>")

        _section("4. 参数写入并回读")
        try:
            cam.set_exposure_us(10000.0)
            _kv("set exposure 10000 → ", cam.get_exposure_us())
        except Exception as e:
            print(f"  [警告] set_exposure_us 失败: {e}")

        try:
            cam.set_gain_db(0.0)
            _kv("set gain 0 → ", cam.get_gain_db())
        except Exception as e:
            print(f"  [警告] set_gain_db 失败: {e}")

        _section("5. 采集 3 帧")
        cam.start_stream()
        try:
            for i in range(3):
                frame = cam.grab_one(timeout_ms=3000)
                img = frame.image
                print(
                    f"  frame[{i}] id={frame.frame_id:>6}  "
                    f"shape={img.shape}  dtype={img.dtype}  "
                    f"min={int(img.min()):>5}  max={int(img.max()):>5}  "
                    f"mean={float(img.mean()):>8.1f}"
                )
        finally:
            cam.stop_stream()

    finally:
        _section("6. 断开")
        cam.disconnect()
        print("  OK")

    print("\n[冒烟通过] 真机 M1 所有环节正常。\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
