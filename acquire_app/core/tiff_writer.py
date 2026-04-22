"""TIFF 主文件 + 侧车 JSON + 预览 JPG 写入。

约束:
- mode=S → uint16; mode=A → float32
- zlib level=6 + predictor=True, 无损
- metadata 同时写入 TIFF description 标签和侧车 .json
- 不覆盖: 三个目标路径任一存在都拒绝; 写入失败时已产生的文件回滚
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import tifffile

from acquire_app.core.metadata import CaptureMetadata
from acquire_app.core.naming import ensure_unique
from acquire_app.config import (
    PREVIEW_MAX_SIDE,
    TIFF_COMPRESSION,
    TIFF_COMPRESSION_LEVEL,
)


class DTypeMismatchError(ValueError):
    pass


def _check_dtype(image: np.ndarray, mode: str) -> None:
    if mode == "S":
        if image.dtype != np.uint16:
            raise DTypeMismatchError(
                f"单帧模式要求 uint16, 实得 {image.dtype}"
            )
    elif mode in ("A", "L"):
        if image.dtype != np.float32:
            raise DTypeMismatchError(
                f"{mode} 模式要求 float32, 实得 {image.dtype}"
            )
    else:
        raise ValueError(f"未知 mode: {mode!r}")


def _derive_paths(out_dir: Path, stem: str) -> tuple[Path, Path, Path]:
    tiff_path = out_dir / f"{stem}.tif"
    json_path = out_dir / f"{stem}.json"
    jpg_path = out_dir / f"{stem}_preview.jpg"
    return tiff_path, json_path, jpg_path


def _build_preview(image: np.ndarray) -> np.ndarray:
    """等比缩放到长边 <= PREVIEW_MAX_SIDE, p2-p98 拉伸到 uint8。"""
    h, w = image.shape[:2]
    long_side = max(h, w)
    if long_side > PREVIEW_MAX_SIDE:
        scale = PREVIEW_MAX_SIDE / long_side
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    else:
        resized = image

    arr = resized.astype(np.float32)
    lo, hi = np.percentile(arr, [2.0, 98.0])
    if hi <= lo:
        hi = lo + 1.0
    stretched = np.clip((arr - lo) / (hi - lo), 0.0, 1.0)
    return (stretched * 255.0).astype(np.uint8)


def write_capture(
    out_dir: Path,
    image: np.ndarray,
    metadata: CaptureMetadata,
    stem: str,
    write_preview: bool = True,
) -> dict[str, Path]:
    """写入主 TIFF + 侧车 JSON + 可选预览 JPG。返回实际写入的路径。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    _check_dtype(image, metadata.acquisition.mode)

    tiff_path, json_path, jpg_path = _derive_paths(out_dir, stem)
    ensure_unique(tiff_path)
    ensure_unique(json_path)
    if write_preview:
        ensure_unique(jpg_path)

    metadata.image.width = int(image.shape[-1])
    metadata.image.height = int(image.shape[-2])
    metadata.image.dtype = str(image.dtype)

    # TIFF description tag 只支持 7-bit ASCII, 含中文要转义; 侧车仍 UTF-8 原样。
    json_ascii = metadata.to_json_str(indent=None, ensure_ascii=True)
    json_pretty = metadata.to_json_str(indent=2, ensure_ascii=False)
    written: list[Path] = []

    try:
        tifffile.imwrite(
            str(tiff_path),
            image,
            compression=TIFF_COMPRESSION,
            compressionargs={"level": TIFF_COMPRESSION_LEVEL},
            predictor=True,
            description=json_ascii,
        )
        written.append(tiff_path)

        json_path.write_text(json_pretty, encoding="utf-8")
        written.append(json_path)

        result: dict[str, Path] = {"tiff": tiff_path, "json": json_path}

        if write_preview:
            preview = _build_preview(image)
            ok = cv2.imwrite(
                str(jpg_path),
                preview,
                [int(cv2.IMWRITE_JPEG_QUALITY), 92],
            )
            if not ok:
                raise IOError(f"预览 JPG 写入失败: {jpg_path}")
            written.append(jpg_path)
            result["preview"] = jpg_path

        return result

    except Exception:
        for p in written:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        raise
