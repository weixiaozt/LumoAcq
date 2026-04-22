import numpy as np
import pytest

from acquire_app.core.image_stats import compute_stats


def test_saturation_ratio_boundary():
    img = np.array([[0, 4095, 4095, 4095]], dtype=np.uint16)
    s = compute_stats(img, full_scale=4095)
    assert s["sat_ratio"] == pytest.approx(0.75)
    assert s["dark_ratio"] == pytest.approx(0.25)


def test_dark_ratio_threshold():
    # 阈值 = 0.01 * 4095 = 40.95
    full = 4095.0
    img = np.array([[0, 20, 40, 4000]], dtype=np.uint16)
    s = compute_stats(img, full_scale=full)
    assert s["dark_ratio"] == pytest.approx(0.75)

    img2 = np.array([[0, 40, 41, 4000]], dtype=np.uint16)
    s2 = compute_stats(img2, full_scale=full)
    assert s2["dark_ratio"] == pytest.approx(0.5)


def test_basic_stats():
    img = np.array([[10, 20, 30]], dtype=np.uint16)
    s = compute_stats(img, full_scale=100)
    assert s["min"] == 10
    assert s["max"] == 30
    assert s["mean"] == pytest.approx(20.0)
    assert s["median"] == pytest.approx(20.0)


def test_histogram_bins():
    img = np.random.randint(0, 4096, size=(100, 100), dtype=np.uint16)
    s = compute_stats(img, full_scale=4095)
    assert len(s["histogram"]) == 256
    assert len(s["hist_edges"]) == 257
    assert sum(s["histogram"]) == img.size


def test_empty_raises():
    with pytest.raises(ValueError):
        compute_stats(np.array([], dtype=np.uint16), full_scale=255)


def test_invalid_full_scale():
    with pytest.raises(ValueError):
        compute_stats(np.zeros((2, 2), dtype=np.uint16), full_scale=0)
