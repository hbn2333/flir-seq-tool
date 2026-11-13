from datetime import datetime
from unittest.mock import Mock

import matplotlib
matplotlib.use('Agg')
from matplotlib.backend_bases import MouseEvent
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
import numpy as np
import pytest

from flir_seq_tool.color_scale import ColorLimits
from flir_seq_tool.color_scale_controller import ColorScaleController
from flir_seq_tool.color_scale_job import ColorScaleContext
from flir_seq_tool.color_scale_panel import ColorScalePanel
from flir_seq_tool.color_scale_state import ColorScaleState
from flir_seq_tool.measurement_job import AnalysisProgress
from flir_seq_tool.selection import SourceIdentity
from flir_seq_tool.transform import FrameTransform


class Job:
    def __init__(self, request):
        self.request = request
        self.progress = AnalysisProgress()
        self.started = self.alive = self.cancelled = False
        self.outcome = None

    def start(self):
        self.started = self.alive = True

    def cancel(self):
        self.cancelled = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.alive = False


@pytest.fixture
def rig(tmp_path):
    figure = Figure(figsize=(10, 8))
    FigureCanvasAgg(figure)
    state = ColorScaleState()
    state.observe_frame(np.array([[20., 45.]]))
    state.apply_manual(20, 45)
    jobs = []
    def factory(request):
        job = Job(request)
        jobs.append(job)
        return job
    controller = ColorScaleController(state, factory)
    changed = Mock()
    panel = ColorScalePanel(figure, state, controller, changed)
    figure.canvas.draw()
    yield panel, state, controller, changed, jobs, tmp_path
    panel.dispose()
    controller.close(wait=True)


def click(button):
    canvas = button.ax.figure.canvas
    x, y = button.ax.transAxes.transform((.5, .5))
    for name in ('button_press_event', 'button_release_event'):
        canvas.callbacks.process(name, MouseEvent(name, canvas, x, y, button=1))


def direct(button):
    button._observers.process('clicked', None)


def context(tmp_path):
    path = tmp_path / 'test.seq'
    path.write_bytes(b'fixture')
    stat = path.stat()
    source = SourceIdentity(path.name, stat.st_size, stat.st_mtime_ns, 2, 2, 3,
                            datetime(2026, 1, 1), datetime(2026, 1, 2))
    return ColorScaleContext(path, source, 0, 2, FrameTransform.identity(2, 2))


def test_explicit_apply_commits_both_textboxes(rig):
    panel, state, _, changed, *_ = rig
    panel.min_box.set_val('21')
    panel.max_box.set_val('46')
    assert state.draft_bounds == ('21', '46')
    assert (state.settings.vmin_c, state.settings.vmax_c) == (20, 45)
    click(panel.apply_button)
    assert (state.settings.vmin_c, state.settings.vmax_c) == (21, 46)
    assert state.settings.fixed_source == 'manual'
    assert not state.draft_pending and changed.called


def test_modes_show_actual_and_restore_draft(rig):
    panel, state, *_ = rig
    panel.min_box.set_val('21')
    state.observe_frame(np.array([[22., 50.]]))
    click(panel.per_frame_button)
    assert panel.per_frame_button.selected and not panel.fixed_button.selected
    assert not panel.min_box.active and not panel.max_box.active
    assert tuple(map(float, (panel.min_box.text, panel.max_box.text))) == (22, 50)
    click(panel.fixed_button)
    assert panel.fixed_button.selected and panel.min_box.active
    assert (panel.min_box.text, panel.max_box.text) == ('21', '45.0')


@pytest.mark.parametrize('bounds', [('nan', '50'), ('20', 'inf'), ('50', '20'), ('20', '20'), ('', '40')])
def test_invalid_input_preserves_applied_and_draft_with_error(rig, bounds):
    panel, state, *_ = rig
    panel.min_box.set_val(bounds[0])
    panel.max_box.set_val(bounds[1])
    click(panel.apply_button)
    assert state.draft_bounds == bounds
    assert (state.settings.vmin_c, state.settings.vmax_c) == (20, 45)
    assert 'invalid' in panel.status_text.get_text().lower()


