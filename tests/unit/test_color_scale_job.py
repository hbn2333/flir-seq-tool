from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta
import os
import threading
import weakref

import numpy as np
import pytest

from flir_seq_tool.color_scale import ColorLimits
from flir_seq_tool.color_scale_job import (
    ColorScaleContext, ColorScaleJob, ColorScaleOutcome, ColorScaleRequest, run_color_scale,
)
from flir_seq_tool.color_scale_models import ColorScaleScope
from flir_seq_tool.frame_transformer import FrameTransformer
from flir_seq_tool.measurement_job import AnalysisProgress
from flir_seq_tool.models import TemperatureFrame
from flir_seq_tool.selection import SourceIdentity
from flir_seq_tool.transform import CropRect, FrameTransform, RotationSettings


START = datetime(2026, 11, 13)


class ReadOnlyReader:
    def __init__(self, factory, path):
        assert path == factory.path and path.is_file()
        self.factory = factory
        self.height, self.width = factory.frames[0].shape
        self.num_frames = len(factory.frames)
        self.temperature_reads = []
        self.timestamp_reads = []
        self.enter_thread = self.exit_thread = None

    def __enter__(self):
        self.enter_thread = threading.get_ident()
        return self

    def __exit__(self, *args):
        self.exit_thread = threading.get_ident()
        if self.factory.on_exit:
            self.factory.on_exit()

    def timestamp_at(self, index):
        self.timestamp_reads.append(index)
        return START + timedelta(seconds=index)

    def temperature_frame_at(self, index):
        self.temperature_reads.append(index)
        if self.factory.on_read:
            self.factory.on_read(index)
        frame = TemperatureFrame(index, START + timedelta(seconds=index), self.factory.frames[index].copy())
        self.factory.references.append(weakref.ref(frame.temperature_c))
        return frame


class ReaderFactory:
    def __init__(self, path, frames):
        self.path, self.frames = path, frames
        self.instances, self.references = [], []
        self.on_read = self.on_exit = None

    def __call__(self, path):
        reader = ReadOnlyReader(self, path)
        self.instances.append(reader)
        return reader


@pytest.fixture
def make_request(tmp_path):
    def make(frames, start=0, end=None, transform=None):
        path = tmp_path / f"source-{len(list(tmp_path.iterdir()))}.seq"
        path.write_bytes(b"source fixture")  # Exactly once; readers never write.
        frames = [np.asarray(frame, dtype=float) for frame in frames]
        height, width = frames[0].shape
        stat = path.stat()
        identity = SourceIdentity(path.name, stat.st_size, stat.st_mtime_ns, width, height,
                                  len(frames), START, START + timedelta(seconds=len(frames) - 1))
        context = ColorScaleContext(path, identity, start, len(frames) - 1 if end is None else end,
                                    transform or FrameTransform.identity(width, height))
        return ColorScaleRequest(context, 1, "session"), ReaderFactory(path, frames)
    return make


def run(request, factory, cancel=None, progress=None):
    return run_color_scale(request, reader_factory=factory, cancel_event=cancel or threading.Event(),
                           progress=progress or AnalysisProgress())


def change_mtime(request):
    path = request.context.source_path
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))


def test_scans_all_selected_frames_and_excludes_crop_outside_hotspot(make_request):
    frames = [[[100, 20], [100, 21]]] * 4 + [[[100, 40], [100, 22]]]
    transform = FrameTransform(2, 2, RotationSettings(0), CropRect(1, 0, 1, 2))
    request, factory = make_request(frames, transform=transform)
    progress = AnalysisProgress()
    outcome = run(request, factory, progress=progress)
    assert outcome.status == "complete" and outcome.limits == ColorLimits(20, 40)
    assert outcome.request == request and outcome.completed == 5 and outcome.empty_frames == 0
    reader = factory.instances[0]
    assert reader.temperature_reads == [0, 1, 2, 3, 4]
    assert reader.timestamp_reads == [0, 4, 0, 4]
    assert reader.exit_thread == reader.enter_thread
    assert progress.snapshot().completed == progress.snapshot().total == 5
    assert progress.snapshot().message == "Source frame 4"
    assert all(reference() is None for reference in factory.references)


def test_inclusive_interval_temperature_reads_only_selected_frames(make_request):
    request, factory = make_request([[[v]] for v in (100, 20, 30, 40, 200)], 1, 3)
    outcome = run(request, factory)
    assert outcome.limits == ColorLimits(20, 40)
    assert factory.instances[0].temperature_reads == [1, 2, 3]


@pytest.mark.parametrize("angle", [90, 10])
def test_rotates_then_crops_with_existing_transformer(make_request, angle):
    frame = np.arange(25, dtype=float).reshape(5, 5)
    transform = FrameTransform.identity(5, 5).with_angle(angle)
    crop = transform.crop
    transform = replace(transform, crop=CropRect(crop.x, crop.y, 1, crop.height))
    request, factory = make_request([frame, frame + 40], transform=transform)
    expected = FrameTransformer(transform).apply(frame)
    limits = run(request, factory).limits
    assert (limits.vmin, limits.vmax) == pytest.approx((float(expected.min()), float(expected.max() + 40)))


@pytest.mark.parametrize("values,expected", [([20, 40], ColorLimits(20, 40)), ([20], ColorLimits(19.5, 20.5))])
def test_constant_expansion_occurs_only_after_global_merge(make_request, values, expected):
    request, factory = make_request([[[value]] for value in values])
    assert run(request, factory).limits == expected


