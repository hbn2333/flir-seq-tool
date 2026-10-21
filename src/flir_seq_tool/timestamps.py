from collections.abc import Sequence
from datetime import datetime
from statistics import median

from .models import TimestampSummary


def analyze_timestamps(times: Sequence[datetime]) -> TimestampSummary:
    if not times:
        raise ValueError("timestamps must not be empty")

    intervals = [(later - earlier).total_seconds() for earlier, later in zip(times, times[1:])]
    positive_intervals = [value for value in intervals if value > 0]
    median_interval = median(positive_intervals) if positive_intervals else None
    gap_after_frames = () if median_interval is None else tuple(
        index
        for index, value in enumerate(intervals)
        if value > 3 * median_interval
    )

    return TimestampSummary(
        frame_count=len(times),
        scanned_frame_count=len(times),
        continuity_checked=True,
        first_time=times[0],
        last_time=times[-1],
        duration_s=(times[-1] - times[0]).total_seconds(),
        median_interval_s=median_interval,
        estimated_fps=None if median_interval is None else 1.0 / median_interval,
        duplicate_after_frames=tuple(
            index for index, value in enumerate(intervals) if value == 0
        ),
        backward_after_frames=tuple(
            index for index, value in enumerate(intervals) if value < 0
        ),
        gap_after_frames=gap_after_frames,
    )
