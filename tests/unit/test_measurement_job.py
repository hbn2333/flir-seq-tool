from dataclasses import replace
from datetime import datetime, timedelta
import os
import threading

import numpy as np
import pytest

from flir_seq_tool.measurement_job import AnalysisJob, AnalysisProgress, AnalysisRequest, run_analysis
from flir_seq_tool.measurement_models import Roi
from flir_seq_tool.measurement_state import AnalysisKey
from flir_seq_tool.models import TemperatureFrame
from flir_seq_tool.selection import SourceIdentity
from flir_seq_tool.transform import FrameTransform


START = datetime(2026, 10, 22)


class FakeReader:
    width = height = num_frames = 2
    def __init__(self, factory, path):
        self.factory, self.path = factory, path
        self.requested_frames = []
        self.enter_thread = self.exit_thread = None

    def __enter__(self):
        self.enter_thread = threading.get_ident()
        return self

    def __exit__(self, *args):
        self.exit_thread = threading.get_ident()
        if self.factory.on_exit:
            self.factory.on_exit()

    def timestamp_at(self, index):
        return START + timedelta(milliseconds=20 * index)

    def temperature_frame_at(self, index):
        self.requested_frames.append(index)
        if self.factory.on_read:
            self.factory.on_read(index)
        return TemperatureFrame(index, self.timestamp_at(index), np.array([[1., 3.], [5., 7.]]) + 10 * index)


class ReaderFactory:
    def __init__(self):
        self.instances = []
        self.on_read = self.on_exit = None

    def __call__(self, path):
        reader = FakeReader(self, path)
        self.instances.append(reader)
        return reader


@pytest.fixture
def request_and_factory(tmp_path):
    source = tmp_path / "source.seq"
    source.write_bytes(b"source")
    stat = source.stat()
    identity = SourceIdentity(source.name, stat.st_size, stat.st_mtime_ns, 2, 2, 2, START,
                              START + timedelta(milliseconds=20))
    transform = FrameTransform.identity(2, 2)
    rois = (Roi("r1", "ROI 1", 0, 0, 2, 2, "#123456", True),
            Roi("r2", "ROI 2", 1, 0, 1, 2, "#abcdef", False))
    key = AnalysisKey.from_inputs(identity, 0, 1, transform, rois)
    return AnalysisRequest(source, identity, 0, 1, transform, rois, key), ReaderFactory()


def run(request, factory, tmp_path, cancel=None, progress=None):
    return run_analysis(request, reader_factory=factory, cancel_event=cancel or threading.Event(),
                        progress=progress or AnalysisProgress(), temp_root=tmp_path)


def test_cancel_before_start_creates_no_result(request_and_factory, tmp_path):
    request, factory = request_and_factory
    cancel = threading.Event()
    cancel.set()
    outcome = run(request, factory, tmp_path, cancel)
    assert outcome.status == "cancelled" and outcome.store is None
    assert not factory.instances
    assert not list(tmp_path.glob("flir-measure-*"))


def test_complete_analysis_reads_each_frame_once_for_all_rois(request_and_factory, tmp_path):
    request, factory = request_and_factory
    progress = AnalysisProgress()
    outcome = run(request, factory, tmp_path, progress=progress)
    assert outcome.status == "complete" and outcome.key == request.key
    try:
        rows = list(outcome.store.iter_rows())
        assert len(rows) == 4
        first = [row for row in rows if row["roi_id"] == "r1"]
        assert [row["mean_c"] for row in first] == [4., 14.]
        assert [row["delta_c"] for row in first] == [6., 6.]
        assert [row["relative_s"] for row in first] == [0., .02]
        assert [row["timestamp"] for row in first] == [START.isoformat(), (START + timedelta(milliseconds=20)).isoformat()]
        assert factory.instances[0].requested_frames == [0, 1]
        assert factory.instances[0].enter_thread == factory.instances[0].exit_thread
        assert (progress.snapshot().completed, progress.snapshot().total) == (2, 2)
    finally:
        outcome.store.cleanup()


