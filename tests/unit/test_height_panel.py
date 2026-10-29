from dataclasses import replace
from datetime import datetime, timedelta
from threading import Event
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from matplotlib.backend_bases import MouseEvent

from flir_seq_tool.measurement_models import MeasurementSettings, Roi
from flir_seq_tool.height_models import HeightSettings, row_profile
from flir_seq_tool.height_panel import sample_zone_curves


def click(ui, widget):
    ui.figure.canvas.draw()
    x, y = widget.ax.transAxes.transform((.5, .5))
    for event in ('button_press_event', 'button_release_event'):
        ui.figure.canvas.callbacks.process(event, MouseEvent(event, ui.figure.canvas, x, y, button=1))


def height_ui(make_measure_ui, two=False):
    ui = make_measure_ui()
    rois = (Roi('a', 'Sample A', 0, 0, 2, 6, '#123456', True),)
    if two:
        rois += (Roi('b', 'Sample B', 2, 0, 2, 6, '#654321', True),)
    ui.session.measurement.settings = MeasurementSettings(rois, 'a')
    ui.pages.switch('measure')
    click(ui, ui.measure_panel.height_button)
    return ui


def test_height_mode_button_draws_checked_profiles_from_heated_edge(make_measure_ui):
    ui = height_ui(make_measure_ui, two=True)
    panel = ui.measure_panel
    assert panel.state.settings.result_view == 'height'
    assert panel.state.settings.height_unit == 'px'
    lines = panel.result_axes.lines
    assert len(lines) == 2
    np.testing.assert_allclose(lines[0].get_xdata(), np.arange(6) + .5)
    np.testing.assert_allclose(lines[0].get_ydata(), [40.5, 32.5, 24.5, 16.5, 8.5, .5])
    click(ui, panel.height_panel.top_button)
    np.testing.assert_allclose(panel.result_axes.lines[0].get_ydata(), [.5, 8.5, 16.5, 24.5, 32.5, 40.5])
    assert panel.state.settings.height_for('a').heated_edge == 'top'


def test_calibration_uses_roi_height_and_geometry_preserves_ratio(make_measure_ui):
    ui = height_ui(make_measure_ui)
    panel, config = ui.measure_panel, ui.measure_panel.height_panel
    config.height_mm_box.set_val('12')
    assert panel.state.settings.height_for('a').mm_per_pixel == 2
    panel.geometry_boxes['height'].set_val('3')
    assert panel.state.settings.height_for('a').mm_per_pixel == 2
    assert float(config.height_mm_box.text) == 6
    before = panel.state.settings
    config.height_mm_box.set_val('nan')
    assert panel.state.settings == before
    assert 'finite' in panel.error_text.get_text()


def test_zones_checkbox_reveals_real_controls_and_validates_geometry(make_measure_ui):
    ui = height_ui(make_measure_ui)
    panel, config = ui.measure_panel, ui.measure_panel.height_panel
    assert not panel.state.settings.height_for('a').zones_enabled
    assert not config.cut_boxes[0].active
    click(ui, config.zones_check)
    assert panel.state.settings.height_for('a').zones_enabled
    assert all(box.active and box.ax.get_visible() for box in config.cut_boxes)
    config.zone_name_boxes[0].set_val('Hot end')
    assert panel.state.settings.height_for('a').zone_names[0] == 'Hot end'
    click(ui, config.pair_a_button)
    assert panel.state.settings.height_for('a').delta_pair == ('middle', 'high')
    click(ui, config.pair_b_button)
    assert panel.state.settings.height_for('a').delta_pair == ('middle', 'low')
    before = panel.state.settings
    config.cut_boxes[0].set_val('5.99')
    assert panel.state.settings == before
    panel.geometry_boxes['height'].set_val('2')
    assert not panel.state.settings.height_for('a').zones_enabled
    assert 'disabled' in panel.error_text.get_text()


def test_mm_comparison_requires_all_checked_rois_calibrated(make_measure_ui):
    ui = height_ui(make_measure_ui, two=True)
    panel, config = ui.measure_panel, ui.measure_panel.height_panel
    config.height_mm_box.set_val('12')
    assert not config.mm_button.active
    assert 'Sample B' in config.note.get_text()
    panel.set_checked('b', False)
    assert config.mm_button.active
    click(ui, config.mm_button)
    assert panel.state.settings.height_unit == 'mm'
    np.testing.assert_allclose(panel.result_axes.lines[0].get_xdata(), np.arange(6) * 2 + 1)
    panel.set_checked('b', True)
    assert panel.state.settings.height_unit == 'px'
    assert not config.mm_button.active


