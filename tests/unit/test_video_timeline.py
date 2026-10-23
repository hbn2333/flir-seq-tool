from datetime import datetime, timedelta, timezone

import pytest

from flir_seq_tool.video_timeline import VideoTimeline


def times(*microseconds):
    zero = datetime(2026, 1, 1)
    return tuple(zero + timedelta(microseconds=u) for u in microseconds)


def test_irregular_times_hold_previous_and_include_last():
    timeline = VideoTimeline.from_timestamps(times(0, 40_000, 100_000), speed=1)
    assert timeline.frame_count == 4
    assert list(timeline.iter_source_indices()) == [0, 0, 1, 2]
    assert timeline.duration_s == 0.1
    assert timeline.video_duration_s == 4 / 30


@pytest.mark.parametrize('speed', [1, 2, 5, 10])
def test_real_time_duration_and_endpoints(speed):
    timeline = VideoTimeline.from_timestamps(times(0, 100_000, 2_001_000), speed)
    indices = list(timeline.iter_source_indices())
    assert indices[0] == 0 and indices[-1] == 2
    assert 0 < timeline.video_duration_s - 2.001 / speed < 2 / 30
    assert indices == sorted(indices)
    assert iter(timeline.iter_source_indices()) is not None


def test_single_frame_is_one_second():
    timeline = VideoTimeline.from_timestamps(times(0), 10)
    assert list(timeline.iter_source_indices()) == [0] * 30
    assert timeline.video_duration_s == 1


@pytest.mark.parametrize('values', [(), times(0, 0), times(100, 0), ('bad',),
    (datetime(2026, 1, 1), datetime(2026, 1, 2, tzinfo=timezone.utc))])
def test_reject_invalid_timestamps(values):
    with pytest.raises(ValueError):
        VideoTimeline.from_timestamps(values, 1)


@pytest.mark.parametrize('speed', [True, 0, 3, 1.0, '1'])
def test_reject_invalid_speed(speed):
    with pytest.raises(ValueError):
        VideoTimeline.from_timestamps(times(0), speed)
