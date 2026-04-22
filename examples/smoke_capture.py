"""M3 真机冒烟: 在真实相机上跑一次单帧 + 一次 N=4 平均, 验证 CaptureWorker 端到端。

运行:
    D:/LumoAcq/.venv/Scripts/python.exe -m examples.smoke_capture

输出: D:\\LumoAcq\\images\\smoke_*  (.tif / .json / _preview.jpg)
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from acquire_app.camera.daheng import DahengCamera
from acquire_app.config import IMAGES_DIR
from acquire_app.core.capture_worker import CaptureRequest, CaptureWorker
from acquire_app.core.metadata import EnvironmentInfo


def _run(request: CaptureRequest) -> int:
    worker = CaptureWorker(request)

    ok = {"done": False}
    err = {"msg": None}

    def on_progress(cur, total):
        print(f"  progress {cur}/{total}")

    def on_finished(payload: dict):
        ok["done"] = True
        print(f"  [OK] stem={payload['stem']}  duration={payload['duration_ms']:.1f} ms")
        for k, v in payload["paths"].items():
            print(f"       {k}: {v}")

    def on_failed(msg: str):
        err["msg"] = msg
        print(f"  [FAIL] {msg}")

    worker.progress.connect(on_progress)
    worker.finished.connect(on_finished)
    worker.failed.connect(on_failed)
    worker.run()

    return 0 if ok["done"] else 1


def main() -> int:
    _ = QCoreApplication(sys.argv)

    infos = DahengCamera.scan()
    if not infos:
        print("未发现设备"); return 2

    cam = DahengCamera()
    cam.connect(0)
    try:
        # 把默认 Mono8 切到 Mono12 以验证高位宽路径
        try:
            cam.set_pixel_format("Mono12")
        except Exception as e:
            print(f"Mono12 不支持, 使用默认 {cam.get_pixel_format()}: {e}")

        cam.set_exposure_us(10000)
        cam.set_gain_db(0)

        out_dir = Path(IMAGES_DIR) / "smoke"
        out_dir.mkdir(parents=True, exist_ok=True)

        env = EnvironmentInfo(
            temperature_c=22.0,
            humidity_pct=55,
            irradiance_wm2=800,
            weather="晴",
            note="M3 冒烟",
        )

        print("\n--- 单帧 S ---")
        rc1 = _run(CaptureRequest(
            camera=cam, mode="S", prefix="smoke", label="S1",
            out_dir=out_dir, environment=env,
            camera_model=infos[0].model, camera_serial=infos[0].serial,
        ))

        print("\n--- 平均 A4 ---")
        rc2 = _run(CaptureRequest(
            camera=cam, mode="A", n_frames=4, prefix="smoke", label="A4",
            out_dir=out_dir, environment=env,
            camera_model=infos[0].model, camera_serial=infos[0].serial,
        ))

        return rc1 | rc2
    finally:
        cam.disconnect()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
