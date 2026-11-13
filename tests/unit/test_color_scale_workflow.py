import pytest

from flir_seq_tool.color_scale import ColorLimits
from flir_seq_tool.color_scale_job import ColorScaleOutcome
from flir_seq_tool.measurement_job import AnalysisProgress


class ControlledColorJob:
    def __init__(self, request):
        self.request = request
        self.started = self.alive = self.cancelled = False
        self.progress = AnalysisProgress()
        self.outcome = None
    def start(self):
        self.started = self.alive = True
    def cancel(self):
        self.cancelled = True
    def is_alive(self):
        return self.alive
    def join(self, timeout=None):
        self.alive = False
    def complete(self, low=1, high=100):
        self.outcome = ColorScaleOutcome('complete', self.request, ColorLimits(low, high), 5, 0)
        self.alive = False


def click(button):
    button._observers.process('clicked', None)


def test_auto_scan_async_frames_and_completion_without_reader_access(make_measure_ui):
    jobs = []
    def factory(request):
        job = ControlledColorJob(request)
        jobs.append(job)
        return job
    ui = make_measure_ui(color_job_factory=factory)
    assert len(jobs) == 1 and jobs[0].started
    assert ui.color_controller.blocks_heavy_tasks
    ui.slider.set_val(2)
    ui.range_slider.set_val((1, 3))
    assert len(jobs) == 1
    panel = ui.color_scale_panel
    panel.min_box.set_val('-')
    panel.max_box.set_val('9e')
    reads = list(ui.session.reader.requested_frames)
    jobs[0].complete()
    ui.poll_color_scale()
    assert ui.image.get_clim() == (1, 100)
    assert (ui.image.colorbar.vmin, ui.image.colorbar.vmax) == (1, 100)
    assert ui.session.reader.requested_frames == reads
    assert (panel.min_box.text, panel.max_box.text) == ('-', '9e')
    assert not ui.color_controller.blocks_heavy_tasks


def test_per_frame_updates_clim_and_keeps_fixed_memory(make_measure_ui):
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    settings = ui.session.color_scale.settings
    click(ui.color_scale_panel.per_frame_button)
    ui.slider.set_val(3)
    assert ui.image.get_clim() == (30, 77)
    assert ui.session.color_scale.settings.vmin_c == settings.vmin_c
    assert ui.session.color_scale.settings.vmax_c == settings.vmax_c


def test_preview_freezes_actual_entry_clim_then_cancel_recalculates(make_measure_ui):
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    click(ui.color_scale_panel.per_frame_button)
    original = ui.image.get_clim()
    click(ui.edit_transform_button)
    ui.slider.set_val(3)
    assert ui.image.get_clim() == original
    assert not ui.color_scale_panel.apply_button.active
    ui.transform_editor.cancel_button._observers.process('clicked', None)
    assert ui.image.get_clim() == (30, 77)


def test_direct_heavy_callbacks_block_while_scan_owned(make_measure_ui):
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    click(ui.video_button)
    click(ui.export_button)
    assert ui.video_controller.job is None
    assert ui.export_state.confirmation is None
    assert ui.pages.busy is None


def test_deferred_scan_and_rebuild_preserve_owner(make_measure_ui):
    ui = make_measure_ui(color_job_factory=ControlledColorJob, defer_color_scan=True)
    assert not ui.color_controller.blocks_heavy_tasks
    ui.color_controller.activate()
    assert ui.color_controller.blocks_heavy_tasks
    owner = ui.color_controller
    ui.dispose(preserve_session=True)
    assert owner.blocks_heavy_tasks
    assert owner._on_change is None
    owner.close(wait=True)

def test_calculate_button_stays_blocked_on_measure_refresh(make_measure_ui):
    from dataclasses import replace
    from flir_seq_tool.measurement_models import Roi
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    ui.session.measurement.settings = replace(ui.session.measurement.settings,
        rois=(Roi('a', 'A', 0, 0, 2, 2, '#123456', True),), result_view='curves')
    ui.pages.switch('measure')
    ui.slider.set_val(2)
    assert not ui.measure_panel.calculate_button.active
    ui.measure_panel.on_calculate()
    assert ui.analysis_job is None


def test_confirm_context_changes_coalesce_retiring_worker(make_measure_ui):
    jobs = []
    def factory(request):
        job = ControlledColorJob(request)
        jobs.append(job)
        return job
    ui = make_measure_ui(color_job_factory=factory)
    ui.range_slider.set_val((1, 3))
    click(ui.confirm_button)
    click(ui.reset_button)
    ui.range_slider.set_val((2, 4))
    click(ui.confirm_button)
    assert len(jobs) == 1 and jobs[0].cancelled
    jobs[0].complete(-999, 999)
    ui.poll_color_scale()
    assert len(jobs) == 2
    assert (jobs[1].request.context.source_start, jobs[1].request.context.source_end) == (2, 4)
    assert ui.image.get_clim() != (-999, 999)


