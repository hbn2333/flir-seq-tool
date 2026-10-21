import math

import numpy as np
import pytest

import flir_seq_tool.transform as transform_module
from flir_seq_tool.transform import (
    CropRect,
    FrameTransform,
    RotationSettings,
    largest_valid_rectangle,
    rotation_geometry,
)


def test_identity_transform_uses_full_source_frame():
    transform = FrameTransform.identity(640, 480)

    assert transform.rotation.angle_deg == 0.0
    assert transform.crop == CropRect(0, 0, 640, 480)
    assert transform.output_shape == (480, 640)


@pytest.mark.parametrize("angle", [15.1, 44.9, 74.9, 105.1])
def test_rotation_rejects_angles_outside_cardinal_fine_ranges(angle):
    with pytest.raises(ValueError, match="within 15 degrees"):
        RotationSettings(angle)


def test_rotation_rejects_unnormalized_angle():
    with pytest.raises(ValueError, match="normalized"):
        RotationSettings(180.0)


@pytest.mark.parametrize("angle", [0.05, -2.34, 89.95])
def test_rotation_rejects_more_than_one_decimal_place(angle):
    with pytest.raises(ValueError, match="0.1"):
        RotationSettings(angle)


def test_rotation_precision_check_uses_absolute_tolerance_only():
    with pytest.raises(ValueError, match="0.1"):
        RotationSettings(179.99999985)


@pytest.mark.parametrize("angle", [15.0, -15.0, 75.0, -75.0, -180.0])
def test_rotation_accepts_cardinal_fine_range_boundaries(angle):
    assert RotationSettings(angle).angle_deg == angle


def test_crop_rejects_boolean_and_nonpositive_dimensions():
    with pytest.raises(ValueError, match="integer"):
        CropRect(True, 0, 3, 2)
    with pytest.raises(ValueError, match="positive"):
        CropRect(0, 0, 0, 2)


def test_frame_transform_rejects_invalid_rotation_type():
    with pytest.raises(ValueError, match="rotation"):
        FrameTransform(640, 480, 0.0, CropRect(0, 0, 640, 480))


def test_frame_transform_rejects_invalid_crop_type():
    with pytest.raises(ValueError, match="crop"):
        FrameTransform(640, 480, RotationSettings(0.0), (0, 0, 640, 480))


