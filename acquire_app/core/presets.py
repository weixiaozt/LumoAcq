"""相机参数预设: 保存/加载一组曝光/增益/PixelFormat/Binning/ROI 等。

存储位置: <ROOT_DIR>/presets/presets.json
格式: {"presets": [{"name": "...", ...}, ...]}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from acquire_app.camera.base import CameraBase
from acquire_app.config import ROOT_DIR
from acquire_app.logger import logger


PRESETS_DIR = ROOT_DIR / "presets"
PRESETS_FILE = PRESETS_DIR / "presets.json"


@dataclass
class CameraPreset:
    name: str
    pixel_format: str = ""
    exposure_us: float = 0.0
    gain_db: float = 0.0
    frame_rate_hz: float = 0.0
    binning: int = 1
    roi: tuple = (0, 0, 0, 0)         # offset_x, offset_y, w, h
    black_level: Optional[float] = None
    trigger_mode: str = "free"        # "free" | "software" | "hardware"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["roi"] = list(self.roi)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CameraPreset":
        return cls(
            name=str(d.get("name", "")),
            pixel_format=str(d.get("pixel_format", "")),
            exposure_us=float(d.get("exposure_us", 0.0)),
            gain_db=float(d.get("gain_db", 0.0)),
            frame_rate_hz=float(d.get("frame_rate_hz", 0.0)),
            binning=int(d.get("binning", 1)),
            roi=tuple(d.get("roi") or (0, 0, 0, 0)),
            black_level=d.get("black_level"),
            trigger_mode=str(d.get("trigger_mode", "free")),
        )


def capture_preset(name: str, cam: CameraBase) -> CameraPreset:
    """把相机当前参数封装为预设。"""
    roi = cam.get_roi()
    return CameraPreset(
        name=name,
        pixel_format=cam.get_pixel_format(),
        exposure_us=float(cam.get_exposure_us()),
        gain_db=float(cam.get_gain_db()),
        frame_rate_hz=float(cam.get_frame_rate_hz() or 0.0),
        binning=int(cam.get_binning()),
        roi=(int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])),
        black_level=cam.get_black_level(),
        trigger_mode=cam.get_capture_trigger(),
    )


def apply_preset(preset: CameraPreset, cam: CameraBase) -> list[str]:
    """应用预设到相机。返回失败项的描述列表 (空列表表示全部成功)。"""
    errors: list[str] = []

    # 像素格式 / Binning / ROI 需要 stop→set→start, daheng 的 set_* 内部已处理
    if preset.pixel_format:
        try:
            cam.set_pixel_format(preset.pixel_format)
        except Exception as e:
            errors.append(f"pixel_format: {e}")

    if preset.binning > 0:
        try:
            cam.set_binning(preset.binning)
        except Exception as e:
            errors.append(f"binning: {e}")

    if any(preset.roi):
        try:
            cam.set_roi(*preset.roi)
        except Exception as e:
            errors.append(f"roi: {e}")

    if preset.exposure_us > 0:
        try:
            cam.set_exposure_us(float(preset.exposure_us))
        except Exception as e:
            errors.append(f"exposure: {e}")

    try:
        cam.set_gain_db(float(preset.gain_db))
    except Exception as e:
        errors.append(f"gain: {e}")

    if preset.frame_rate_hz > 0:
        try:
            cam.set_frame_rate_hz(float(preset.frame_rate_hz))
        except Exception as e:
            errors.append(f"frame_rate: {e}")

    if preset.black_level is not None:
        try:
            cam.set_black_level(float(preset.black_level))
        except Exception as e:
            errors.append(f"black_level: {e}")

    try:
        cam.set_capture_trigger(preset.trigger_mode)
    except Exception as e:
        errors.append(f"trigger_mode: {e}")

    return errors


def load_presets() -> list[CameraPreset]:
    if not PRESETS_FILE.exists():
        return []
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        items = data.get("presets", [])
        return [CameraPreset.from_dict(x) for x in items]
    except Exception as e:
        logger.warning(f"加载预设失败: {e}")
        return []


def save_presets(presets: list[CameraPreset]) -> None:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    data = {"presets": [p.to_dict() for p in presets]}
    PRESETS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def upsert_preset(presets: list[CameraPreset], preset: CameraPreset) -> list[CameraPreset]:
    """同名替换, 否则追加。返回新列表。"""
    out = [p for p in presets if p.name != preset.name]
    out.append(preset)
    return out


def delete_preset(presets: list[CameraPreset], name: str) -> list[CameraPreset]:
    return [p for p in presets if p.name != name]
