"""暗场 / 平场校准.

工作流:
    1. 拍暗场 (盖镜头)  → dark_ref.tif  + dark_ref.json
    2. 拍平场 (均匀白)  → flat_ref.tif  + flat_ref.json
    3. 后续采集勾选"扣暗场"或"除平场", 落盘前做修正:
        corrected = (raw - dark) / (flat - dark) * mean(flat - dark)

注意:
- 参考帧绑定曝光/增益 — 应用时跟当前设置不一致会警告 (非致命)
- 参考帧为 float32, 跟 N 帧平均相同路径写入
- 修正结果仍是 float32
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import tifffile

from acquire_app.config import ROOT_DIR
from acquire_app.logger import logger


CALIB_DIR = ROOT_DIR / "calibration"
DARK_TIFF = CALIB_DIR / "dark_ref.tif"
DARK_META = CALIB_DIR / "dark_ref.json"
FLAT_TIFF = CALIB_DIR / "flat_ref.tif"
FLAT_META = CALIB_DIR / "flat_ref.json"


@dataclass
class CalibrationRefMeta:
    kind: str                         # "dark" | "flat"
    captured_at: str                  # ISO
    pixel_format: str
    exposure_us: float
    gain_db: float
    n_frames: int
    shape: tuple                      # (h, w)
    dtype: str                        # "float32"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["shape"] = list(self.shape)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CalibrationRefMeta":
        return cls(
            kind=str(d.get("kind", "")),
            captured_at=str(d.get("captured_at", "")),
            pixel_format=str(d.get("pixel_format", "")),
            exposure_us=float(d.get("exposure_us", 0.0)),
            gain_db=float(d.get("gain_db", 0.0)),
            n_frames=int(d.get("n_frames", 0)),
            shape=tuple(d.get("shape") or (0, 0)),
            dtype=str(d.get("dtype", "float32")),
        )


def save_dark(image: np.ndarray, meta: CalibrationRefMeta) -> None:
    _save_ref(image, meta, DARK_TIFF, DARK_META)


def save_flat(image: np.ndarray, meta: CalibrationRefMeta) -> None:
    _save_ref(image, meta, FLAT_TIFF, FLAT_META)


def load_dark() -> tuple[Optional[np.ndarray], Optional[CalibrationRefMeta]]:
    return _load_ref(DARK_TIFF, DARK_META)


def load_flat() -> tuple[Optional[np.ndarray], Optional[CalibrationRefMeta]]:
    return _load_ref(FLAT_TIFF, FLAT_META)


def _save_ref(
    image: np.ndarray,
    meta: CalibrationRefMeta,
    tiff_path: Path,
    meta_path: Path,
) -> None:
    CALIB_DIR.mkdir(parents=True, exist_ok=True)
    img = image.astype(np.float32, copy=False)
    tifffile.imwrite(
        str(tiff_path),
        img,
        compression="zlib",
        compressionargs={"level": 6},
        predictor=True,
        description=json.dumps(meta.to_dict(), ensure_ascii=True),
    )
    meta_path.write_text(
        json.dumps(meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_ref(
    tiff_path: Path, meta_path: Path
) -> tuple[Optional[np.ndarray], Optional[CalibrationRefMeta]]:
    if not tiff_path.exists() or not meta_path.exists():
        return None, None
    try:
        img = tifffile.imread(str(tiff_path)).astype(np.float32)
        meta = CalibrationRefMeta.from_dict(
            json.loads(meta_path.read_text(encoding="utf-8"))
        )
        return img, meta
    except Exception as e:
        logger.warning(f"加载参考帧失败 {tiff_path.name}: {e}")
        return None, None


def apply_correction(
    raw: np.ndarray,
    dark: Optional[np.ndarray],
    flat: Optional[np.ndarray],
    apply_dark: bool,
    apply_flat: bool,
) -> np.ndarray:
    """返回修正后的 float32 图像。raw 可以是 uint16 或 float32。"""
    if not apply_dark and not apply_flat:
        return raw.astype(np.float32, copy=False)

    r = raw.astype(np.float32)

    d = dark.astype(np.float32) if (apply_dark and dark is not None) else None
    f = flat.astype(np.float32) if (apply_flat and flat is not None) else None

    # 暗场: raw - dark
    if d is not None and _shapes_compatible(r, d):
        r = r - d

    # 平场: (raw-dark) / (flat-dark) * mean(flat-dark)
    if f is not None and _shapes_compatible(r, f):
        fd = (f - d) if d is not None else f
        scale = float(fd.mean())
        # 避免除 0: 把极小值夹到 scale * 1e-3
        safe = np.where(np.abs(fd) < max(1e-6, scale * 1e-3), scale * 1e-3, fd)
        r = r / safe * scale

    return r.astype(np.float32)


def _shapes_compatible(a: np.ndarray, b: np.ndarray) -> bool:
    ok = a.shape == b.shape
    if not ok:
        logger.warning(
            f"校准参考帧尺寸不匹配: raw {a.shape} vs ref {b.shape}, 跳过该项"
        )
    return ok


def warn_exposure_mismatch(
    cur_exp_us: float, cur_gain_db: float, ref_meta: CalibrationRefMeta,
) -> list[str]:
    """返回不匹配项的警告字符串列表。"""
    warnings: list[str] = []
    if abs(cur_exp_us - ref_meta.exposure_us) / max(1.0, ref_meta.exposure_us) > 0.02:
        warnings.append(
            f"曝光 {cur_exp_us:.0f} us 与参考 {ref_meta.exposure_us:.0f} us 不同"
        )
    if abs(cur_gain_db - ref_meta.gain_db) > 0.1:
        warnings.append(
            f"增益 {cur_gain_db:.1f} dB 与参考 {ref_meta.gain_db:.1f} dB 不同"
        )
    return warnings


def make_ref_meta(
    kind: str,
    image: np.ndarray,
    pixel_format: str,
    exposure_us: float,
    gain_db: float,
    n_frames: int,
) -> CalibrationRefMeta:
    return CalibrationRefMeta(
        kind=kind,
        captured_at=datetime.now().isoformat(timespec="seconds"),
        pixel_format=pixel_format,
        exposure_us=float(exposure_us),
        gain_db=float(gain_db),
        n_frames=int(n_frames),
        shape=tuple(image.shape),
        dtype="float32",
    )