def test_manual_apply_dirties_display_without_invalidating_analysis(make_measure_ui):
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    state = ui.session.measurement
    key = ui.session.analysis_key()
    state.result_key = key
    state.status = 'complete'
    marker = object()
    state.result_store = marker
    ui.session.color_scale.edit_bounds('5', '75')
    click(ui.color_scale_panel.apply_button)
    assert ui.image.get_clim() == (5, 75)
    assert ui.session.has_unsaved_changes()
    assert state.result_store is marker and state.result_key is key and state.status == 'complete'
    state.result_store = None


def test_idle_poll_does_not_notify_or_draw(make_measure_ui, monkeypatch):
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    ui.color_controller.cancel()
    ui.color_controller._job.complete()
    ui.poll_color_scale()
    draws = []
    monkeypatch.setattr(ui.figure.canvas, 'draw_idle', lambda: draws.append(1))
    ui.poll_color_scale()
    ui.poll_color_scale()
    assert draws == []

def test_scale_header_does_not_cover_visible_page_controls(make_measure_ui):
    from itertools import product
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    for page in ('process', 'measure'):
        ui.pages.switch(page)
        others = (ui.normal_control_axes if page == 'process' else tuple(ui.measure_panel.control_axes))
        for color_axis, other in product(ui.color_scale_panel.control_axes, others):
            if other.get_visible():
                assert not color_axis.get_position().overlaps(other.get_position())


def test_roi_geometry_change_preserves_scan_scope(make_measure_ui):
    from dataclasses import replace
    from flir_seq_tool.measurement_models import Roi
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    context = ui.color_controller.context
    ui.session.measurement.settings = replace(ui.session.measurement.settings,
        rois=(Roi('a', 'A', 0, 0, 2, 2, '#123456', True),))
    ui.measure_panel.on_geometry_change()
    assert ui.color_controller.context is context
    assert not ui.color_controller._job.cancelled


def test_loaded_manual_setup_is_clean_and_cancelled_load_keeps_drafts(make_measure_ui, tmp_path):
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    panel = ui.color_scale_panel
    panel.min_box.set_val('5')
    panel.max_box.set_val('75')
    click(panel.apply_button)
    dialogs = ui.measure_panel.dialogs
    dialogs.save = dialogs.load = tmp_path / 'view.json'
    click(ui.save_button)
    panel.min_box.set_val('-')
    panel.max_box.set_val('9e')
    dialogs.answer = False
    click(ui.load_button)
    assert (panel.min_box.text, panel.max_box.text) == ('-', '9e')
    assert ui.session.color_scale.draft_pending
    dialogs.answer = True
    click(ui.load_button)
    assert ui.image.get_clim() == (5, 75)
    assert not ui.session.has_unsaved_changes()
    assert not ui.session.color_scale.draft_pending


def test_context_preparation_error_is_bounded_and_non_mutating(make_measure_ui, monkeypatch):
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    ui.range_slider.set_val((1, 3))
    before = ui.session.measure_source_range()
    def fail(**kwargs):
        raise OSError('source unavailable')
    monkeypatch.setattr(ui.session, '_prepare_color_context', fail)
    click(ui.confirm_button)
    assert ui.session.measure_source_range() == before
    assert 'source unavailable' in ui.message_details['message']


def test_csv_guard_is_kind_specific_current_png_stays_available(make_measure_ui):
    from dataclasses import replace
    from flir_seq_tool.measurement_models import Roi
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    ui.pages.switch('measure')
    panel = ui.measure_panel
    state = ui.session.measurement
    state.settings = replace(state.settings,
        rois=(Roi('a', 'A', 0, 0, 2, 2, '#123456', True),), result_view='curves')
    state.result_key = ui.session.analysis_key()
    state.result_store = object()
    state.status = 'complete'
    try:
        assert panel.can_export_kind('png')
        assert not panel.can_export_kind('csv')
        reads = list(ui.session.reader.requested_frames)
        panel.on_export('csv')
        assert ui.session.reader.requested_frames == reads
        assert panel.dialogs.analysis_choices == []
        assert not panel.can_export_height('height')
        assert not panel.can_export_height('zones')
    finally:
        state.result_store = None


