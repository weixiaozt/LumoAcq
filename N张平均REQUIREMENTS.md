# SWIR 图像采集软件 — 需求规格 v0.3

> 面向员工开发. 只描述软件功能. 最简可用, 不做本期用不上的功能.

---

## 1. 功能总览

一个带 UI 的相机采集程序, 做四件事:

1. 连接并控制大恒 IMX992 SWIR 相机
2. 实时预览 + 参数调节 (曝光/增益/帧率/位深/Binning/ROI/触发)
3. **两种采集模式**: 单帧 / N 帧平均
4. 输出高位宽 TIFF (无损压缩, 完整元数据, 文件名可自定义)

**本期明确不做**: 闪烁双态聚类、暗场/平场校正、HDR 多曝光融合、实时 DPL 反演.
以上都是下期再加, 架构上留接口但不实现.

---

## 2. 硬件与 SDK

| 项 | 规格 |
|---|---|
| 相机 | 大恒 Daheng SWIR, Sony IMX992 |
| 分辨率 | **2560 × 2048** (5.24 MP) |
| 位深 | 原生 10/12-bit |
| 接口 | GigE Vision |
| SDK | Galaxy SDK + `gxipy` Python 绑定 |
| SDK 路径 | `C:\Program Files\Daheng Imaging\GalaxySDK\Development\Samples\Python` |
| 像素格式 | Mono8 / Mono10 / Mono12 / Mono14 / Mono16 (视型号) |

**位深策略**:
- 默认 `Mono12`, 原始帧存 `uint16` (低 12 bit 有效)
- N 帧平均后存 `float32` (除 N 产生小数)
- **不使用 8-bit JPEG 作为分析输入**

---

## 3. 软件架构

### 3.1 技术栈

- Python 3.10+
- PyQt5 (GUI)
- `gxipy` (相机驱动)
- `tifffile` + `imagecodecs` (浮点 TIFF + 压缩)
- `numpy`, `opencv-python` (预览与图像处理)

### 3.2 目录结构

```
C:\AIWork\单波长不切换DPL\
├── acquire_app\                本次新建
│   ├── main.py
│   ├── camera\                 复用 reference/camera_code/{base,daheng}.py
│   ├── gui\
│   │   ├── main_window.py
│   │   ├── camera_panel.py     连接 + 参数
│   │   ├── capture_panel.py    模式切换 + 文件命名
│   │   ├── env_panel.py        温度/湿度/辐照度/天气/备注
│   │   ├── preview_widget.py   预览 + 直方图 + 饱和
│   │   └── status_bar.py
│   ├── core\
│   │   ├── capture_worker.py   QThread 抓帧循环
│   │   ├── averager.py         N 帧平均
│   │   └── io_utils.py         TIFF 读写 + 元数据
│   └── config.py
├── reference\camera_code\      已就位, 可直接 import
├── scripts\                    (已有) 径向分箱 DPL 处理
└── images\                     默认输出
```

### 3.3 线程模型

- 主线程: 只做 UI
- `CaptureWorker` (QThread): 抓帧循环, 用 Qt 信号把帧推给 UI
- 平均累加在 Worker 内, `float64` 累加避免精度丢失
- **采集 N 帧期间参数面板锁死**

---

## 4. UI 功能

### 4.1 相机连接面板

- "扫描设备" → 列出所有相机
- 下拉选择 + 连接 / 断开
- 显示: 型号、序列号、IP
- 连接后自动进入预览流

### 4.2 参数面板 (连接后启用)

| 控件 | 参数名 (gxipy) | 备注 |
|---|---|---|
| 曝光 (μs) | `ExposureTime` | SpinBox + 对数滑杆, 10 μs – 10 s |
| 增益 (dB) | `Gain` | 0 – 24, 步 0.1 |
| 帧率 (Hz) | `AcquisitionFrameRate` | 先开 `AcquisitionFrameRateMode=On` |
| 像素格式 | `PixelFormat` | Mono8 / Mono10 / Mono12 / Mono16, 默认 Mono12 |
| Binning | `BinningHorizontal/Vertical` | 1 / 2 / 4 |
| 触发模式 | `TriggerMode` | Off (连续) / On (软触发) |
| ROI | `Width/Height/OffsetX/OffsetY` | 可禁用, 默认全幅 |
| 黑电平 | `BlackLevel` | 可读可调 (若支持) |

