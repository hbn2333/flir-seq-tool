import numpy as np
import pytest

from flir_seq_tool.color_scale import ColorLimits, compute_color_limits


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
