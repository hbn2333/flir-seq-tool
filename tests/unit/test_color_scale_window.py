"""Color workers belong to committed windows, including modal rollback paths."""
from pathlib import Path

import matplotlib.pyplot as plt
import pytest

from test_color_scale_workflow import ControlledColorJob
from test_viewer_window import make_window
from flir_seq_tool.viewer import build_viewer


def controlled_window(tmp_path, *, failure=None):
    jobs, events, holder = [], [], {}

    def factory(request):
        window = holder.get('window')
        # This also checks initial activation once start has committed ownership.
        assert window is not None and window.session is window.ui.session
        assert window.session.reader.path == request.context.source_path
        job = ControlledColorJob(request)
        original_join = job.join
        def join(timeout=None):
            events.append(('join', request.context.source_path.name))
            original_join(timeout)
        job.join = join
        jobs.append(job)
        return job

    def builder(session, **kwargs):
        assert kwargs.get('defer_color_scan') is True
        ui = build_viewer(session, color_job_factory=factory, **kwargs)
        if Path(session.reader.path).stem == 'second' and failure:
            if failure == 'activate':
                activate = ui.color_controller.activate
                def fail_activation():
                    activate()
                    raise RuntimeError('activation callback failed')
                ui.color_controller.activate = fail_activation
                return ui
            if failure == 'build':
                raise RuntimeError('candidate build failed')
            draw = ui.figure.canvas.draw
            def fail_once(*args, **options):
                ui.figure.canvas.draw = draw
                raise RuntimeError('candidate draw failed')
            ui.figure.canvas.draw = fail_once
        return ui

    # make_window starts internally; expose its window before activation through builder.
    from flir_seq_tool import viewer_window
    original = viewer_window.ViewerWindow
    class VisibleWindow(original):
        def start(self, *args, **kwargs):
            holder['window'] = self
            return super().start(*args, **kwargs)
    from unittest.mock import patch
    from test_viewer_window import Reader
    original_exit = Reader.__exit__
    def close_reader(reader, *args):
        matching = [job for job in jobs if job.request.context.source_path == reader.path]
        assert all(not job.alive for job in matching)
        assert events.count(('join', reader.path.name)) >= len(matching)
        events.append(('reader_close', reader.path.name))
        return original_exit(reader, *args)
    with patch.object(viewer_window, 'ViewerWindow', VisibleWindow), patch.object(Reader, '__exit__', close_reader):
        window, readers, dialogs = make_window(tmp_path, builder=builder)
    return window, readers, dialogs, jobs, events


@pytest.fixture
def window_case(tmp_path):
    cases = []
    def make(**kwargs):
        case = controlled_window(tmp_path, **kwargs)
        cases.append(case)
        return case
    yield make
    for window, *_ in cases:
        window.close(wait=True)
        plt.close(window.figure)


def test_initial_and_switch_workers_start_only_after_commit_and_join_before_reader_close(window_case):
    window, readers, _, jobs, events = window_case()
    assert len(jobs) == 1 and jobs[0].started
    figure, old_ui = window.figure, window.ui
    before = {name: len(cbs) for name, cbs in figure.canvas.callbacks.callbacks.items()}
    assert window.open_source(Path(readers[0].path).with_name('second.seq'))
    assert len(jobs) == 2 and jobs[0].cancelled and readers[0].closed
    jobs[0].complete(-999, 999)
    old_ui.poll_color_scale()
    assert window.ui.image.get_clim() != (-999, 999)
    assert window.open_source(readers[0].path)
    assert window.figure is figure
    assert before == {name: len(cbs) for name, cbs in figure.canvas.callbacks.callbacks.items()}


@pytest.mark.parametrize('failure', ['build', 'draw', 'activate'])
def test_failed_install_rebuilds_same_controller_and_resumes_retiring_scan(window_case, failure):
    window, readers, _, jobs, _ = window_case(failure=failure)
    session, owner = window.session, window.ui.color_controller
    session.color_scale.edit_bounds('-', '9e')
    window.dialogs.choose_color_draft_action = lambda message: 'cancel'
    # First cancellation retains draft, without preparing the candidate.
    assert not window.open_source(readers[0].path.with_name('second.seq'))
    assert session.color_scale.draft_bounds == ('-', '9e')
    window.dialogs.choose_color_draft_action = lambda message: 'discard'
    assert not window.open_source(readers[0].path.with_name('second.seq'))
    assert window.session is session and window.ui.color_controller is owner
    expected = 2 if failure == 'activate' else 1
    assert len(jobs) == expected and jobs[0].cancelled and not readers[0].closed
    if failure == 'activate':
        assert jobs[1].cancelled and not jobs[1].alive and readers[1].closed
    jobs[0].complete(-999, 999)
    window.ui.poll_color_scale()
    assert len(jobs) == expected + 1 and window.ui.image.get_clim() != (-999, 999)


