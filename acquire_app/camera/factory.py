from __future__ import annotations

from acquire_app.camera.base import CameraBase


def create_camera(role: str = "daheng") -> CameraBase:
    """
    role:
        "daheng"     — 大恒 (gxipy + Galaxy SDK)
        "hikvision"  — 海康 (MVS SDK, 同时支持 USB3V / GigE)
        "dummy"      — 虚拟相机 (无硬件调试用)
    """
    if role == "dummy":
        from acquire_app.camera.dummy import DummyCamera
        return DummyCamera()

    if role == "daheng":
        from acquire_app.camera.daheng import DahengCamera
        return DahengCamera()

    if role == "hikvision":
        from acquire_app.camera.hikvision import HikvisionCamera
        return HikvisionCamera()

    raise ValueError(
        f"未知相机角色: {role!r}，可选: 'daheng' / 'hikvision' / 'dummy'"
    )