@pytest.mark.parametrize('method', ['set_busy', 'set_editing'])
def test_disabled_direct_callbacks_cannot_mutate(rig, method):
    panel, state, controller, *_ = rig
    panel.min_box.set_val('21')
    before = state.settings
    getattr(panel, method)(True)
    for button in (panel.per_frame_button, panel.fixed_button, panel.apply_button,
                   panel.auto_button, panel.cancel_button):
        direct(button)
    panel.max_box.set_val('99')
    assert state.settings == before
    assert state.draft_bounds == ('21', '45.0')
    assert controller.status == 'idle'


def test_preview_snapshot_also_disables_controls(rig):
    panel, state, *_ = rig
    state.freeze_preview()
    panel.sync()
    direct(panel.per_frame_button)
    assert state.settings.scale_mode == 'fixed'
    assert not any(widget.active for widget in panel.widgets)


@pytest.mark.parametrize('capture', [False, True])
def test_auto_completion_preserves_both_pending_texts_without_draw_or_events(rig, monkeypatch, capture):
    panel, state, controller, changed, _, tmp_path = rig
    scope = context(tmp_path).scope
    state.set_scope(scope)
    state.restore_auto()
    panel.sync()
    panel.min_box.set_val('21')
    texts = (panel.min_box.text, panel.max_box.text)
    panel.min_box.capturekeystrokes = capture
    state.accept_auto(scope, ColorLimits(10, 80))
    changed.reset_mock()
    monkeypatch.setattr(panel.min_box.ax.figure.canvas, 'draw_idle', Mock())
    monkeypatch.setattr(panel.min_box.ax.figure.canvas, 'draw', Mock(side_effect=AssertionError('sync drew')))
    monkeypatch.setattr(controller, 'reconcile', Mock(side_effect=AssertionError('sync reconciled')))
    for _ in range(3):
        state.observe_frame(np.array([[30., 60.]]))
        panel.sync()
    assert (panel.min_box.text, panel.max_box.text) == texts
    assert state.draft_bounds == texts
    changed.assert_not_called()
    panel.min_box.capturekeystrokes = False


def test_idle_mode_switch_never_forces_draw(rig, monkeypatch):
    panel, *_ = rig
    canvas = panel.min_box.ax.figure.canvas
    monkeypatch.setattr(canvas, 'draw_idle', Mock())
    monkeypatch.setattr(canvas, 'draw', Mock(side_effect=AssertionError('forced draw')))
    direct(panel.per_frame_button)
    direct(panel.fixed_button)


def test_auto_clears_draft_cancel_and_retry_routes(rig):
    panel, state, controller, _, jobs, tmp_path = rig
    controller.set_context(context(tmp_path))
    controller.activate()
    panel.min_box.set_val('21')
    click(panel.auto_button)
    assert not state.draft_pending and state.settings.fixed_source == 'auto'
    assert controller.scanning and len(jobs) == 1
    panel.sync()
    assert panel.min_box.active and panel.per_frame_button.active
    assert panel.cancel_button.active
    assert '0/3' in panel.status_text.get_text()
    click(panel.cancel_button)
    assert jobs[0].cancelled
    jobs[0].alive = False
    controller.poll()
    panel.sync()
    click(panel.auto_button)
    assert len(jobs) == 2 and controller.scanning


def test_dispose_disconnects_and_retained_callbacks_are_inert(rig):
    panel, state, *_ = rig
    panel.dispose()
    panel.dispose()
    panel.min_box.set_val('99')
    direct(panel.per_frame_button)
    assert not state.draft_pending and state.settings.scale_mode == 'fixed'


def test_sync_does_not_replace_controller_attachment(rig):
    panel, _, controller, *_ = rig
    callback = Mock()
    controller.attach(callback)
    panel.sync()
    click(panel.per_frame_button)
    callback.assert_called_once_with()


def test_untouched_active_field_keeps_caret_then_refreshes_on_blur(rig):
    panel, state, _, changed, _, tmp_path = rig
    scope = context(tmp_path).scope
    state.set_scope(scope)
    state.restore_auto()
    panel.sync()
    panel.min_box.begin_typing()
    texts = (panel.min_box.text, panel.max_box.text)
    state.accept_auto(scope, ColorLimits(10.123456789012345, 80.12345678901234))
    panel.sync()
    assert (panel.min_box.text, panel.max_box.text) == texts
    panel.min_box.stop_typing()
    assert tuple(map(float, (panel.min_box.text, panel.max_box.text))) == (
        10.123456789012345, 80.12345678901234)
    assert not state.draft_pending


