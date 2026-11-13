"""Read-only scanning of a frozen source interval for fixed color limits."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import threading

from .color_scale import ColorLimits, finite_extrema, limits_from_extrema
from .color_scale_models import ColorScaleScope
from .frame_transformer import FrameTransformer
from .measurement_job import AnalysisCancelled, AnalysisProgress, validate_reader_identity
from .reader import ScienceFileReader
from .selection import SourceIdentity, validate_source_identity
from .transform import FrameTransform


@dataclass(frozen=True)
class ColorScaleContext:
    source_path: Path
    source_identity: SourceIdentity
    source_start: int
    source_end: int
    transform: FrameTransform

    def __post_init__(self):
        try:
            object.__setattr__(self, "source_path", Path(self.source_path))
        except TypeError as exc:
            raise ValueError("color scale requires a source path") from exc
        if not isinstance(self.source_identity, SourceIdentity) or not isinstance(self.transform, FrameTransform):
            raise ValueError("color scale requires source identity and transform")
        if any(isinstance(value, bool) or not isinstance(value, int)
               for value in (self.source_start, self.source_end)):
            raise ValueError("color scale range must use integers")
        if not 0 <= self.source_start <= self.source_end < self.source_identity.num_frames:
            raise ValueError("color scale range exceeds source bounds")
        if (self.transform.source_width, self.transform.source_height) != (
                self.source_identity.width, self.source_identity.height):
            raise ValueError("color scale transform dimensions mismatch")

    @property
    def scope(self) -> ColorScaleScope:
        return ColorScaleScope.from_inputs(self.source_identity, self.source_start, self.source_end, self.transform)


@dataclass(frozen=True)
class ColorScaleRequest:
    context: ColorScaleContext
    generation: int
    session_token: str

    def __post_init__(self):
        if not isinstance(self.context, ColorScaleContext):
            raise ValueError("color scale request requires a frozen context")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation <= 0:
            raise ValueError("color scale generation must be a positive integer")
        if not isinstance(self.session_token, str) or not self.session_token:
            raise ValueError("color scale session token must be non-empty")


@dataclass(frozen=True)
class ColorScaleOutcome:
    status: str
    request: ColorScaleRequest
    limits: ColorLimits | None
    completed: int
    empty_frames: int
    error: str | None = None

    def __post_init__(self):
        if self.status not in ("complete", "cancelled", "failed"):
            raise ValueError("unknown color scale outcome")
        if self.status == "complete":
            if not isinstance(self.limits, ColorLimits):
                raise ValueError("complete outcomes require color limits")
        elif self.limits is not None:
            raise ValueError("only complete outcomes can publish color limits")


def run_color_scale(request, *, reader_factory=ScienceFileReader, cancel_event, progress) -> ColorScaleOutcome:
    context = request.context
    total = context.source_end - context.source_start + 1
    completed = empty_frames = 0
    current_source_frame = context.source_start
    minimum = maximum = None
    progress.update(0, total, "Starting")
    try:
        if cancel_event.is_set():
            raise AnalysisCancelled()
        transformer = FrameTransformer(context.transform)
        with reader_factory(context.source_path) as reader:
            validate_reader_identity(reader, context.source_path, context.source_identity)
            for source_index in range(context.source_start, context.source_end + 1):
                current_source_frame = source_index
                if cancel_event.is_set():
                    raise AnalysisCancelled()
                frame = reader.temperature_frame_at(source_index)
                values = transformer.apply(frame.temperature_c)
                extrema = finite_extrema(values)
                if extrema is None:
                    empty_frames += 1
                else:
                    low, high = extrema
                    minimum = low if minimum is None else min(minimum, low)
                    maximum = high if maximum is None else max(maximum, high)
                del values, frame
                completed += 1
                progress.update(completed, total, f"Source frame {source_index}")
            validate_reader_identity(reader, context.source_path, context.source_identity)
        if cancel_event.is_set():
            raise AnalysisCancelled()
        if minimum is None:
            raise ValueError("selected transformed frames contain no finite temperatures")
        limits = limits_from_extrema(minimum, maximum)
        # Reader teardown and limit construction must finish before publication.
        # Recheck the filesystem here because the SDK reader is already closed.
        stat = context.source_path.stat()
        actual = replace(context.source_identity, filename=context.source_path.name,
                         size_bytes=stat.st_size, modified_time_ns=stat.st_mtime_ns)
        validate_source_identity(context.source_identity, actual)
        if cancel_event.is_set():
            raise AnalysisCancelled()
        return ColorScaleOutcome("complete", request, limits, completed, empty_frames)
    except AnalysisCancelled:
        return ColorScaleOutcome("cancelled", request, None, completed, empty_frames)
    except Exception as exc:
        if cancel_event.is_set():
            return ColorScaleOutcome("cancelled", request, None, completed, empty_frames)
        return ColorScaleOutcome("failed", request, None, completed, empty_frames,
                                 f"source frame {current_source_frame}: {exc}")


class ColorScaleJob:
    def __init__(self, request, *, reader_factory=ScienceFileReader):
        self.request = request
        self.progress = AnalysisProgress()
        self._cancel = threading.Event()
        self._outcome = None
        self._thread = threading.Thread(target=self._run, args=(reader_factory,), daemon=False,
                                        name="flir-color-scale")

    def _run(self, reader_factory):
        self._outcome = run_color_scale(self.request, reader_factory=reader_factory,
                                        cancel_event=self._cancel, progress=self.progress)

    @property
    def outcome(self):
        return self._outcome

    def start(self):
        self._thread.start()

    def cancel(self):
        self._cancel.set()

    def is_alive(self):
        return self._thread.is_alive()

    def join(self, timeout=None):
        self._thread.join(timeout)
