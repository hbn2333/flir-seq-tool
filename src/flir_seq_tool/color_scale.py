from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ColorLimits:
    vmin: float
    vmax: float


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
