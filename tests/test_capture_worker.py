"""CaptureWorker 端到端测试 (用 DummyCamera, 同步调用 run())。

不依赖 pytest-qt: 直接在测试线程里 connect 捕获信号。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication

from acquire_app.camera.dummy import DummyCamera
from acquire_app.core.capture_worker import CaptureRequest, CaptureWorker
from acquire_app.core.metadata import EnvironmentInfo


@pytest.fixture(scope="session")
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


class _SignalRecorder:
    def __init__(self, worker: CaptureWorker) -> None:
        self.started = 0
        self.progress: list[tuple[int, int]] = []
        self.frames: list[np.ndarray] = []
        self.finished_payload: dict | None = None
        self.failed_msg: str | None = None

        worker.started.connect(self._on_started)
        worker.progress.connect(self._on_progress)
        worker.frame_captured.connect(self._on_frame)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)

    def _on_started(self) -> None:
        self.started += 1

    def _on_progress(self, cur: int, total: int) -> None:
        self.progress.append((cur, total))

    def _on_frame(self, img: np.ndarray) -> None:
        self.frames.append(img)

    def _on_finished(self, payload: dict) -> None:
        self.finished_payload = payload

    def _on_failed(self, msg: str) -> None:
        self.failed_msg = msg


def _make_cam(pixel_format: str = "Mono12") -> DummyCamera:
    cam = DummyCamera()
    cam.set_pixel_format(pixel_format)
    cam.set_exposure_us(5000)
    cam.set_gain_db(0)
    cam.set_frame_rate_hz(200)  # 测试提速
    cam.set_roi(0, 0, 64, 48)
    return cam


def test_single_frame_success(qapp, tmp_path: Path):
    cam = _make_cam()
    req = CaptureRequest(
        camera=cam,
        mode="S",
        prefix="t1",
        label="OC",
        out_dir=tmp_path,
        camera_model="dummy",
        camera_serial="D-001",
    )
    worker = CaptureWorker(req)
    rec = _SignalRecorder(worker)
    worker.run()

    assert rec.failed_msg is None
    assert rec.started == 1
    assert rec.progress == [(0, 1), (1, 1)]
    assert len(rec.frames) == 1
    assert rec.finished_payload is not None
    assert Path(rec.finished_payload["paths"]["tiff"]).exists()
    assert Path(rec.finished_payload["paths"]["json"]).exists()
    assert Path(rec.finished_payload["paths"]["preview"]).exists()
    assert rec.finished_payload["metadata"]["acquisition"]["mode"] == "S"


def test_average_mode_success(qapp, tmp_path: Path):
    cam = _make_cam()
    req = CaptureRequest(
        camera=cam,
        mode="A",
        n_frames=4,
        prefix="t2",
        label="SC",
        out_dir=tmp_path,
    )
    worker = CaptureWorker(req)
    rec = _SignalRecorder(worker)
    worker.run()

    assert rec.failed_msg is None
    assert rec.progress[0] == (0, 4)
    assert rec.progress[-1] == (4, 4)
    assert len(rec.frames) == 4
    assert rec.finished_payload["metadata"]["acquisition"]["mode"] == "A"
    assert rec.finished_payload["metadata"]["acquisition"]["n_frames"] == 4


def test_invalid_n_frames_fails_before_acquire(qapp, tmp_path: Path):
    cam = _make_cam()
    req = CaptureRequest(
        camera=cam, mode="A", n_frames=0, prefix="t3", label="OC", out_dir=tmp_path,
    )
    worker = CaptureWorker(req)
    rec = _SignalRecorder(worker)
    worker.run()

    assert rec.failed_msg is not None
    assert rec.started == 0  # 校验阶段就挂了
    assert len(list(tmp_path.iterdir())) == 0


def test_average_n1_allowed(qapp, tmp_path: Path):
    """N=1 的平均模式是合法但退化的用例: 一帧 float32 输出。"""
    cam = _make_cam()
    req = CaptureRequest(
        camera=cam, mode="A", n_frames=1, prefix="t3b", label="OC", out_dir=tmp_path,
    )
    worker = CaptureWorker(req)
    rec = _SignalRecorder(worker)
    worker.run()

    assert rec.failed_msg is None
    assert rec.finished_payload is not None
    assert rec.finished_payload["metadata"]["acquisition"]["n_frames"] == 1
    assert rec.finished_payload["metadata"]["image"]["dtype"] == "float32"


def test_cancel_before_first_grab(qapp, tmp_path: Path):
    cam = _make_cam()
    req = CaptureRequest(
        camera=cam, mode="A", n_frames=8, prefix="t4", label="OC", out_dir=tmp_path,
    )
    worker = CaptureWorker(req)
    worker.cancel()
    rec = _SignalRecorder(worker)
    worker.run()

    assert rec.failed_msg is not None
    assert "中止" in rec.failed_msg
    assert rec.finished_payload is None
    # 无文件落盘
    assert not any(tmp_path.glob("*.tif"))
    assert not any(tmp_path.glob("*.json"))


def test_grab_exception_no_persist(qapp, tmp_path: Path, monkeypatch):
    cam = _make_cam()

    def boom(self, timeout_ms: int = 2000):
        raise TimeoutError("mocked timeout")

    monkeypatch.setattr(DummyCamera, "grab_one", boom)

    req = CaptureRequest(
        camera=cam, mode="S", prefix="t5", label="OC", out_dir=tmp_path,
    )
    worker = CaptureWorker(req)
    rec = _SignalRecorder(worker)
    worker.run()

    assert rec.failed_msg is not None
    assert "超时" in rec.failed_msg
    assert rec.finished_payload is None
    assert not any(tmp_path.glob("*.tif"))


def test_diagnostics_average_mode(qapp, tmp_path: Path):
    """N 帧平均应记录 frame_ids / 一致性 / noise_reduction_factor。"""
    cam = _make_cam()
    req = CaptureRequest(
        camera=cam, mode="A", n_frames=8, prefix="d1", label="OC",
        out_dir=tmp_path,
    )
    worker = CaptureWorker(req)
    rec = _SignalRecorder(worker)
    worker.run()

    assert rec.failed_msg is None
    diag = rec.finished_payload["metadata"]["acquisition"]["diagnostics"]
    assert len(diag["frame_ids"]) == 8
    assert diag["dropped_frames"] == 0
    assert diag["exposure_consistent"] is True
    assert diag["gain_consistent"] is True
    # 合成帧场景统一 + 随机噪声, noise_reduction_factor 应接近 sqrt(8) ≈ 2.83
    assert diag["noise_reduction_factor"] is not None
    assert diag["expected_noise_factor"] == pytest.approx(8 ** 0.5, abs=1e-6)


def test_single_mode_no_noise_factor(qapp, tmp_path: Path):
    cam = _make_cam()
    req = CaptureRequest(
        camera=cam, mode="S", prefix="d2", label="OC", out_dir=tmp_path,
    )
    worker = CaptureWorker(req)
    rec = _SignalRecorder(worker)
    worker.run()

    assert rec.failed_msg is None
    diag = rec.finished_payload["metadata"]["acquisition"]["diagnostics"]
    assert len(diag["frame_ids"]) == 1
    assert diag["noise_reduction_factor"] is None


def test_drop_frame_aborts(qapp, tmp_path: Path, monkeypatch):
    """仿冒 grab_one 在第 3 帧跳号, 应中止且不落盘。"""
    cam = _make_cam()

    original_grab = DummyCamera.grab_one
    counter = {"n": 0}

    def fake_grab(self, timeout_ms: int = 2000):
        counter["n"] += 1
        frame = original_grab(self, timeout_ms)
        # 人为在第 3 帧跳到 ID=100 制造掉帧
        if counter["n"] == 3:
            frame.frame_id = 100
        return frame

    monkeypatch.setattr(DummyCamera, "grab_one", fake_grab)

    req = CaptureRequest(
        camera=cam, mode="A", n_frames=4, prefix="d3", label="OC", out_dir=tmp_path,
    )
    worker = CaptureWorker(req)
    rec = _SignalRecorder(worker)
    worker.run()

    assert rec.failed_msg is not None
    assert "掉帧" in rec.failed_msg
    assert rec.finished_payload is None
    assert not any(tmp_path.glob("*.tif"))


def test_environment_propagated(qapp, tmp_path: Path):
    cam = _make_cam()
    env = EnvironmentInfo(
        temperature_c=22.5,
        humidity_pct=55,
        irradiance_wm2=800,
        weather="晴",
        note="厂区A",
    )
    req = CaptureRequest(
        camera=cam, mode="S", prefix="t6", label="OC", out_dir=tmp_path,
        environment=env,
    )
    worker = CaptureWorker(req)
    rec = _SignalRecorder(worker)
    worker.run()

    assert rec.finished_payload is not None
    meta_env = rec.finished_payload["metadata"]["environment"]
    assert meta_env["temperature_c"] == 22.5
    assert meta_env["humidity_pct"] == 55
    assert meta_env["weather"] == "晴"
