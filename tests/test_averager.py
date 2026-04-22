import numpy as np
import pytest

from acquire_app.core.averager import FrameAverager


def test_basic_average():
    avg = FrameAverager(3)
    avg.add(np.full((4, 4), 10, dtype=np.uint16))
    avg.add(np.full((4, 4), 20, dtype=np.uint16))
    avg.add(np.full((4, 4), 30, dtype=np.uint16))
    result = avg.result()
    assert result.dtype == np.float32
    assert np.allclose(result, 20.0)


def test_float_precision_preserved():
    # 对 N=3 取均值, 应保留 0.333 级别精度
    avg = FrameAverager(3)
    avg.add(np.full((2, 2), 0, dtype=np.uint16))
    avg.add(np.full((2, 2), 1, dtype=np.uint16))
    avg.add(np.full((2, 2), 1, dtype=np.uint16))
    assert np.allclose(avg.result(), 2.0 / 3.0)


def test_no_overflow_for_many_bright_frames():
    # 若用 uint16 累加会溢出, float64 不会
    avg = FrameAverager(100)
    bright = np.full((8, 8), 60000, dtype=np.uint16)
    for _ in range(100):
        avg.add(bright)
    assert np.allclose(avg.result(), 60000.0)


def test_progress_counts():
    avg = FrameAverager(4)
    assert avg.count == 0 and not avg.complete
    avg.add(np.zeros((2, 2), dtype=np.uint16))
    assert avg.count == 1 and not avg.complete
    for _ in range(3):
        avg.add(np.zeros((2, 2), dtype=np.uint16))
    assert avg.complete


def test_result_before_complete_raises():
    avg = FrameAverager(2)
    avg.add(np.zeros((2, 2), dtype=np.uint16))
    with pytest.raises(RuntimeError):
        avg.result()


def test_add_after_complete_raises():
    avg = FrameAverager(1)
    avg.add(np.zeros((2, 2), dtype=np.uint16))
    with pytest.raises(RuntimeError):
        avg.add(np.zeros((2, 2), dtype=np.uint16))


def test_shape_mismatch_raises():
    avg = FrameAverager(2)
    avg.add(np.zeros((2, 2), dtype=np.uint16))
    with pytest.raises(ValueError):
        avg.add(np.zeros((3, 3), dtype=np.uint16))


def test_dtype_mismatch_raises():
    avg = FrameAverager(2)
    avg.add(np.zeros((2, 2), dtype=np.uint16))
    with pytest.raises(ValueError):
        avg.add(np.zeros((2, 2), dtype=np.uint8))


def test_target_must_be_positive():
    with pytest.raises(ValueError):
        FrameAverager(0)