def test_manual_mode_keeps_heavy_block_until_cancelled_worker_reaped(make_measure_ui):
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    worker = ui.color_controller._job
    ui.color_scale_panel.min_box.set_val('0')
    ui.color_scale_panel.max_box.set_val('100')
    click(ui.color_scale_panel.apply_button)
    assert worker.cancelled
    assert not ui.video_button.active and not ui.export_button.active
    click(ui.video_button)
    assert ui.video_controller.job is None
    worker.complete(-999, 999)
    ui.poll_color_scale()
    assert ui.video_button.active and ui.export_button.active
    assert ui.image.get_clim() == (0, 100)

def test_current_statistics_do_not_cover_measure_toolbar(make_measure_ui):
    from dataclasses import replace
    from flir_seq_tool.measurement_models import Roi
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    ui.session.measurement.settings = replace(ui.session.measurement.settings,
        rois=(Roi('a', 'A', 0, 0, 2, 2, '#123456', True),))
    ui.pages.switch('measure')
    ui.measure_panel.select('a')
    ui.figure.canvas.draw()
    bounds = ui.measure_panel.stats_text.get_window_extent(ui.figure.canvas.get_renderer())
    assert bounds.y0 > ui.measure_panel.calculate_button.ax.get_window_extent().y1

def test_owned_worker_notification_does_not_restart_running_timer(make_measure_ui, monkeypatch):
    ui = make_measure_ui(color_job_factory=ControlledColorJob, defer_color_scan=True)
    starts = []
    # Locate the timer actually registered with the public poll callback.
    from matplotlib.backend_bases import TimerBase
    monkeypatch.setattr(TimerBase, 'start', lambda self, interval=None: starts.append(self))
    ui.color_controller.activate()
    assert len(starts) == 1
    ui.poll_color_scale()
    ui.poll_color_scale()
    assert len(starts) == 1


@pytest.mark.parametrize('mode,ready,no_finite,status', [
    ('per_frame', True, False, 'per_frame'),
    ('fixed', False, False, 'temporary'),
    ('fixed', False, True, 'temporary'),
    ('fixed', True, False, 'auto_ready'),
])
def test_png_callback_freezes_policy_and_effective_scale_before_dialog(
        make_measure_ui, tmp_path, monkeypatch, mode, ready, no_finite, status):
    from flir_seq_tool.measurement_models import MeasurementSettings, Roi
    from flir_seq_tool.selection import DisplaySettings
    import flir_seq_tool.viewer as viewer
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    ui.session.measurement.settings = MeasurementSettings((Roi('r1', 'ROI 1', 0, 0, 2, 2, '#123456', True),), 'r1')
    state = ui.session.color_scale
    scope = ui.color_controller.context.scope
    policy = DisplaySettings('inferno', 20., 45., scale_mode=mode, fixed_source='auto',
                             auto_scope=scope if ready else None)
    state.load_settings(policy)
    state.set_scope(scope)
    state.observe_frame([[float('nan')]] if no_finite else [[22., 50.]])
    effective = state.render_snapshot().limits
    ui.pages.switch('measure')
    captured = []
    def choose(*args):
        state.observe_frame([[-100., 200.]])
        state.restore_auto()
        assert state.accept_auto(scope, ColorLimits(-10., 100.))
        state.apply_manual(0., 5.)
        return tmp_path / 'frozen.png'
    ui.measure_panel.dialogs.choose_analysis_export = choose
    monkeypatch.setattr(viewer, 'export_png', lambda target, snapshot, **kwargs: captured.append(snapshot))
    ui.measure_panel.on_export('png')
    assert len(captured) == 1
    snapshot = captured[0]
    assert snapshot.display == policy
    assert snapshot.effective_limits == effective
    assert snapshot.scale_status == status and snapshot.no_finite is no_finite

