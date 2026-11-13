from dataclasses import replace
from datetime import datetime
import os

import pytest

from flir_seq_tool.color_scale import ColorLimits
from flir_seq_tool.color_scale_controller import ColorScaleController
from flir_seq_tool.color_scale_job import ColorScaleContext, ColorScaleOutcome
from flir_seq_tool.color_scale_state import ColorScaleState
from flir_seq_tool.measurement_job import AnalysisProgress
from flir_seq_tool.selection import SourceIdentity
from flir_seq_tool.transform import FrameTransform


class ControlledColorJob:
    def __init__(self, request):
        self.request = request
        self.progress = AnalysisProgress()
        self.alive = self.started = self.cancelled = False
        self.outcome = None
        self.joins = 0

    def start(self):
        self.started = self.alive = True

    def cancel(self):
        self.cancelled = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        assert self.started
        self.joins += 1
        if timeout is None:
            self.alive = False

    def finish(self, status='complete', request=None):
        self.alive = False
        self.outcome = ColorScaleOutcome(status, request or self.request,
                                        ColorLimits(10, 90) if status == 'complete' else None,
                                        3, 0, 'scan failed' if status == 'failed' else None)


@pytest.fixture
def rig(tmp_path):
    jobs = []
    def factory(request):
        assert not any(job.alive for job in jobs)
        job = ControlledColorJob(request)
        jobs.append(job)
        return job
    def context(name='a', end=2):
        path = tmp_path / (name + '.seq')
        if not path.exists():
            path.write_bytes(b'fixture')
        stat = path.stat()
        identity = SourceIdentity(path.name, stat.st_size, stat.st_mtime_ns, 2, 2, 3,
                                  datetime(2026, 1, 1), datetime(2026, 1, 2))
        return ColorScaleContext(path, identity, 0, end, FrameTransform.identity(2, 2))
    state = ColorScaleState()
    controller = ColorScaleController(state, job_factory=factory)
    return controller, state, jobs, context


def start(rig):
    controller, state, jobs, context = rig
    controller.set_context(context())
    controller.activate()
    return controller, state, jobs, context


def test_no_candidate_before_activation_and_latest_context_wins(rig):
    controller, state, jobs, context = rig
    a, b, c = context(), context('b'), context('c')
    controller.set_context(a)
    controller.reconcile()
    assert jobs == []
    controller.activate()
    old = jobs[0]
    controller.set_context(b)
    controller.set_context(c)
    assert old.cancelled and len(jobs) == 1
    assert controller.blocks_heavy_tasks
    old.finish()
    controller.poll()
    assert not state.fixed_ready
    assert len(jobs) == 2 and jobs[1].request.context == c
    jobs[1].finish()
    controller.poll()
    assert state.fixed_ready and controller.status == 'complete'


@pytest.mark.parametrize('mismatch', ['generation', 'session', 'context'])
def test_mismatched_outcomes_never_publish(rig, mismatch):
    controller, state, jobs, context = start(rig)
    request = jobs[0].request
    changes = {'generation': request.generation + 1} if mismatch == 'generation' else (
        {'session_token': 'another-session'} if mismatch == 'session' else {'context': context('b')})
    jobs[0].finish(request=replace(request, **changes))
    controller.poll()
    assert not state.fixed_ready and controller.status == 'failed'


@pytest.mark.parametrize('policy', ['manual', 'per_frame', 'paused'])
def test_policy_change_rejects_late_completion(rig, policy):
    controller, state, jobs, _ = start(rig)
    if policy == 'manual':
        state.apply_manual(20, 30)
    elif policy == 'per_frame':
        state.select_mode('per_frame')
    else:
        controller.suspend('open')
    controller.reconcile()
    jobs[0].finish()
    controller.poll()
    assert state.settings.auto_scope is None
    assert not controller.blocks_heavy_tasks


def test_user_cancel_survives_refresh_and_retry_waits_for_retirement(rig):
    controller, state, jobs, context = start(rig)
    controller.cancel()
    controller.set_context(context())
    controller.reconcile()
    jobs[0].finish()
    controller.poll()
    controller.reconcile()
    assert len(jobs) == 1 and not state.fixed_ready
    assert controller.status == 'cancelled'
    controller.request_auto()
    assert len(jobs) == 2
    controller.cancel()
    controller.request_auto()
    assert len(jobs) == 2
    jobs[1].finish()
    controller.poll()
    assert len(jobs) == 3


def test_real_context_change_clears_user_cancel(rig):
    controller, _, jobs, context = start(rig)
    controller.cancel()
    controller.set_context(context('b'))
    jobs[0].finish('cancelled')
    controller.poll()
    assert len(jobs) == 2


def test_nested_suspend_and_heavy_busy_gate_start(rig):
    controller, _, jobs, context = rig
    controller.suspend('open')
    controller.suspend('transform')
    controller.set_context(context())
    controller.activate()
    controller.resume('open')
    assert jobs == [] and controller.status == 'paused'
    controller.set_heavy_busy(True)
    controller.resume('transform')
    assert jobs == []
    controller.set_heavy_busy(False)
    assert len(jobs) == 1
    controller.suspend('transform')
    assert jobs[0].cancelled and controller.blocks_heavy_tasks


def test_same_scope_cache_does_not_rescan(rig):
    controller, state, jobs, context = start(rig)
    jobs[0].finish()
    controller.poll()
    state.apply_manual(20, 30)
    controller.reconcile()
    controller.request_auto()
    controller.set_context(context())
    assert state.fixed_ready and len(jobs) == 1


