import numpy as np
import pytest

from flir_seq_tool.color_scale import (
    ColorLimits, compute_color_limits, finite_extrema,
    frame_color_limits, limits_from_extrema,
)


def test_raw_constant_extrema_merge_includes_late_hot_frame():
    first = finite_extrema(np.full((2, 2), 20.0))
    last = finite_extrema(np.array([[40.0, np.nan], [np.inf, 40.0]]))

    assert first == (20.0, 20.0)
    assert last == (40.0, 40.0)
    assert limits_from_extrema(min(first[0], last[0]), max(first[1], last[1])) == ColorLimits(20.0, 40.0)


def test_frame_color_limits_expands_only_constant_final_range():
    assert frame_color_limits(np.full((2, 2), 25.0)) == ColorLimits(24.5, 25.5)
    assert frame_color_limits([[10.0, 50.0, np.nan]]) == ColorLimits(10.0, 50.0)


@pytest.mark.parametrize("values", [[], [np.nan, np.inf, -np.inf]])
def test_finite_range_helpers_return_none_without_finite_values(values):
    assert finite_extrema(values) is None
    assert frame_color_limits(values) is None


@pytest.mark.parametrize("minimum,maximum", [(np.nan, 3), (1, np.inf), (-np.inf, 2), (4, 2)])
def test_limits_from_extrema_rejects_invalid_bounds(minimum, maximum):
    with pytest.raises(ValueError):
        limits_from_extrema(minimum, maximum)


def test_compute_color_limits_ignores_nonfinite_values():
    values = np.array([[0.0, 10.0, np.nan], [20.0, 30.0, np.inf]])

    limits = compute_color_limits(
        values,
        lower_percentile=0,
        upper_percentile=100,
    )

    assert limits == ColorLimits(vmin=0.0, vmax=30.0)


def test_compute_color_limits_expands_constant_frame():
    limits = compute_color_limits(np.full((2, 2), 25.0))

    assert limits == ColorLimits(vmin=24.5, vmax=25.5)


def test_compute_color_limits_rejects_frame_without_finite_values():
    with pytest.raises(ValueError, match="frame has no finite temperatures"):
        compute_color_limits(np.array([[np.nan, np.inf]]))


@pytest.mark.parametrize(
    ("lower", "upper"),
    [(-1, 99), (1, 101), (50, 50), (60, 40)],
)
def test_compute_color_limits_rejects_invalid_percentiles(lower: float, upper: float):
    with pytest.raises(ValueError, match="percentiles must satisfy"):
        compute_color_limits(
            np.array([[1.0, 2.0]]),
            lower_percentile=lower,
            upper_percentile=upper,
        )
