"""Session-owned, UI-independent coordination of one color scan worker.

All methods are called by the main caller. Workers only write their own progress
and outcome; publication and source metadata checks happen here during poll.
"""

from __future__ import annotations

from uuid import uuid4

from .color_scale_job import ColorScaleContext, ColorScaleJob, ColorScaleRequest
from .measurement_job import ProgressSnapshot


class ColorScaleController:
    def __init__(self, state, job_factory=ColorScaleJob):
        self._state = state
        self._job_factory = job_factory
        self._context = None
        self._session_token = uuid4().hex
        self._generation = 1
        self._active = self._closed = False
        self._job = None
        self._on_change = None
        self._suspensions = set()
        self._heavy_busy = False
        self._cancelled_scope = None
        self._attempted = False
        self._policy = self._policy_key()
        self._status = 'idle'
        self._error = None
        self._progress = ProgressSnapshot(0, 0, '')
        self._outcome = None
        self._startup_error = None

    @property
    def context(self):
        return self._context

    @property
    def session_token(self):
        return self._session_token

    @property
    def generation(self):
        return self._generation

    @property
    def status(self):
        return self._status

    @property
    def error(self):
        return self._error

    @property
    def progress_snapshot(self):
        return self._progress

    @property
    def scanning(self):
        return self._status == 'scanning'

    @property
    def blocks_heavy_tasks(self):
        # An ended thread still owns resources until poll/close has joined it.
        return self._job is not None

    def _policy_key(self):
        settings = self._state.settings
        return settings.scale_mode, settings.fixed_source

    def _auto_policy(self):
        return self._policy_key() == ('fixed', 'auto')

    def _notify(self):
        if not self._closed and self._on_change is not None:
            self._on_change()

    def attach(self, on_change):
        if not callable(on_change):
            raise ValueError('on_change must be callable')
        if not self._closed:
            self._on_change = on_change

    def detach(self):
        self._on_change = None

    def _invalidate(self):
        self._generation += 1
        self._attempted = False
        self._error = None
        self._startup_error = None
        if self._job is not None:
            self._job.cancel()

    def set_context(self, context):
        self._set_context(context, settings_loaded=False)

    def load_context(self, context):
        """Begin a loaded configuration's scan epoch without changing its policy."""
        self._set_context(context, settings_loaded=True)

    def _set_context(self, context, *, settings_loaded):
        if not isinstance(context, ColorScaleContext):
            raise ValueError('context must be a ColorScaleContext')
        if self._closed:
            return
        if settings_loaded or context != self._context:
            self._invalidate()
            self._context = context
            self._cancelled_scope = None
        self._state.set_scope(context.scope)
        self.reconcile()

    def activate(self):
        if not self._closed:
            self._active = True
            self.reconcile()

    def suspend(self, reason):
        if not self._closed and reason not in self._suspensions:
            self._suspensions.add(reason)
            self._invalidate()
            self.reconcile()

    def resume(self, reason):
        if not self._closed and reason in self._suspensions:
            self._suspensions.remove(reason)
            self._invalidate()
            self.reconcile()

    def set_heavy_busy(self, busy):
        if not self._closed and bool(busy) != self._heavy_busy:
            self._heavy_busy = bool(busy)
            self._invalidate()
            self.reconcile()

    def request_auto(self):
        if self._closed:
            return
        self._cancelled_scope = None
        self._invalidate()
        self._state.restore_auto()
        self.reconcile()

    def cancel(self):
        if self._closed:
            return
        self._cancelled_scope = self._context.scope if self._context else None
        self._invalidate()
        self._status = 'cancelled'
        self.reconcile()

    def _source_valid(self):
        context = self._context
        try:
            stat = context.source_path.stat()
            identity = context.source_identity
            if (context.source_path.name, stat.st_size, stat.st_mtime_ns) != (
                    identity.filename, identity.size_bytes, identity.modified_time_ns):
                raise ValueError('source changed; reopen the source before retrying')
        except (OSError, ValueError) as exc:
            self._state.invalidate_auto()
            if self._job is not None and self._job.request.generation == self._generation:
                self._invalidate()
            self._attempted = True
            self._error = str(exc)
            self._status = 'failed'
            return False
        return True

    def _eligible(self):
        return (not self._closed and self._active and self._context is not None
                and self._auto_policy() and not self._suspensions and not self._heavy_busy
                and self._cancelled_scope != self._context.scope)

    def _matches(self, request):
        return (self._eligible() and request.context == self._context
                and request.context.scope == self._context.scope
                and request.session_token == self._session_token
                and request.generation == self._generation)

    def _reconcile(self):
        if self._closed:
            return
        policy = self._policy_key()
        if policy != self._policy:
            self._invalidate()
            self._policy = policy
        if self._context is not None and self._auto_policy() and not self._source_valid():
            return
        if self._job is not None:
            if not self._matches(self._job.request):
                self._job.cancel()
                self._status = 'cancelling'
            else:
                self._status = 'scanning'
            return
        if not self._active or self._context is None:
            self._status = 'idle'
        elif self._suspensions or self._heavy_busy:
            self._status = 'paused'
        elif self._cancelled_scope == self._context.scope:
            self._status = 'cancelled'
        elif not self._auto_policy():
            self._status = 'idle'
        elif self._state.fixed_ready or self._state.reuse_cached_auto():
            self._status = 'complete'
        elif not self._attempted:
            self._start()

    def reconcile(self):
        self._reconcile()
        self._notify()

    def _start(self):
        self._attempted = True
        self._error = None
        self._outcome = None
        self._progress = ProgressSnapshot(0, self._context.source_end - self._context.source_start + 1,
                                          'Starting')
        request = ColorScaleRequest(self._context, self._generation, self._session_token)
        job = None
        try:
            job = self._job_factory(request)
            job.start()
        except Exception as exc:
            self._status, self._error = 'failed', str(exc)
            # start() may raise after the thread has already run to completion.
            # Start history, rather than liveness, determines join ownership.
            if job is not None and job.started:
                self._job = job
                self._startup_error = str(exc)
                self._generation += 1
                job.cancel()
            return
        self._job = job
        self._status = 'scanning'

    def poll(self):
        self._reconcile()  # Notice mode/source edits before accepting a result.
        job = self._job
        if job is not None:
            self._progress = job.progress.snapshot()
            if not job.is_alive():
                job.join()
                self._job = None
                outcome = job.outcome
                self._outcome = outcome
                if self._startup_error is not None:
                    self._status, self._error = 'failed', self._startup_error
                    self._startup_error = None
                elif self._matches(job.request):
                    if outcome is None:
                        self._status, self._error = 'failed', 'worker ended without an outcome'
                    elif outcome.request != job.request:
                        self._status, self._error = 'failed', 'worker outcome request mismatch'
                    elif outcome.status == 'complete':
                        if self._source_valid():
                            if self._state.accept_auto(outcome.request.context.scope, outcome.limits):
                                self._status, self._error = 'complete', None
                            else:
                                self._status, self._error = 'failed', 'automatic limits were rejected'
                    else:
                        self._status, self._error = outcome.status, outcome.error
        self._reconcile()
        self._notify()

    def close(self, wait=False):
        if not self._closed:
            self.detach()
            self._closed = True
            self._active = False
            self._invalidate()
            self._status = 'cancelled'
        if self._job is not None and (wait or not self._job.is_alive()):
            self._job.join()
            self._job = None