交互:
- 曝光/增益实时生效, 无需重启预览
- PixelFormat / Binning / ROI 自动 `stop→set→start`
- "保存预设" / "加载预设" (JSON)

### 4.3 预览面板

- 自适应缩放, 长边 ≤ 1024 px (下采样显示, 减轻 CPU)
- 拉伸模式:
  - **自动 p2-p98** (默认)
  - 自动 min-max
  - 手动 (低/高点, 原始 uint16 值)
- **直方图**: 256-bin, 线性 / 对数切换, 5-10 Hz 刷新
- **饱和提示**: ≥ 99% 满量程的像素比例, > 0.1% 时红字警告 + 叠加红色
- **暗部提示**: ≤ 1% 满量程的像素比例
- 鼠标悬停: `(x, y, 原始值)`
- 可选十字线 / 九宫格

### 4.4 采集面板 (两种模式)

#### 模式 A: 单帧

- "拍一张" 按钮
- 输出 `uint16` TIFF (原始位深, 不平均)
- 用途: 构图、曝光验证

#### 模式 B: N 帧平均

- N 选择: 1 / 4 / 8 / 16 / 32 / 64 / 128 / 256, 手填也行
- "采集并平均" 按钮 + 进度条 (1/N)
- 参数面板锁死
- 输出 `float32` TIFF (√N SNR 改善)

### 4.5 文件命名

实时预览文件名. 字段:
- `prefix` (必填, 例 `panel01`)
- `label` 下拉: `OC` / `SC` / 自定义
- `note` 可选备注
- 自动追加: 模式 (`S` 或 `A{N}`) / 曝光 / 增益 / 毫秒时间戳

例:
```
panel01_OC_A16_e20000us_g0dB_20260420_143022_178.tif
panel01_SC_A16_e20000us_g0dB_20260420_143045_021.tif
panel01_OC_S_e5000us_g0dB_20260420_142910_500.tif
```

- 同名**禁止覆盖** (冲突直接报错)
- 命名模板可编辑, 但必须保留 `{timestamp}` 保证唯一性

### 4.6 状态栏 + 日志

- 当前 fps (最近 1 s)
- 掉帧数 (从 `frame_id` 跳变推算)
- 相机温度 (若支持 `DeviceTemperature`)
- 已存文件数 / 累计 GB
- 最近 20 条采集记录表, 双击打开文件夹
- 所有操作写入 `logs/acquire_YYYYMMDD.log`

---

## 5. 采集管线

### 5.1 单帧

```python
frame = cam.grab_one(timeout_ms=2000)
save_tiff(path, frame.image.astype(np.uint16), meta)
```

### 5.2 N 帧平均

```python
acc = np.zeros(shape, dtype=np.float64)    # float64 累加避免精度丢失
for i in range(N):
    frame = cam.grab_one(timeout_ms=2000)
    acc += frame.image.astype(np.float64)
mean = (acc / N).astype(np.float32)
save_tiff(path, mean, meta, compression="zlib", predictor=True)
```

### 5.3 校验 (每次采集必做, 写进元数据)

- `frame_id` 是否连续 (掉帧数)
- 所有帧曝光/增益是否一致
- 采集中途任何超时或掉帧 → 采集中止 + 弹窗报错, **不保存半成品**
- 平均后诊断 `noise_reduction_factor_measured = std(first_frame) / std(mean)`, 应约 √N

---

## 6. 文件输出 & 压缩

### 6.1 TIFF 写入统一入口

