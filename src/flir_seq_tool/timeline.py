from dataclasses import dataclass, field


@dataclass
class TimelineState:
    source_num_frames: int
    source_start: int = field(init=False, default=0)
    source_end: int = field(init=False)
    current_local_frame: int = field(init=False, default=0)
    pending_start: int = field(init=False, default=0)
    pending_end: int = field(init=False)

    def __post_init__(self) -> None:
        if self.source_num_frames < 1:
            raise ValueError("source_num_frames must be positive")
        self.source_end = self.source_num_frames - 1
        self.pending_end = self.source_end

    @property
    def local_num_frames(self) -> int:
        return self.source_end - self.source_start + 1

    @property
    def pending_range(self) -> tuple[int, int]:
        return self.pending_start, self.pending_end

    def _validate_local(self, local_frame: int) -> None:
        if not 0 <= local_frame < self.local_num_frames:
            raise IndexError(f"local frame out of range: {local_frame}")

    def source_frame_at(self, local_frame: int) -> int:
        self._validate_local(local_frame)
        return self.source_start + local_frame

    def local_frame_at(self, source_frame: int) -> int:
        if not self.source_start <= source_frame <= self.source_end:
            raise IndexError(f"source frame outside work range: {source_frame}")
        return source_frame - self.source_start

    def set_current(self, local_frame: int) -> None:
        self._validate_local(local_frame)
        self.current_local_frame = local_frame

    def set_pending(self, start: int, end: int) -> None:
        self._validate_local(start)
        self._validate_local(end)
        if start > end:
            raise ValueError("start frame must not exceed end frame")
        self.pending_start = start
        self.pending_end = end

    def set_pending_start(self, start: int) -> None:
        self.set_pending(start, self.pending_end)

    def set_pending_end(self, end: int) -> None:
        self.set_pending(self.pending_start, end)

    def confirm_range(self) -> bool:
        if self.pending_range == (0, self.local_num_frames - 1):
            return False

        old_source_start = self.source_start
        new_start = old_source_start + self.pending_start
        new_end = old_source_start + self.pending_end
        self.source_start = new_start
        self.source_end = new_end
        self.current_local_frame = 0
        self.pending_start = 0
        self.pending_end = self.local_num_frames - 1
        return True

    def reset_full(self) -> None:
        self.source_start = 0
        self.source_end = self.source_num_frames - 1
        self.current_local_frame = 0
        self.pending_start = 0
        self.pending_end = self.source_end

    def restore_source_range(self, start: int, end: int) -> None:
        if not 0 <= start <= end < self.source_num_frames:
            raise ValueError(f"invalid source frame range: [{start}, {end}]")
        self.source_start = start
        self.source_end = end
        self.current_local_frame = 0
        self.pending_start = 0
        self.pending_end = self.local_num_frames - 1
