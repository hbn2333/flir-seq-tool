from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math

import numpy as np


_ANGLE_TOLERANCE = 1e-9
_TRIG_TOLERANCE = 1e-12
_GEOMETRY_TOLERANCE = 1e-9


def normalize_angle(angle_deg: float) -> float:
    if isinstance(angle_deg, bool) or not isinstance(angle_deg, (int, float)):
        raise ValueError("angle_deg must be numeric")

    value = float(angle_deg)
    if not math.isfinite(value):
        raise ValueError("angle_deg must be finite")

    normalized = (value + 180.0) % 360.0 - 180.0
    if math.isclose(
        normalized,
        0.0,
        rel_tol=0.0,
        abs_tol=_ANGLE_TOLERANCE,
    ):
        return 0.0
    return normalized


def _validate_tenth_degree(angle_deg: float) -> None:
    if not math.isclose(
        angle_deg,
        round(angle_deg, 1),
        rel_tol=0.0,
        abs_tol=_ANGLE_TOLERANCE,
    ):
        raise ValueError("angle_deg must use 0.1 degree precision")

    distance = abs((angle_deg + 45.0) % 90.0 - 45.0)
    if distance > 15.0 + _ANGLE_TOLERANCE:
        raise ValueError("angle_deg must be within 15 degrees of a cardinal angle")


@dataclass(frozen=True)
class RotationSettings:
    angle_deg: float

    def __post_init__(self) -> None:
        normalize_angle(self.angle_deg)
        value = float(self.angle_deg)
        if value < -180.0 or value >= 180.0:
            raise ValueError("angle_deg must already be normalized to [-180, 180)")

        _validate_tenth_degree(value)
        canonical = normalize_angle(round(value, 1))
        if canonical == 0.0:
            canonical = 0.0
        object.__setattr__(self, "angle_deg", canonical)


@dataclass(frozen=True)
class CropRect:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ValueError("crop coordinates and dimensions must be integers")
        if self.x < 0 or self.y < 0:
            raise ValueError("crop coordinates must be non-negative")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("crop dimensions must be positive")


def _snapped_trig(angle_deg: float) -> tuple[float, float]:
    radians = math.radians(angle_deg)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    if math.isclose(cosine, 0.0, rel_tol=0.0, abs_tol=_TRIG_TOLERANCE):
        cosine = 0.0
    if math.isclose(sine, 0.0, rel_tol=0.0, abs_tol=_TRIG_TOLERANCE):
        sine = 0.0
    return cosine, sine


def _immutable_array(values: np.ndarray) -> np.ndarray:
    return np.frombuffer(values.tobytes(), dtype=values.dtype).reshape(values.shape)


def rotation_geometry(
    width: int,
    height: int,
    angle_deg: float,
) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    dimensions = (width, height)
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in dimensions
    ):
        raise ValueError("source dimensions must be integers")
    if width <= 0 or height <= 0:
        raise ValueError("source dimensions must be positive")

    angle = RotationSettings(angle_deg).angle_deg
    try:
        return _rotation_geometry_cached(width, height, angle)
    except (ArithmeticError, ValueError, np.linalg.LinAlgError) as exc:
        raise ValueError(
            "source dimensions cannot be represented safely for rotation geometry"
        ) from exc


