"""Validated measurement settings and numerical result records (no UI dependencies)."""

from dataclasses import asdict, dataclass, replace
import re
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .height_models import HeightSettings


@dataclass(frozen=True)
class Roi:
    id: str
    name: str
    x: int
    y: int
    width: int
    height: int
    color: str
    checked: bool

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("ROI id must be nonempty")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("ROI name must be nonempty")
        object.__setattr__(self, "name", self.name.strip())
        for name in ("x", "y", "width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"ROI {name} must be an integer")
            if value < (1 if name in {"width", "height"} else 0):
                raise ValueError(f"ROI {name} is out of range")
        if not isinstance(self.color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", self.color):
            raise ValueError("ROI color must be #RRGGBB")
        if not isinstance(self.checked, bool):
            raise ValueError("ROI checked must be bool")

    def validate_bounds(self, width: int, height: int) -> None:
        if self.x + self.width > width or self.y + self.height > height:
            raise ValueError("ROI exceeds image bounds")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "Roi":
        _require_keys(payload, {"id", "name", "x", "y", "width", "height", "color", "checked"}, "ROI")
        return cls(**payload)


def _require_keys(payload: dict, expected: set[str], label: str) -> None:
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError(f"{label} keys mismatch")


@dataclass(frozen=True)
class MeasurementSettings:
    rois: tuple[Roi, ...] = ()
    active_roi_id: str | None = None
    result_view: str = "current"
    frame_view: str = "distribution"
    profile_direction: str = "horizontal"
    curve_metric: str = "mean"
    height_settings: tuple['HeightSettings', ...] = ()
    height_view: str = "profile"
    height_unit: str = "px"

    def __post_init__(self) -> None:
        if not isinstance(self.rois, tuple) or not all(isinstance(roi, Roi) for roi in self.rois):
            raise ValueError("rois must be a tuple of ROI records")
        ids = [roi.id for roi in self.rois]
        names = [roi.name for roi in self.rois]
        if len(set(ids)) != len(ids) or len(set(names)) != len(names):
            raise ValueError("ROI ids and names must be unique")
        if self.active_roi_id is not None and (
            not isinstance(self.active_roi_id, str) or self.active_roi_id not in ids
        ):
            raise ValueError("active ROI id must reference an existing ROI")
        for name, options in (
            ("result_view", ("current", "curves", "height")),
            ("frame_view", ("distribution", "profile")),
            ("profile_direction", ("horizontal", "vertical")),
            ("curve_metric", ("mean", "min", "max")),
            ("height_view", ("profile", "zones")),
            ("height_unit", ("px", "mm")),
        ):
            if getattr(self, name) not in options:
                raise ValueError(f"unknown {name}")
        from .height_models import HeightSettings
        if not isinstance(self.height_settings, tuple) or not all(isinstance(s, HeightSettings) for s in self.height_settings):
            raise ValueError("height_settings must be a tuple of HeightSettings")
        seen = set()
        by_id = {roi.id: roi for roi in self.rois}
        for config in self.height_settings:
            if config.roi_id not in by_id or config.roi_id in seen:
                raise ValueError("height settings must reference unique existing ROI ids")
            seen.add(config.roi_id)
            config.validate_height(by_id[config.roi_id].height)

    def height_for(self, roi_id: str) -> 'HeightSettings':
        from .height_models import HeightSettings
        if roi_id not in {roi.id for roi in self.rois}:
            raise ValueError("height settings require an existing ROI")
        return next((s for s in self.height_settings if s.roi_id == roi_id), HeightSettings(roi_id))

    def with_height(self, config: 'HeightSettings') -> 'MeasurementSettings':
        from .height_models import HeightSettings
        if not isinstance(config, HeightSettings):
            raise ValueError("height settings must be HeightSettings")
        if any(s.roi_id == config.roi_id for s in self.height_settings):
            configs = tuple(config if s.roi_id == config.roi_id else s for s in self.height_settings)
        else:
            configs = self.height_settings + (config,)
        return replace(self, height_settings=configs)

    def validate_bounds(self, width: int, height: int) -> None:
        for roi in self.rois:
            roi.validate_bounds(width, height)

    def to_dict(self) -> dict:
        return {
            "coordinate_space": "cropped_pixel",
            "rois": [roi.to_dict() for roi in self.rois],
            "active_roi_id": self.active_roi_id,
            "result_view": self.result_view,
            "frame_view": self.frame_view,
            "profile_direction": self.profile_direction,
            "curve_metric": self.curve_metric,
            "height_settings": [settings.to_dict() for settings in self.height_settings],
            "height_view": self.height_view,
            "height_unit": self.height_unit,
        }

    @classmethod
    def from_dict(cls, payload: dict, *, legacy: bool = False) -> "MeasurementSettings":
        expected = {"coordinate_space", "rois", "active_roi_id", "result_view",
                    "frame_view", "profile_direction", "curve_metric"}
        if not legacy:
            expected |= {"height_settings", "height_view", "height_unit"}
        _require_keys(payload, expected, "measurement")
        if payload["coordinate_space"] != "cropped_pixel":
            raise ValueError("measurement coordinate_space must be cropped_pixel")
        if not isinstance(payload["rois"], list):
            raise ValueError("measurement rois must be a list")
        height = {}
        if legacy:
            if payload['result_view'] not in ('current', 'curves'):
                raise ValueError('unknown legacy result_view')
        else:
            from .height_models import HeightSettings
            if not isinstance(payload['height_settings'], list):
                raise ValueError('height_settings must be a list')
            height = dict(height_settings=tuple(HeightSettings.from_dict(s) for s in payload['height_settings']),
                          height_view=payload['height_view'], height_unit=payload['height_unit'])
        return cls(rois=tuple(Roi.from_dict(roi) for roi in payload["rois"]),
                   **height,
                   **{name: payload[name] for name in (
                       "active_roi_id", "result_view", "frame_view", "profile_direction", "curve_metric")})


@dataclass(frozen=True)
class FrameStats:
    mean_c: float | None
    min_c: float | None
    max_c: float | None
    delta_c: float | None
    std_c: float | None
    valid_pixels: int
    total_pixels: int


@dataclass(frozen=True)
class Histogram:
    edges: np.ndarray
    counts: np.ndarray
    fractions: np.ndarray
    valid_pixels: int


@dataclass(frozen=True)
class Profile:
    direction: str
    positions: np.ndarray
    means_c: np.ndarray
    valid_counts: np.ndarray


@dataclass(frozen=True)
class CurrentAnalysis:
    source_frame: int
    roi_id: str
    stats: FrameStats
    histogram: Histogram
    horizontal: Profile
    vertical: Profile
