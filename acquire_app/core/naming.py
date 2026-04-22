"""文件命名 + 唯一性校验。

格式: {prefix}_{label}_{mode}_{exposure}_{gain}_{timestamp}.tif
示例: panel01_OC_A16_e20000us_g0dB_20260420_143022_178.tif
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_token(value: str, field: str) -> str:
    if not value:
        raise ValueError(f"{field} 不能为空")
    if not _NAME_RE.fullmatch(value):
        raise ValueError(f"{field} 含非法字符 (仅允许 A-Za-z0-9_-): {value!r}")
    return value


def format_mode(mode: str, n_frames: int | None = None) -> str:
    """mode: 'S' 单帧 / 'A' + n 平均 / 'L' + n 锁相总帧数。"""
    mode = mode.upper()
    if mode == "S":
        return "S"
    if mode == "A":
        if n_frames is None or n_frames < 1:
            raise ValueError("平均模式必须提供 n_frames >= 1")
        return f"A{int(n_frames)}"
    if mode == "L":
        if n_frames is None or n_frames < 2:
            raise ValueError("锁相模式必须提供 >= 2 的 n_frames")
        return f"L{int(n_frames)}"
    raise ValueError(f"未知模式: {mode!r}")


def format_exposure(exposure_us: float) -> str:
    return f"e{int(round(exposure_us))}us"


def format_gain(gain_db: float) -> str:
    v = int(round(gain_db)) if abs(gain_db - round(gain_db)) < 1e-6 else gain_db
    if isinstance(v, int):
        return f"g{v}dB"
    return f"g{v:.1f}dB".replace(".", "p")


def format_timestamp(ts: datetime) -> str:
    return ts.strftime("%Y%m%d_%H%M%S") + f"_{ts.microsecond // 1000:03d}"


def build_filename(
    prefix: str,
    label: str,
    mode: str,
    exposure_us: float,
    gain_db: float,
    n_frames: int | None = None,
    timestamp: datetime | None = None,
    ext: str = ".tif",
    suffix: str = "",
) -> str:
    """
    suffix: 在时间戳后、扩展名前插入的附加标识 (如 '_bright' / '_dark').
            如果非空且不以下划线开头, 会自动补 '_' 前缀.
    """
    validate_token(prefix, "prefix")
    validate_token(label, "label")
    mode_tag = format_mode(mode, n_frames)
    exp_tag = format_exposure(exposure_us)
    gain_tag = format_gain(gain_db)
    ts_tag = format_timestamp(timestamp or datetime.now())
    if suffix and not suffix.startswith("_"):
        suffix = "_" + suffix
    return f"{prefix}_{label}_{mode_tag}_{exp_tag}_{gain_tag}_{ts_tag}{suffix}{ext}"


def ensure_unique(path: Path) -> Path:
    """路径已存在 → FileExistsError；否则返回原路径。"""
    if path.exists():
        raise FileExistsError(f"文件已存在，禁止覆盖: {path}")
    return path