@pytest.mark.parametrize('route', ['chooser', 'bad', 'unsaved_cancel', 'save_cancel', 'save_fail'])
def test_cancelled_open_suspends_before_modals_and_resumes_after_retirement(window_case, route, monkeypatch):
    window, readers, dialogs, jobs, _ = window_case()
    def modal(answer):
        assert jobs[0].cancelled
        window.ui.poll_color_scale()
        assert len(jobs) == 1
        return answer
    path = readers[0].path.with_name('second.seq')
    if route == 'chooser':
        dialogs.choose_seq_open = lambda folder: modal(None)
        path = None
    elif route == 'bad':
        path = path.with_name('bad.seq')
    else:
        window.session.timeline.set_pending(1, 3)
        dialogs.choose_unsaved_action = lambda message: modal('cancel' if route == 'unsaved_cancel' else 'save')
        if route == 'save_fail':
            dialogs.save = path.with_suffix('.selection.json')
            def fail(*args, **kwargs):
                raise OSError('disk full')
            monkeypatch.setattr(window.session.selection_store, 'save_to', fail)
    assert not window.open_source(path)
    assert window.session.reader is readers[0] and not readers[0].closed
    jobs[0].complete()
    window.ui.poll_color_scale()
    assert len(jobs) == 2


@pytest.mark.parametrize('failure', [None, 'build'])
def test_user_cancel_survives_cancelled_open_and_page_changes(window_case, failure):
    window, readers, _, jobs, _ = window_case(failure=failure)
    window.ui.color_controller.cancel()
    assert not window.open_source(readers[0].path.with_name('second.seq') if failure else None)
    jobs[0].complete()
    window.ui.poll_color_scale()
    window.ui.pages.switch('measure')
    window.ui.pages.switch('process')
    assert len(jobs) == 1


def test_draft_apply_then_cancelled_save_retains_applied_limits(window_case):
    window, readers, dialogs, jobs, _ = window_case()
    state = window.session.color_scale
    state.edit_bounds('5', '75')
    dialogs.choose_color_draft_action = lambda message: 'apply'
    dialogs.choose_unsaved_action = lambda message: 'save'
    assert not window.open_source(readers[0].path.with_name('second.seq'))
    assert (state.settings.vmin_c, state.settings.vmax_c) == (5, 75)
    assert state.settings.fixed_source == 'manual' and not state.draft_pending
    assert len(readers) == len(jobs) == 1 and not readers[0].closed


@pytest.mark.parametrize('action', ['apply', 'discard', 'cancel', 'invalid'])
def test_draft_choice_precedes_unsaved_and_preserves_applied_policy(window_case, action):
    window, readers, dialogs, jobs, _ = window_case()
    state = window.session.color_scale
    original = state.settings
    window.session.timeline.set_pending(1, 3)
    state.edit_bounds('-' if action == 'invalid' else '5', '75')
    prompts = []
    dialogs.choose_color_draft_action = lambda message: prompts.append('draft') or ('apply' if action == 'invalid' else action)
    dialogs.choose_unsaved_action = lambda message: prompts.append('unsaved') or 'cancel'
    assert not window.open_source(readers[0].path.with_name('second.seq'))
    assert prompts[0] == 'draft'
    if action == 'apply':
        assert prompts == ['draft', 'unsaved']
        assert (state.settings.vmin_c, state.settings.vmax_c) == (5, 75)
        assert not state.draft_pending
    else:
        assert state.settings == original
        assert state.draft_pending == (action != 'discard')
        if action in ('cancel', 'invalid'):
            assert prompts == ['draft']
    assert len(jobs) == 1


def test_alias_and_chooser_cancel_never_prepare_candidate_or_prompt_for_drafts(window_case):
    import os
    window, readers, dialogs, jobs, _ = window_case()
    window.session.color_scale.edit_bounds('-', '9e')
    dialogs.choose_color_draft_action = lambda message: pytest.fail('alias prompted')
    alias = readers[0].path.with_name('alias.seq')
    os.link(readers[0].path, alias)
    assert not window.open_source(alias)
    assert not window.open_source()
    assert len(readers) == len(jobs) == 1
    assert window.session.color_scale.draft_bounds == ('-', '9e')


