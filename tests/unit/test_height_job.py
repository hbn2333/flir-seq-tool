from datetime import datetime, timedelta
from datetime import timezone
import importlib
import os
import threading

import numpy as np
import pytest

from flir_seq_tool.measurement_job import AnalysisRequest
from flir_seq_tool.measurement_models import Roi
from flir_seq_tool.measurement_state import AnalysisKey
from flir_seq_tool.models import TemperatureFrame
from flir_seq_tool.selection import SourceIdentity
from flir_seq_tool.transform import FrameTransform


START = datetime(2026, 10, 30)


class Reader:
    width, height, num_frames = 2, 3, 5

    def __init__(self, path):
        self.path, self.reads = path, []
        self.timestamps = [START, START + timedelta(seconds=2), START + timedelta(seconds=2), START - timedelta(seconds=1), START + timedelta(seconds=1)]
        self.on_read = None

    def __enter__(self):
        self.worker_thread = threading.get_ident()
        return self

    def __exit__(self, *args):
        self.closed = True

    def timestamp_at(self, index):
        return self.timestamps[index]

    def temperature_frame_at(self, index):
        self.reads.append(index)
        if self.on_read:
            self.on_read(index)
        return TemperatureFrame(index, self.timestamps[index], np.array([[1., 3.], [np.nan, np.inf], [5., 7.]]))


def make_request(tmp_path):
    path = tmp_path / 'source.seq'
    path.write_bytes(b'source')
    stat = path.stat()
    identity = SourceIdentity(path.name, stat.st_size, stat.st_mtime_ns, 2, 3, 5, START, START + timedelta(seconds=1))
    transform = FrameTransform.identity(2, 3)
    rois = (Roi('b', 'B', 0, 0, 2, 3, '#123456', False), Roi('a', 'A', 0, 0, 1, 3, '#abcdef', True))
    key = AnalysisKey.from_inputs(identity, 0, 4, transform, rois)
    return AnalysisRequest(path, identity, 0, 4, transform, rois, key)


def job_type():
    try:
        return importlib.import_module('flir_seq_tool.height_job').HeightAnalysisJob
    except ModuleNotFoundError:
        pytest.fail('HeightAnalysisJob is not implemented')


def test_height_worker_scans_once_all_rois_shared_t0_and_keeps_bad_times(tmp_path):
    request = make_request(tmp_path)
    reader = Reader(request.source_path)
    job = job_type()(request, reader_factory=lambda _: reader, temp_root=tmp_path)
    job.start()
    job.join(5)
    assert not job.is_alive() and job.outcome.status == 'complete', job.outcome
    store = job.outcome.store
    try:
        assert store.has_height and store.key_json == request.key.to_json()
        assert reader.reads == [0, 1, 2, 3, 4] and reader.closed
        assert reader.worker_thread != threading.get_ident()
        rows = list(store.iter_height_rows())
        assert len(rows) == 30
        first_roi = [r for r in rows if r['roi_id'] == 'b' and r['roi_row_index'] == 0]
        assert [r['relative_s'] for r in first_roi] == [0, 2, 2, -1, 1]
        assert [r['time_status'] for r in first_roi] == ['ok', 'ok', 'duplicate', 'backwards', 'ok']
        assert rows[1]['valid_pixels'] == 0 and rows[1]['row_mean_c'] is None
        assert len(list(store.iter_rows())) == 10
        assert job.progress.snapshot().completed == 5
    finally:
        store.cleanup()


@pytest.mark.parametrize('failure', ['cancel-before', 'cancel-during', 'missing-time', 'read', 'source-change', 'disk'])
def test_height_worker_failure_cancel_cleanup(tmp_path, monkeypatch, failure):
    request = make_request(tmp_path)
    reader = Reader(request.source_path)
    job = job_type()(request, reader_factory=lambda _: reader, temp_root=tmp_path)
    if failure == 'cancel-before':
        job.cancel()
    elif failure == 'cancel-during':
        reader.on_read = lambda _: job.cancel()
    elif failure == 'missing-time':
        reader.timestamps[1] = None
    elif failure == 'read':
        reader.on_read = lambda i: (_ for _ in ()).throw(OSError('read failed'))
    elif failure == 'source-change':
        stat = request.source_path.stat()
        reader.on_read = lambda _: os.utime(request.source_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000))
    else:
        monkeypatch.setattr('flir_seq_tool.height_store.HeightResultStore.append_height_rows',
                            lambda *a, **k: (_ for _ in ()).throw(OSError('disk full')))
    job.start()
    job.join(5)
    assert job.outcome.status == ('cancelled' if failure.startswith('cancel') else 'failed')
    assert job.outcome.store is None and not list(tmp_path.glob('flir-measure-*'))
    if failure == 'missing-time':
        assert 'source frame 1' in job.outcome.error


@pytest.mark.parametrize('failure', ['first-time', 'timezone', 'complete-cancel', 'cleanup'])
def test_time_failures_and_final_boundary_cancellation_are_not_complete(tmp_path, monkeypatch, failure):
    request = make_request(tmp_path)
    reader = Reader(request.source_path)
    Job = job_type()
    job = Job(request, reader_factory=lambda _: reader, temp_root=tmp_path)
    from flir_seq_tool.height_store import HeightResultStore
    original_cleanup = HeightResultStore.cleanup
    owned = []
    if failure == 'first-time':
        reader.on_read = lambda i: reader.timestamps.__setitem__(i, None) if i == 0 else None
    elif failure == 'timezone':
        reader.timestamps[1] = START.replace(tzinfo=timezone.utc)
    elif failure == 'complete-cancel':
        original_complete = HeightResultStore.complete
        def complete(self, **kwargs):
            original_complete(self, **kwargs)
            job.cancel()
        monkeypatch.setattr(HeightResultStore,'complete',complete)
    else:
        reader.on_read = lambda i: (_ for _ in ()).throw(OSError('read failed'))
        def cleanup(self):
            self.close()
            owned.append(self)
            raise OSError('cleanup blocked')
        monkeypatch.setattr(HeightResultStore,'cleanup',cleanup)
    job.start()
    job.join(5)
    try:
        assert job.outcome.status == ('cancelled' if failure == 'complete-cancel' else 'failed')
        if failure == 'cleanup':
            assert job.outcome.cleanup_store is not None and 'cleanup blocked' in job.outcome.error
        else:
            assert not list(tmp_path.glob('flir-measure-*'))
    finally:
        for store in owned:
            original_cleanup(store)


def test_cancel_during_integrity_validation_is_cancelled_and_cleans_store(tmp_path, monkeypatch):
    request=make_request(tmp_path)
    reader=Reader(request.source_path)
    job=job_type()(request,reader_factory=lambda _:reader,temp_root=tmp_path)
    from flir_seq_tool.height_store import HeightResultStore
    original=HeightResultStore._validate_height
    def cancelling(self,*args):
        assert getattr(self,'_validation_cancel_event',None) is job._cancel
        job.cancel()
        return original(self,*args)
    monkeypatch.setattr(HeightResultStore,'_validate_height',cancelling)
    job.start()
    job.join(5)
    assert job.outcome.status=='cancelled' and not list(tmp_path.glob('flir-measure-*'))
