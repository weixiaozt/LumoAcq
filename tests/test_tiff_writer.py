import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
import tifffile

from acquire_app.core.image_stats import compute_stats
from acquire_app.core.metadata import (
    AppInfo,
    CaptureMetadata,
)
from acquire_app.core.tiff_writer import (
    DTypeMismatchError,
    write_capture,
)


def _meta(mode: str = "S", n_frames: int = 1) -> CaptureMetadata:
    md = CaptureMetadata(app=AppInfo(name="LumoAcq", version="0.1.0"))
    md.acquisition.mode = mode
    md.acquisition.n_frames = n_frames
    md.acquisition.pixel_format = "Mono12"
    md.acquisition.exposure_us = 20000.0
    md.acquisition.gain_db = 0.0
    md.capture.prefix = "panel01"
    md.capture.label = "OC"
    md.stamp_timing(datetime(2026, 4, 20, 14, 30, 22), datetime(2026, 4, 20, 14, 30, 23))
    return md


def test_single_frame_roundtrip(tmp_path: Path):
    img = np.random.randint(0, 4096, size=(64, 80), dtype=np.uint16)
    md = _meta("S")
    md.set_image_stats(compute_stats(img, full_scale=4095))

    result = write_capture(tmp_path, img, md, stem="test_S")

    assert result["tiff"].exists()
    assert result["json"].exists()
    assert result["preview"].exists()

    with tifffile.TiffFile(result["tiff"]) as tf:
        assert tf.pages[0].shape == img.shape
        assert tf.pages[0].dtype == np.uint16
        assert np.array_equal(tf.asarray(), img)
        desc = tf.pages[0].tags["ImageDescription"].value
        parsed = json.loads(desc)
        assert parsed["app"]["name"] == "LumoAcq"

    sidecar = json.loads(result["json"].read_text(encoding="utf-8"))
    assert sidecar["image"]["width"] == 80
    assert sidecar["image"]["height"] == 64
    assert sidecar["image"]["dtype"] == "uint16"


def test_average_mode_float32(tmp_path: Path):
    img = np.random.uniform(0.0, 4095.0, size=(32, 40)).astype(np.float32)
    md = _meta("A", n_frames=16)
    md.set_image_stats(compute_stats(img, full_scale=4095))

    result = write_capture(tmp_path, img, md, stem="test_A")

    with tifffile.TiffFile(result["tiff"]) as tf:
        assert tf.pages[0].dtype == np.float32
        assert np.allclose(tf.asarray(), img)


def test_dtype_mismatch_single_mode(tmp_path: Path):
    img = np.zeros((8, 8), dtype=np.float32)
    md = _meta("S")
    with pytest.raises(DTypeMismatchError):
        write_capture(tmp_path, img, md, stem="bad")


def test_dtype_mismatch_average_mode(tmp_path: Path):
    img = np.zeros((8, 8), dtype=np.uint16)
    md = _meta("A", n_frames=4)
    with pytest.raises(DTypeMismatchError):
        write_capture(tmp_path, img, md, stem="bad")


def test_no_overwrite(tmp_path: Path):
    img = np.zeros((8, 8), dtype=np.uint16)
    md = _meta("S")
    md.set_image_stats(compute_stats(img + 1, full_scale=4095))

    write_capture(tmp_path, img, md, stem="dup")

    with pytest.raises(FileExistsError):
        write_capture(tmp_path, img, md, stem="dup")


def test_rollback_on_preview_failure(tmp_path: Path, monkeypatch):
    """若预览写入失败, 应删除已经写入的 tiff / json。"""
    import acquire_app.core.tiff_writer as tw

    def fake_imwrite(*args, **kwargs):
        return False

    monkeypatch.setattr(tw.cv2, "imwrite", fake_imwrite)

    img = np.zeros((8, 8), dtype=np.uint16)
    md = _meta("S")
    md.set_image_stats(compute_stats(img + 1, full_scale=4095))

    with pytest.raises(IOError):
        write_capture(tmp_path, img, md, stem="rollback")

    assert not (tmp_path / "rollback.tif").exists()
    assert not (tmp_path / "rollback.json").exists()
    assert not (tmp_path / "rollback_preview.jpg").exists()


def test_preview_skipped(tmp_path: Path):
    img = np.zeros((8, 8), dtype=np.uint16)
    md = _meta("S")
    md.set_image_stats(compute_stats(img + 1, full_scale=4095))
    result = write_capture(tmp_path, img, md, stem="nojpg", write_preview=False)
    assert "preview" not in result
    assert not (tmp_path / "nojpg_preview.jpg").exists()
