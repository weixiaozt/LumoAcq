import numpy as np
import pytest

from acquire_app.core.lockin import FreeRunLockIn, LockInAverager, PairwiseLockIn


def _make_pair(bright_val: int, dark_val: int, shape=(16, 16)) -> tuple:
    b = np.full(shape, bright_val, dtype=np.uint16)
    d = np.full(shape, dark_val, dtype=np.uint16)
    return b, d


def test_lockin_averager_is_pairwise_alias():
    """向后兼容: LockInAverager 就是 PairwiseLockIn."""
    assert LockInAverager is PairwiseLockIn


def test_basic_lockin_amplitude():
    """同样大小的亮暗帧交替, result = bright - dark。"""
    avg = LockInAverager(8)
    for _ in range(4):
        b, d = _make_pair(2000, 500)
        avg.add(b)
        avg.add(d)
    assert avg.complete
    result = avg.result()
    assert result.dtype == np.float32
    assert np.allclose(result, 1500.0)


def test_order_insensitive():
    """把暗帧放前面、亮帧放后面, 结果应一致 (自动判方向)。"""
    avg = LockInAverager(4)
    b, d = _make_pair(3000, 100)
    avg.add(d)
    avg.add(b)
    avg.add(d)
    avg.add(b)
    result = avg.result()
    assert np.allclose(result, 2900.0)


def test_no_modulation_near_zero():
    """光源不调制, 所有帧一致 → result ≈ 0 (可能因噪声略有偏差)。"""
    avg = LockInAverager(8)
    rng = np.random.default_rng(42)
    for _ in range(8):
        img = (1000 + rng.normal(0, 5, (16, 16))).astype(np.uint16)
        avg.add(img)
    result = avg.result()
    # 纯随机噪声无相位相关, 幅度量级应远小于调制信号
    assert np.abs(result).mean() < 20.0


def test_odd_total_rejected():
    with pytest.raises(ValueError):
        LockInAverager(7)


def test_too_small_total_rejected():
    with pytest.raises(ValueError):
        LockInAverager(0)
    with pytest.raises(ValueError):
        LockInAverager(1)


def test_aliased_pairs_counted():
    """对内亮暗差很小 (混叠对) 应被记入 diagnostics.aliased_pairs。"""
    avg = LockInAverager(6)
    good_b, good_d = _make_pair(1000, 100)
    bad_b, bad_d = _make_pair(1000, 990)     # 差 10, 比例 1% < 5% → 混叠

    avg.add(good_b); avg.add(good_d)
    avg.add(bad_b);  avg.add(bad_d)
    avg.add(good_b); avg.add(good_d)

    diag = avg.diagnostics()
    assert diag["pairs"] == 3
    assert diag["aliased_pairs"] == 1


def test_frame_means_and_is_bright_logged():
    avg = LockInAverager(4)
    avg.add(np.full((4, 4), 500, dtype=np.uint16))   # 对 0, 第一帧
    avg.add(np.full((4, 4), 2000, dtype=np.uint16))  # 对 0, 第二帧 → 第二帧亮
    avg.add(np.full((4, 4), 2100, dtype=np.uint16))  # 对 1, 第一帧亮
    avg.add(np.full((4, 4), 600, dtype=np.uint16))

    diag = avg.diagnostics()
    assert diag["frame_means"] == [500.0, 2000.0, 2100.0, 600.0]
    assert diag["is_bright"] == [False, True, True, False]


def test_result_before_complete_raises():
    avg = LockInAverager(4)
    avg.add(np.zeros((2, 2), dtype=np.uint16))
    with pytest.raises(RuntimeError):
        avg.result()


def test_add_after_complete_raises():
    avg = LockInAverager(2)
    avg.add(np.zeros((2, 2), dtype=np.uint16))
    avg.add(np.ones((2, 2), dtype=np.uint16))
    with pytest.raises(RuntimeError):
        avg.add(np.zeros((2, 2), dtype=np.uint16))


def test_shape_mismatch_raises():
    avg = LockInAverager(4)
    avg.add(np.zeros((4, 4), dtype=np.uint16))
    with pytest.raises(ValueError):
        avg.add(np.zeros((3, 3), dtype=np.uint16))


def test_contrast_ratio_computed():
    avg = LockInAverager(4)
    avg.add(np.full((4, 4), 2000, dtype=np.uint16))
    avg.add(np.full((4, 4), 500, dtype=np.uint16))
    avg.add(np.full((4, 4), 2000, dtype=np.uint16))
    avg.add(np.full((4, 4), 500, dtype=np.uint16))

    diag = avg.diagnostics()
    assert diag["bright_mean_avg"] == pytest.approx(2000.0)
    assert diag["dark_mean_avg"] == pytest.approx(500.0)
    assert diag["contrast_ratio"] == pytest.approx(0.75)   # (2000-500)/2000


