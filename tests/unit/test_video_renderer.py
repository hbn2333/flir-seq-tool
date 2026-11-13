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


def test_per_frame_limits_and_shared_colorbar(monkeypatch):
    display = DisplaySettings('magma', 10, 80, scale_mode='per_frame')
    with VideoRenderer(display, speed=10) as renderer:
        assert renderer.display == display
        assert renderer.last_valid_limits is None
        calls = []
        original = renderer.image.set_clim
        def record(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)
        monkeypatch.setattr(renderer.image, 'set_clim', record)
        for values, expected in [([[22, 30]], (22, 30)), ([[23, 50]], (23, 50)),
                                 ([[23, 50]], (23, 50)), ([[np.nan, np.inf]], (23, 50))]:
            renderer.render(np.array(values), 0)
            assert renderer.image.get_clim() == expected
            assert renderer.colorbar.mappable.norm is renderer.image.norm
            assert renderer.colorbar.ax.get_ylim() == expected
        assert len(calls) == 2
        assert renderer.status_text.get_text() == 'No finite temperatures'
        renderer.render(np.array([[25., 25.]]), 1)
        assert renderer.image.get_clim() == (24.5, 25.5)
        assert renderer.status_text.get_text() == ''


@pytest.mark.parametrize('mode,source,label', [('per_frame', 'auto', 'Per-frame adaptive'),
    ('fixed', 'auto', 'Fixed automatic'), ('fixed', 'manual', 'Fixed manual')])
@pytest.mark.parametrize('shape', [(100, 200), (100, 100), (200, 100), (86, 145), (480, 640)])
def test_invalid_fallback_labels_and_actual_video_bounds(mode, source, label, shape):
    from matplotlib.transforms import Bbox
    with VideoRenderer(DisplaySettings('magma', 20, 40, mode, source), 2) as renderer:
        renderer.render(np.full(shape, np.nan), 0)
        assert renderer.image.get_clim() == ((0, 1) if mode == 'per_frame' else (20, 40))
        assert renderer.mode_text.get_text() == label
        assert renderer.status_text.get_text() == 'No finite temperatures'
        draw = renderer.canvas.get_renderer()
        artists = [renderer.mode_text, renderer.status_text]
        occupied = [renderer.axes.get_window_extent(), renderer.time_text.get_window_extent(draw),
                    renderer.colorbar.ax.get_tightbbox(draw)]
        boxes = [artist.get_window_extent(draw) for artist in artists]
        canvas = Bbox.from_bounds(0, 0, 1920, 1080)
        for box in boxes:
            assert canvas.contains(box.x0, box.y0) and canvas.contains(box.x1, box.y1)
            assert all(not box.overlaps(other) for other in occupied)
        assert not boxes[0].overlaps(boxes[1])
        renderer.render(np.array([[22., 30.], [np.nan, np.inf]]), 1)
        assert renderer.status_text.get_text() == ''
        renderer.render(np.array([[23., 50.], [np.nan, -np.inf]]), 2)
        assert renderer.image.get_clim() == ((23, 50) if mode == 'per_frame' else (20, 40))
