from datetime import datetime, timedelta

import pytest

from flir_seq_tool.timestamps import analyze_timestamps


def test_analyze_timestamps_reports_continuity_anomalies():
    start = datetime(2026, 8, 28, 9, 0, 0)
    times = [
        start,
        start + timedelta(milliseconds=20),
        start + timedelta(milliseconds=20),
        start + timedelta(milliseconds=10),
        start + timedelta(milliseconds=30),
        start + timedelta(milliseconds=230),
    ]

    result = analyze_timestamps(times)

    assert result.frame_count == 6
    assert result.scanned_frame_count == 6
    assert result.continuity_checked is True
    assert result.duplicate_after_frames == (1,)
    assert result.backward_after_frames == (2,)
    assert result.gap_after_frames == (4,)
    assert result.median_interval_s == pytest.approx(0.02)
    assert result.estimated_fps == pytest.approx(50.0)


def test_analyze_timestamps_rejects_empty_input():
    with pytest.raises(ValueError, match="timestamps must not be empty"):
        analyze_timestamps([])


def test_analyze_timestamps_handles_single_frame():
    timestamp = datetime(2026, 8, 28, 9, 0, 0)

    result = analyze_timestamps([timestamp])

    assert result.frame_count == 1
    assert result.duration_s == 0.0
    assert result.median_interval_s is None
    assert result.estimated_fps is None
    assert result.duplicate_after_frames == ()
    assert result.backward_after_frames == ()
    assert result.gap_after_frames == ()