@pytest.mark.parametrize("failure", ["read", "cancel", "publish-cancel", "source-change", "write"])
def test_failure_and_cancel_close_reader_and_remove_partial_store(request_and_factory, tmp_path, monkeypatch, failure):
    request, factory = request_and_factory
    cancel = threading.Event()
    if failure == "read":
        def on_read(index):
            if index == 1:
                raise OSError("read failed")
        factory.on_read = on_read
    elif failure == "cancel":
        factory.on_read = lambda index: cancel.set()
    elif failure == "publish-cancel":
        factory.on_exit = cancel.set
    elif failure == "source-change":
        stat = request.source_path.stat()
        factory.on_read = lambda index: os.utime(request.source_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    else:
        def fail_write(self, rows):
            raise OSError("disk full")
        monkeypatch.setattr("flir_seq_tool.measurement_job.ResultStore.append_rows", fail_write)
    outcome = run(request, factory, tmp_path, cancel)
    assert outcome.status == ("cancelled" if "cancel" in failure else "failed")
    assert outcome.store is None
    reader = factory.instances[0]
    assert reader.exit_thread == reader.enter_thread
    if failure == "read":
        assert "source frame 1" in outcome.error
    assert not list(tmp_path.glob("flir-measure-*"))


def test_job_reader_and_write_connection_belong_to_worker_thread(request_and_factory, tmp_path):
    request, factory = request_and_factory
    job = AnalysisJob(request, reader_factory=factory, temp_root=tmp_path)
    job.start()
    job.join(timeout=5)
    assert not job.is_alive()
    assert job.outcome.status == "complete"
    try:
        reader = factory.instances[0]
        assert reader.enter_thread == reader.exit_thread != threading.get_ident()
        # A live worker SQLite connection would raise ProgrammingError here.
        assert len(list(job.outcome.store.iter_rows())) == 4
    finally:
        job.outcome.store.cleanup()


@pytest.mark.parametrize("changes", [{"rois": ()}, {"source_start": True}, {"source_end": 2},
                                    {"source_start": 1}, {"key": None},
                                    {"transform": FrameTransform.identity(3, 2)}])
def test_request_rejects_inconsistent_frozen_inputs(request_and_factory, changes):
    request, factory = request_and_factory
    with pytest.raises(ValueError):
        replace(request, **changes)


def test_cleanup_failure_still_returns_failed_outcome_with_owned_resource(request_and_factory, tmp_path, monkeypatch):
    from flir_seq_tool.measurement_store import ResultStore
    request, factory = request_and_factory
    factory.on_read = lambda index: (_ for _ in ()).throw(OSError("read failed"))
    original = ResultStore.cleanup
    retained = []
    def fail_cleanup(self):
        retained.append(self)
        self.close()
        raise OSError("cleanup blocked")
    monkeypatch.setattr(ResultStore, "cleanup", fail_cleanup)
    outcome = None
    try:
        outcome = run(request, factory, tmp_path)
        assert outcome.status == "failed" and outcome.store is None
        assert outcome.cleanup_store is not None
        assert "cleanup blocked" in outcome.error
    finally:
        for store in retained:
            original(store)


def test_cancel_during_sqlite_completion_is_not_published(request_and_factory, tmp_path, monkeypatch):
    from flir_seq_tool.measurement_store import ResultStore
    request, factory = request_and_factory
    cancel = threading.Event()
    original = ResultStore.complete
    def complete_and_cancel(self, **kwargs):
        original(self, **kwargs)
        cancel.set()
    monkeypatch.setattr(ResultStore, "complete", complete_and_cancel)
    outcome = run(request, factory, tmp_path, cancel)
    try:
        assert outcome.status == "cancelled" and outcome.store is None
        assert not list(tmp_path.glob("flir-measure-*"))
    finally:
        if outcome.store is not None:
            outcome.store.cleanup()