```python
tifffile.imwrite(
    path,
    image,                           # uint16 (单帧) 或 float32 (平均)
    compression="zlib",              # 无损
    compressionargs={"level": 6},
    predictor=True,                  # 关键: 浮点/整数预测器, 压缩比 +30~50%
    photometric="minisblack",
    description=json.dumps(meta, ensure_ascii=False),
    metadata={"axes": "YX"},
)
```

**默认**: `zlib level=6` + `predictor=True`, 无损.
UI 提供下拉可切换 (`zlib` / `zstd` / `lzw` / `none`), 默认无需改.

### 6.2 文件大小 (2560 × 2048)

| 类型 | 无压缩 | zlib+predictor 预期 |
|---|---|---|
| `uint16` 单帧 | 10.5 MB | 4-6 MB |
| `float32` 平均 | 21 MB | 7-11 MB |

压缩/解压约 100 MB/s 量级, 对采集速率无影响.

### 6.3 侧车文件

- `{stem}.json` — 与 TIFF 内嵌元数据一致, 便于快速读取
- `{stem}_preview.jpg` — 8-bit JPEG (quality=92), 仅供 labelme 标注

---

## 7. 元数据 JSON

```json
{
  "schema_version": "1.0",
  "capture_mode": "average",
  "capture_time_iso": "2026-04-20T14:30:22.178+08:00",
  "filename_fields": {
    "prefix": "panel01", "label": "OC", "note": ""
  },
  "camera": {
    "vendor": "Daheng", "model": "MER2-...", "serial": "XXXX",
    "sensor": "IMX992",
    "pixel_format": "Mono12", "bit_depth": 12,
    "firmware_version": "...", "sdk_version": "gxipy x.y.z",
    "black_level": 20.0
  },
  "params": {
    "exposure_us": 20000.0, "gain_db": 0.0,
    "frame_rate_hz": 15.0, "binning": 1,
    "roi": [0, 0, 2560, 2048]
  },
  "capture": {
    "n_frames": 16,
    "frame_id_start": 1021, "frame_id_end": 1036,
    "frame_id_gaps": 0,
    "duration_s": 1.07
  },
  "image": {
    "shape": [2048, 2560], "dtype": "float32",
    "min": 25.0, "max": 3872.5,
    "mean": 1102.3, "std": 410.0,
    "saturated_pct": 0.02, "near_zero_pct": 0.01
  },
  "diagnostics": {
    "per_frame_mean_adu": [1101.2, 1103.1, ...],
    "per_frame_std_adu":  [110.0, 109.8, ...],
    "noise_reduction_factor_measured": 3.98
  },
  "environment": {
    "host": "WORKSTATION-01",
    "ambient_temperature_c": 18.5,
    "relative_humidity_pct": 62.0,
    "irradiance_w_m2": 820.0,
    "weather": "阴转多云",
    "user_note": "阴天上午, 光伏板面朝南偏东 30°"
  },
  "software_version": "0.3.0"
}
```

**环境信息面板** (UI 一块独立区域, 所有字段均可选):
- 环境温度 (°C) — 数值框, 1 位小数
- 相对湿度 (%) — 数值框, 整数
- 辐照度 (W/m²) — 数值框, 整数 (用户从辐照计读数手填)
- 天气 — 下拉: 晴 / 多云 / 阴 / 雨 / 自定义
- 备注 — 多行文本框

所有字段在 UI 顶部常驻, 每次采集**自动附加到当前元数据**. 值修改后保留, 直到手动清空或程序退出. 文件名不携带这些字段 (避免过长).

---

## 8. 性能与稳定性

| 指标 | 目标 |
|---|---|
| 预览刷新 | ≥ 10 fps (下采样后) |
| N=16 采集总时 | ≈ N/fps + 1 s 开销 |
| 内存常驻 | < 500 MB |
| 连续运行 | 8 小时无泄漏 / 崩溃 |
| 掉帧处理 | 单次自动重试 1 次, 仍失败则中止 |

---

## 9. 可扩展点 (为下期铺路, 本期不实现)

