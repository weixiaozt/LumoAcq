"""锁相累加器: 两种算法, 两种工作模式.

PairwiseLockIn  ── 硬件同步模式 (相机与光源相位锁定)
    相邻帧配对, 对内高者为亮、低者为暗, 做差后 float64 增量累加.
    要求: total_frames 偶数, 相机帧率严格 2× 光源频率.
    内存: O(单帧), 与 N 无关.

FreeRunLockIn   ── 自由运行模式 (相机异步采集, 光源自由调制, EL 测试等)
    全部帧先缓存并计算每帧均值, 用 median + MAD 鲁棒分类出亮/暗/过渡,
    过渡帧剔除不参与累加, 最后 mean(亮帧) - mean(暗帧) 得锁相幅度.
    要求: total_frames >= 4.
    内存: O(N × H × W), 受 _MAX_BYTES_SOFT_CAP 软上限约束.

两者都输出 float32 带符号结果图, 接口一致:
    add(image) / complete / count / frame_means / is_bright / result() / diagnostics()
"""
from __future__ import annotations

import numpy as np


# ══════════════════════════════════════════════════════════
# 硬件同步模式 (原 LockInAverager, 保留作为 hardware sync 时用)
# ══════════════════════════════════════════════════════════

class PairwiseLockIn:
    """2-桶锁相累加器 (硬件同步): 相邻帧配对做差, float64 增量累加."""

    # 对内对比度低于此阈值视为"混叠对"
    _ALIAS_THRESHOLD = 0.05

    def __init__(self, total_frames: int) -> None:
        if total_frames < 2:
            raise ValueError(f"total_frames 必须 >= 2, 实得 {total_frames}")
        if total_frames % 2 != 0:
            raise ValueError(f"total_frames 必须是偶数, 实得 {total_frames}")
        self._total = int(total_frames)

        self._diff_acc: np.ndarray | None = None
        self._first_shape: tuple[int, ...] | None = None
        self._first_dtype: np.dtype | None = None

        self._bright_sum = 0.0
        self._dark_sum = 0.0
        self._aliased_pairs = 0
        self._pairs_done = 0

        self._pending_img: np.ndarray | None = None
        self._pending_mean: float | None = None

        self._frame_means: list[float] = []
        self._is_bright: list[bool | None] = []

        self._count = 0

    @property
    def total(self) -> int:
        return self._total

    @property
    def n_pairs(self) -> int:
        return self._total // 2

    @property
    def count(self) -> int:
        return self._count

    @property
    def complete(self) -> bool:
        return self._count >= self._total

    @property
    def frame_means(self) -> list[float]:
        return list(self._frame_means)

    @property
    def is_bright(self) -> list[bool | None]:
        return list(self._is_bright)

    def add(self, image: np.ndarray) -> None:
        if self.complete:
            raise RuntimeError(f"已满 ({self._count}/{self._total})")

        if self._first_shape is None:
            self._first_shape = image.shape
            self._first_dtype = image.dtype
        else:
            if image.shape != self._first_shape:
                raise ValueError(
                    f"帧尺寸不一致: 期望 {self._first_shape}, 实得 {image.shape}"
                )
            if image.dtype != self._first_dtype:
                raise ValueError(
                    f"帧 dtype 不一致: 期望 {self._first_dtype}, 实得 {image.dtype}"
                )

        mean = float(image.mean())

        if self._pending_img is None:
            self._pending_img = image
            self._pending_mean = mean
            self._frame_means.append(mean)
            self._is_bright.append(None)
            self._count += 1
            return

        assert self._pending_mean is not None
        if self._pending_mean >= mean:
            bright_img, dark_img = self._pending_img, image
            bright_m, dark_m = self._pending_mean, mean
            self._is_bright[-1] = True
            self._is_bright.append(False)
        else:
            bright_img, dark_img = image, self._pending_img
            bright_m, dark_m = mean, self._pending_mean
            self._is_bright[-1] = False
            self._is_bright.append(True)
        self._frame_means.append(mean)

        if self._diff_acc is None:
            self._diff_acc = np.zeros(image.shape, dtype=np.float64)

        self._diff_acc += bright_img.astype(np.float64) - dark_img.astype(np.float64)

        self._bright_sum += bright_m
        self._dark_sum += dark_m

        if bright_m > 0:
            ratio = (bright_m - dark_m) / bright_m
            if ratio < self._ALIAS_THRESHOLD:
                self._aliased_pairs += 1

        self._pending_img = None
        self._pending_mean = None
        self._pairs_done += 1
        self._count += 1

    def result(self) -> np.ndarray:
        if not self.complete:
            raise RuntimeError(
                f"尚未完成 ({self._count}/{self._total}), 不能取 result"
            )
        assert self._diff_acc is not None
        return (self._diff_acc / self._pairs_done).astype(np.float32)

    def diagnostics(self) -> dict:
        pairs = max(1, self._pairs_done)
        bright_avg = self._bright_sum / pairs
        dark_avg = self._dark_sum / pairs
        contrast = (bright_avg - dark_avg) / bright_avg if bright_avg > 0 else 0.0
        return {
            "mode": "hardware",
            "pairs": self._pairs_done,
            "aliased_pairs": self._aliased_pairs,
            "bright_mean_avg": float(bright_avg),
            "dark_mean_avg": float(dark_avg),
            "contrast_ratio": float(contrast),
            "frame_means": list(self._frame_means),
            "is_bright": [bool(x) if x is not None else None for x in self._is_bright],
        }


