import numpy as np
import pytest

from flir_seq_tool.selection import DisplaySettings
from flir_seq_tool.video_renderer import VideoRenderer


@pytest.mark.parametrize('shape', [(100, 200), (200, 100), (100, 100), (20, 500)])
def test_rgb_aspect_fixed_limits_and_no_input_changes(shape):
    display = DisplaySettings(colormap='magma', vmin_c=20, vmax_c=40)
    values = np.full(shape, 30.0)
    before = values.copy()
    with VideoRenderer(display, speed=2) as renderer:
        rgb = renderer.render(values, relative_s=12.34)
        assert rgb.shape == (1080, 1920, 3) and rgb.dtype == np.uint8
        assert rgb.flags.c_contiguous
        assert renderer.image.get_clim() == (20, 40)
        assert renderer.time_text.get_text() == 't=12.34s   2×'
        bounds = renderer.image.axes.get_window_extent()
        assert bounds.width / bounds.height == pytest.approx(shape[1] / shape[0])
        assert not bounds.overlaps(renderer.colorbar.ax.get_window_extent())
        renderer.render(np.full(shape, np.nan), relative_s=20)
        assert renderer.image.get_clim() == (20, 40)
    np.testing.assert_array_equal(values, before)
