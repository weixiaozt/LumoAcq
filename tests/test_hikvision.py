"""海康 MVS 相机驱动单元测试.

关键策略: 真实 MVS SDK 可能不存在于 CI 环境, 所以所有测试都基于:
  - 模块能否加载 (即便 SDK 缺失)
  - 工厂/扫描/异常翻译是否优雅降级
  - 常量与抽象基类契合度

真机路径靠手动 smoke 测试 (见文档), 这里只做行为不依赖硬件的部分。
"""
from __future__ import annotations

import pytest

from acquire_app.camera.base import (
    CameraBase,
    CameraBusyError,
    CameraConnectError,
    CameraInfo,
)
from acquire_app.camera.factory import create_camera


def test_factory_exposes_hikvision_role():
    """即便没装 MVS SDK, 工厂也应识别 role='hikvision' 并尝试构造。
    未安装 SDK 时应抛 RuntimeError (不是 ValueError 未知角色)."""
    try:
        cam = create_camera("hikvision")
    except RuntimeError as e:
        msg = str(e)
        assert "MVS" in msg or "海康" in msg, (
            f"未装 SDK 时应给出 MVS/海康相关错误, 实际: {msg}"
        )
    except ValueError:
        pytest.fail("工厂必须识别 'hikvision' 角色, 不该抛 ValueError")
    else:
        # 机器上有 SDK: 构造成功, 至少是 CameraBase 子类
        assert isinstance(cam, CameraBase)


def test_factory_rejects_unknown_role():
    with pytest.raises(ValueError):
        create_camera("canon")


def test_base_exceptions_importable_from_base():
    """CameraBusyError / CameraConnectError 应当从 base 可导入 (两家驱动共享)."""
    assert issubclass(CameraBusyError, RuntimeError)
    assert issubclass(CameraConnectError, RuntimeError)


def test_hikvision_scan_does_not_raise_without_sdk():
    """未装 SDK 时 scan() 应返回空列表或抛带明确提示的 RuntimeError, 不能让整个
    GUI 扫描挂掉 (main_window 用 try/except 兜底, 但驱动本身也不能塞奇怪东西)."""
    from acquire_app.camera import hikvision

    if hikvision._MVS_AVAILABLE:
        # 真机环境: scan 不应抛, 返回 list[CameraInfo]
        results = hikvision.HikvisionCamera.scan()
        assert isinstance(results, list)
        for info in results:
            assert isinstance(info, CameraInfo)
            assert info.vendor == "Hikvision"
    else:
        # 无 SDK: _require_mvs 会抛 RuntimeError
        with pytest.raises(RuntimeError, match="MVS"):
            hikvision.HikvisionCamera.scan()


def test_hikvision_translate_busy_error():
    from acquire_app.camera import hikvision

    # 模拟 SDK 异常消息里带占用关键字
    err = hikvision._translate_connect_error(
        Exception("open failed, ret=0x80000006 resource in use")
    )
    assert isinstance(err, CameraBusyError)


def test_hikvision_translate_unreachable_error():
    from acquire_app.camera import hikvision

    err = hikvision._translate_connect_error(
        Exception("device no response, ret=0x8000001a timeout")
    )
    assert isinstance(err, CameraConnectError)
    assert "无响应" in str(err) or "无法连接" in str(err)


def test_hikvision_translate_generic_error():
    from acquire_app.camera import hikvision

    err = hikvision._translate_connect_error(Exception("weird vendor message"))
    # 兜底也得是 CameraConnectError, 不能泄露原始 Exception
    assert isinstance(err, CameraConnectError)
    assert "weird vendor message" in str(err)


def test_base_sensor_mode_default_noop():
    """Daheng / Dummy 没实现 sensor_mode — 默认返回 None / 抛 NotImplementedError."""
    from acquire_app.camera.dummy import DummyCamera

    cam = DummyCamera()
    assert cam.list_sensor_modes() is None
    assert cam.get_sensor_mode() is None
    with pytest.raises(NotImplementedError):
        cam.set_sensor_mode("高灵敏度")
