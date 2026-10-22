"""One independent read-only worker for complete, frozen ROI analyses."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import threading

from .frame_transformer import FrameTransformer
from .measurement_models import MeasurementSettings, Roi
from .measurement_state import AnalysisKey
from .measurement_stats import summarize
from .measurement_store import ResultStore
from .reader import ScienceFileReader
from .selection import SourceIdentity, validate_source_identity
from .transform import FrameTransform


@dataclass(frozen=True)
class AnalysisRequest:
    source_path: Path
    source_identity: SourceIdentity
    source_start: int
    source_end: int
    transform: FrameTransform
    rois: tuple[Roi, ...]
    key: AnalysisKey

    def __post_init__(self):
        object.__setattr__(self, "source_path", Path(self.source_path))
        if not isinstance(self.source_identity, SourceIdentity) or not isinstance(self.transform, FrameTransform):
            raise ValueError("analysis requires source identity and transform")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (self.source_start, self.source_end)):
            raise ValueError("analysis range must use integers")
        if not 0 <= self.source_start <= self.source_end < self.source_identity.num_frames:
            raise ValueError("analysis range exceeds source bounds")
        if (self.transform.source_width, self.transform.source_height) != (self.source_identity.width, self.source_identity.height):
            raise ValueError("analysis transform dimensions mismatch")
        if not self.rois:
            raise ValueError("analysis requires at least one ROI")
        MeasurementSettings(self.rois).validate_bounds(self.transform.crop.width, self.transform.crop.height)
        if self.key != AnalysisKey.from_inputs(self.source_identity, self.source_start, self.source_end,
                                               self.transform, self.rois):
            raise ValueError("analysis key does not match frozen inputs")


@dataclass(frozen=True)
class ProgressSnapshot:
    completed: int
    total: int
    message: str


class AnalysisProgress:
    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot = ProgressSnapshot(0, 0, "")

    def update(self, completed, total, message=""):
        with self._lock:
            self._snapshot = ProgressSnapshot(completed, total, message)

    def snapshot(self):
        with self._lock:
            return self._snapshot


@dataclass(frozen=True)
class AnalysisOutcome:
    status: str
    store: ResultStore | None
    key: AnalysisKey
    error: str | None = None
    cleanup_store: ResultStore | None = None

    def __post_init__(self):
        if self.status not in ("complete", "cancelled", "failed"):
            raise ValueError("unknown analysis outcome")
        if (self.status == "complete") != (self.store is not None):
            raise ValueError("only complete outcomes can own a result store")
        if self.status == "complete" and self.cleanup_store is not None:
            raise ValueError("complete results cannot have a failed cleanup resource")


class AnalysisCancelled(Exception):
    pass


def validate_reader_identity(reader, path, expected):
    stat = Path(path).stat()
    actual = SourceIdentity(Path(path).name, stat.st_size, stat.st_mtime_ns,
                            int(reader.width), int(reader.height), int(reader.num_frames),
                            reader.timestamp_at(0), reader.timestamp_at(reader.num_frames - 1))
    validate_source_identity(expected, actual)


def run_analysis(request, *, reader_factory=ScienceFileReader, cancel_event, progress, temp_root=None):
    store = None
    current_source_frame = request.source_start
    total = request.source_end - request.source_start + 1
    progress.update(0, total, "Starting")
    try:
        if cancel_event.is_set():
            raise AnalysisCancelled()
        transformer = FrameTransformer(request.transform)
        first_time = None
        with reader_factory(request.source_path) as reader:
            validate_reader_identity(reader, request.source_path, request.source_identity)
            store = ResultStore.create(temp_root)
            for local_frame, source_frame in enumerate(range(request.source_start, request.source_end + 1)):
                current_source_frame = source_frame
                if cancel_event.is_set():
                    raise AnalysisCancelled()
                frame = reader.temperature_frame_at(source_frame)
                values = transformer.apply(frame.temperature_c)
                if first_time is None:
                    first_time = frame.timestamp
                rows = []
                for roi in request.rois:
                    rows.append(dict(roi_id=roi.id, local_frame=local_frame, source_frame=source_frame,
                                     timestamp=frame.timestamp.isoformat(),
                                     relative_s=(frame.timestamp - first_time).total_seconds(),
                                     **asdict(summarize(values, roi))))
                store.append_rows(rows)
                progress.update(local_frame + 1, total, f"Source frame {source_frame}")
            validate_reader_identity(reader, request.source_path, request.source_identity)
        if cancel_event.is_set():
            raise AnalysisCancelled()
        store.complete(expected_rows=total * len(request.rois), key_json=request.key.to_json())
        if cancel_event.is_set():
            raise AnalysisCancelled()
        store.close()
        outcome = AnalysisOutcome("complete", store, request.key)
        store = None  # Transfer directory ownership only after reader and writer are closed.
    except AnalysisCancelled:
        outcome = AnalysisOutcome("cancelled", None, request.key)
    except Exception as exc:
        outcome = AnalysisOutcome("failed", None, request.key, f"source frame {current_source_frame}: {exc}")
    finally:
        if store is not None:
            try:
                store.cleanup()
            except Exception as exc:
                error = f"{outcome.error or outcome.status}; cleanup failed: {store.path}: {exc}"
                outcome = AnalysisOutcome("failed", None, request.key, error, cleanup_store=store)
    return outcome


class AnalysisJob:
    def __init__(self, request, *, reader_factory=ScienceFileReader, temp_root=None):
        self.request = request
        self.progress = AnalysisProgress()
        self._cancel = threading.Event()
        self._outcome = None
        self._thread = threading.Thread(target=self._run, args=(reader_factory, temp_root), daemon=False,
                                        name="flir-measure-analysis")

    def _run(self, reader_factory, temp_root):
        self._outcome = run_analysis(self.request, reader_factory=reader_factory, cancel_event=self._cancel,
                                     progress=self.progress, temp_root=temp_root)

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
