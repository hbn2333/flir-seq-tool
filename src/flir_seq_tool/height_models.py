"""Canonical ROI row reductions and derived physical-height settings."""

from dataclasses import asdict, dataclass, replace
import math

import numpy as np

from .measurement_stats import roi_values


ZONE_KEYS = ('low', 'middle', 'high')


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be finite and positive')


def _height(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError('height must be a positive integer')


@dataclass(frozen=True)
class HeightSettings:
    roi_id: str
    heated_edge: str = 'bottom'
    mm_per_pixel: float | None = None
    reference_height_px: int | None = None
    reference_height_mm: float | None = None
    zones_enabled: bool = False
    cuts: tuple[float, float] = (1 / 3, 2 / 3)
    zone_names: tuple[str, str, str] = ('Low', 'Middle', 'High')
    delta_pair: tuple[str, str] = ('low', 'high')

    def __post_init__(self):
        if not isinstance(self.roi_id, str) or not self.roi_id.strip():
            raise ValueError('height ROI id must be nonempty')
        if self.heated_edge not in ('bottom', 'top'):
            raise ValueError('unknown heated edge')
        if not isinstance(self.zones_enabled, bool):
            raise ValueError('zones_enabled must be bool')
        if (not isinstance(self.cuts, tuple) or len(self.cuts) != 2 or
                any(isinstance(q, bool) or not isinstance(q, (int, float)) or not math.isfinite(q) for q in self.cuts)
                or not 0 < self.cuts[0] < self.cuts[1] < 1):
            raise ValueError('cuts must be two ordered finite fractions inside (0,1)')
        if (not isinstance(self.zone_names, tuple) or len(self.zone_names) != 3 or
                any(not isinstance(n, str) or not n.strip() for n in self.zone_names)):
            raise ValueError('zone names must contain three nonempty names')
        if (not isinstance(self.delta_pair, tuple) or len(self.delta_pair) != 2 or
                any(n not in ZONE_KEYS for n in self.delta_pair) or self.delta_pair[0] == self.delta_pair[1]):
            raise ValueError('delta pair must contain two distinct zone keys')
        calibration = (self.mm_per_pixel, self.reference_height_px, self.reference_height_mm)
        if any(v is not None for v in calibration):
            if any(v is None for v in calibration):
                raise ValueError('calibration requires ratio and both reference heights')
            _positive(self.mm_per_pixel, 'mm_per_pixel')
            _height(self.reference_height_px)
            _positive(self.reference_height_mm, 'reference_height_mm')
            if not math.isclose(self.mm_per_pixel * self.reference_height_px, self.reference_height_mm,
                                rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError('calibration reference values disagree')

    @property
    def height_direction(self):
        return 'up' if self.heated_edge == 'bottom' else 'down'

    def to_dict(self):
        payload = asdict(self)
        for key in ('cuts', 'zone_names', 'delta_pair'):
            payload[key] = list(payload[key])
        payload['height_direction'] = self.height_direction
        return payload

    @classmethod
    def from_dict(cls, payload):
        keys = set(cls.__dataclass_fields__) | {'height_direction'}
        if not isinstance(payload, dict) or set(payload) != keys:
            raise ValueError('height settings keys mismatch')
        values = {k: v for k, v in payload.items() if k != 'height_direction'}
        for key in ('cuts', 'zone_names', 'delta_pair'):
            if not isinstance(values[key], list):
                raise ValueError(f'{key} must be a list')
            values[key] = tuple(values[key])
        result = cls(**values)
        if payload['height_direction'] != result.height_direction:
            raise ValueError('height direction disagrees with heated edge')
        return result

    def calibrate(self, height_px, length_mm):
        _height(height_px)
        _positive(length_mm, 'length_mm')
        return replace(self, mm_per_pixel=length_mm / height_px,
                       reference_height_px=height_px, reference_height_mm=length_mm)

    def validate_height(self, height_px):
        _height(height_px)
        if self.zones_enabled:
            z = height_positions(height_px, self)[0]
            labels = np.searchsorted(np.asarray(self.cuts) * height_px, z, side='right')
            if any(not np.any(labels == k) for k in range(3)):
                raise ValueError('each enabled zone must contain a geometric row')

    def for_height(self, height_px):
        _height(height_px)
        try:
            self.validate_height(height_px)
        except ValueError:
            return replace(self, zones_enabled=False)
        return self


def _frozen_array(values, dtype):
    result = np.array(values, dtype=dtype, copy=True)
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class RowProfile:
    roi_row_indices: np.ndarray
    sums_c: np.ndarray
    valid_counts: np.ndarray
    means_c: np.ndarray
    total_pixels: int
    roi_range_c: float | None = None

    def __post_init__(self):
        for name in ('roi_row_indices', 'valid_counts'):
            if np.asarray(getattr(self, name)).dtype.kind not in ('i', 'u'):
                raise ValueError(f'{name} must contain integers')
        for name, dtype in (('roi_row_indices', np.int64), ('sums_c', np.float64),
                            ('valid_counts', np.int64), ('means_c', np.float64)):
            object.__setattr__(self, name, _frozen_array(getattr(self, name), dtype))
        n = len(self.means_c)
        if (n < 1 or any(getattr(self, name).shape != (n,) for name in
                         ('roi_row_indices', 'sums_c', 'valid_counts', 'means_c')) or
                not np.array_equal(self.roi_row_indices, np.arange(n))):
            raise ValueError('profile must contain every canonical ROI row')
        _height(self.total_pixels)
        if np.any(self.valid_counts < 0) or np.any(self.valid_counts > self.total_pixels):
            raise ValueError('invalid row valid counts')


def row_profile(values, roi):
    block = roi_values(values, roi)
    valid = np.isfinite(block)
    counts = valid.sum(axis=1)
    sums = np.where(valid, block, 0.).sum(axis=1, dtype=np.float64)
    means = np.full(roi.height, np.nan)
    np.divide(sums, counts, out=means, where=counts > 0)
    finite = block[valid]
    return RowProfile(np.arange(roi.height), sums, counts, means, roi.width,
                      float(finite.max() - finite.min()) if finite.size else None)


def height_positions(height, settings):
    _height(height)
    z = np.arange(height, dtype=np.float64) + .5
    if settings.heated_edge == 'bottom':
        z = height - z
    return z, None if settings.mm_per_pixel is None else z * settings.mm_per_pixel


@dataclass(frozen=True)
class ZoneStats:
    key: str
    name: str
    lower_px: float
    upper_px: float
    lower_mm: float | None
    upper_mm: float | None
    mean_c: float | None
    valid_pixels: int
    total_pixels: int
    status: str


@dataclass(frozen=True)
class ZoneStatistics:
    zones: tuple[ZoneStats, ...]
    delta_zone_a: str
    delta_zone_b: str
    zone_delta_c: float | None
    delta_a_valid_pixels: int
    delta_b_valid_pixels: int
    roi_range_c: float | None


def zone_statistics(profile, settings):
    height = len(profile.means_c)
    # Explicit aggregation is useful for preview too, but never allows empty geometry.
    replace(settings, zones_enabled=True).validate_height(height)
    z = height_positions(height, settings)[0]
    boundaries = (0., settings.cuts[0] * height, settings.cuts[1] * height, float(height))
    labels = np.searchsorted(boundaries[1:3], z, side='right')
    zones = []
    p = settings.mm_per_pixel
    for k, key in enumerate(ZONE_KEYS):
        mask = labels == k
        count = int(profile.valid_counts[mask].sum())
        total = int(mask.sum()) * profile.total_pixels
        zones.append(ZoneStats(key, settings.zone_names[k], boundaries[k], boundaries[k+1],
                               None if p is None else p * boundaries[k],
                               None if p is None else p * boundaries[k+1],
                               float(profile.sums_c[mask].sum() / count) if count else None,
                               count, total, 'no_finite_pixels' if not count else
                               'partial_invalid' if count < total else 'ok'))
    a, b = (zones[ZONE_KEYS.index(key)] for key in settings.delta_pair)
    return ZoneStatistics(tuple(zones), a.key, b.key,
                          None if a.mean_c is None or b.mean_c is None else a.mean_c - b.mean_c,
                          a.valid_pixels, b.valid_pixels, profile.roi_range_c)