@pytest.mark.parametrize('dpi', [100, 150, 200])
def test_large_roi_statistics_and_invalid_geometry_have_separate_regions(make_measure_ui, tmp_path, dpi):
    from dataclasses import replace
    import numpy as np
    from flir_seq_tool.measurement_models import Roi
    from flir_seq_tool.models import TemperatureFrame
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    ui.figure.set_size_inches(10, 8)
    ui.figure.set_dpi(dpi)
    frame = np.full((480, 640), 22.586210250854492)
    frame[0, 0] = np.nan
    ui.pages.switch('measure')
    ui.image.set_data(frame)
    ui.image.set_extent((-.5, 639.5, 479.5, -.5))
    ui.session.measurement.settings = replace(ui.session.measurement.settings,
        rois=(Roi('a', 'A', 0, 0, 640, 480, '#123456', True),), active_roi_id='a')
    panel = ui.measure_panel
    panel.refresh_frame(TemperatureFrame(0, ui.session.reader.timestamp_at(0), frame))
    panel._sync()
    panel.geometry_boxes['width'].set_val('invalid')
    ui.figure.canvas.draw()
    renderer = ui.figure.canvas.get_renderer()
    stats = panel.stats_text.get_window_extent(renderer)
    error = panel.error_text.get_window_extent(renderer)
    assert '307199/307200 (1 excluded)' in panel.stats_text.get_text()
    assert 'ddof=0' in panel.stats_text.get_text()
    assert stats.x1 <= ui.figure.bbox.x1
    from matplotlib.patches import FancyBboxPatch
    roi_card = next(a for a in ui.figure.artists
                    if isinstance(a, FancyBboxPatch) and a.get_x() == .69)
    card = roi_card.get_window_extent(renderer)
    assert card.contains(stats.x0, stats.y0) and card.contains(stats.x1, stats.y1)
    assert not stats.overlaps(error)
    assert stats.y0 > panel.current_button.ax.bbox.y1
    assert error.y0 > panel.current_button.ax.bbox.y1
    ui.figure.savefig(tmp_path / f'excluded-invalid-roi-{dpi}.png')

@pytest.mark.parametrize('control', ['frame', 'range'])
def test_twenty_real_drag_events_keep_scan_owned_without_forced_draw(make_measure_ui, monkeypatch, control):
    from matplotlib.backend_bases import MouseEvent
    jobs = []
    def factory(request):
        job = ControlledColorJob(request)
        jobs.append(job)
        return job
    ui = make_measure_ui(color_job_factory=factory)
    ui.figure.canvas.draw()
    canvas = ui.figure.canvas
    draws = []
    monkeypatch.setattr(canvas, 'draw_idle', lambda: None)
    monkeypatch.setattr(canvas, 'draw', lambda: draws.append(1))
    def forbidden(*args):
        pytest.fail('continuous navigation must update artists without TextBox.set_val')
    for box in (ui.current_frame_box, ui.color_scale_panel.min_box, ui.color_scale_panel.max_box):
        monkeypatch.setattr(box, 'set_val', forbidden)
    widget = ui.slider if control == 'frame' else ui.range_slider
    try:
        x, y = widget.ax.transData.transform((0, .5))
        canvas.callbacks.process('button_press_event', MouseEvent('button_press_event', canvas, x, y, button=1))
        for i in range(20):
            value = (i % 4) + .1
            x, y = widget.ax.transData.transform((value, .5))
            canvas.callbacks.process('motion_notify_event', MouseEvent('motion_notify_event', canvas, x, y, button=1))
            ui.poll_color_scale()
            assert len(jobs) == 1 and jobs[0].alive
        canvas.callbacks.process('button_release_event', MouseEvent('button_release_event', canvas, x, y, button=1))
        assert not draws
        assert len(ui.session.source_cache) <= 3
        assert len(ui.session.transformed_cache) <= 3
        if control == 'frame':
            assert ui.session.timeline.current_local_frame == 3
            import numpy as np
            np.testing.assert_array_equal(ui.image.get_array(), np.arange(48).reshape(6, 8) + 30)
        else:
            click(ui.confirm_button)
            assert jobs[0].cancelled and len(jobs) == 1
            jobs[0].complete()
            ui.poll_color_scale()
            assert len(jobs) == 2
    finally:
        for job in jobs:
            job.cancel()
            job.join()
        ui.poll_color_scale()

