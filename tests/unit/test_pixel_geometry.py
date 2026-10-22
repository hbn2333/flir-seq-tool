import pytest

from flir_seq_tool.pixel_geometry import rect_from_extents
from flir_seq_tool.transform import CropRect


@pytest.mark.parametrize("extents,expected", [
    ((.5, 3.5, .5, 2.5), CropRect(1, 1, 3, 2)),
    ((-.5, .5, -.5, .5), CropRect(0, 0, 1, 1)),
    ((3.5, .5, 2.5, .5), CropRect(1, 1, 3, 2)),
    ((1. + 1e-12, 3. - 1e-12, 1., 2.), CropRect(1, 1, 3, 2)),
])
def test_extents_to_integer_pixel_rect(extents, expected):
    assert rect_from_extents(extents) == expected


@pytest.mark.parametrize("extents", [(1, 1, 0, 2), (0, 2, 1, 1), (0, float("nan"), 0, 2),
                                    (0, float("inf"), 0, 2), (1, 2, 3)])
def test_invalid_extents_rejected(extents):
    with pytest.raises(ValueError):
        rect_from_extents(extents)