@pytest.mark.parametrize('loaded', [False, True])
def test_external_source_change_invalidates_cached_auto(rig, loaded):
    controller, state, jobs, context = start(rig)
    jobs[0].finish()
    controller.poll()
    if loaded:
        state = ColorScaleState(state.settings)
        controller = ColorScaleController(state, job_factory=lambda _: pytest.fail('stale rescan'))
        controller.set_context(context())
    controller.context.source_path.write_bytes(b'changed size')
    controller.activate()
    controller.reconcile()
    assert controller.status == 'failed' and controller.error
    assert not state.fixed_ready
    controller.request_auto()
    assert not state.fixed_ready and controller.status == 'failed'


def test_final_filesystem_check_before_publication(rig):
    controller, state, jobs, _ = start(rig)
    jobs[0].finish()
    controller.context.source_path.write_bytes(b'changed')
    controller.poll()
    assert not state.fixed_ready and controller.status == 'failed'


@pytest.mark.parametrize('status', ['failed', 'cancelled', None])
def test_worker_failure_and_missing_outcome_do_not_restart(rig, status):
    controller, state, jobs, _ = start(rig)
    if status:
        jobs[0].finish(status)
    else:
        jobs[0].alive = False
    controller.poll()
    controller.reconcile()
    assert not state.fixed_ready and len(jobs) == 1
    assert controller.status == ('cancelled' if status == 'cancelled' else 'failed')


@pytest.mark.parametrize('during_start', [False, True])
def test_startup_errors_are_reported_without_joining_unstarted_job(rig, during_start):
    controller, state, _, context = rig
    unstarted = []
    def factory(request):
        if not during_start:
            raise RuntimeError('factory failure')
        job = ControlledColorJob(request)
        unstarted.append(job)
        def fail():
            raise RuntimeError('start failure')
        job.start = fail
        return job
    controller = ColorScaleController(state, job_factory=factory)
    controller.set_context(context())
    controller.activate()
    controller.reconcile()
    assert controller.status == 'failed' and controller.error
    assert not controller.blocks_heavy_tasks
    controller.close(wait=True)
    assert all(job.joins == 0 for job in unstarted)


def test_attach_replacement_detach_and_close_preserve_worker_ownership(rig):
    controller, state, jobs, _ = start(rig)
    calls = []
    controller.attach(lambda: calls.append('old'))
    controller.attach(lambda: calls.append('new'))
    calls.clear()
    jobs[0].progress.update(1, 3, 'frame 1')
    controller.poll()
    assert calls == ['new']
    assert controller.progress_snapshot.completed == 1
    controller.detach()
    controller.poll()
    assert calls == ['new'] and len(jobs) == 1
    controller.attach(lambda: calls.append('rebuilt'))
    jobs[0].finish()
    controller.poll()
    assert state.fixed_ready and len(jobs) == 1 and calls[-1] == 'rebuilt'
    controller.set_context(replace(controller.context, source_end=1))
    assert len(jobs) == 2
    controller.close(wait=False)
    count = len(calls)
    assert jobs[1].cancelled and controller.blocks_heavy_tasks
    controller.close(wait=True)
    assert jobs[1].joins and not controller.blocks_heavy_tasks
    controller.activate()
    controller.request_auto()
    controller.poll()
    assert len(calls) == count and len(jobs) == 2


def test_session_tokens_are_unique(rig):
    controller, state, _, _ = rig
    assert controller.session_token != ColorScaleController(state).session_token


@pytest.mark.parametrize('change', ['mtime', 'missing'])
def test_cached_source_validation_checks_mtime_and_missing_file(rig, change):
    controller, state, jobs, _ = start(rig)
    jobs[0].finish()
    controller.poll()
    path = controller.context.source_path
    if change == 'missing':
        path.unlink()
    else:
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    controller.reconcile()
    assert not state.fixed_ready and controller.status == 'failed'
    assert len(jobs) == 1


def test_heavy_busy_cancels_and_waits_for_worker_before_restarting(rig):
    controller, state, jobs, _ = start(rig)
    generation = controller.generation
    controller.set_heavy_busy(True)
    assert jobs[0].cancelled and controller.generation > generation
    controller.set_heavy_busy(False)
    assert len(jobs) == 1 and controller.blocks_heavy_tasks
    jobs[0].finish()
    controller.poll()
    assert not state.fixed_ready and len(jobs) == 2


def test_close_then_late_success_is_reaped_without_publication(rig):
    controller, state, jobs, _ = start(rig)
    calls = []
    controller.attach(lambda: calls.append(1))
    controller.close()
    jobs[0].finish()
    controller.poll()
    assert jobs[0].joins == 1 and not controller.blocks_heavy_tasks
    assert not state.fixed_ready and calls == []
    controller.close(wait=True)
    assert jobs[0].joins == 1


def test_partial_start_failure_keeps_worker_then_reports_failure_after_reap(rig):
    _, state, _, context = rig
    jobs = []
    def factory(request):
        job = ControlledColorJob(request)
        def start_then_fail():
            job.started = job.alive = True
            raise RuntimeError('start partially succeeded')
        job.start = start_then_fail
        jobs.append(job)
        return job
    controller = ColorScaleController(state, job_factory=factory)
    controller.set_context(context())
    controller.activate()
    assert jobs[0].cancelled and controller.blocks_heavy_tasks
    jobs[0].finish()
    controller.poll()
    assert not controller.blocks_heavy_tasks
    assert controller.status == 'failed' and 'partially' in controller.error
    assert not state.fixed_ready and len(jobs) == 1
