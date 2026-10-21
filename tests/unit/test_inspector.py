from datetime import datetime, timedelta
from pathlib import Path

import pytest

from flir_seq_tool.inspector import inspect_seq, inspection_to_dict


class FakeReader:
    def __init__(self, path: Path, times: list[datetime]) -> None:
        self.path = path
        self.times = times
        self.width = 640
        self.height = 480
        self.num_frames = len(times)
        self.requested_frames: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def timestamp_at(self, frame_index: int) -> datetime:
        self.requested_frames.append(frame_index)
        return self.times[frame_index]

    def iter_timestamps(self):
        for frame_index in range(self.num_frames):
            yield self.timestamp_at(frame_index)


def create_input(tmp_path: Path) -> Path:
    path = tmp_path / "sample.seq"
    path.write_bytes(b"fixture")
    return path


def test_inspector_endpoints_reads_only_first_and_last_frame(tmp_path: Path):
    path = create_input(tmp_path)
    start = datetime(2026, 8, 28, 9, 0, 0)
    fake = FakeReader(path, [start + timedelta(milliseconds=20 * i) for i in range(3)])

    result = inspect_seq(
        path,
        timestamp_mode="endpoints",
        reader_factory=lambda _: fake,
    )

    assert fake.requested_frames == [0, 2]
    assert result.num_frames == 3
    assert result.timestamps.scanned_frame_count == 2
    assert result.timestamps.continuity_checked is False
    assert result.timestamps.median_interval_s is None
    assert result.timestamps.estimated_fps is None


def test_inspector_full_scans_all_frames(tmp_path: Path):
    path = create_input(tmp_path)
    start = datetime(2026, 8, 28, 9, 0, 0)
    fake = FakeReader(path, [start + timedelta(milliseconds=20 * i) for i in range(3)])

    result = inspect_seq(
        path,
        timestamp_mode="full",
        reader_factory=lambda _: fake,
    )

    assert fake.requested_frames == [0, 1, 2]
    assert result.timestamps.frame_count == 3
    assert result.timestamps.continuity_checked is True
    assert result.timestamps.estimated_fps == pytest.approx(50.0)


def test_inspector_rejects_unknown_timestamp_mode(tmp_path: Path):
    path = create_input(tmp_path)

    with pytest.raises(ValueError, match="unknown timestamp mode"):
        inspect_seq(path, timestamp_mode="quick")


def test_inspection_to_dict_uses_json_safe_values(tmp_path: Path):
    path = create_input(tmp_path)
    start = datetime(2026, 8, 28, 9, 0, 0)
    fake = FakeReader(path, [start])

    payload = inspection_to_dict(
        inspect_seq(path, timestamp_mode="endpoints", reader_factory=lambda _: fake)
    )

    assert payload["path"] == str(path.resolve())
    assert payload["timestamps"]["first_time"] == "2026-08-28T09:00:00"
    assert payload["timestamps"]["duplicate_after_frames"] == []
