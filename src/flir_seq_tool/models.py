from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class TimestampSummary:
    frame_count: int
    scanned_frame_count: int
    continuity_checked: bool
    first_time: datetime
    last_time: datetime
    duration_s: float
    median_interval_s: float | None
    estimated_fps: float | None
    duplicate_after_frames: tuple[int, ...]
    backward_after_frames: tuple[int, ...]
    gap_after_frames: tuple[int, ...]


@dataclass(frozen=True)
class SeqInspection:
    path: Path
    size_bytes: int
    modified_time_ns: int
    width: int
    height: int
    num_frames: int
    timestamp_mode: str
    timestamps: TimestampSummary


@dataclass(frozen=True)
class ObjectParameterSnapshot:
    emissivity: float
    reflected_temp_k: float
    distance_m: float
    atmosphere_temp_k: float
    relative_humidity: float
    atmospheric_transmission: float


@dataclass(frozen=True)
class TemperatureFrame:
    frame_index: int
    timestamp: datetime
    temperature_c: np.ndarray
