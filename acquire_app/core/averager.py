"""N 帧累加平均。

float64 累加避免 uint16 溢出, result() 除 N 得 float32 (保留亚 ADU 精度,
同时比 float64 节省一半存储)。
"""
from __future__ import annotations

import numpy as np


class FrameAverager:
    def __init__(self, target: int) -> None:
        if target < 1:
            raise ValueError(f"target 必须 >= 1, 实得 {target}")
        self._target = int(target)
        self._accumulator: np.ndarray | None = None
        self._count = 0
        self._first_shape: tuple[int, ...] | None = None
        self._first_dtype: np.dtype | None = None

    @property
    def target(self) -> int:
        return self._target

    @property
    def count(self) -> int:
        return self._count

    @property
    def complete(self) -> bool:
        return self._count >= self._target

    def add(self, image: np.ndarray) -> None:
        if self.complete:
            raise RuntimeError(f"已完成 ({self._count}/{self._target}), 不能再 add")

        if self._accumulator is None:
            self._first_shape = image.shape
            self._first_dtype = image.dtype
            self._accumulator = image.astype(np.float64, copy=True)
        else:
            if image.shape != self._first_shape:
                raise ValueError(
                    f"帧尺寸不一致: 期望 {self._first_shape}, 实得 {image.shape}"
                )
            if image.dtype != self._first_dtype:
                raise ValueError(
                    f"帧 dtype 不一致: 期望 {self._first_dtype}, 实得 {image.dtype}"
                )
            self._accumulator += image

        self._count += 1

    def result(self) -> np.ndarray:
        if not self.complete:
            raise RuntimeError(
                f"尚未完成 ({self._count}/{self._target}), 不能取 result"
            )
        assert self._accumulator is not None
        return (self._accumulator / self._target).astype(np.float32)