def test_enter_only_submits_draft_without_applying(rig):
    panel, state, *_ = rig
    panel.min_box.begin_typing()
    panel.min_box.set_val('21')
    panel.min_box._observers.process('submit', panel.min_box.text)
    assert state.draft_bounds == ('21', '45.0')
    assert state.settings.vmin_c == 20


def test_auto_range_is_fixed_only_even_for_direct_callbacks(rig):
    panel, state, *_ = rig
    click(panel.per_frame_button)
    before = state.settings
    assert not panel.auto_button.active
    direct(panel.auto_button)
    assert state.settings == before


def test_pending_hint_remains_visible_during_scan(rig):
    panel, state, controller, _, jobs, tmp_path = rig
    controller.set_context(context(tmp_path))
    controller.activate()
    click(panel.auto_button)
    panel.min_box.set_val('21')
    panel.sync()
    assert '0/3' in panel.status_text.get_text()
    assert 'unapplied bounds' in panel.status_text.get_text()


def test_pending_hint_survives_draw_time_error_truncation(rig):
    panel, state, *_ = rig
    invalid = 'invalid' * 300
    panel.min_box.set_val(invalid)
    click(panel.apply_button)
    panel.figure.canvas.draw()
    displayed = panel.status_text.get_text()
    details = panel.message_details['color_scale']
    assert 'unapplied bounds' in displayed
    assert 'F1' in displayed
    assert len(displayed.splitlines()) <= 2
    assert invalid in details
    assert state.draft_bounds == (invalid, '45.0')

@pytest.mark.parametrize('dpi', [100, 150, 200])
def test_precise_bounds_fit_and_long_draft_has_local_viewport(rig, dpi):
    panel, state, *_ = rig
    panel.figure.set_dpi(dpi)
    state.apply_manual(22.586210250854492, 50.18052673339844)
    panel.sync()
    panel.figure.canvas.draw()
    renderer = panel.figure.canvas.get_renderer()
    for box in (panel.min_box, panel.max_box):
        bounds = box.text_disp.get_window_extent(renderer)
        assert box.ax.bbox.contains(bounds.x0, bounds.y0)
        assert box.ax.bbox.contains(bounds.x1, bounds.y1)
    draft = 'invalid-' * 100
    panel.min_box.set_val(draft)
    assert panel.min_box.text == draft
    assert panel.min_box.text_disp.get_clip_on()
    assert panel.min_box.cursor.get_clip_on()
    from matplotlib.backend_bases import KeyEvent
    panel.min_box.begin_typing()
    for key in ('home', 'end', 'left', 'right', 'home'):
        panel.min_box._keypress(KeyEvent('key_press_event', panel.figure.canvas, key=key))
        segment = panel.min_box.cursor.get_segments()[0]
        assert panel.min_box.ax.bbox.x0 <= segment[0, 0] <= panel.min_box.ax.bbox.x1
    assert panel.min_box.text == draft


def test_scrolled_draft_restores_readable_values_after_mode_switch(rig, monkeypatch):
    panel, state, *_ = rig
    draft = 'invalid-' * 100
    panel.min_box.set_val(draft)
    panel.min_box.begin_typing()
    panel.min_box.cursor_index = len(draft)
    draws = []
    monkeypatch.setattr(panel.figure.canvas, 'draw_idle', lambda: None)
    monkeypatch.setattr(panel.figure.canvas, 'draw', lambda: draws.append(1))
    panel.min_box._rendercursor()
    assert panel.min_box.text_disp.get_position()[0] < 0
    assert not draws
    click(panel.per_frame_button)
    assert float(panel.min_box.text) == 20
    assert panel.min_box.text_disp.get_position()[0] == .05
    click(panel.fixed_button)
    assert panel.min_box.text == draft
    assert panel.min_box.text_disp.get_position()[0] == .05
    assert state.draft_bounds[0] == draft

def test_empty_scale_draft_keeps_visible_cursor(rig):
    panel, *_ = rig
    panel.min_box.set_val('')
    segment = panel.min_box.cursor.get_segments()[0]
    assert segment[1, 1] > segment[0, 1]
