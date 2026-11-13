from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ColorLimits:
    vmin: float
    vmax: float


def finite_extrema(values: np.ndarray) -> tuple[float, float] | None:
    """Return raw finite extrema, preserving constant frames for later merging."""
    array = np.asarray(values)
    finite_values = array[np.isfinite(array)]
    if finite_values.size == 0:
        return None
    return float(finite_values.min()), float(finite_values.max())


def limits_from_extrema(minimum: float, maximum: float) -> ColorLimits:
    """Build display limits after all contributing extrema have been merged."""
    if not np.isfinite(minimum) or not np.isfinite(maximum):
        raise ValueError("temperature extrema must be finite")
    if minimum > maximum:
        raise ValueError("minimum must not exceed maximum")
    if minimum == maximum:
        return ColorLimits(float(minimum - 0.5), float(maximum + 0.5))
    return ColorLimits(float(minimum), float(maximum))


def frame_color_limits(values: np.ndarray) -> ColorLimits | None:
    extrema = finite_extrema(values)
    return None if extrema is None else limits_from_extrema(*extrema)


def compute_color_limits(
    values: np.ndarray,
    lower_percentile: float = 1,
    upper_percentile: float = 99,
) -> ColorLimits:
    if not 0 <= lower_percentile < upper_percentile <= 100:
        raise ValueError("percentiles must satisfy 0 <= lower < upper <= 100")

    array = np.asarray(values)
    finite_values = array[np.isfinite(array)]
    if finite_values.size == 0:
        raise ValueError("frame has no finite temperatures")

    vmin, vmax = np.percentile(
        finite_values,
        [lower_percentile, upper_percentile],
    )
    if vmin == vmax:
        return ColorLimits(vmin=float(vmin - 0.5), vmax=float(vmax + 0.5))
    return ColorLimits(vmin=float(vmin), vmax=float(vmax))