def test_frame_transform_construction_does_not_build_full_valid_mask(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("full valid mask must be lazy")

    monkeypatch.setattr(transform_module, "_rotation_valid_mask", fail_if_called)

    transform = FrameTransform(
        8,
        6,
        RotationSettings(10.0),
        CropRect(1, 2, 7, 4),
    )

    assert transform.crop == CropRect(1, 2, 7, 4)


def test_rotation_geometry_reports_huge_dimensions_as_value_error():
    with pytest.raises(ValueError, match="source dimensions"):
        rotation_geometry(10**400, 2, 0.0)


def test_zero_angle_geometry_uses_full_source_frame():
    transform = FrameTransform.identity(4, 3).with_angle(0.0)

    assert transform.rotated_shape == (3, 4)
    assert transform.valid_mask.dtype == np.bool_
    assert transform.valid_mask.all()
    assert transform.crop == CropRect(0, 0, 4, 3)


def test_quarter_turn_maps_source_and_swaps_canvas_shape():
    transform = FrameTransform.identity(3, 2).with_angle(90.0)

    assert transform.rotated_shape == (3, 2)
    assert transform.source_to_rotated(2.0, 0.0) == pytest.approx((0.0, 0.0))
    assert transform.rotated_to_source(0.0, 0.0) == pytest.approx((2.0, 0.0))


def test_arbitrary_angle_coordinate_round_trip():
    transform = FrameTransform.identity(640, 480).with_angle(-2.3)

    cropped = transform.source_to_cropped(321.25, 201.75)
    assert transform.cropped_to_source(*cropped) == pytest.approx(
        (321.25, 201.75), abs=1e-9
    )


def test_largest_valid_rectangle_uses_top_then_left_tie_break():
    mask = np.array(
        [
            [True, True, False, True, True],
            [True, True, False, True, True],
        ],
        dtype=bool,
    )

    assert largest_valid_rectangle(mask) == CropRect(0, 0, 2, 2)


def test_largest_valid_rectangle_handles_full_single_row_and_column_masks():
    assert largest_valid_rectangle(np.ones((2, 3), dtype=bool)) == CropRect(0, 0, 3, 2)
    assert largest_valid_rectangle(np.ones((1, 4), dtype=bool)) == CropRect(0, 0, 4, 1)
    assert largest_valid_rectangle(np.ones((3, 1), dtype=bool)) == CropRect(0, 0, 1, 3)


@pytest.mark.parametrize(
    "mask",
    [
        np.zeros((2, 3), dtype=bool),
        np.ones((0, 3), dtype=bool),
        np.ones((3,), dtype=bool),
        np.ones((2, 3), dtype=np.uint8),
    ],
)
def test_largest_valid_rectangle_rejects_invalid_masks(mask):
    with pytest.raises(ValueError):
        largest_valid_rectangle(mask)


def test_nonzero_angle_crop_contains_only_valid_pixels():
    transform = FrameTransform.identity(8, 6).with_angle(10.0)
    region = transform.valid_mask[
        transform.crop.y : transform.crop.y + transform.crop.height,
        transform.crop.x : transform.crop.x + transform.crop.width,
    ]

    assert region.all()


def test_frame_transform_rejects_crop_outside_rotated_canvas():
    with pytest.raises(ValueError, match="canvas"):
        FrameTransform(4, 3, RotationSettings(0.0), CropRect(3, 0, 2, 2))


def test_frame_transform_rejects_crop_containing_rotation_blank_pixels():
    rotated = FrameTransform.identity(8, 6).with_angle(10.0)

    with pytest.raises(ValueError, match="invalid"):
        FrameTransform(
            8,
            6,
            RotationSettings(10.0),
            CropRect(0, 0, rotated.rotated_shape[1], rotated.rotated_shape[0]),
        )


def test_fingerprint_is_stable_and_changes_with_geometry():
    identity = FrameTransform.identity(4, 3)
    same = FrameTransform.identity(4, 3)
    negative_zero = FrameTransform(
        4,
        3,
        RotationSettings(-0.0),
        CropRect(0, 0, 4, 3),
    )
    rotated = identity.with_angle(10.0)
    cropped = FrameTransform(4, 3, RotationSettings(0.0), CropRect(1, 0, 3, 3))

    assert identity.fingerprint == same.fingerprint
    assert identity.fingerprint == negative_zero.fingerprint
    assert identity.fingerprint != rotated.fingerprint
    assert identity.fingerprint != cropped.fingerprint


def test_geometry_arrays_are_cached_per_configuration():
    transform = FrameTransform.identity(8, 6).with_angle(10.0)

    assert transform.source_to_rotated_matrix is transform.source_to_rotated_matrix
    assert transform.rotated_to_source_matrix is transform.rotated_to_source_matrix
    assert transform.valid_mask is transform.valid_mask


def test_cached_geometry_arrays_are_read_only():
    transform = FrameTransform.identity(8, 6).with_angle(10.0)

    with pytest.raises(ValueError, match="read-only"):
        transform.source_to_rotated_matrix[0, 0] = 0.0
    with pytest.raises(ValueError, match="read-only"):
        transform.rotated_to_source_matrix[0, 0] = 0.0
    with pytest.raises(ValueError, match="read-only"):
        transform.valid_mask[0, 0] = True


def test_rotation_settings_store_tolerance_close_zero_canonically():
    rotation = RotationSettings(5e-10)
    transform = FrameTransform(640, 480, rotation, CropRect(0, 0, 640, 480))

    assert rotation.angle_deg == 0.0
    assert math.copysign(1.0, rotation.angle_deg) == 1.0
    assert transform.rotated_shape == (480, 640)
    assert transform.crop == CropRect(0, 0, 640, 480)
    assert transform.fingerprint == FrameTransform.identity(640, 480).fingerprint


def test_rotation_settings_store_tolerance_close_quarter_turn_canonically():
    rotation = RotationSettings(89.9999999995)
    transform = FrameTransform(3, 2, rotation, CropRect(0, 0, 2, 3))

    assert rotation.angle_deg == 90.0
    assert transform.rotated_shape == (3, 2)


def test_rotation_settings_store_tolerance_close_negative_half_turn_canonically():
    assert RotationSettings(-179.9999999995).angle_deg == -180.0


def test_cached_arrays_cannot_be_made_writeable_or_polluted():
    transform = FrameTransform.identity(8, 6).with_angle(10.0)
    arrays = (
        transform.source_to_rotated_matrix,
        transform.rotated_to_source_matrix,
        transform.valid_mask,
    )
    baselines = tuple(array.copy() for array in arrays)

    for array in arrays:
        try:
            with pytest.raises(ValueError):
                array.setflags(write=True)
        finally:
            array.setflags(write=False)

    same_configuration = FrameTransform.identity(8, 6).with_angle(10.0)
    cached_arrays = (
        same_configuration.source_to_rotated_matrix,
        same_configuration.rotated_to_source_matrix,
        same_configuration.valid_mask,
    )
    for original, cached, baseline in zip(arrays, cached_arrays, baselines):
        assert cached is original
        np.testing.assert_array_equal(cached, baseline)


@pytest.mark.parametrize(
    ("angle", "source_point", "rotated_point", "shape"),
    [
        (-90.0, (0.0, 0.0), (1.0, 0.0), (3, 2)),
        (-180.0, (0.0, 0.0), (2.0, 1.0), (2, 3)),
    ],
)
def test_negative_cardinal_turns_have_known_geometry(
    angle, source_point, rotated_point, shape
):
    transform = FrameTransform.identity(3, 2).with_angle(angle)

    assert transform.rotated_shape == shape
    assert transform.source_to_rotated(*source_point) == pytest.approx(rotated_point)


@pytest.mark.parametrize(("width", "height"), [(5, 3), (4, 2), (4, 3)])
def test_rotation_keeps_source_center_at_rotated_canvas_center(width, height):
    transform = FrameTransform.identity(width, height).with_angle(10.0)
    rotated_height, rotated_width = transform.rotated_shape

    assert transform.source_to_rotated(
        (width - 1) / 2.0,
        (height - 1) / 2.0,
    ) == pytest.approx(
        ((rotated_width - 1) / 2.0, (rotated_height - 1) / 2.0)
    )


def test_arbitrary_rotation_has_independent_canvas_shape_and_corner_envelope():
    transform = FrameTransform.identity(4, 3).with_angle(10.0)

    assert transform.rotated_shape == (4, 5)
    rotated_height, rotated_width = transform.rotated_shape
    for source_point in ((0.0, 0.0), (3.0, 0.0), (0.0, 2.0), (3.0, 2.0)):
        x, y = transform.source_to_rotated(*source_point)
        assert 0.0 <= x <= rotated_width - 1
        assert 0.0 <= y <= rotated_height - 1
