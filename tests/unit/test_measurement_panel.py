from dataclasses import replace

from matplotlib.backend_bases import MouseEvent, KeyEvent
import pytest

from flir_seq_tool.measurement_models import MeasurementSettings, Roi
from flir_seq_tool.models import TemperatureFrame
import numpy as np


def send_mouse(ui, name, x, y):
    px, py = ui.axes.transData.transform((x, y))
    ui.figure.canvas.callbacks.process(name, MouseEvent(name, ui.figure.canvas, px, py, button=1))


def click_button(ui, button):
    ui.figure.canvas.draw()
    px, py = button.ax.transAxes.transform((.5, .5))
    for kind in ("button_press_event", "button_release_event"):
        ui.figure.canvas.callbacks.process(kind, MouseEvent(kind, ui.figure.canvas, px, py, button=1))


def draw(ui, start=(.5, .5), end=(3.5, 2.5)):
    ui.figure.canvas.draw()
    send_mouse(ui, "button_press_event", *start)
    send_mouse(ui, "motion_notify_event", *end)
    send_mouse(ui, "button_release_event", *end)


def test_draw_roi_in_measure_coordinates_from_real_add_button(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    click_button(ui, ui.measure_panel.add_button)
    draw(ui)
    roi = ui.session.measurement.settings.rois[0]
    assert (roi.x, roi.y, roi.width, roi.height) == (1, 1, 3, 2)
    assert ui.session.measurement.settings.active_roi_id == roi.id
    assert not ui.measure_panel.adding
    assert ui.measure_panel.selector.active
    shown = []
    ui.measure_panel.dialogs.show_details = shown.append
    for name in (roi.name, "New long sample name " * 8):
        ui.measure_panel.name_box.set_val(name)
        ui.figure.canvas.callbacks.process("key_press_event", KeyEvent("key_press_event", ui.figure.canvas, key="f1"))
        assert name.strip() in shown[-1]


def test_empty_click_and_escape_do_not_create_roi(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    ui.measure_panel.begin_add()
    draw(ui, (1, 1), (1, 1))
    assert not ui.session.measurement.settings.rois
    ui.figure.canvas.callbacks.process("key_press_event", KeyEvent("key_press_event", ui.figure.canvas, key="escape"))
    assert not ui.measure_panel.adding


def test_numeric_invalid_geometry_preserves_previous_roi(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    ui.measure_panel.begin_add()
    draw(ui)
    before = ui.session.measurement.settings
    ui.measure_panel.geometry_boxes["width"].set_val("999")
    assert ui.session.measurement.settings == before
    assert "bounds" in ui.measure_panel.error_text.get_text()


def test_multiple_rois_rename_checked_and_pagination_do_not_invalidate_results(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    panel = ui.measure_panel
    for _ in range(5):
        panel.begin_add()
        draw(ui)
    assert len(ui.session.measurement.settings.rois) == 5
    assert panel.page_index == 1
    assert len(panel.selector.artists) > 0
    last = ui.session.measurement.settings.rois[-1]
    ui.session.measurement.status = "complete"
    panel.name_box.set_val("Renamed")
    panel.set_checked(last.id, False)
    assert ui.session.measurement.status == "complete"
    updated = ui.session.measurement.settings.rois[-1]
    assert updated.name == "Renamed" and not updated.checked
    panel.name_box.set_val("ROI 1")
    assert ui.session.measurement.settings.rois[-1] == updated
    panel.select(ui.session.measurement.settings.rois[0].id)
    assert panel.page_index == 0


def test_delete_confirms_and_selects_neighbor(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    panel = ui.measure_panel
    for _ in range(2):
        panel.begin_add()
        draw(ui)
    panel.dialogs.answer = False
    click_button(ui, panel.delete_button)
    assert len(ui.session.measurement.settings.rois) == 2
    panel.dialogs.answer = True
    click_button(ui, panel.delete_button)
    assert len(ui.session.measurement.settings.rois) == 1
    assert ui.session.measurement.settings.active_roi_id == ui.session.measurement.settings.rois[0].id
    click_button(ui, panel.delete_button)
    assert not ui.session.measurement.settings.rois
    assert not panel.geometry_boxes["x"].active


def test_page_and_toolbar_disable_roi_creation_and_restore(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    panel = ui.measure_panel
    panel.begin_add()
    ui.axes.set_navigate_mode("PAN")
    draw(ui)
    assert not ui.session.measurement.settings.rois
    ui.axes.set_navigate_mode(None)
    draw(ui)
    assert len(ui.session.measurement.settings.rois) == 1
    ui.pages.switch("process")
    assert not panel.selector.active
    assert not panel.add_button.active
    before = ui.session.measurement.settings
    panel.begin_add()
    assert ui.session.measurement.settings == before


def test_repeated_page_switch_preserves_checked_model_and_visible_checkbox(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    panel = ui.measure_panel
    for _ in range(2):
        panel.begin_add()
        draw(ui)
    first, second = ui.session.measurement.settings.rois
    panel.set_checked(first.id, False)
    before = ui.session.measurement.settings
    for _ in range(5):
        ui.pages.switch("process")
        ui.pages.switch("measure")
    assert ui.session.measurement.settings == before
    assert panel.row_checks[0].get_status() == [False]
    assert panel.row_checks[1].get_status() == [True]


def test_single_pixel_roi_and_drag_selected_geometry(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    panel = ui.measure_panel
    panel.begin_add()
    # Use an interior pixel: MouseEvent rounds screen positions to integers,
    # which can place an exact Axes-border press outside the image Axes.
    draw(ui, (.5, .5), (1.5, 1.5))
    roi = ui.session.measurement.settings.rois[0]
    assert (roi.x, roi.y, roi.width, roi.height) == (1, 1, 1, 1)
    panel.begin_add()
    draw(ui)
    ui.session.measurement.status = "complete"
    draw(ui, (2., 1.5), (3., 2.5))
    roi = ui.session.measurement.settings.rois[-1]
    assert (roi.x, roi.y, roi.width, roi.height) == (2, 2, 3, 2)
    assert ui.session.measurement.status == "stale"


def test_current_analysis_reads_one_frame_and_has_correct_profile(make_measure_ui):
    ui = make_measure_ui()
    roi = Roi("r1", "ROI 1", 0, 0, 2, 2, "#123456", True)
    ui.session.measurement.settings = MeasurementSettings((roi,), "r1")
    ui.pages.switch("measure")
    panel = ui.measure_panel
    panel.refresh_frame(ui.session.frame_at_local(0))
    current = panel.current_analysis
    assert current.stats.mean_c == pytest.approx(4.5)
    np.testing.assert_allclose(current.horizontal.means_c, [4, 5])
    np.testing.assert_allclose(current.vertical.means_c, [.5, 8.5])
    assert ui.session.reader.requested_frames.count(0) == 1
    assert "4.50" in panel.stats_text.get_text()
    panel.refresh_frame(ui.session.frame_at_local(0))
    assert panel.current_analysis is current
    ui.slider.set_val(1)
    assert panel.current_analysis.source_frame == 1
    assert panel.current_analysis.stats.mean_c == pytest.approx(14.5)


def test_missing_current_analysis_and_subview_controls(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    panel = ui.measure_panel
    assert panel.current_analysis is None
    assert "Add" in panel.stats_text.get_text()
    panel.begin_add()
    draw(ui)
    frame = ui.session.frame_at_local(0)
    panel.refresh_frame(TemperatureFrame(10, frame.timestamp, np.full((6, 8), np.nan)))
    assert panel.current_analysis.stats.mean_c is None
    assert "N/A" in panel.stats_text.get_text()
    click_button(ui, panel.profile_button)
    assert ui.session.measurement.settings.frame_view == "profile"
    click_button(ui, panel.vertical_button)
    assert ui.session.measurement.settings.profile_direction == "vertical"
    assert "downward" in panel.result_axes.get_xlabel()
    ui.pages.switch("process")
    assert not panel.profile_button.active
    assert not panel.stats_text.get_visible()


def test_drag_coalesces_current_analysis_and_release_commits_final_position(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    panel = ui.measure_panel
    panel.begin_add()
    draw(ui)
    before = panel.current_analysis
    send_mouse(ui, "button_press_event", 2, 1.5)
    for x in np.linspace(2, 3, 20):
        send_mouse(ui, "motion_notify_event", x, 2.5)
    assert panel.current_analysis is before
    panel.flush_drag()
    assert panel.current_analysis is not before
    send_mouse(ui, "button_release_event", 3, 2.5)
    roi = panel.selected
    block = ui.session.frame_at_local(0).temperature_c[roi.y:roi.y+roi.height, roi.x:roi.x+roi.width]
    assert panel.current_analysis.stats.mean_c == pytest.approx(block.mean())
    assert panel.geometry_boxes["x"].text == str(roi.x)
    assert panel.geometry_boxes["y"].text == str(roi.y)
    assert not panel.drag_pending
    ui.pages.switch("process")
    panel.flush_drag()
    assert not panel.drag_pending


def test_render_measure_preview(make_measure_ui, tmp_path):
    ui = make_measure_ui()
    first = Roi("r1", "Sample left", 1, 1, 3, 3, "#1f77b4", True)
    second = Roi("r2", "Sample right", 4, 2, 2, 3, "#ff7f0e", True)
    ui.session.measurement.settings = MeasurementSettings((first, second), "r1")
    ui.figure.set_size_inches(10, 8)
    ui.pages.switch("measure")
    for view, direction in (("distribution", "horizontal"), ("profile", "vertical")):
        ui.measure_panel._set_view("frame_view", view)
        ui.measure_panel._set_view("profile_direction", direction)
        ui.figure.canvas.draw()
        path = tmp_path / f"measure-{view}.png"
        ui.figure.savefig(path)
        assert path.stat().st_size > 0
        print(f"MEASURE_PREVIEW={path}")


@pytest.mark.parametrize("dpi", [100, 150, 200])
@pytest.mark.parametrize("size", [(10, 8), (16, 10)])
@pytest.mark.parametrize("view", ["distribution", "horizontal", "vertical", "curves"])
def test_measure_all_subview_layouts_use_real_text_extents(make_measure_ui, tmp_path, dpi, size, view):
    ui = make_measure_ui()
    roi = Roi("r1", "A long sample name " * 8, 1, 1, 3, 3, "#1f77b4", True)
    ui.session.measurement.settings = MeasurementSettings((roi,), "r1",
        result_view="curves" if view == "curves" else "current",
        frame_view="distribution" if view == "distribution" else "profile",
        profile_direction="vertical" if view == "vertical" else "horizontal")
    ui.figure.set_dpi(dpi)
    ui.figure.set_size_inches(*size)
    ui.pages.switch("measure")
    ui.figure.canvas.draw()
    renderer = ui.figure.canvas.get_renderer()
    panel = ui.measure_panel
    controls = [ax for ax in (*panel.control_axes, ui.current_frame_box.ax) if ax.get_visible()]
    text_artists = [panel.stats_text, panel.analysis_note, ui.axes.title, ui.current_frame_box.label,
                    panel.result_axes.xaxis.label, panel.result_axes.yaxis.label]
    text_artists.extend(box.label for box in (panel.name_box, *panel.geometry_boxes.values()))
    if view == "curves":
        text_artists.extend((panel.delta_axes.xaxis.label, panel.delta_axes.yaxis.label))
    for text in text_artists:
        if not text.get_visible() or not text.get_text():
            continue
        box = text.get_window_extent(renderer)
        assert ui.figure.bbox.contains(box.x0, box.y0), text.get_text()
        assert ui.figure.bbox.contains(box.x1, box.y1), text.get_text()
        for ax in controls:
            assert not box.overlaps(ax.get_window_extent(renderer)), text.get_text()
    for output in panel.output_controls:
        for result_ax in (panel.result_axes, panel.delta_axes):
            if result_ax.get_visible():
                assert not result_ax.get_tightbbox(renderer).overlaps(output.ax.get_window_extent(renderer))
    if dpi == 100 and size == (10, 8) or dpi == 200 and size == (16, 10):
        path = tmp_path / f"measure-final-{view}-{size[0]}x{size[1]}-{dpi}.png"
        ui.figure.savefig(path)
        print(f"MEASURE_PREVIEW={path}")


def test_measure_text_stays_outside_controls_with_long_name_and_invalid_pixels(make_measure_ui, tmp_path):
    ui = make_measure_ui()
    roi = Roi("r1", "Long sample name " * 8, 1, 1, 3, 3, "#1f77b4", True)
    ui.session.measurement.settings = MeasurementSettings((roi,), "r1", frame_view="profile")
    ui.figure.set_size_inches(10, 8)
    ui.pages.switch("measure")
    panel = ui.measure_panel
    frame = ui.session.frame_at_local(0)
    values = frame.temperature_c.copy()
    values[1, 1] = np.nan
    panel.resampled = True
    panel.refresh_frame(TemperatureFrame(1, frame.timestamp, values))
    ui.figure.canvas.draw()
    renderer = ui.figure.canvas.get_renderer()
    controls = [ax for ax in panel.control_axes if ax.get_visible()]
    for text in (panel.stats_text, panel.analysis_note, ui.axes.title):
        box = text.get_window_extent(renderer)
        assert ui.figure.bbox.contains(box.x0, box.y0)
        assert ui.figure.bbox.contains(box.x1, box.y1)
        for ax in controls:
            assert not box.overlaps(ax.get_window_extent(renderer)), text.get_text()
    colorbar_ax = next(ax for ax in ui.figure.axes if ax.get_ylabel() == "Temperature (°C)")
    for tick in ui.axes.get_xticklabels():
        assert not panel.analysis_note.get_window_extent(renderer).overlaps(tick.get_window_extent(renderer))
    for ax in controls:
        assert not colorbar_ax.yaxis.label.get_window_extent(renderer).overlaps(ax.get_window_extent(renderer))
    path = tmp_path / "measure-long-invalid.png"
    ui.figure.savefig(path)
    print(f"MEASURE_PREVIEW={path}")