def test_skips_empty_frames_and_counts_them(make_request):
    request, factory = make_request([[[np.nan, np.inf]], [[20, -np.inf]], [[np.nan, np.nan]], [[40, 30]]])
    outcome = run(request, factory)
    assert outcome.status == "complete" and outcome.limits == ColorLimits(20, 40)
    assert outcome.completed == 4 and outcome.empty_frames == 2


def test_all_nonfinite_is_failed_without_limits(make_request):
    request, factory = make_request([[[np.nan]], [[np.inf]]])
    outcome = run(request, factory)
    assert outcome.status == "failed" and outcome.limits is None
    assert "finite" in outcome.error and outcome.completed == outcome.empty_frames == 2


def test_read_exception_identifies_original_source_frame(make_request):
    request, factory = make_request([[[20]]] * 5, 2, 4)
    def fail(index):
        if index == 3:
            raise OSError("read failed")
    factory.on_read = fail
    outcome = run(request, factory)
    assert outcome.status == "failed" and outcome.limits is None and outcome.completed == 1
    assert "source frame 3" in outcome.error and "read failed" in outcome.error
    assert factory.instances[0].exit_thread is not None


@pytest.mark.parametrize("when", ["before", "during", "close", "publish"])
def test_source_mutation_never_publishes_limits(make_request, monkeypatch, when):
    request, factory = make_request([[[20]], [[40]]])
    if when == "before":
        change_mtime(request)
    elif when == "during":
        factory.on_read = lambda index: change_mtime(request) if index == 1 else None
    elif when == "close":
        factory.on_exit = lambda: change_mtime(request)
    else:
        from flir_seq_tool import color_scale_job
        original = color_scale_job.limits_from_extrema
        def mutate(*args):
            limits = original(*args)
            change_mtime(request)
            return limits
        monkeypatch.setattr(color_scale_job, "limits_from_extrema", mutate)
    outcome = run(request, factory)
    assert outcome.status == "failed" and outcome.limits is None
    assert factory.instances[0].exit_thread is not None
    if when == "before":
        assert factory.instances[0].temperature_reads == []


@pytest.mark.parametrize("when", ["before", "mid", "last", "close", "publish", "read-error"])
def test_cancellation_never_publishes_partial_limits(make_request, monkeypatch, when):
    request, factory = make_request([[[20]], [[40]]])
    cancel = threading.Event()
    if when == "before":
        cancel.set()
    elif when in ("mid", "last", "read-error"):
        def on_read(index):
            if index == (1 if when == "last" else 0):
                cancel.set()
                if when == "read-error":
                    raise OSError("cancelled read")
        factory.on_read = on_read
    elif when == "close":
        factory.on_exit = cancel.set
    else:
        from flir_seq_tool import color_scale_job
        original = color_scale_job.limits_from_extrema
        def cancel_publish(*args):
            limits = original(*args)
            cancel.set()
            return limits
        monkeypatch.setattr(color_scale_job, "limits_from_extrema", cancel_publish)
    outcome = run(request, factory, cancel)
    assert outcome.status == "cancelled" and outcome.limits is None and outcome.request == request
    if when == "before":
        assert factory.instances == []
    else:
        assert factory.instances[0].exit_thread is not None


def test_job_owns_independent_non_daemon_reader_thread(make_request):
    request, factory = make_request([[[20]], [[40]]])
    entered, release = threading.Event(), threading.Event()
    def on_read(index):
        entered.set()
        assert release.wait(5)
        assert not threading.current_thread().daemon
    factory.on_read = on_read
    job = ColorScaleJob(request, reader_factory=factory)
    assert job.outcome is None
    job.start()
    try:
        assert entered.wait(5) and job.is_alive()
        job.cancel()
    finally:
        release.set()
        job.join(5)
    assert not job.is_alive() and job.outcome.status == "cancelled"
    assert factory.instances[0].enter_thread == factory.instances[0].exit_thread != threading.get_ident()


def test_job_success_and_progress(make_request):
    request, factory = make_request([[[20]], [[40]]])
    job = ColorScaleJob(request, reader_factory=factory)
    job.start()
    job.join(5)
    assert job.outcome.status == "complete" and job.outcome.request == request
    assert job.progress.snapshot().completed == 2


@pytest.mark.parametrize("changes", [{"source_identity": None}, {"transform": None}, {"source_start": True},
    {"source_end": 2}, {"source_start": -1}, {"source_start": 1, "source_end": 0}, {"source_end": 0.5},
    {"source_path": None}, {"transform": FrameTransform.identity(2, 1)}])
def test_context_rejects_bad_frozen_inputs(make_request, changes):
    request, _ = make_request([[[20]], [[40]]])
    with pytest.raises(ValueError):
        replace(request.context, **changes)


@pytest.mark.parametrize("changes", [{"context": None}, {"generation": True}, {"generation": 0},
    {"generation": 1.5}, {"session_token": ""}, {"session_token": None}])
def test_request_rejects_bad_inputs(make_request, changes):
    request, _ = make_request([[[20]]])
    with pytest.raises(ValueError):
        replace(request, **changes)


def test_frozen_context_scope(make_request):
    request, _ = make_request([[[20]]])
    context = request.context
    assert context.scope == ColorScaleScope.from_inputs(context.source_identity, 0, 0, context.transform)
    with pytest.raises(FrozenInstanceError):
        context.source_start = 1
    with pytest.raises(FrozenInstanceError):
        request.generation = 2


@pytest.mark.parametrize("status,limits", [("unknown", None), ("complete", None),
    ("failed", ColorLimits(20, 40)), ("cancelled", ColorLimits(20, 40)), ("complete", (20, 40))])
def test_outcome_rejects_invalid_publication(make_request, status, limits):
    request, _ = make_request([[[20]]])
    with pytest.raises(ValueError):
        ColorScaleOutcome(status, request, limits, 0, 0, None)
