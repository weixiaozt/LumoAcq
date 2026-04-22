"""图像统计: 基础统计 + 饱和/暗部比例 + 256-bin 直方图。"""
from __future__ import annotations

from typing import TypedDict

import numpy as np


class ImageStats(TypedDict):
    min: float
    max: float
    mean: float
    std: float
    median: float
    sat_ratio: float
    dark_ratio: float
    histogram: list[int]
    hist_edges: list[float]


def compute_stats(img: np.ndarray, full_scale: float) -> ImageStats:
    """full_scale: 满量程值 (Mono8=255, Mono12=4095, Mono16=65535, float=1.0 或自定)。"""
    if img.size == 0:
        raise ValueError("空图像")
    if full_scale <= 0:
        raise ValueError("full_scale 必须 > 0")

    flat = img.reshape(-1)
    sat_thr = 0.99 * full_scale
    dark_thr = 0.01 * full_scale

    sat_count = int(np.count_nonzero(flat >= sat_thr))
    dark_count = int(np.count_nonzero(flat <= dark_thr))
    total = flat.size

    hist, edges = np.histogram(flat, bins=256, range=(0.0, float(full_scale)))

    return ImageStats(
        min=float(flat.min()),
        max=float(flat.max()),
        mean=float(flat.mean()),
        std=float(flat.std()),
        median=float(np.median(flat)),
        sat_ratio=sat_count / total,
        dark_ratio=dark_count / total,
        histogram=hist.astype(int).tolist(),
        hist_edges=edges.astype(float).tolist(),
    )
