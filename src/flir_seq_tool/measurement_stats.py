"""Pure statistics over finite temperatures in cropped-pixel ROIs."""

import numpy as np

from .measurement_models import FrameStats, Histogram, Profile, Roi


def roi_values(values: np.ndarray, roi: Roi) -> np.ndarray:
    array = np.asarray(values)
    if array.ndim != 2 or not np.issubdtype(array.dtype, np.number):
        raise ValueError("temperature must be a numeric 2-D array")
    if np.iscomplexobj(array):
        raise ValueError("temperature must be real")
    roi.validate_bounds(array.shape[1], array.shape[0])
    return np.asarray(array[roi.y:roi.y + roi.height,
                            roi.x:roi.x + roi.width], dtype=np.float64)


def summarize(values: np.ndarray, roi: Roi) -> FrameStats:
    block = roi_values(values, roi)
    finite = block[np.isfinite(block)]
    count = int(finite.size)
    if not count:
        return FrameStats(None, None, None, None, None, 0, int(block.size))
    low, high = float(finite.min()), float(finite.max())
    return FrameStats(float(finite.mean()), low, high, high - low,
                      float(finite.std(ddof=0)), count, int(block.size))


def histogram(values: np.ndarray, roi: Roi) -> Histogram:
    block = roi_values(values, roi)
    finite = block[np.isfinite(block)]
    if not finite.size:
        return Histogram(np.array([], float), np.array([], int), np.array([], float), 0)
    low, high = float(finite.min()), float(finite.max())
    if low == high:
        low, high = low - 0.5, high + 0.5
    counts, edges = np.histogram(finite, bins=32, range=(low, high))
    return Histogram(edges, counts, counts / finite.size, int(finite.size))


def profile(values: np.ndarray, roi: Roi, direction: str) -> Profile:
    if direction not in {"horizontal", "vertical"}:
        raise ValueError("unknown profile direction")
    block = roi_values(values, roi)
    axis = 0 if direction == "horizontal" else 1
    valid = np.isfinite(block)
    counts = valid.sum(axis=axis)
    sums = np.where(valid, block, 0.).sum(axis=axis, dtype=np.float64)
    means = np.full(counts.shape, np.nan)
    np.divide(sums, counts, out=means, where=counts > 0)
    start = roi.x if axis == 0 else roi.y
    return Profile(direction, np.arange(start, start + len(means)), means, counts)