def test_preserve_session_rebuild_retains_draft_state_controller_and_retiring_job(window_case):
    window, readers, _, jobs, _ = window_case()
    session, owner, figure = window.session, window.ui.color_controller, window.figure
    state = session.color_scale
    state.edit_bounds('-', '9e')
    owner.suspend('open')
    callbacks = {name: len(cbs) for name, cbs in figure.canvas.callbacks.callbacks.items()}
    timer_count = sum(len(resource.timers) for resource in figure._viewer_resources)
    window.ui.dispose(preserve_session=True)
    assert owner.blocks_heavy_tasks and owner._on_change is None
    window.ui = window._install(window.current)
    assert window.ui.color_controller is owner and session.color_scale is state
    assert state.draft_bounds == ('-', '9e') and len(jobs) == 1
    assert callbacks == {name: len(cbs) for name, cbs in figure.canvas.callbacks.callbacks.items()}
    assert timer_count == sum(len(resource.timers) for resource in figure._viewer_resources)
    owner.resume('open')
    jobs[0].complete()
    window.ui.poll_color_scale()
    assert len(jobs) == 2


def test_failed_candidate_and_failed_restore_do_not_resume_without_live_ui(window_case):
    window, readers, _, jobs, _ = window_case()
    old_session, old_ui = window.session, window.ui
    owner = old_ui.color_controller
    attempted = []
    def fail_install(session, **kwargs):
        attempted.append(session)
        if session is not old_session:
            jobs[0].complete(-999, 999)
            owner.poll()
            assert not owner.blocks_heavy_tasks
        raise RuntimeError('both installations fail')
    window.builder = fail_install
    assert not window.open_source(readers[0].path.with_name('second.seq'))
    assert len(attempted) == 2 and attempted[0] is not old_session and attempted[1] is old_session
    assert window.session is old_session and window.ui is old_ui
    assert old_ui.pages.disposed and readers[1].closed
    assert jobs[0].cancelled and not jobs[0].alive
    assert len(jobs) == 1
    assert owner.status == 'paused' and not owner.blocks_heavy_tasks
    window.close(wait=True)
    assert readers[0].closed and len(jobs) == 1


@pytest.mark.parametrize('policy', ['auto', 'manual'])
def test_discard_draft_then_save_persists_only_applied_settings(window_case, policy):
    window, readers, dialogs, jobs, _ = window_case()
    old = window.session
    if policy == 'manual':
        old.color_scale.apply_manual(10, 50)
        window.ui.color_controller.reconcile()
    applied = old.color_scale.settings
    old.timeline.set_pending(1, 3)
    old.color_scale.edit_bounds('999', '1000')
    dialogs.choose_color_draft_action = lambda message: 'discard'
    dialogs.choose_unsaved_action = lambda message: 'save'
    dialogs.save = readers[0].path.with_name('applied.selection.json')
    assert window.open_source(readers[0].path.with_name('second.seq'))
    saved = old.selection_store.load_path(dialogs.save)
    assert saved.display == applied
    assert saved.selection.source_start_frame == 1
    assert saved.selection.source_end_frame == 3


def test_close_false_cancels_promptly_then_final_close_joins_before_reader(window_case):
    window, readers, _, jobs, events = window_case()
    window.close(wait=False)
    assert jobs[0].cancelled and jobs[0].alive and not readers[0].closed
    assert not events
    window.close(wait=True)
    assert events == [('join', 'first.seq'), ('reader_close', 'first.seq')] and readers[0].closed
    window.close(wait=True)
    assert events == [('join', 'first.seq'), ('reader_close', 'first.seq')]


def test_initial_activation_exception_reaps_committed_ui_worker_and_reader(tmp_path):
    from flir_seq_tool.viewer_window import ViewerWindow
    from test_viewer_window import Reader
    from test_viewer import FakeSelectionDialogs
    readers, jobs, uis = [], [], []
    def reader_factory(path):
        readers.append(Reader(path))
        return readers[-1]
    def job_factory(request):
        assert window.current.session is window.ui.session
        jobs.append(ControlledColorJob(request))
        return jobs[-1]
    def builder(session, **kwargs):
        ui = build_viewer(session, color_job_factory=job_factory, **kwargs)
        uis.append(ui)
        activate = ui.color_controller.activate
        def fail():
            activate()
            raise RuntimeError('initial activation failed')
        ui.color_controller.activate = fail
        return ui
    window = ViewerWindow(reader_factory=reader_factory, builder=builder, dialogs=FakeSelectionDialogs())
    try:
        with pytest.raises(RuntimeError, match='initial activation failed'):
            window.start(tmp_path / 'first.seq')
        assert window.current is window.candidate is window.ui is None
        assert readers[0].closed and jobs[0].cancelled and not jobs[0].alive
        assert not uis[0].figure.axes and not uis[0].figure._viewer_resources
    finally:
        window.close(wait=True)
        if window.figure is not None:
            plt.close(window.figure)
