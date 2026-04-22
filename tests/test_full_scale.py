import pytest

from acquire_app.core.full_scale import full_scale_for, supported_formats


def test_known_formats():
    assert full_scale_for("Mono8") == 255
    assert full_scale_for("Mono10") == 1023
    assert full_scale_for("Mono12") == 4095
    assert full_scale_for("Mono16") == 65535


def test_unknown_format_raises():
    with pytest.raises(ValueError):
        full_scale_for("BayerRG12")


def test_supported_list():
    assert set(supported_formats()) == {"Mono8", "Mono10", "Mono12", "Mono16"}