def test_csv_buttons_are_independent_of_graph_checked_and_subview(make_measure_ui):
    ui = height_ui(make_measure_ui)
    panel, config = ui.measure_panel, ui.measure_panel.height_panel
    requested = []
    panel.on_export_height = requested.append
    panel.can_export_height = lambda kind: True
    panel.set_checked('a', False)
    panel._sync_analysis_controls()
    click(ui, config.export_height_button)
    assert requested == ['height']
    assert not config.export_zones_button.active
    click(ui, config.zones_check)
    click(ui, config.zones_button)
    click(ui, config.export_zones_button)
    assert requested == ['height', 'zones']
    assert config.export_height_button.ax.get_visible()


def test_height_controls_disable_when_hidden_busy_or_disposed(make_measure_ui):
    ui = height_ui(make_measure_ui)
    panel, config = ui.measure_panel, ui.measure_panel.height_panel
    click(ui, config.zones_check)
    panel.set_busy(True)
    before = panel.state.settings
    config.height_mm_box.set_val('12')
    assert panel.state.settings == before
    assert not config.zones_check.active
    assert not panel.selector.active
    panel.set_busy(False)
    click(ui, panel.current_button)
    assert all(not widget.active for widget in config.widgets)
    ui.pages.switch('process')
    panel.dispose()
    panel.dispose()
    assert not config._timer.callbacks
    assert not panel._drag_timer.callbacks


def test_height_hides_legacy_png_and_loaded_mm_view_falls_back_to_px(make_measure_ui):
    ui = make_measure_ui()
    roi = Roi('a', 'A', 0, 0, 2, 6, '#123456', True)
    ui.session.measurement.settings = MeasurementSettings((roi,), 'a', result_view='height', height_unit='mm')
    ui.pages.switch('measure')
    panel = ui.measure_panel
    assert panel.state.settings.height_unit == 'px'
    assert not panel.export_png_button.ax.get_visible()
    assert not panel.export_png_button.active


def test_unavailable_zone_preview_cancels_pending_derivation(make_measure_ui):
    ui = height_ui(make_measure_ui)
    panel, config = ui.measure_panel, ui.measure_panel.height_panel
    click(ui, config.zones_check)
    click(ui, config.zones_button)
    pending = config._cancel = Event()
    panel.set_checked('a', False)
    assert pending.is_set()


def test_long_names_remain_inside_height_preview(make_measure_ui):
    ui = height_ui(make_measure_ui)
    panel, config = ui.measure_panel, ui.measure_panel.height_panel
    panel.name_box.set_val('A very long sample name ' * 12)
    ui.figure.set_size_inches(10, 8)
    ui.figure.canvas.draw()
    renderer = ui.figure.canvas.get_renderer()
    legend = panel.result_axes.get_legend().get_window_extent(renderer)
    assert ui.figure.bbox.contains(legend.x0, legend.y0)
    assert ui.figure.bbox.contains(legend.x1, legend.y1)
    for widget in config.widgets:
        if widget.ax.get_visible():
            assert not legend.overlaps(widget.ax.get_window_extent(renderer))


def test_cut_positions_use_px_or_mm_and_image_boundaries_follow_heated_edge(make_measure_ui):
    ui = height_ui(make_measure_ui)
    panel, config = ui.measure_panel, ui.measure_panel.height_panel
    click(ui, config.zones_check)
    assert [float(b.text) for b in config.cut_boxes] == [2, 4]
    assert 'px' in config.cut_boxes[0].label.get_text()
    config.cut_boxes[0].set_val('3')
    assert panel.state.settings.height_for('a').cuts[0] == .5
    np.testing.assert_allclose([line.get_ydata()[0] for line in config.zone_lines], [2.5, 1.5])
    config.height_mm_box.set_val('12')
    click(ui, config.mm_button)
    assert [float(b.text) for b in config.cut_boxes] == [6, 8]
    assert 'mm' in config.cut_boxes[0].label.get_text()
    config.cut_boxes[0].set_val('5')
    assert panel.state.settings.height_for('a').cuts[0] == pytest.approx(5 / 12)
    click(ui, config.top_button)
    np.testing.assert_allclose([line.get_ydata()[0] for line in config.zone_lines], [2, 3.5])


