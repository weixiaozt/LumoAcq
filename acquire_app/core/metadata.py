"""采集元数据：dataclass 聚合 + JSON 序列化。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

from acquire_app.core.image_stats import ImageStats


@dataclass
class AppInfo:
    name: str
    version: str


@dataclass
class CameraInfo:
    model: str = ""
    serial: str = ""
    sdk_version: str = ""


@dataclass
class ROI:
    offset_x: int = 0
    offset_y: int = 0
    width: int = 0
    height: int = 0


@dataclass
class AcquisitionDiagnostics:
    """每次采集的校验信息 (需求 §5.3)。"""
    frame_ids: list[int] = field(default_factory=list)
    dropped_frames: int = 0
    exposure_consistent: bool = True
    gain_consistent: bool = True
    exposure_observed_us: list[float] = field(default_factory=list)
    gain_observed_db: list[float] = field(default_factory=list)
    noise_reduction_factor: float | None = None
    expected_noise_factor: float | None = None


@dataclass
class AcquisitionInfo:
    mode: str = "S"  # "S" 或 "A"
    n_frames: int = 1
    pixel_format: str = ""
    exposure_us: float = 0.0
    gain_db: float = 0.0
    binning: int = 1
    roi: ROI = field(default_factory=ROI)
    black_level: float | None = None
    dropped_frames: int = 0
    diagnostics: AcquisitionDiagnostics = field(default_factory=AcquisitionDiagnostics)


@dataclass
class TimingInfo:
    capture_start: str = ""  # ISO 8601
    capture_end: str = ""
    duration_ms: float = 0.0


@dataclass
class ImageInfo:
    width: int = 0
    height: int = 0
    dtype: str = ""
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class EnvironmentInfo:
    temperature_c: float | None = None
    humidity_pct: int | None = None
    irradiance_wm2: int | None = None
    weather: str = ""
    note: str = ""


@dataclass
class CaptureTokens:
    prefix: str = ""
    label: str = ""


@dataclass
class GroupedAverageInfo:
    """分组平均采集信息 (mode='A' + suffix 区分亮/暗 时填充).

    一次采集产两张图, 两张图的 JSON 共享绝大部分字段, 只有 group 和
    group_frame_count 不同. 其它分组统计 (n_bright/n_dark/n_transition 等)
    两张图都完整记录, 便于单张 JSON 也能溯源整个采集过程.
    """
    group: str = ""                  # "bright" | "dark"
    group_frame_count: int = 0       # 本组用于平均的帧数
    total_frames: int = 0            # 原始采集帧数
    reject_z: float = 0.0
    n_bright: int = 0
    n_dark: int = 0
    n_transition: int = 0
    transition_frames: list[int] = field(default_factory=list)
    bright_mean_avg: float = 0.0
    dark_mean_avg: float = 0.0
    frame_means: list[float] = field(default_factory=list)
    is_bright: list = field(default_factory=list)


@dataclass
class LockInInfo:
    """锁相采集信息 (mode='L' 时填充)。"""
    total_frames: int = 0
    light_freq_hz: float = 0.0
    sync_mode: str = "free"          # "free" | "hardware"
    has_phase: bool = False          # 2-桶只有幅度; 未来 4-桶相位时置 True
    # ── free 模式字段 (median+MAD 分类)
    reject_z: float = 0.0
    n_bright: int = 0
    n_dark: int = 0
    n_transition: int = 0
    transition_frames: list[int] = field(default_factory=list)
    # ── hardware 模式字段 (相邻对做差)
    n_pairs: int = 0
    aliased_pairs: int = 0
    # ── 通用
    bright_mean_avg: float = 0.0
    dark_mean_avg: float = 0.0
    contrast_ratio: float = 0.0
    frame_means: list[float] = field(default_factory=list)
    is_bright: list = field(default_factory=list)  # 元素 bool 或 None


@dataclass
class CaptureMetadata:
    app: AppInfo
    camera: CameraInfo = field(default_factory=CameraInfo)
    acquisition: AcquisitionInfo = field(default_factory=AcquisitionInfo)
    timing: TimingInfo = field(default_factory=TimingInfo)
    image: ImageInfo = field(default_factory=ImageInfo)
    environment: EnvironmentInfo = field(default_factory=EnvironmentInfo)
    capture: CaptureTokens = field(default_factory=CaptureTokens)
    lockin: LockInInfo | None = None    # 仅锁相模式填充
    grouped_average: GroupedAverageInfo | None = None    # 分组平均模式填充
    schema_version: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json_str(
        self, indent: int | None = 2, ensure_ascii: bool = False
    ) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=ensure_ascii, indent=indent, default=_default
        )

    def set_image_stats(self, stats: ImageStats) -> None:
        self.image.stats = dict(stats)

    def stamp_timing(self, start: datetime, end: datetime) -> None:
        self.timing.capture_start = start.isoformat(timespec="milliseconds")
        self.timing.capture_end = end.isoformat(timespec="milliseconds")
        self.timing.duration_ms = (end - start).total_seconds() * 1000.0


def _default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat(timespec="milliseconds")
    raise TypeError(f"无法序列化类型: {type(obj).__name__}")
