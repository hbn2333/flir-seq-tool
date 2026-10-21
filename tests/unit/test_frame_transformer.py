import numpy as np
import pytest
from dataclasses import replace

from flir_seq_tool.frame_transformer import FrameTransformer
from flir_seq_tool.transform import CropRect, FrameTransform


@pytest.mark.parametrize("angle", [0.0, 90.0, -90.0, -180.0])
@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
def test_cardinal_crop_preserves_exact_pixels_without_spreading_invalid_values(
    angle, invalid
):
    values = np.arange(20, dtype=np.float32).reshape(4, 5)
    values[1, 2] = invalid
    before = values.copy()
    transform = replace(
        FrameTransform.identity(5, 4).with_angle(angle),
        crop=CropRect(1, 1, 3, 2),
    )
    transformer = FrameTransformer(transform)
    expected = np.rot90(values, int(angle / 90))

    output = transformer.apply(values)
    preview, mask = transformer.preview(values)

    np.testing.assert_array_equal(output, expected[1:3, 1:4])
    np.testing.assert_array_equal(preview, expected)
    np.testing.assert_array_equal(values, before)
    assert output.dtype == values.dtype
    assert mask.all()


def test_identity_fast_path_returns_original_array_object():
    values = np.arange(6, dtype=np.float32).reshape(2, 3)
    transformer = FrameTransformer(FrameTransform.identity(3, 2))

    assert transformer.apply(values) is values


def test_positive_quarter_turn_matches_numpy_counterclockwise_rotation():
    values = np.arange(6, dtype=np.float32).reshape(2, 3)
    transform = FrameTransform.identity(3, 2).with_angle(90.0)

    assert np.array_equal(FrameTransformer(transform).apply(values), np.rot90(values))


def test_bilinear_rotation_preserves_planar_temperature_field():
    yy, xx = np.indices((9, 11), dtype=np.float32)
    values = 2.0 * xx + 3.0 * yy + 5.0
    transform = FrameTransform.identity(11, 9).with_angle(10.0)

    output = FrameTransformer(transform).apply(values)
    source_x, source_y = transform.cropped_to_source(2.0, 2.0)

    assert output[2, 2] == pytest.approx(2.0 * source_x + 3.0 * source_y + 5.0)
    assert np.isfinite(output).all()


def test_source_nan_is_not_misclassified_as_rotation_blank():
    values = np.ones((4, 4), dtype=np.float32)
    values[1, 1] = np.nan

    output = FrameTransformer(FrameTransform.identity(4, 4)).apply(values)

    assert np.isnan(output[1, 1])


@pytest.mark.parametrize("method_name", ["apply", "preview"])
def test_source_shape_mismatch_is_rejected(method_name):
    transformer = FrameTransformer(FrameTransform.identity(3, 2))

    with pytest.raises(ValueError, match=r"frame shape.*source.*\(2, 3\)"):
        getattr(transformer, method_name)(np.ones((3, 2), dtype=np.float32))


def test_preview_returns_rotated_canvas_and_safe_valid_mask():
    values = np.arange(63, dtype=np.float32).reshape(7, 9)
    transform = FrameTransform.identity(9, 7).with_angle(10.0)

    rotated, mask = FrameTransformer(transform).preview(values)

    assert rotated.shape == transform.rotated_shape
    assert mask.shape == transform.rotated_shape
    assert np.array_equal(mask, transform.valid_mask)
    assert np.isnan(rotated[~mask]).all()
    assert np.isfinite(rotated[mask]).all()
    assert mask is not transform.valid_mask
    mask[:] = False
    assert transform.valid_mask.any()


def test_non_identity_apply_has_output_shape_and_finite_values():
    values = np.arange(99, dtype=np.float32).reshape(9, 11)
    transform = FrameTransform.identity(11, 9).with_angle(10.0)

    output = FrameTransformer(transform).apply(values)

    assert output.shape == transform.output_shape
    assert np.isfinite(output).all()


def test_crop_and_preview_sampling_coordinate_objects_are_cached():
    values = np.arange(99, dtype=np.float32).reshape(9, 11)
    transform = FrameTransform.identity(11, 9).with_angle(10.0)
    transformer = FrameTransformer(transform)
    crop_coordinates = transformer._crop_coordinates

    transformer.apply(values)
    transformer.apply(values)
    assert transformer._crop_coordinates is crop_coordinates

    assert transformer._preview_coordinates is None
    transformer.preview(values)
    preview_coordinates = transformer._preview_coordinates
    transformer.preview(values)

    assert preview_coordinates is not None
    assert transformer._preview_coordinates is preview_coordinates


def test_identity_validates_shape_before_returning_original_values():
    transformer = FrameTransformer(FrameTransform.identity(3, 2))
    values = np.arange(6, dtype=np.int16).reshape(2, 3)

    output = transformer.apply(values)

    assert output is values
    assert output.dtype == values.dtype
    assert np.array_equal(output, values)

    with pytest.raises(ValueError, match=r"frame shape.*source"):
        transformer.apply(np.arange(6, dtype=np.int16).reshape(3, 2))


def test_identity_converts_array_like_input_to_ndarray():
    values = [[0, 1, 2], [3, 4, 5]]

    output = FrameTransformer(FrameTransform.identity(3, 2)).apply(values)

    assert isinstance(output, np.ndarray)
    assert np.array_equal(output, np.asarray(values))


def test_identity_skips_unused_crop_coordinate_allocation():
    identity = FrameTransformer(FrameTransform.identity(3, 2))
    rotated = FrameTransformer(FrameTransform.identity(11, 9).with_angle(10.0))

    assert identity._crop_coordinates is None
    assert rotated._crop_coordinates is not None


def test_transform_property_cannot_be_rebound():
    transformer = FrameTransformer(FrameTransform.identity(3, 2))

    with pytest.raises(AttributeError):
        transformer.transform = FrameTransform.identity(4, 3)


def test_integer_rotation_preserves_fractional_bilinear_result():
    yy, xx = np.indices((9, 11), dtype=np.int16)
    values = 2 * xx + 3 * yy + 5
    transform = FrameTransform.identity(11, 9).with_angle(10.0)

    output = FrameTransformer(transform).apply(values)
    source_x, source_y = transform.cropped_to_source(2.0, 2.0)
    expected = 2.0 * source_x + 3.0 * source_y + 5.0

    assert np.issubdtype(output.dtype, np.floating)
    assert output[2, 2] == pytest.approx(expected)
    assert output[2, 2] != round(expected)


def test_boolean_rotation_produces_finite_floating_output():
    values = np.ones((9, 11), dtype=np.bool_)
    transform = FrameTransform.identity(11, 9).with_angle(10.0)

    output = FrameTransformer(transform).apply(values)

    assert np.issubdtype(output.dtype, np.floating)
    assert np.isfinite(output).all()