def test_invalid_reenable_keeps_zone_settings_editable_to_repair_cuts(make_measure_ui):
    ui = height_ui(make_measure_ui)
    panel, config = ui.measure_panel, ui.measure_panel.height_panel
    click(ui, config.zones_check)
    config.cut_boxes[0].set_val('.6')
    panel.geometry_boxes['height'].set_val('3')
    assert not panel.state.settings.height_for('a').zones_enabled
    click(ui, config.zones_check)
    assert not panel.state.settings.height_for('a').zones_enabled
    assert config.cut_boxes[0].active
    config.cut_boxes[0].set_val('1')
    click(ui, config.zones_check)
    assert panel.state.settings.height_for('a').zones_enabled


def test_heat_arrow_and_caption_follow_the_active_heated_edge(make_measure_ui):
    ui = height_ui(make_measure_ui)
    config = ui.measure_panel.height_panel
    assert config.height_arrow.xy[1] < config.height_arrow.xyann[1]
    assert 'bottom' in config.direction_note.get_text()
    click(ui, config.top_button)
    assert config.height_arrow.xy[1] > config.height_arrow.xyann[1]
    assert 'top' in config.direction_note.get_text()


def test_page_hiding_only_stops_textboxes_that_are_typing(make_measure_ui, monkeypatch):
    ui = height_ui(make_measure_ui)
    panel = ui.measure_panel
    for widget in panel.widgets:
        if hasattr(widget, 'stop_typing'):
            original = widget.stop_typing
            def guarded(widget=widget, original=original):
                assert widget.capturekeystrokes, 'Idle TextBox.stop_typing redraws the entire figure'
                return original()
            monkeypatch.setattr(widget, 'stop_typing', guarded)
    panel.set_visible(False)
    panel.dispose()


def test_height_profile_explains_all_invalid_temperatures(make_measure_ui):
    from flir_seq_tool.models import TemperatureFrame
    ui = height_ui(make_measure_ui)
    panel = ui.measure_panel
    frame = ui.session.frame_at_local(0)
    panel.refresh_frame(TemperatureFrame(frame.frame_index, frame.timestamp, np.full((6, 8), np.nan)))
    assert any('No finite temperatures' in text.get_text() for text in panel.result_axes.texts)


@pytest.mark.parametrize('upper_has_values', [False, True])
def test_zone_preview_reports_missing_temperatures_and_delta_independently(make_measure_ui, upper_has_values):
    ui = height_ui(make_measure_ui)
    panel, config = ui.measure_panel, ui.measure_panel.height_panel
    settings = panel.state.settings.with_height(HeightSettings('a', zones_enabled=True))
    panel.state.settings = replace(settings, height_view='zones')
    # The render path consumes the already bounded worker output only.
    panel.state.result_store = SimpleNamespace(has_height=True)
    panel.state.status = 'complete'
    missing = np.array([[0., np.nan], [1., np.nan]])
    valid = np.array([[0., 20.], [1., 30.]])
    channels = (valid if upper_has_values else missing, missing, missing, missing)
    config._data_key = config._key()
    config._data = (((settings.rois[0], settings.height_for('a'), channels),), False, False)
    try:
        panel._plot_key = None
        panel._render_analysis()
        assert any('No finite' in text.get_text() for text in panel.delta_axes.texts)
        if not upper_has_values:
            assert any('No finite temperatures' in text.get_text() for text in panel.result_axes.texts)
        else:
            assert panel.result_axes.axison
    finally:
        panel.state.result_store = None
        panel.state.status = 'empty'


