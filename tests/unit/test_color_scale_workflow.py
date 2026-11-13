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
