"""像素格式 → 满量程值。"""
from __future__ import annotations

_FULL_SCALE = {
    "Mono8": 255,
    "Mono10": 1023,
    "Mono12": 4095,
    "Mono16": 65535,
}


def full_scale_for(pixel_format: str) -> int:
    try:
        return _FULL_SCALE[pixel_format]
    except KeyError:
        raise ValueError(f"未知像素格式: {pixel_format!r}") from None


def supported_formats() -> list[str]:
    return list(_FULL_SCALE.keys())
