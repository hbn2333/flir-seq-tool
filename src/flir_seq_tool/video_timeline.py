"""Exact source-time mapping to a fixed 30 fps presentation stream."""

from dataclasses import dataclass
from datetime import datetime


VIDEO_FPS = 30
VIDEO_SPEEDS = (1, 2, 5, 10)


@dataclass(frozen=True)
class VideoTimeline:
    relative_us: tuple[int, ...]
    speed: int

    @classmethod
    def from_timestamps(cls, timestamps, speed):
        if type(speed) is not int or speed not in VIDEO_SPEEDS:
            raise ValueError("Video speed must be 1, 2, 5 or 10")
        timestamps = tuple(timestamps)
        if not timestamps or any(not isinstance(t, datetime) for t in timestamps):
            raise ValueError("Video requires valid datetime timestamps")
        first = timestamps[0]
        awareness = first.utcoffset() is not None
        values = []
        for timestamp in timestamps:
            if (timestamp.utcoffset() is not None) != awareness:
                raise ValueError("Video timestamps must use matching timezone awareness")
            delta = timestamp - first
            us = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
            if values and us <= values[-1]:
                raise ValueError("Video timestamps must be strictly increasing")
            values.append(us)
        return cls(tuple(values), speed)

    @property
    def duration_s(self):
        return self.relative_us[-1] / 1_000_000

    @property
    def frame_count(self):
        denominator = self.speed * 1_000_000
        return (VIDEO_FPS if len(self.relative_us) == 1 else
                (self.relative_us[-1] * VIDEO_FPS + denominator - 1) // denominator + 1)

    @property
    def video_duration_s(self):
        return self.frame_count / VIDEO_FPS

    def iter_source_indices(self):
        index = 0
        last = len(self.relative_us) - 1
        for k in range(self.frame_count):
            while index < last and self.relative_us[index + 1] * VIDEO_FPS <= k * self.speed * 1_000_000:
                index += 1
            yield last if k == self.frame_count - 1 else index