@pytest.mark.parametrize('dpi', [100, 150, 200])
@pytest.mark.parametrize('view', ['process', 'editor', 'current', 'curves', 'height'])
def test_scale_layout_real_text_regions_in_all_views(make_measure_ui, tmp_path, dpi, view):
    from dataclasses import replace
    from itertools import combinations
    from flir_seq_tool.measurement_models import Roi
    from flir_seq_tool.measurement_job import ProgressSnapshot
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    ui.figure.set_size_inches(10, 8)
    ui.figure.set_dpi(dpi)
    ui.session.color_scale.apply_manual(22.586210250854492, 50.18052673339844)
    panel = ui.color_scale_panel
    ui.session.measurement.settings = replace(ui.session.measurement.settings,
        rois=(Roi('r', 'Sample', 0, 0, 2, 2, '#123456', True),), active_roi_id='r')
    if view == 'curves':
        from flir_seq_tool.measurement_store import ResultStore
        store = ResultStore.create(tmp_path)
        store.append_rows(
            dict(roi_id='r', local_frame=i, source_frame=i, timestamp='2026-10-22T00:00:00',
                 relative_s=i * .02, mean_c=4. + i, min_c=1. + i, max_c=7. + i,
                 delta_c=6., std_c=2., valid_pixels=4, total_pixels=4)
            for i in range(5))
        key = ui.session.analysis_key()
        store.complete(expected_rows=5, key_json=key.to_json())
        ui.session.measurement.result_store = store
        ui.session.measurement.result_key = key
        ui.session.measurement.status = 'complete'
    if view == 'editor':
        click(ui.edit_transform_button)
    elif view != 'process':
        ui.pages.switch('measure')
        ui.measure_panel._set_view('result_view', view)
    # Exercise all status forms with pending text, preserving prefix and details.
    for status in ('scanning', 'cancelling', 'error'):
        ui.color_controller._status = status
        ui.color_controller._error = 'source failure ' * 100 if status == 'error' else None
        ui.color_controller._progress = ProgressSnapshot(1, 5, 'wide source path ' * 100)
        panel._error = 'source failure ' * 100 if status == 'error' else None
        ui.session.color_scale.edit_bounds('22.586210250854492', '50.18052673339844')
        panel.sync()
        ui.figure.canvas.draw()
        renderer = ui.figure.canvas.get_renderer()
        text = panel.status_text
        assert text.get_text().startswith('unapplied bounds')
        assert 'F1' in text.get_text()
        bounds = text.get_window_extent(renderer)
        assert bounds.x1 <= ui.figure.bbox.x1
        header = [w.ax for w in panel.widgets]
        for a, b in combinations(header, 2):
            assert not a.bbox.overlaps(b.bbox)
        targets = [ui.axes, ui.current_frame_box.ax, *header]
        targets += [a for a in ui.measure_panel.control_axes if a.get_visible()]
        for ax in targets:
            if ax.get_visible():
                assert not bounds.overlaps(ax.bbox)
        assert bounds.y1 < min(ax.bbox.y0 for ax in header)
        assert bounds.y0 > ui.axes.title.get_window_extent(renderer).y1
        for box in (panel.min_box, panel.max_box):
            glyphs = box.text_disp.get_window_extent(renderer)
            assert box.ax.bbox.contains(glyphs.x0, glyphs.y0)
            assert box.ax.bbox.contains(glyphs.x1, glyphs.y1)
        if view == 'editor':
            assert not any(w.active for w in panel.widgets)
    if view == 'height':
        ui.measure_panel.geometry_boxes['width'].set_val('invalid')
        ui.figure.canvas.draw()
        error = ui.measure_panel.error_text.get_window_extent(renderer)
        for ax in ui.measure_panel.control_axes:
            if ax.get_visible():
                assert not error.overlaps(ax.bbox)
    if view == 'curves':
        temp, delta = ui.measure_panel.result_axes, ui.measure_panel.delta_axes
        assert temp.lines and delta.lines
        assert delta.get_tightbbox(renderer).y1 < temp.bbox.y0
    ui.figure.savefig(tmp_path / f'{view}-{dpi}.png')

@pytest.mark.parametrize('dpi', [100, 150, 200])
@pytest.mark.parametrize('shape', [(6, 8), (480, 640)])
@pytest.mark.parametrize('view', ['current', 'curves', 'height'])
def test_measure_image_xlabel_clears_frame_navigation(make_measure_ui, dpi, shape, view, tmp_path):
    import numpy as np
    ui = make_measure_ui(color_job_factory=ControlledColorJob)
    ui.figure.set_size_inches(10, 8)
    ui.figure.set_dpi(dpi)
    ui.pages.switch('measure')
    ui.measure_panel._set_view('result_view', view)
    height, width = shape
    ui.image.set_data(np.zeros(shape))
    ui.image.set_extent((-.5, width - .5, height - .5, -.5))
    ui.figure.canvas.draw()
    renderer = ui.figure.canvas.get_renderer()
    xlabel = ui.axes.xaxis.label.get_window_extent(renderer)
    assert ui.axes.xaxis.label.get_visible()
    assert xlabel.y0 > ui.slider.ax.get_window_extent(renderer).y1
    assert not xlabel.overlaps(ui.current_frame_box.ax.get_window_extent(renderer))
    assert not xlabel.overlaps(ui.current_frame_box.label.get_window_extent(renderer))
    if view == 'current' and dpi == 100:
        ui.figure.savefig(tmp_path / f'current-xlabel-{width}x{height}-100.png')