# 向后兼容: 旧代码 / 测试里仍用 LockInAverager 这个名字
LockInAverager = PairwiseLockIn


# ══════════════════════════════════════════════════════════
# 自由运行模式 (median + MAD 鲁棒分类, EL 测试主力)
# ══════════════════════════════════════════════════════════

class FreeRunLockIn:
    """自由运行锁相: 缓存全部帧, 按均值分亮/暗/过渡, 过渡剔除后做差.

    使用 median + MAD (1.4826 × 中位数绝对偏差 ≈ sigma) 判断每帧属于哪个簇,
    距本簇中心 z 值 > reject_z 的帧标为过渡 (剔除).
    结果 = mean(亮帧) - mean(暗帧).
    """

    # 缓存字节数软上限: 超过则拒绝, 防止爆内存. 2592×2056×2 ≈ 10 MB/帧 → 400 帧封顶.
    _MAX_BYTES_SOFT_CAP = 4 * 1024 ** 3   # 4 GB
    _DEFAULT_REJECT_Z = 2.5

    def __init__(self, total_frames: int, reject_z: float | None = None) -> None:
        if total_frames < 4:
            raise ValueError(
                f"自由运行锁相 total_frames 必须 >= 4, 实得 {total_frames}"
            )
        self._total = int(total_frames)
        self._reject_z = (
            float(reject_z) if reject_z is not None else self._DEFAULT_REJECT_Z
        )
        if self._reject_z <= 0:
            raise ValueError(f"reject_z 必须 > 0, 实得 {self._reject_z}")

        self._frames: list[np.ndarray] = []
        self._means: list[float] = []
        self._labels: list[bool | None] = []     # None=未定/过渡 True=亮 False=暗

        self._first_shape: tuple[int, ...] | None = None
        self._first_dtype: np.dtype | None = None

        self._classified = False
        self._n_bright = 0
        self._n_dark = 0
        self._n_transition = 0
        self._transition_frames: list[int] = []
        self._bright_mean_avg = 0.0
        self._dark_mean_avg = 0.0
        self._result: np.ndarray | None = None
        self._bright_image: np.ndarray | None = None   # 亮组逐像素平均, float32
        self._dark_image: np.ndarray | None = None     # 暗组逐像素平均, float32

    @property
    def total(self) -> int:
        return self._total

    @property
    def count(self) -> int:
        return len(self._frames)

    @property
    def complete(self) -> bool:
        return self.count >= self._total

    @property
    def frame_means(self) -> list[float]:
        return list(self._means)

    @property
    def is_bright(self) -> list[bool | None]:
        return list(self._labels)

    @property
    def reject_z(self) -> float:
        return self._reject_z

    def add(self, image: np.ndarray) -> None:
        if self.complete:
            raise RuntimeError(f"已满 ({self.count}/{self._total})")

        if self._first_shape is None:
            self._first_shape = image.shape
            self._first_dtype = image.dtype
            # 首帧: 内存预检
            est_bytes = int(image.nbytes) * self._total
            if est_bytes > self._MAX_BYTES_SOFT_CAP:
                raise MemoryError(
                    f"锁相帧缓存估计 {est_bytes/1024**3:.2f} GB 超软上限 "
                    f"{self._MAX_BYTES_SOFT_CAP/1024**3:.1f} GB. "
                    f"单帧 {image.nbytes/1024**2:.1f} MB × {self._total} 帧. "
                    f"请减小 total_frames 或缩小 ROI."
                )
        else:
            if image.shape != self._first_shape:
                raise ValueError(
                    f"帧尺寸不一致: 期望 {self._first_shape}, 实得 {image.shape}"
                )
            if image.dtype != self._first_dtype:
                raise ValueError(
                    f"帧 dtype 不一致: 期望 {self._first_dtype}, 实得 {image.dtype}"
                )

        # 必须 copy: 相机驱动可能复用内部 buffer, 下次 grab 会覆盖
        self._frames.append(image.copy())
        self._means.append(float(image.mean()))
        self._labels.append(None)

    def _classify_and_reduce(self) -> None:
        means = np.asarray(self._means, dtype=np.float64)

        # 初始分组: 1D k-means(k=2), 用 min/max 播种.
        # 用 median 分组在 duty-cycle 偏离 50% 时会塌陷 (多数派吞没中位数),
        # k-means 对 duty 不敏感.
        c_lo, c_hi = float(means.min()), float(means.max())
        if c_hi - c_lo < 1e-6:
            raise RuntimeError(
                "所有帧均值几乎相同, 无法分出亮/暗两组 (光源未调制或饱和)"
            )
        for _ in range(50):
            lo_mask = np.abs(means - c_lo) <= np.abs(means - c_hi)
            new_lo = float(means[lo_mask].mean()) if lo_mask.any() else c_lo
            new_hi = float(means[~lo_mask].mean()) if (~lo_mask).any() else c_hi
            if abs(new_lo - c_lo) < 1e-6 and abs(new_hi - c_hi) < 1e-6:
                break
            c_lo, c_hi = new_lo, new_hi
        if c_hi <= c_lo:
            raise RuntimeError("k-means 未能分出亮/暗两簇, 光源可能未调制")

        hi_mask = np.abs(means - c_hi) < np.abs(means - c_lo)
        hi_group = means[hi_mask]
        lo_group = means[~hi_mask]
        if len(hi_group) == 0 or len(lo_group) == 0:
            raise RuntimeError(
                "k-means 分组后有一簇为空, 光源未调制或帧数过少"
            )

        # 各簇内 median + MAD → 稳健的 sigma
        med_hi = float(np.median(hi_group))
        med_lo = float(np.median(lo_group))
        mad_hi = float(np.median(np.abs(hi_group - med_hi)))
        mad_lo = float(np.median(np.abs(lo_group - med_lo)))
        # MAD → sigma: 1.4826 × MAD. 防 0 除
        s_hi = max(1.4826 * mad_hi, 1e-6)
        s_lo = max(1.4826 * mad_lo, 1e-6)

        z = self._reject_z
        labels: list[bool | None] = []
        bright_idx: list[int] = []
        dark_idx: list[int] = []
        transition_idx: list[int] = []
        for i, m in enumerate(means):
            d_hi = abs(m - med_hi) / s_hi
            d_lo = abs(m - med_lo) / s_lo
            if d_hi < z and d_hi <= d_lo:
                labels.append(True)
                bright_idx.append(i)
            elif d_lo < z and d_lo < d_hi:
                labels.append(False)
                dark_idx.append(i)
            else:
                labels.append(None)
                transition_idx.append(i)

        if not bright_idx or not dark_idx:
            raise RuntimeError(
                f"分类后无可用帧: bright={len(bright_idx)} dark={len(dark_idx)} "
                f"transition={len(transition_idx)}. "
                f"所有帧被剔除或光源未调制; 可适当放宽 reject_z 或检查光源."
            )

        bright_acc = np.zeros(self._first_shape, dtype=np.float64)
        for i in bright_idx:
            bright_acc += self._frames[i]
        dark_acc = np.zeros(self._first_shape, dtype=np.float64)
        for i in dark_idx:
            dark_acc += self._frames[i]
        bright_mean = bright_acc / len(bright_idx)
        dark_mean = dark_acc / len(dark_idx)
        result = bright_mean - dark_mean

        self._labels = labels
        self._n_bright = len(bright_idx)
        self._n_dark = len(dark_idx)
        self._n_transition = len(transition_idx)
        self._transition_frames = transition_idx
        self._bright_mean_avg = float(means[bright_idx].mean())
        self._dark_mean_avg = float(means[dark_idx].mean())
        self._result = result.astype(np.float32)
        self._bright_image = bright_mean.astype(np.float32)
        self._dark_image = dark_mean.astype(np.float32)
        self._classified = True

    def result(self) -> np.ndarray:
        if not self.complete:
            raise RuntimeError(
                f"尚未完成 ({self.count}/{self._total}), 不能取 result"
            )
        if not self._classified:
            self._classify_and_reduce()
        assert self._result is not None
        return self._result

    def bright_image(self) -> np.ndarray:
        """亮组逐像素平均图 (float32). 用于分组平均落盘."""
        if not self.complete:
            raise RuntimeError(
                f"尚未完成 ({self.count}/{self._total}), 不能取 bright_image"
            )
        if not self._classified:
            self._classify_and_reduce()
        assert self._bright_image is not None
        return self._bright_image

    def dark_image(self) -> np.ndarray:
        """暗组逐像素平均图 (float32). 用于分组平均落盘."""
        if not self.complete:
            raise RuntimeError(
                f"尚未完成 ({self.count}/{self._total}), 不能取 dark_image"
            )
        if not self._classified:
            self._classify_and_reduce()
        assert self._dark_image is not None
        return self._dark_image

    def diagnostics(self) -> dict:
        if not self._classified:
            return {
                "mode": "free",
                "reject_z": self._reject_z,
                "n_bright": 0,
                "n_dark": 0,
                "n_transition": 0,
                "transition_frames": [],
                "bright_mean_avg": 0.0,
                "dark_mean_avg": 0.0,
                "contrast_ratio": 0.0,
                "frame_means": list(self._means),
                "is_bright": list(self._labels),
            }
        b_avg, d_avg = self._bright_mean_avg, self._dark_mean_avg
        contrast = (b_avg - d_avg) / b_avg if b_avg > 0 else 0.0
        return {
            "mode": "free",
            "reject_z": self._reject_z,
            "n_bright": self._n_bright,
            "n_dark": self._n_dark,
            "n_transition": self._n_transition,
            "transition_frames": list(self._transition_frames),
            "bright_mean_avg": float(b_avg),
            "dark_mean_avg": float(d_avg),
            "contrast_ratio": float(contrast),
            "frame_means": list(self._means),
            "is_bright": list(self._labels),
        }
