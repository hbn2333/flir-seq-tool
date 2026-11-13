from pathlib import Path
from types import SimpleNamespace

import pytest

from flir_seq_tool.video_controller import VideoController


class FakeJob:
    def __init__(self, request):
        self.request = request
        self.events = []
        self.closed = self.confirmed = self.started = self.cancelled = False
        self.cancel_allowed = True

    def start(self):
        self.started = True

    def poll(self):
        events, self.events = self.events, []
        return events

    def confirm(self):
        self.confirmed = True

    def cancel(self):
        self.cancelled = True
        return True

    def close(self):
        self.closed = True


@pytest.fixture
def controller(tmp_path, monkeypatch):
    import flir_seq_tool.video_controller as module
    frozen = SimpleNamespace(target_path=tmp_path / 'out.mp4')
    monkeypatch.setattr(module, 'build_video_request', lambda session, target, speed: frozen)
    calls = []
    session = SimpleNamespace(reader=SimpleNamespace(path=Path('source.seq')),
                              measure_source_range=lambda: (2, 8))
    dialogs = SimpleNamespace(choose_video_speed=lambda: 5,
        choose_video_export=lambda folder, name: tmp_path / name, confirm=lambda summary: True)
    result = VideoController(session, dialogs, tmp_path, FakeJob,
        lambda busy: calls.append(('busy', busy)), lambda message: calls.append(('message', message)))
    return result, calls, dialogs


def test_preflight_confirm_progress_and_finish(controller):
    control, calls, _ = controller
    control.start()
    job = control.job
    assert job.started and ('busy', True) in calls
    job.events = [dict(kind='ready', summary='summary')]
    control.poll()
    assert job.confirmed
    job.events = [dict(kind='progress', stage='encoding', current=1, total=2)]
    control.poll()
    assert '50.0%' in calls[-1][1]
    job.events = [dict(kind='result', status='succeeded', message='done')]
    control.poll()
    assert job.closed and control.job is None
    assert calls[-1] == ('busy', False)


def test_cancel_ready_and_close(controller):
    control, calls, dialogs = controller
    dialogs.confirm = lambda _: False
    control.start()
    job = control.job
    job.events = [dict(kind='ready', summary='summary')]
    control.poll()
    assert job.cancelled and not job.confirmed
    control.close()
    assert job.closed
    before = list(calls)
    control.poll()
    assert calls == before


def test_speed_cancel_does_not_start(controller):
    control, calls, dialogs = controller
    dialogs.choose_video_speed = lambda: None
    control.start()
    assert control.job is None
    assert calls[-1] == ('busy', False)


def test_direct_start_pending_auto_reports_action_and_clears_busy(tmp_path):
    from datetime import datetime
    from flir_seq_tool.selection import SourceIdentity
    from flir_seq_tool.transform import FrameTransform
    from flir_seq_tool.color_scale_state import ColorScaleState
    path = tmp_path / 'source.seq'
    path.write_bytes(b'seq')
    stat = path.stat()
    source = SourceIdentity(path.name, stat.st_size, stat.st_mtime_ns, 4, 3, 10,
                            datetime(2026, 1, 1), datetime(2026, 1, 2))
    session = SimpleNamespace(reader=SimpleNamespace(path=path), source_identity=lambda: source,
        measure_source_range=lambda: (2, 5), applied_transform=FrameTransform.identity(4, 3),
        color_scale=ColorScaleState())
    calls, jobs = [], []
    dialogs = SimpleNamespace(choose_video_speed=lambda: 1,
        choose_video_export=lambda *_: tmp_path / 'out.mp4')
    control = VideoController(session, dialogs, tmp_path, jobs.append,
        lambda value: calls.append(value), lambda message: calls.append(message))
    control.start()
    assert jobs == [] and control.job is None
    assert calls[0] is True and calls[-1] is False
    assert any(isinstance(item, str) and 'Auto' in item and 'manual' in item
               and 'Per-frame' in item for item in calls)