架构上预留:
1. `on_capture_ready(array, meta)` 信号 — 下期接 DPL 反演模块
2. `camera/factory.py` — 未来加相机型号
3. 第三种采集模式的 Tab 位置预留 — 下期加闪烁聚类
4. 预设 JSON 机制 — 方便 OC/SC 两组参数一键切换

---

## 10. 验收标准

### T1 连接与参数
- [ ] 能扫描并连接真机
- [ ] 曝光 100μs → 1s 滑动, 预览亮度对应变化
- [ ] `PixelFormat` 在 Mono8 ↔ Mono12 切换后位深正确

### T2 预览
- [ ] 遮镜头 → 直方图左移, 饱和警告清零
- [ ] 强光 → 饱和警告触发
- [ ] 鼠标悬停读数与 `uint16` 原值一致

### T3 采集
- [ ] **单帧**: 输出 `uint16`, `tifffile.imread(path).dtype == np.uint16`, 内容与 grab 一致
- [ ] **N=16 平均**: 输出 `float32`; 测得 `noise_reduction_factor ≈ 4` (±20%)
- [ ] 采集中试图改参数, UI 锁死
- [ ] 半路掉帧, 弹窗报错, 无半成品文件

### T4 文件输出
- [ ] 命名按模板生成, 毫秒戳保证不冲突
- [ ] 重名直接报错, 不覆盖
- [ ] `float32` TIFF 用 `tifffile`、Fiji、`cv2.imread(..., IMREAD_UNCHANGED)` 均可读
- [ ] zlib+predictor 压缩比 ≥ 2× (相比无压缩)
- [ ] 元数据 JSON 字段齐全, 内嵌与侧车一致

### T5 后端兼容
- [ ] 输出的 TIFF 能被 `scripts/process_single_image.py` 直接读入跑通
- [ ] `_preview.jpg` 能在 labelme 打开标注

---

## 11. 里程碑

| 阶段 | 交付 | 估时 |
|---|---|---|
| M1 | 命令行 demo: 连接 + 抓 1 帧 + 存 float32 TIFF | 1 天 |
| M2 | UI 骨架: 主窗口 + 参数面板 + 实时预览 + 直方图 | 2 天 |
| M3 | 采集模式 A + B, 文件命名, 元数据, 压缩 | 1.5 天 |
| M4 | 饱和警告, 历史, 日志, 预设 | 0.5 天 |
| M5 | 验收 + 打磨 | 1 天 |

合计 **约 6 天**.

---

## 12. 依赖

```
PyQt5>=5.15
numpy
opencv-python
tifffile>=2023.7
imagecodecs           # 启用 predictor 与备选压缩器
# gxipy — 来自大恒本地 SDK, 不走 pip
```

---

## 13. 约束

1. **采集期间锁定参数** — 多帧平均过程中曝光/增益/PixelFormat 不得变化
2. **不覆盖写入** — 文件名冲突报错
3. **原始精度** — 分析输入一律高位宽 TIFF, 禁止 8-bit JPEG
4. **浮点 TIFF 压缩必须无损** — 禁用 JPEG-in-TIFF

---

## 参考实现 (在 `reference/camera_code/`)

| 文件 | 本期是否用 | 作用 |
|---|---|---|
| `base.py` | ✅ 直接拷 | `CameraBase` + `Frame` / `CameraInfo` |
| `daheng.py` | ✅ 直接拷 | gxipy 封装, 参数 setter/getter 齐全 |
| `dummy.py` | ✅ 调试用 | 虚拟相机, 无硬件也能跑 UI |
| `factory.py` | ✅ | 按 role 生成相机 |
| `tiff_writer.py` | ✅ 参考 | `tifffile` 封装, 需要加 `predictor=True` |
| `metadata.py` | ✅ 参考 | 元数据数据类 |
| `flicker_clusterer.py` | ❌ 本期不用 | 下期加闪烁模式时再用 |
| `sync_grabber.py` | ❌ 本期不用 | 多相机同步 |
