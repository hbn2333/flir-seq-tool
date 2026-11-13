from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from flir_seq_tool.color_scale import ColorLimits
from flir_seq_tool.selection import SourceIdentity
from flir_seq_tool.transform import FrameTransform
from flir_seq_tool.video_export import build_video_request, assert_request_current, publish_video


@pytest.fixture
def video_session(tmp_path):
    path = tmp_path / 'source.seq'
    path.write_bytes(b'original')
    stat = path.stat()
    source = SourceIdentity(path.name, stat.st_size, stat.st_mtime_ns, 4, 3, 10,
                            datetime(2026, 1, 1), datetime(2026, 1, 2))
    return SimpleNamespace(reader=SimpleNamespace(path=path),
        source_identity=lambda: source, measure_source_range=lambda: (2, 5),
        applied_transform=FrameTransform.identity(4, 3),
        color_limits=ColorLimits(20, 40), colormap='magma')


def test_frozen_request_uses_confirmed_range(video_session, tmp_path):
    request = build_video_request(video_session, tmp_path / 'out.MP4', 5)
    assert (request.start_frame, request.end_frame) == (2, 5)
    assert request.speed == 5
    with pytest.raises(FrozenInstanceError):
        request.speed = 1


@pytest.mark.parametrize('change', ['new', 'modified', 'deleted', 'source'])
def test_snapshot_changes_block_publication(video_session, tmp_path, change):
    target = tmp_path / 'out.mp4'
    if change in ('modified', 'deleted'):
        target.write_bytes(b'old')
    request = build_video_request(video_session, target, 1)
    if change == 'deleted':
        target.unlink()
    elif change == 'source':
        video_session.reader.path.write_bytes(b'changed source')
    else:
        target.write_bytes(b'external')
    partial = tmp_path / 'own.partial.mp4'
    partial.write_bytes(b'new video')
    before = target.read_bytes() if target.exists() else None
    with pytest.raises((RuntimeError, ValueError)):
        publish_video(request, partial)
    assert (target.read_bytes() if target.exists() else None) == before


def test_protected_paths(video_session, tmp_path):
    with pytest.raises(ValueError):
        build_video_request(video_session, Path(r'D:\carbonfoam\infrared\raw\bad.mp4'), 1)
    with pytest.raises(ValueError):
        build_video_request(video_session, video_session.reader.path, 1)
    alias = tmp_path / 'alias.mp4'
    alias.hardlink_to(video_session.reader.path)
    with pytest.raises(ValueError):
        build_video_request(video_session, alias, 1)


def test_successful_atomic_publication(video_session, tmp_path):
    target = tmp_path / 'out.mp4'
    target.write_bytes(b'old')
    request = build_video_request(video_session, target, 1)
    partial = tmp_path / 'own.partial.mp4'
    partial.write_bytes(b'verified video')
    assert_request_current(request)
    publish_video(request, partial)
    assert target.read_bytes() == b'verified video'
    assert not partial.exists()


def scale_session(session, *, mode='fixed', source='auto', ready=False):
    from flir_seq_tool.color_scale_state import ColorScaleState
    from flir_seq_tool.color_scale_models import ColorScaleScope
    from flir_seq_tool.selection import DisplaySettings
    scope = ColorScaleScope.from_inputs(session.source_identity(), 2, 5, session.applied_transform)
    state = ColorScaleState(DisplaySettings('magma', 10, 80, mode, source))
    state.set_scope(scope)
    if ready:
        assert state.accept_auto(scope, ColorLimits(10, 80))
    session.color_scale = state
    return state, scope


def test_pending_auto_blocks_video(video_session, tmp_path):
    scale_session(video_session)
    with pytest.raises(RuntimeError, match='Auto.*manual.*Per-frame'):
        build_video_request(video_session, tmp_path / 'pending.mp4', 1)


def test_ready_auto_freezes_full_confirmed_scope(video_session, tmp_path):
    state, scope = scale_session(video_session, ready=True)
    request = build_video_request(video_session, tmp_path / 'ready.mp4', 10)
    assert request.display == state.settings
    assert request.display.auto_scope == scope
    # The full 2..5 scope, including any hot unsampled frame, is reused at 10x.
    assert (scope.source_start, scope.source_end) == (request.start_frame, request.end_frame)
    state.apply_manual(0, 1)
    assert (request.display.vmin_c, request.display.vmax_c) == (10, 80)


@pytest.mark.parametrize('change', ['source', 'range', 'transform'])
def test_stale_auto_scope_blocks_even_if_state_ready(video_session, tmp_path, change):
    state, _ = scale_session(video_session, ready=True)
    if change == 'source':
        video_session.reader.path.write_bytes(b'modified source contents')
    elif change == 'range':
        video_session.measure_source_range = lambda: (3, 5)
    else:
        from dataclasses import replace
        from flir_seq_tool.transform import CropRect
        video_session.applied_transform = replace(video_session.applied_transform, crop=CropRect(0, 0, 2, 2))
    assert state.fixed_ready
    with pytest.raises(RuntimeError, match='Auto'):
        build_video_request(video_session, tmp_path / 'stale.mp4', 1)


@pytest.mark.parametrize('mode,source', [('fixed', 'manual'), ('per_frame', 'auto')])
def test_manual_and_per_frame_freeze_policy_without_matching_auto(video_session, tmp_path, mode, source):
    state, _ = scale_session(video_session, mode=mode, source=source)
    video_session.measure_source_range = lambda: (3, 5)
    request = build_video_request(video_session, tmp_path / 'permitted.mp4', 1)
    assert request.display == state.settings
    assert (request.display.vmin_c, request.display.vmax_c) == (10, 80)


@pytest.mark.parametrize('mode,source,label', [('fixed', 'auto', 'Fixed automatic'),
    ('fixed', 'manual', 'Fixed manual'), ('per_frame', 'auto', 'Per-frame adaptive')])
def test_scale_summary_describes_frozen_policy(mode, source, label):
    from flir_seq_tool.video_export import video_scale_summary
    from flir_seq_tool.selection import DisplaySettings
    summary = video_scale_summary(DisplaySettings('magma', 10, 80, mode, source))
    assert label in summary
    if mode == 'per_frame':
        assert '10' not in summary and '80' not in summary
    else:
        assert '10–80 °C' in summary
