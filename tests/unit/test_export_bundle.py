from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from flir_seq_tool.selection import SourceIdentity, DisplaySettings, build_selection_document
from flir_seq_tool.transform import FrameTransform


START = datetime(2026, 8, 28, 9)


def view_document(source, count=5, angle=0.0):
    stat = source.stat()
    identity = SourceIdentity(source.name, stat.st_size, stat.st_mtime_ns, 20, 12, 20,
                              START, START + timedelta(seconds=19))
    return build_selection_document(
        source=identity, source_start_frame=3, source_end_frame=3 + count - 1,
        start_timestamp=START + timedelta(seconds=3),
        end_timestamp=START + timedelta(seconds=3 + count - 1),
        display=DisplaySettings("inferno", 20, 40),
        transform=FrameTransform.identity(20, 12).with_angle(angle),
    )


class MetadataReader:
    def __init__(self, document):
        self.width = document.source.width
        self.height = document.source.height
        self.num_frames = document.selection.frame_count
        self.start = document.selection.start_timestamp

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def timestamp_at(self, index):
        return self.start + timedelta(seconds=index)

    def temperature_frame_at(self, index):
        raise AssertionError("metadata builder must not request temperatures")


@pytest.mark.parametrize("count", [1, 5])
@pytest.mark.parametrize("angle", [0.0, -2.3])
def test_clip_document_uses_child_identity_and_preserves_view(tmp_path, count, angle):
    from flir_seq_tool.export_bundle import build_clip_document
    source = tmp_path / "source.seq"
    source.write_bytes(b"source")
    view = view_document(source, count, angle)
    partial = tmp_path / "unique.partial.seq"
    partial.write_bytes(b"child")
    target = tmp_path / "clip.seq"
    child = build_clip_document(partial, target, view, reader_factory=lambda _: MetadataReader(view))
    assert child.source.filename == "clip.seq"
    assert child.source.size_bytes == partial.stat().st_size
    assert child.source.modified_time_ns == partial.stat().st_mtime_ns
    assert (child.source.width, child.source.height) == (20, 12)
    assert child.source.num_frames == count
    assert (child.selection.source_start_frame, child.selection.source_end_frame) == (0, count - 1)
    assert child.selection.start_timestamp == view.selection.start_timestamp
    assert child.selection.end_timestamp == view.selection.end_timestamp
    assert child.transform == view.transform
    assert child.display == view.display


@pytest.mark.parametrize("field,value", [("width", 19), ("num_frames", 4), ("start", START)])
def test_clip_document_rejects_mismatched_output(tmp_path, field, value):
    from flir_seq_tool.export_bundle import build_clip_document
    source = tmp_path / "source.seq"
    source.write_bytes(b"source")
    view = view_document(source)
    reader = MetadataReader(view)
    setattr(reader, field, value)
    with pytest.raises(ValueError, match="mismatch"):
        build_clip_document(source, tmp_path / "clip.seq", view, reader_factory=lambda _: reader)


def paired_request(tmp_path):
    from flir_seq_tool.extraction import build_export_request
    source = tmp_path / "source.seq"
    source.write_bytes(b"source")
    view = view_document(source)
    return build_export_request(
        source_path=source, clips_root=tmp_path / "clips", clip_name="clip",
        source_start_frame=3, source_end_frame=7,
        start_timestamp=view.selection.start_timestamp, end_timestamp=view.selection.end_timestamp,
        source_num_frames=20, selection_document=view,
    )


def confirm(request):
    from flir_seq_tool.extraction import preflight_export
    return preflight_export(request, work_source_range=(0, 19),
                            writable_formats=lambda: ["SEQ-FFF"], free_bytes=lambda _: 10**9)


def test_pair_confirmation_includes_both_snapshots_and_frozen_view(tmp_path):
    from flir_seq_tool.export_bundle import sidecar_path
    request = paired_request(tmp_path)
    first = confirm(request)
    config = sidecar_path(request.target_path)
    config.write_bytes(b"external config")
    second = confirm(request)
    assert first.signature != second.signature
    assert str(config) in second.summary
    assert "OVERWRITE" in second.summary
    changed_view = replace(request.selection_document, display=DisplaySettings("inferno", 21, 41))
    third = confirm(replace(second.request, selection_document=changed_view))
    assert second.signature != third.signature


def test_request_rejects_inconsistent_view_and_pending(tmp_path):
    from flir_seq_tool.export_bundle import pending_path
    request = paired_request(tmp_path)
    with pytest.raises(ValueError):
        replace(request, source_start_frame=2)
    with pytest.raises(ValueError):
        replace(request, sidecar_snapshot=None)
    request.clips_root.mkdir()
    pending_path(request.target_path).write_text("untrusted pending")
    with pytest.raises(RuntimeError, match="pending"):
        confirm(request)
