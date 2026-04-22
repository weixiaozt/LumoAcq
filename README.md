# LumoAcq

**LumoAcq** is a SWIR camera acquisition application for **Electroluminescence (EL) testing of solar panels**, built with PySide6 and pyqtgraph.

## Features

| Feature | Description |
|---|---|
| 实时预览 | Live camera preview with auto-stretch, histogram, saturation overlay, FPS readout |
| 拍单帧 | Single-frame capture with TIFF + JSON sidecar |
| 拍 N 帧平均 | Multi-frame average — saves float32 TIFF (mean image) |
| 拍分组平均 (亮/暗双图) | Capture N frames, auto-classify bright/dark via k-means, save `_bright` and `_dark` float32 TIFFs for external tools (e.g. Halcon `sub_image`) |
| 锁相采集 | Lock-in imaging: free-run k-means mode (software trigger) or pairwise hardware-sync mode; saves lock-in result + bright/dark averages |
| 锁相监视器 | Real-time waveform monitor with per-group statistics table during lock-in capture |
| 相机参数 | Exposure, gain, ROI, binning, pixel format, trigger mode (free/software/hardware) |
| 标定 | Flat-field calibration with persistence |

## Lock-in Algorithm

Two modes are supported:

- **FreeRunLockIn** (software trigger): accumulates all frames, runs 1D k-means (seeded at min/max) to split bright vs dark clusters, then uses median + MAD z-score (default `reject_z = 2.5`) to flag transition frames. Result = `mean(bright) − mean(dark)` in float32.
- **PairwiseLockIn** (hardware sync): classic adjacent-frame pair subtraction in O(1) memory. Best used when the camera is externally synchronized to the EL source.

The free-run mode tolerates unequal bright/dark counts and rejects partial-transition frames automatically.

## Hardware

- **Camera**: Daheng Imaging SWIR series (via `gxipy` SDK — install separately from Daheng MVS)
- Dummy camera included for UI development / offline testing

## Installation

```bash
# 1. Install Daheng gxipy SDK (from Daheng MVS package, not on PyPI)

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# 3. Install Python dependencies
pip install -r requirements.txt
```

**requirements.txt**
```
PySide6>=6.6
pyqtgraph>=0.13
numpy
opencv-python
tifffile>=2023.7
imagecodecs
# gxipy — from Daheng local SDK, not pip
```

## Running

```bash
python -m acquire_app.main
```

## Project Structure

```
acquire_app/
├── camera/          # Camera abstraction (base, daheng, dummy, factory)
├── core/            # Business logic
│   ├── lockin.py              # FreeRunLockIn + PairwiseLockIn algorithms
│   ├── lockin_worker.py       # QThread worker for lock-in capture
│   ├── capture_worker.py      # QThread worker for single/average capture
│   ├── grouped_average_worker.py  # QThread worker for bright/dark pair
│   ├── preview_worker.py      # Continuous live preview worker
│   ├── metadata.py            # Dataclasses: CaptureMetadata, LockInInfo, …
│   ├── naming.py              # Output filename builder
│   └── tiff_writer.py         # float32 TIFF + JSON sidecar writer
└── gui/
    ├── main_window.py
    ├── panels/                # Connection, Parameters, Capture, Preview, …
    ├── dialogs/               # Lock-in monitor, calibration, …
    └── widgets/               # Card, themed buttons, …
tests/                         # pytest test suite
```

## Output Files

All images are saved to `images/` (configurable) with structured filenames:

```
{panel_id}_{mode}_{L/N}{count}_{exposure}us_{gain}dB_{date}_{time}[_suffix].tif
```

Each TIFF is accompanied by a `.json` sidecar with full acquisition metadata.

## License

MIT