def test_naming_lockin_mode():
    """L 模式生成的文件名形式验证。"""
    from datetime import datetime
    from acquire_app.core.naming import build_filename

    ts = datetime(2026, 4, 21, 15, 0, 0, 123_000)
    name = build_filename(
        "panel01", "OC", "L", 50000, 0, n_frames=64, timestamp=ts
    )
    assert name == "panel01_OC_L64_e50000us_g0dB_20260421_150000_123.tif"


def test_naming_lockin_accepts_odd():
    """自由运行模式不再强制偶数, naming 应接受奇数 n_frames."""
    from acquire_app.core.naming import build_filename
    name = build_filename("p", "OC", "L", 1000, 0, n_frames=7)
    assert "_L7_" in name


def test_naming_lockin_rejects_too_small():
    from acquire_app.core.naming import build_filename
    with pytest.raises(ValueError):
        build_filename("p", "OC", "L", 1000, 0, n_frames=1)


# ══════════════════════════════════════════════════════════
# FreeRunLockIn
# ══════════════════════════════════════════════════════════

def _square_wave_frames(
    n: int,
    bright_val: int = 2000,
    dark_val: int = 500,
    period: int = 4,
    phase: int = 0,
    noise: float = 0.0,
    transitions: dict[int, int] | None = None,
    shape=(16, 16),
    seed: int = 0,
) -> list[np.ndarray]:
    """构造方波照明序列.

    默认一个周期 4 帧, 前半亮后半暗. transitions 指定第 k 帧用指定均值覆盖 (模拟过渡).
    """
    rng = np.random.default_rng(seed)
    frames = []
    for i in range(n):
        base = bright_val if ((i + phase) % period) < period // 2 else dark_val
        if transitions and i in transitions:
            base = transitions[i]
        img = np.full(shape, base, dtype=np.uint16)
        if noise > 0:
            img = (img + rng.normal(0, noise, shape)).astype(np.uint16)
        frames.append(img)
    return frames


def test_freerun_basic_amplitude():
    """无过渡帧的干净方波, Δ = bright - dark."""
    frames = _square_wave_frames(n=16, bright_val=2000, dark_val=500)
    avg = FreeRunLockIn(16)
    for f in frames:
        avg.add(f)
    result = avg.result()
    assert result.dtype == np.float32
    assert np.allclose(result, 1500.0)
    diag = avg.diagnostics()
    assert diag["mode"] == "free"
    assert diag["n_bright"] == 8
    assert diag["n_dark"] == 8
    assert diag["n_transition"] == 0


def test_freerun_rejects_transition_frame():
    """插入一个过渡帧 (中间值), 应被识别并剔除."""
    frames = _square_wave_frames(
        n=16, bright_val=2000, dark_val=500,
        transitions={7: 1250},   # 中间值
    )
    avg = FreeRunLockIn(16)
    for f in frames:
        avg.add(f)
    result = avg.result()
    diag = avg.diagnostics()
    assert 7 in diag["transition_frames"]
    assert diag["n_transition"] >= 1
    # 剔除后结果不含过渡帧贡献, 应当非常接近 1500
    assert abs(float(result.mean()) - 1500.0) < 1e-3


def test_freerun_reject_z_controls_strictness():
    """reject_z 越小剔除越严格."""
    # 把原本很干净的 dark 帧加一点噪声变 "边缘", 让 z 阈值影响显现
    frames = _square_wave_frames(
        n=20, bright_val=2000, dark_val=500, noise=2.0,
        transitions={9: 1250},   # 明显过渡
    )
    strict = FreeRunLockIn(20, reject_z=1.5)
    loose = FreeRunLockIn(20, reject_z=4.0)
    for f in frames:
        strict.add(f); loose.add(f)
    _ = strict.result(); _ = loose.result()
    assert strict.diagnostics()["n_transition"] >= loose.diagnostics()["n_transition"]
    # 过渡帧在严格和宽松模式下都应被识别
    assert 9 in strict.diagnostics()["transition_frames"]
    assert 9 in loose.diagnostics()["transition_frames"]


def test_freerun_rejects_too_few_frames():
    with pytest.raises(ValueError):
        FreeRunLockIn(3)


def test_freerun_rejects_negative_z():
    with pytest.raises(ValueError):
        FreeRunLockIn(8, reject_z=0)


def test_freerun_result_before_complete_raises():
    avg = FreeRunLockIn(8)
    avg.add(np.zeros((4, 4), dtype=np.uint16))
    with pytest.raises(RuntimeError):
        avg.result()


def test_freerun_add_after_complete_raises():
    frames = _square_wave_frames(n=4)
    avg = FreeRunLockIn(4)
    for f in frames:
        avg.add(f)
    # complete 状态: result 前不允许再加帧
    with pytest.raises(RuntimeError):
        avg.add(np.zeros((16, 16), dtype=np.uint16))


def test_freerun_shape_mismatch_raises():
    avg = FreeRunLockIn(4)
    avg.add(np.full((4, 4), 2000, dtype=np.uint16))
    with pytest.raises(ValueError):
        avg.add(np.full((3, 3), 2000, dtype=np.uint16))


