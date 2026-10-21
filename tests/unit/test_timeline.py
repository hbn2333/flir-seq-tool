import pytest

from flir_seq_tool.timeline import TimelineState


def test_timeline_starts_with_full_source_range():
    timeline = TimelineState(65)

    assert timeline.source_start == 0
    assert timeline.source_end == 64
    assert timeline.local_num_frames == 65
    assert timeline.current_local_frame == 0
    assert timeline.pending_range == (0, 64)


def test_timeline_maps_between_local_and_source_frames():
    timeline = TimelineState(65)
    timeline.set_pending(10, 50)
    assert timeline.confirm_range() is True

    assert timeline.source_frame_at(0) == 10
    assert timeline.source_frame_at(40) == 50
    assert timeline.local_frame_at(25) == 15


def test_timeline_can_narrow_repeatedly_and_reset_once():
    timeline = TimelineState(65)
    timeline.set_pending(10, 50)
    timeline.confirm_range()
    timeline.set_pending(5, 20)
    timeline.confirm_range()

    assert (timeline.source_start, timeline.source_end) == (15, 30)
    assert timeline.pending_range == (0, 15)
    assert timeline.current_local_frame == 0

    timeline.reset_full()

    assert (timeline.source_start, timeline.source_end) == (0, 64)
    assert timeline.pending_range == (0, 64)


@pytest.mark.parametrize("start,end", [(-1, 2), (0, 65), (5, 4)])
def test_invalid_pending_range_does_not_change_state(start, end):
    timeline = TimelineState(65)
    before = timeline.pending_range

    with pytest.raises((IndexError, ValueError)):
        timeline.set_pending(start, end)

    assert timeline.pending_range == before


def test_single_frame_range_is_valid():
    timeline = TimelineState(65)
    timeline.set_pending(12, 12)
    timeline.confirm_range()

    assert (timeline.source_start, timeline.source_end) == (12, 12)
    assert timeline.local_num_frames == 1


def test_restore_source_range_renumbers_locally_and_is_atomic():
    timeline = TimelineState(20)
    timeline.set_pending(5, 15)
    timeline.confirm_range()

    timeline.restore_source_range(7, 9)

    assert (timeline.source_start, timeline.source_end) == (7, 9)
    assert timeline.current_local_frame == 0
    assert timeline.pending_range == (0, 2)
    with pytest.raises(ValueError, match="invalid source frame range"):
        timeline.restore_source_range(9, 7)
    assert (timeline.source_start, timeline.source_end) == (7, 9)
