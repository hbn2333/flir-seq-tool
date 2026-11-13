"""Rendered workspace contracts: image scale, aspect, and rail separation."""
import numpy as np
import pytest


@pytest.mark.parametrize("size,dpi", [((10, 8), 100), ((10, 8), 150),
                                      ((10, 8), 200), ((25.6, 14.4), 100)])
def test_process_image_uses_left_workspace_and_preserves_pixels(make_measure_ui, size, dpi):
    ui = make_measure_ui(defer_color_scan=True)
    ui.image.set_data(np.zeros((480, 640)))
    ui.axes.set_xlim(-.5, 639.5)
    ui.axes.set_ylim(479.5, -.5)
    ui.figure.set_size_inches(*size)
    ui.figure.set_dpi(dpi)
    ui.figure.canvas.draw()
    bounds = ui.axes.get_window_extent()
    assert bounds.height >= .5 * ui.figure.bbox.height
    assert bounds.x1 < .70 * ui.figure.bbox.width
    assert bounds.width / bounds.height == pytest.approx(4 / 3)
    assert "\n" in ui.axes.get_title()
    assert ui.confirm_button.ax.get_window_extent().x0 > .75 * ui.figure.bbox.width
    before = ui.image.get_array().copy(), ui.image.get_clim()
    ui.pages.switch("measure")
    ui.figure.canvas.draw()
    assert ui.axes.get_position(original=True).bounds == pytest.approx((.075, .62, .46, .19))
    assert "\n" not in ui.axes.get_title()
    assert ui.status_text.get_position() == pytest.approx((.15, .102))
    assert ui.status_text.right_edge == .95
    ui.pages.switch("process")
    ui.figure.canvas.draw()
    assert ui.axes.get_window_extent().height == pytest.approx(bounds.height)
    assert "\n" in ui.axes.get_title()
    np.testing.assert_array_equal(ui.image.get_array(), before[0])
    assert ui.image.get_clim() == before[1]


@pytest.mark.parametrize("dpi", [100, 150, 200])
def test_colorbar_tracks_actual_image_on_resize_and_shape_change(make_measure_ui, dpi):
    ui = make_measure_ui(defer_color_scan=True)
    ui.figure.set_dpi(dpi)
    cax = ui.image.colorbar.ax
    for size in ((10, 8), (25.6, 14.4), (10, 8)):
        ui.figure.set_size_inches(*size)
        for height, width in ((480, 640), (640, 480), (480, 80), (8, 10)):
            data = np.arange(height * width).reshape(height, width)
            ui.image.set_data(data)
            ui.image.set_extent((-.5, width - .5, height - .5, -.5))
            ui.axes.set_xlim(-.5, width - .5)
            ui.axes.set_ylim(height - .5, -.5)
            limits = ui.image.get_clim()
            ui.figure.canvas.draw()
            box, bar = ui.axes.bbox, cax.bbox
            assert box.width / box.height == pytest.approx(width / height)
            assert bar.y0 == pytest.approx(box.y0)
            assert bar.height == pytest.approx(box.height)
            assert bar.x0 - box.x1 == pytest.approx(.012 * ui.figure.bbox.width)
            assert not bar.overlaps(ui.confirm_button.ax.bbox)
            np.testing.assert_array_equal(ui.image.get_array(), data)
            assert ui.image.get_clim() == limits


@pytest.mark.parametrize("dpi", [100, 150, 200])
def test_process_and_editor_rendered_decorations_clear_rail_and_status(make_measure_ui, dpi):
    ui = make_measure_ui(defer_color_scan=True)
    ui.figure.set_size_inches(10, 8)
    ui.figure.set_dpi(dpi)
    for editing in (False, True):
        if editing:
            ui.edit_transform_button._observers.process("clicked", None)
        for angle in ((0, 90, .1) if editing else (0,)):
            if editing:
                ui.transform_editor.set_angle(angle)
            ui.figure.canvas.draw()
            renderer = ui.figure.canvas.get_renderer()
            controls = ui.transform_editor.control_axes if editing else ui.normal_control_axes
            axes = (ui.axes, ui.image.colorbar.ax)
            for ax in axes:
                bounds = ax.get_tightbbox(renderer)
                for control in controls:
                    if control.get_visible():
                        assert not bounds.overlaps(control.get_tightbbox(renderer))
            title = ui.axes.title.get_window_extent(renderer)
            status = ui.color_scale_panel.status_text.get_window_extent(renderer)
            assert status.y0 - title.y1 >= 2 * dpi / 100
            xlabel = ui.axes.xaxis.label.get_window_extent(renderer)
            assert xlabel.y0 > ui.slider.ax.bbox.y1
            assert not ui.transform_status_text.get_window_extent(renderer).overlaps(ui.slider.ax.bbox)
        if editing:
            ui.transform_editor.cancel_button._observers.process("clicked", None)
            assert ui.range_slider.active


@pytest.mark.parametrize("size", [(10, 8), (25.6, 14.4)])
def test_process_footer_keeps_time_information_visible(make_measure_ui, monkeypatch, size):
    from conftest import SyntheticReader
    initialize = SyntheticReader.__init__
    def initialize_long_recording(reader, path):
        initialize(reader, path)
        reader.num_frames = 2934
    monkeypatch.setattr(SyntheticReader, "__init__", initialize_long_recording)
    ui = make_measure_ui(defer_color_scan=True)
    ui.figure.set_size_inches(*size)
    ui.slider.set_val(2)
    ui.figure.canvas.draw()
    displayed = ui.status_text.get_text()
    assert "Absolute " in displayed
    assert "Relative " in displayed
    assert "Duration " in displayed
    renderer = ui.figure.canvas.get_renderer()
    box = ui.status_text.get_window_extent(renderer)
    assert box.y0 >= ui.figure.bbox.y0
    assert not box.overlaps(ui.transform_status_text.get_window_extent(renderer))
    assert not box.overlaps(ui.range_slider.ax.bbox)
