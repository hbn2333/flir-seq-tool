"""Convert image rectangle edges using the established crop pixel convention."""

import math

from .transform import CropRect


def rect_from_extents(extents) -> CropRect:
    if len(extents) != 4 or not all(math.isfinite(value) for value in extents):
        raise ValueError("rectangle extents must be finite")
    low_x, high_x = sorted(extents[:2])
    low_y, high_y = sorted(extents[2:])
    if high_x <= low_x or high_y <= low_y:
        raise ValueError("rectangle has zero span")
    tolerance = 1e-9
    left = math.ceil(low_x - tolerance)
    right = math.floor(high_x + 1.0 + tolerance)
    top = math.ceil(low_y - tolerance)
    bottom = math.floor(high_y + 1.0 + tolerance)
    return CropRect(left, top, right - left, bottom - top)