@lru_cache(maxsize=128)
def _rotation_geometry_cached(
    width: int,
    height: int,
    angle: float,
) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    cosine, sine = _snapped_trig(angle)
    center_x = (width - 1) / 2.0
    center_y = (height - 1) / 2.0
    source_to_unshifted = np.array(
        [
            [
                cosine,
                sine,
                center_x - cosine * center_x - sine * center_y,
            ],
            [
                -sine,
                cosine,
                center_y + sine * center_x - cosine * center_y,
            ],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    corners = np.array(
        [
            [0.0, width - 1.0, 0.0, width - 1.0],
            [0.0, 0.0, height - 1.0, height - 1.0],
            [1.0, 1.0, 1.0, 1.0],
        ],
        dtype=np.float64,
    )
    rotated_corners = source_to_unshifted @ corners
    minimum_x = float(rotated_corners[0].min())
    maximum_x = float(rotated_corners[0].max())
    minimum_y = float(rotated_corners[1].min())
    maximum_y = float(rotated_corners[1].max())

    span_x = maximum_x - minimum_x
    span_y = maximum_y - minimum_y
    rotated_width = math.ceil(span_x - 1e-12) + 1
    rotated_height = math.ceil(span_y - 1e-12) + 1
    margin_x = (rotated_width - 1 - span_x) / 2.0
    margin_y = (rotated_height - 1 - span_y) / 2.0

    shift = np.array(
        [
            [1.0, 0.0, -minimum_x + margin_x],
            [0.0, 1.0, -minimum_y + margin_y],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    source_to_rotated = _immutable_array(shift @ source_to_unshifted)
    rotated_to_source = _immutable_array(np.linalg.inv(source_to_rotated))
    return (
        source_to_rotated,
        rotated_to_source,
        (rotated_height, rotated_width),
    )


def largest_valid_rectangle(mask: np.ndarray) -> CropRect:
    values = np.asarray(mask)
    if values.ndim != 2 or values.size == 0 or values.dtype != np.bool_:
        raise ValueError("mask must be a non-empty two-dimensional boolean array")
    if not values.any():
        raise ValueError("mask must contain at least one valid pixel")

    height, width = values.shape
    column_heights = np.zeros(width, dtype=np.int64)
    best_area = 0
    best: tuple[int, int, int, int] | None = None

    for row in range(height):
        column_heights = np.where(values[row], column_heights + 1, 0)
        stack: list[int] = []
        for column in range(width + 1):
            current_height = int(column_heights[column]) if column < width else 0
            while stack and int(column_heights[stack[-1]]) > current_height:
                bar = stack.pop()
                rectangle_height = int(column_heights[bar])
                left = stack[-1] + 1 if stack else 0
                rectangle_width = column - left
                area = rectangle_height * rectangle_width
                top = row - rectangle_height + 1
                candidate = (top, left, rectangle_width, rectangle_height)
                if (
                    area > best_area
                    or area == best_area
                    and best is not None
                    and candidate[:2] < best[:2]
                ):
                    best_area = area
                    best = candidate
            stack.append(column)

    if best is None:
        raise ValueError("mask must contain at least one valid pixel")
    top, left, rectangle_width, rectangle_height = best
    return CropRect(left, top, rectangle_width, rectangle_height)


@lru_cache(maxsize=128)
def _rotation_valid_mask(width: int, height: int, angle_deg: float) -> np.ndarray:
    _, rotated_to_source, rotated_shape = rotation_geometry(
        width,
        height,
        angle_deg,
    )
    rotated_y, rotated_x = np.indices(rotated_shape, dtype=np.float64)
    homogeneous = np.stack(
        (rotated_x.ravel(), rotated_y.ravel(), np.ones(rotated_x.size)),
    )
    source = rotated_to_source @ homogeneous
    source_x = source[0].reshape(rotated_shape)
    source_y = source[1].reshape(rotated_shape)
    mask = (
        (source_x >= -_GEOMETRY_TOLERANCE)
        & (source_x <= width - 1 + _GEOMETRY_TOLERANCE)
        & (source_y >= -_GEOMETRY_TOLERANCE)
        & (source_y <= height - 1 + _GEOMETRY_TOLERANCE)
    )
    return _immutable_array(mask)


@dataclass(frozen=True)
class FrameTransform:
    source_width: int
    source_height: int
    rotation: RotationSettings
    crop: CropRect

    def __post_init__(self) -> None:
        dimensions = (self.source_width, self.source_height)
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in dimensions
        ):
            raise ValueError("source dimensions must be integers")
        if self.source_width <= 0 or self.source_height <= 0:
            raise ValueError("source dimensions must be positive")
        if not isinstance(self.rotation, RotationSettings):
            raise ValueError("rotation must be a RotationSettings instance")
        if not isinstance(self.crop, CropRect):
            raise ValueError("crop must be a CropRect instance")

        rotated_height, rotated_width = self.rotated_shape
        if (
            self.crop.x + self.crop.width > rotated_width
            or self.crop.y + self.crop.height > rotated_height
        ):
            raise ValueError("crop must stay within the rotated canvas")
        left = self.crop.x
        right = self.crop.x + self.crop.width - 1
        top = self.crop.y
        bottom = self.crop.y + self.crop.height - 1
        source_corners = (
            self.rotated_to_source(left, top),
            self.rotated_to_source(right, top),
            self.rotated_to_source(left, bottom),
            self.rotated_to_source(right, bottom),
        )
        if any(
            source_x < -_GEOMETRY_TOLERANCE
            or source_x > self.source_width - 1 + _GEOMETRY_TOLERANCE
            or source_y < -_GEOMETRY_TOLERANCE
            or source_y > self.source_height - 1 + _GEOMETRY_TOLERANCE
            for source_x, source_y in source_corners
        ):
            raise ValueError("crop must not contain invalid rotated pixels")

    @classmethod
    def identity(cls, width: int, height: int) -> FrameTransform:
        return cls(
            source_width=width,
            source_height=height,
            rotation=RotationSettings(0.0),
            crop=CropRect(0, 0, width, height),
        )

    @property
    def output_shape(self) -> tuple[int, int]:
        return self.crop.height, self.crop.width

    @property
    def source_to_rotated_matrix(self) -> np.ndarray:
        return rotation_geometry(
            self.source_width,
            self.source_height,
            self.rotation.angle_deg,
        )[0]

    @property
    def rotated_to_source_matrix(self) -> np.ndarray:
        return rotation_geometry(
            self.source_width,
            self.source_height,
            self.rotation.angle_deg,
        )[1]

    @property
    def rotated_shape(self) -> tuple[int, int]:
        return rotation_geometry(
            self.source_width,
            self.source_height,
            self.rotation.angle_deg,
        )[2]

    @property
    def valid_mask(self) -> np.ndarray:
        return _rotation_valid_mask(
            self.source_width,
            self.source_height,
            self.rotation.angle_deg,
        )

    @staticmethod
    def _map_point(matrix: np.ndarray, x: float, y: float) -> tuple[float, float]:
        mapped = matrix @ np.array([x, y, 1.0], dtype=np.float64)
        return float(mapped[0] / mapped[2]), float(mapped[1] / mapped[2])

    def source_to_rotated(self, x: float, y: float) -> tuple[float, float]:
        return self._map_point(self.source_to_rotated_matrix, x, y)

    def rotated_to_source(self, x: float, y: float) -> tuple[float, float]:
        return self._map_point(self.rotated_to_source_matrix, x, y)

    def source_to_cropped(self, x: float, y: float) -> tuple[float, float]:
        rotated_x, rotated_y = self.source_to_rotated(x, y)
        return rotated_x - self.crop.x, rotated_y - self.crop.y

    def cropped_to_source(self, x: float, y: float) -> tuple[float, float]:
        return self.rotated_to_source(x + self.crop.x, y + self.crop.y)

    def with_angle(self, angle_deg: float) -> FrameTransform:
        rotation = RotationSettings(angle_deg)
        crop = largest_valid_rectangle(
            _rotation_valid_mask(
                self.source_width,
                self.source_height,
                rotation.angle_deg,
            )
        )
        return FrameTransform(
            self.source_width,
            self.source_height,
            rotation,
            crop,
        )

    @property
    def fingerprint(self) -> str:
        canonical_angle = (
            0.0 if self.rotation.angle_deg == 0.0 else self.rotation.angle_deg
        )
        payload = {
            "angle": canonical_angle,
            "crop": {
                "height": self.crop.height,
                "width": self.crop.width,
                "x": self.crop.x,
                "y": self.crop.y,
            },
            "source_height": self.source_height,
            "source_width": self.source_width,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
