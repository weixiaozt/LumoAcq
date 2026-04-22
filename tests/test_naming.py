from datetime import datetime
from pathlib import Path

import pytest

from acquire_app.core.naming import (
    build_filename,
    ensure_unique,
    format_gain,
    format_mode,
    format_timestamp,
    validate_token,
)


def test_build_filename_average():
    ts = datetime(2026, 4, 20, 14, 30, 22, 178_000)
    name = build_filename("panel01", "OC", "A", 20000, 0, n_frames=16, timestamp=ts)
    assert name == "panel01_OC_A16_e20000us_g0dB_20260420_143022_178.tif"


def test_build_filename_single():
    ts = datetime(2026, 4, 20, 14, 29, 10, 500_000)
    name = build_filename("panel01", "SC", "S", 5000, 0, timestamp=ts)
    assert name == "panel01_SC_S_e5000us_g0dB_20260420_142910_500.tif"


def test_validate_token_rejects_illegal():
    with pytest.raises(ValueError):
        validate_token("panel 01", "prefix")
    with pytest.raises(ValueError):
        validate_token("", "prefix")
    with pytest.raises(ValueError):
        validate_token("a/b", "prefix")


def test_validate_token_accepts_allowed():
    assert validate_token("a_1-B2", "prefix") == "a_1-B2"


def test_format_mode_requires_n_for_average():
    with pytest.raises(ValueError):
        format_mode("A")
    assert format_mode("A", 16) == "A16"
    assert format_mode("S") == "S"


def test_format_gain_integer_vs_fraction():
    assert format_gain(0.0) == "g0dB"
    assert format_gain(6.0) == "g6dB"
    assert format_gain(6.5) == "g6p5dB"


def test_format_timestamp_milliseconds():
    ts = datetime(2026, 4, 20, 14, 30, 22, 178_999)
    assert format_timestamp(ts) == "20260420_143022_178"


def test_ensure_unique_raises_on_existing(tmp_path: Path):
    p = tmp_path / "a.tif"
    p.write_bytes(b"x")
    with pytest.raises(FileExistsError):
        ensure_unique(p)


def test_ensure_unique_ok_when_missing(tmp_path: Path):
    p = tmp_path / "b.tif"
    assert ensure_unique(p) == p