def test_freerun_no_modulation_raises():
    """所有帧完全一样 → 分不出亮/暗."""
    avg = FreeRunLockIn(8)
    for _ in range(8):
        avg.add(np.full((4, 4), 1000, dtype=np.uint16))
    with pytest.raises(RuntimeError):
        avg.result()


def test_freerun_memory_cap_prevents_explosion():
    """单帧 × total 超软上限 → MemoryError."""
    # 单帧 100 MB × 100 帧 = 10 GB, 远超 4 GB 软顶
    avg = FreeRunLockIn(100)
    big = np.zeros((10_000, 10_000), dtype=np.uint8)   # 100 MB
    with pytest.raises(MemoryError):
        avg.add(big)


def test_freerun_unequal_bright_dark_counts_ok():
    """亮暗帧数不等 (比如 occupancy 不是 50%) 也能得到正确 Δ."""
    # 12 亮 + 4 暗 (duty cycle 75%)
    frames = []
    for _ in range(12):
        frames.append(np.full((8, 8), 2000, dtype=np.uint16))
    for _ in range(4):
        frames.append(np.full((8, 8), 500, dtype=np.uint16))
    avg = FreeRunLockIn(16)
    for f in frames:
        avg.add(f)
    result = avg.result()
    diag = avg.diagnostics()
    assert diag["n_bright"] == 12
    assert diag["n_dark"] == 4
    assert np.allclose(result, 1500.0)


def test_freerun_frames_are_copied_not_aliased():
    """相机可能复用 buffer → FreeRunLockIn 必须 copy, 不然所有帧都是最后一帧."""
    avg = FreeRunLockIn(4)
    buf = np.full((4, 4), 2000, dtype=np.uint16)
    avg.add(buf)
    buf[:] = 500
    avg.add(buf)
    buf[:] = 2000
    avg.add(buf)
    buf[:] = 500
    avg.add(buf)
    result = avg.result()
    # 如果没 copy, 所有 frame_means 都是最后一帧的值 500 → 分不出组
    assert avg.diagnostics()["n_bright"] == 2
    assert avg.diagnostics()["n_dark"] == 2
    assert np.allclose(result, 1500.0)


def test_freerun_bright_dark_image_equals_result():
    """bright_image - dark_image 必须等于 result() (分组平均与锁相数学等价)."""
    frames = _square_wave_frames(n=16, bright_val=2000, dark_val=500)
    avg = FreeRunLockIn(16)
    for f in frames:
        avg.add(f)
    b = avg.bright_image()
    d = avg.dark_image()
    r = avg.result()
    assert b.dtype == np.float32
    assert d.dtype == np.float32
    assert b.shape == d.shape == r.shape
    assert np.allclose(b, 2000.0)
    assert np.allclose(d, 500.0)
    assert np.allclose(b - d, r)


def test_freerun_bright_dark_image_excludes_transitions():
    """过渡帧不应计入 bright/dark 平均."""
    frames = _square_wave_frames(
        n=16, bright_val=2000, dark_val=500,
        transitions={7: 1250},
    )
    avg = FreeRunLockIn(16)
    for f in frames:
        avg.add(f)
    b = avg.bright_image()
    d = avg.dark_image()
    # 如果过渡帧被归入暗组, dark_image 均值会被拉高到 ~583 ((500*7+1250)/8)
    # 被归入亮组, bright_image 会被拉低. 正确行为: 过渡被剔除, b=2000 d=500
    assert np.allclose(b, 2000.0)
    assert np.allclose(d, 500.0)


def test_freerun_bright_image_before_complete_raises():
    avg = FreeRunLockIn(8)
    avg.add(np.zeros((4, 4), dtype=np.uint16))
    with pytest.raises(RuntimeError):
        avg.bright_image()
    with pytest.raises(RuntimeError):
        avg.dark_image()


def test_naming_suffix_inserted_before_ext():
    """build_filename suffix 参数测试."""
    from datetime import datetime
    from acquire_app.core.naming import build_filename
    ts = datetime(2026, 4, 22, 11, 50, 27, 404_000)
    name = build_filename(
        "panel01", "OC", "A", 10000, 0,
        n_frames=32, timestamp=ts, suffix="_bright",
    )
    assert name == "panel01_OC_A32_e10000us_g0dB_20260422_115027_404_bright.tif"

    # 无前缀 _ 时自动补
    name2 = build_filename(
        "panel01", "OC", "A", 10000, 0,
        n_frames=32, timestamp=ts, suffix="bright",
    )
    assert name2 == "panel01_OC_A32_e10000us_g0dB_20260422_115027_404_bright.tif"


def test_naming_suffix_empty_backward_compat():
    """空 suffix 时行为跟旧代码一致."""
    from datetime import datetime
    from acquire_app.core.naming import build_filename
    ts = datetime(2026, 4, 22, 11, 50, 27, 404_000)
    name = build_filename(
        "panel01", "OC", "A", 10000, 0, n_frames=32, timestamp=ts,
    )
    assert name == "panel01_OC_A32_e10000us_g0dB_20260422_115027_404.tif"
