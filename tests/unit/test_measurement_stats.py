from dataclasses import replace

import numpy as np
import pytest

from flir_seq_tool.measurement_models import Roi
from flir_seq_tool.measurement_stats import histogram, profile, summarize
from flir_seq_tool.frame_transformer import FrameTransformer
from flir_seq_tool.transform import CropRect, FrameTransform


ROI = dict(id="r1", name="ROI 1", x=0, y=0, width=2, height=2,
           color="#1f77b4", checked=True)


def test_statistics_use_finite_pixels_and_population_std():
    result = summarize(np.array([[1., 3.], [np.nan, np.inf]]), Roi(**ROI))
    assert (result.mean_c, result.min_c, result.max_c) == (2., 1., 3.)
    assert (result.delta_c, result.std_c) == (2., 1.)
    assert (result.valid_pixels, result.total_pixels) == (2, 4)


def test_all_invalid_is_missing_not_zero():
    result = summarize(np.array([[np.nan, np.inf], [-np.inf, np.nan]]), Roi(**ROI))
    assert (result.mean_c, result.min_c, result.max_c, result.delta_c, result.std_c) == (None,) * 5
    assert (result.valid_pixels, result.total_pixels) == (0, 4)


def test_outside_roi_is_rejected_not_clipped():
    with pytest.raises(ValueError, match="bounds"):
        summarize(np.ones((2, 2)), replace(Roi(**ROI), x=1))


@pytest.mark.parametrize("values", [np.array([[-12.]]), np.full((1, 1), 20.)])
def test_single_pixel_has_zero_spread(values):
    result = summarize(values, replace(Roi(**ROI), width=1, height=1))
    assert result.mean_c == values.item()
    assert (result.delta_c, result.std_c) == (0., 0.)


def test_float32_uses_float64_accumulation_and_overlapping_rois_are_independent():
    values = np.array([[100000000, 100000008, 100000016]], dtype=np.float32)
    roi = replace(Roi(**ROI), width=2, height=1)
    first, second = summarize(values, roi), summarize(values, replace(roi, x=1))
    assert (first.mean_c, second.mean_c) == (100000004., 100000012.)
    assert first.std_c == 4.


@pytest.mark.parametrize("values", [np.ones(4), np.ones((1, 2, 2)), [["x"]], [[True]], [[1j]]])
def test_temperature_requires_real_numeric_2d_array(values):
    with pytest.raises(ValueError, match="temperature"):
        summarize(values, replace(Roi(**ROI), width=1, height=1))


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("id", "  "), ("name", " "), ("name", 1),
    ("color", "blue"), ("color", "#fff"), ("checked", 1),
    ("x", True), ("y", -1), ("width", 0), ("height", 1.5),
])
def test_roi_rejects_invalid_fields(field, value):
    with pytest.raises(ValueError):
        Roi(**(ROI | {field: value}))


def test_roi_trims_name_and_validates_bounds():
    roi = Roi(**(ROI | {"name": "  ROI 1  "}))
    assert roi.name == "ROI 1"
    roi.validate_bounds(2, 2)
    with pytest.raises(ValueError, match="bounds"):
        roi.validate_bounds(1, 2)


def test_profiles_average_only_finite_values_along_requested_axis():
    roi = replace(Roi(**ROI), x=1)
    values = np.array([[99, 1, np.nan], [99, 3, 5]], dtype=float)
    horizontal, vertical = profile(values, roi, "horizontal"), profile(values, roi, "vertical")
    np.testing.assert_array_equal(horizontal.positions, [1, 2])
    np.testing.assert_allclose(horizontal.means_c, [2, 5])
    np.testing.assert_array_equal(horizontal.valid_counts, [2, 1])
    np.testing.assert_allclose(vertical.means_c, [1, 4])


def test_constant_histogram_has_32_bins_and_unit_probability():
    result = histogram(np.full((2, 2), 20.), Roi(**ROI))
    assert len(result.counts) == 32
    assert (result.edges[0], result.edges[-1]) == (19.5, 20.5)
    assert result.fractions.sum() == pytest.approx(1.)


def test_histogram_covers_last_edge_and_excludes_invalid_pixels():
    result = histogram(np.array([[0., 32.], [np.inf, np.nan]]), Roi(**ROI))
    assert result.counts[0] == result.counts[-1] == 1
    assert result.counts.sum() == result.valid_pixels == 2
    assert result.fractions.sum() == 1.


def test_all_invalid_histogram_is_empty():
    result = histogram(np.full((2, 2), np.nan), Roi(**ROI))
    assert len(result.edges) == len(result.counts) == len(result.fractions) == result.valid_pixels == 0


@pytest.mark.parametrize("direction,values,counts", [
    ("horizontal", [[np.nan, 2.], [np.inf, 4.]], [0, 2]),
    ("vertical", [[np.nan, np.inf], [2., 4.]], [0, 2]),
])
def test_profile_missing_positions_are_nan_without_warnings(direction, values, counts):
    with np.errstate(all="raise"):
        result = profile(values, Roi(**ROI), direction)
    assert np.isnan(result.means_c[0])
    assert result.means_c[1] == 3.
    np.testing.assert_array_equal(result.valid_counts, counts)


@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
def test_one_pixel_profile_has_one_position(direction):
    result = profile(np.array([[9.]]), replace(Roi(**ROI), width=1, height=1), direction)
    np.testing.assert_array_equal(result.positions, [0])
    np.testing.assert_array_equal(result.means_c, [9.])


def test_unknown_profile_direction_rejected():
    with pytest.raises(ValueError, match="direction"):
        profile(np.ones((2, 2)), Roi(**ROI), "diagonal")


@pytest.mark.parametrize("angle", [0., 90., 2.3, -2.3])
def test_transformed_frame_analysis_matches_direct_matrix(angle):
    values = np.arange(63, dtype=np.float32).reshape(7, 9)
    transform = FrameTransform.identity(9, 7).with_angle(angle)
    if angle == 0:
        transform = replace(transform, crop=CropRect(1, 1, 6, 4))
    actual = FrameTransformer(transform).apply(values)
    roi = replace(Roi(**ROI), x=1, y=1)
    block = actual[1:3, 1:3].astype(np.float64)
    assert summarize(actual, roi).mean_c == pytest.approx(block.mean())
    np.testing.assert_allclose(profile(actual, roi, "horizontal").means_c, block.mean(axis=0))
    np.testing.assert_allclose(profile(actual, roi, "vertical").means_c, block.mean(axis=1))
    assert histogram(actual, roi).counts.sum() == block.size
