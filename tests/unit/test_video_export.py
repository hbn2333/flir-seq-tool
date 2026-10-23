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