def test_height_calculation_and_zone_preview_use_background_store_and_actual_seconds(make_measure_ui, tmp_path, monkeypatch):
    from flir_seq_tool.height_job import HeightAnalysisJob
    from flir_seq_tool.height_store import HeightResultStore
    from flir_seq_tool.models import TemperatureFrame
    jobs = []
    class Reader:
        width, height, num_frames = 8, 6, 5
        def __init__(self, path):
            self.path = path
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def timestamp_at(self, index):
            return datetime(2026, 10, 22) + timedelta(seconds=[0, .02, .02, -.01, .08][index])
        def temperature_frame_at(self, index):
            return TemperatureFrame(index, self.timestamp_at(index), np.arange(48.).reshape(6, 8) + 10 * index)
    def factory(request):
        job = HeightAnalysisJob(request, reader_factory=Reader, temp_root=tmp_path)
        jobs.append(job)
        return job
    ui = make_measure_ui(height_job_factory=factory)
    roi = Roi('a', 'A', 0, 0, 2, 6, '#123456', True)
    ui.session.measurement.settings = MeasurementSettings((roi,), 'a', result_view='height')
    ui.pages.switch('measure')
    panel, config = ui.measure_panel, ui.measure_panel.height_panel
    click(ui, config.zones_check)
    click(ui, panel.calculate_button)
    jobs[0].join(5)
    assert jobs[0].outcome.status == 'complete'
    ui.poll_analysis()
    main_thread = threading.get_ident()
    threads = []
    original = HeightResultStore.open_readonly
    def checked_open(cls, path, **kwargs):
        threads.append(threading.get_ident())
        assert threads[-1] != main_thread
        assert 'cancel_event' in kwargs
        return original(path, **kwargs)
    monkeypatch.setattr(HeightResultStore, 'open_readonly', classmethod(checked_open))
    click(ui, config.zones_button)
    config._worker.join(5)
    assert not config._worker.is_alive()
    config.poll()
    assert threads
    assert len(panel.result_axes.lines) == 3
    assert len(panel.delta_axes.lines) == 1
    x = panel.result_axes.lines[0].get_xdata()
    assert -.01 in x and .02 in x
    assert np.isnan(x).any()
    assert 'timestamps' in config.note.get_text()
    previous_reads = list(ui.session.reader.requested_frames)
    config.height_mm_box.set_val('12')
    click(ui, config.top_button)
    config._worker.join(5)
    config.poll()
    assert len(jobs) == 1
    assert ui.session.reader.requested_frames == previous_reads
    assert panel.state.status == 'complete'
    path = tmp_path / 'height-zones.png'
    ui.figure.savefig(path)
    print(f'HEIGHT_PREVIEW={path}')
    panel.dispose()
    assert not config._timer.callbacks


def test_bounded_zone_sampler_preserves_weighting_extrema_and_time_breaks():
    roi = Roi('a', 'A', 0, 0, 2, 6, '#123456', True)
    settings = MeasurementSettings((roi,), 'a').with_height(HeightSettings('a', zones_enabled=True))
    class Store:
        def height_series(self, roi_id):
            for i in range(1000):
                values = np.full((6, 2), 20.)
                values[4:] = [[np.nan, 80.], [20., 20.]]
                if i == 432:
                    values[4:] = 200
                yield dict(local_frame=i, relative_s=-1 if i == 532 else i * .02,
                           time_status='backwards' if i == 532 else 'ok'), row_profile(values, roi)
    rows, reduced, bad_time = sample_zone_curves(Store(), settings, 1000, Event(), buckets=10)
    assert reduced and bad_time
    low = rows[0][2][0]
    assert len(low) <= 90
    assert np.nanmax(low[:, 1]) == 200
    assert 40 in low[:, 1]  # finite-pixel weighting: (80+20+20)/3
    assert -1 in low[:, 0]
    idx = np.where(low[:, 0] == -1)[0][0]
    assert np.isnan(low[idx - 1, 0]) and np.isnan(low[idx + 1, 0])


@pytest.mark.parametrize('dpi', [100, 150, 200])
@pytest.mark.parametrize('size', [(10, 8), (16, 10)])
def test_height_expanded_layout_extents(make_measure_ui, tmp_path, dpi, size):
    ui = height_ui(make_measure_ui)
    panel, config = ui.measure_panel, ui.measure_panel.height_panel
    ui.figure.set_dpi(dpi)
    ui.figure.set_size_inches(*size)
    click(ui, config.zones_check)
    ui.figure.canvas.draw()
    renderer = ui.figure.canvas.get_renderer()
    controls = [w.ax for w in panel.widgets if w.ax.get_visible()]
    for text in (config.note, panel.result_axes.xaxis.label, panel.result_axes.yaxis.label,
                 *(box.label for box in (config.height_mm_box, *config.zone_name_boxes, *config.cut_boxes))):
        box = text.get_window_extent(renderer)
        assert ui.figure.bbox.contains(box.x0, box.y0), text.get_text()
        assert ui.figure.bbox.contains(box.x1, box.y1), text.get_text()
        for ax in controls:
            assert not box.overlaps(ax.get_window_extent(renderer)), text.get_text()
    for widget in config.widgets:
        if widget.ax.get_visible():
            assert not panel.result_axes.get_tightbbox(renderer).overlaps(widget.ax.get_window_extent(renderer))
    if dpi == 100 and size == (10, 8):
        path = tmp_path / 'height-expanded.png'
        ui.figure.savefig(path)
        print(f'HEIGHT_PREVIEW={path}')
