from __future__ import annotations

import math
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import fnv.file
import numpy as np

from .reader import ScienceFileReader
from .file_safety import TargetSnapshot, validate_write_path
from .selection import SelectionDocument
from .export_bundle import sidecar_path, pending_path


_ILLEGAL_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*]')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_MIN_SPACE_MARGIN = 64 * 1024 * 1024
DEFAULT_RAW_ROOT = Path(r"D:\carbonfoam\infrared\raw")


def normalize_clip_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("clip name must be text")
    value = name.strip()
    if value.lower().endswith(".seq"):
        value = value[:-4]
    if not value or value in {".", ".."}:
        raise ValueError("clip name must not be empty")
    if value.endswith((" ", ".")):
        raise ValueError("clip name must not end with a space or dot")
    if _ILLEGAL_WINDOWS_CHARS.search(value):
        raise ValueError("clip name contains an illegal Windows character")
    if value.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
        raise ValueError("clip name is a reserved Windows device name")
    return value


@dataclass(frozen=True)
class ExportRequest:
    source_path: Path
    clips_root: Path
    target_path: Path
    source_start_frame: int
    source_end_frame: int
    start_timestamp: datetime
    end_timestamp: datetime
    source_num_frames: int
    source_size_bytes: int
    source_modified_time_ns: int
    target_snapshot: TargetSnapshot
    selection_document: SelectionDocument | None = None
    sidecar_snapshot: TargetSnapshot | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.source_start_frame <= self.source_end_frame < self.source_num_frames:
            raise ValueError("invalid export source frame range")
        if self.start_timestamp > self.end_timestamp:
            raise ValueError("export timestamps must be ordered")
        if self.source_size_bytes < 0 or self.source_modified_time_ns < 0:
            raise ValueError("invalid source file identity")
        self.validate_view()

    def validate_view(self) -> None:
        document = self.selection_document
        if (document is None) != (self.sidecar_snapshot is None):
            raise ValueError("paired export requires both document and sidecar snapshot")
        if document is None:
            return
        source, selection = document.source, document.selection
        if (source.filename, source.size_bytes, source.modified_time_ns, source.num_frames) != (
            self.source_path.name, self.source_size_bytes, self.source_modified_time_ns, self.source_num_frames,
        ):
            raise ValueError("export document source identity mismatch")
        if (selection.source_start_frame, selection.source_end_frame, selection.start_timestamp, selection.end_timestamp) != (
            self.source_start_frame, self.source_end_frame, self.start_timestamp, self.end_timestamp,
        ):
            raise ValueError("export document selection mismatch")

    @property
    def frame_count(self) -> int:
        return self.source_end_frame - self.source_start_frame + 1

    @property
    def duration_s(self) -> float:
        return (self.end_timestamp - self.start_timestamp).total_seconds()


def build_export_request(
    *,
    source_path: Path,
    clips_root: Path,
    clip_name: str,
    source_start_frame: int,
    source_end_frame: int,
    start_timestamp: datetime,
    end_timestamp: datetime,
    source_num_frames: int,
    selection_document: SelectionDocument | None = None,
) -> ExportRequest:
    source = Path(source_path).resolve(strict=True)
    root = Path(clips_root).resolve()
    stem = normalize_clip_name(clip_name)
    stat = source.stat()
    target = root / f"{stem}.seq"
    return ExportRequest(
        source_path=source,
        clips_root=root,
        target_path=target,
        source_start_frame=source_start_frame,
        source_end_frame=source_end_frame,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        source_num_frames=source_num_frames,
        source_size_bytes=stat.st_size,
        source_modified_time_ns=stat.st_mtime_ns,
        target_snapshot=TargetSnapshot.capture(target),
        selection_document=selection_document,
        sidecar_snapshot=None if selection_document is None else TargetSnapshot.capture(sidecar_path(target)),
    )


def required_free_bytes(
    source_size_bytes: int, source_num_frames: int, export_frame_count: int
) -> int:
    if source_num_frames < 1 or export_frame_count < 1:
        raise ValueError("frame counts must be positive")
    estimate = math.ceil(source_size_bytes / source_num_frames * export_frame_count)
    margin = max(math.ceil(estimate * 0.1), _MIN_SPACE_MARGIN)
    return estimate + margin


@dataclass(frozen=True)
class ExportConfirmation:
    request: ExportRequest
    work_source_range: tuple[int, int]
    overwrite: bool
    required_bytes: int
    signature: tuple[Any, ...]
    summary: str


def _available_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _validate_export_paths(
    request: ExportRequest, *, raw_root: Path | None = None
) -> None:
    """Enforce read-only source/raw paths before any filesystem mutation."""
    root = request.clips_root.resolve()
    target = request.target_path.resolve()
    if request.target_path.parent.resolve() != root:
        raise ValueError("export target must be directly under clips root")
    if target == request.source_path.resolve(strict=True):
        raise ValueError("export target must differ from source")
    protected = Path(DEFAULT_RAW_ROOT if raw_root is None else raw_root).resolve()
    if root.is_relative_to(protected) or target.is_relative_to(protected):
        raise ValueError("export target must not be written under raw")
    validate_write_path(request.target_path, source_path=request.source_path, raw_root=protected)
    request.validate_view()
    if request.selection_document is not None:
        for path in (sidecar_path(request.target_path), pending_path(request.target_path)):
            resolved = validate_write_path(path, source_path=request.source_path, raw_root=protected)
            if resolved.parent != root:
                raise ValueError("paired output must be directly under clips root")
        if pending_path(request.target_path).exists():
            raise RuntimeError(f"unresolved export pending marker: {pending_path(request.target_path)}")


def preflight_export(
    request: ExportRequest,
    *,
    work_source_range: tuple[int, int],
    writable_formats: Callable[[], list[str]] = fnv.file.ImagerFileWriter.get_writable_formats,
    free_bytes: Callable[[Path], int] = _available_bytes,
    raw_root: Path = DEFAULT_RAW_ROOT,
) -> ExportConfirmation:
    _validate_export_paths(request, raw_root=raw_root)
    request.clips_root.mkdir(parents=True, exist_ok=True)
    root = request.clips_root.resolve(strict=True)
    if "SEQ-FFF" not in writable_formats():
        raise RuntimeError("FileSDK does not report writable SEQ-FFF format")

    with tempfile.NamedTemporaryFile(dir=root, prefix=".write-probe-", delete=False) as stream:
        probe = Path(stream.name)
    probe.unlink(missing_ok=True)

    required = required_free_bytes(
        request.source_size_bytes, request.source_num_frames, request.frame_count
    )
    available = free_bytes(root)
    if available < required:
        raise RuntimeError(
            f"insufficient free space: {available} available, {required} required"
        )
    target_snapshot = TargetSnapshot.capture(request.target_path)
    sidecar_snapshot = None if request.selection_document is None else TargetSnapshot.capture(sidecar_path(request.target_path))
    request = replace(request, target_snapshot=target_snapshot, sidecar_snapshot=sidecar_snapshot)
    overwrite = target_snapshot.exists or (sidecar_snapshot is not None and sidecar_snapshot.exists)
    signature = (
        request.target_path.name,
        request.source_start_frame,
        request.source_end_frame,
        work_source_range[0],
        work_source_range[1],
        target_snapshot,
        sidecar_snapshot,
        None if request.selection_document is None else json.dumps(request.selection_document.to_dict(), sort_keys=True),
    )
    overwrite_text = "YES (OVERWRITE)" if target_snapshot.exists else "NO"
    summary = (
        f"Target: {request.target_path}\n"
        f"Source frames: [{request.source_start_frame}, {request.source_end_frame}] | "
        f"Frames: {request.frame_count} | Duration: {request.duration_s:.3f} s\n"
        f"Time: {request.start_timestamp.isoformat()} -> "
        f"{request.end_timestamp.isoformat()} | Existing target: {overwrite_text}"
    )
    if sidecar_snapshot is not None:
        summary += (f"\nSelection: {sidecar_path(request.target_path)} | Existing selection: "
                    + ("YES (OVERWRITE)" if sidecar_snapshot.exists else "NO"))
    return ExportConfirmation(
        request=request,
        work_source_range=work_source_range,
        overwrite=overwrite,
        required_bytes=required,
        signature=signature,
        summary=summary,
    )


class ExportStatus(Enum):
    READY = "ready"
    RUNNING = "running"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


@dataclass(frozen=True)
class ExportProgressSnapshot:
    status: ExportStatus
    current: int
    total: int
    message: str


@dataclass
class ExportProgress:
    _status: ExportStatus = ExportStatus.READY
    _current: int = 0
    _total: int = 0
    _message: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(
        self,
        *,
        status: ExportStatus | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        with self._lock:
            if status is not None:
                self._status = status
            if current is not None:
                self._current = int(current)
            if total is not None:
                self._total = int(total)
            if message is not None:
                self._message = message

    def snapshot(self) -> ExportProgressSnapshot:
        with self._lock:
            return ExportProgressSnapshot(
                self._status, self._current, self._total, self._message
            )


@dataclass(frozen=True)
class ExportResult:
    status: ExportStatus
    target_path: Path
    message: str


class ExportAbortHandle:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._options: Any | None = None

    def set_options(self, options: Any, cancel_event: threading.Event) -> None:
        with self._lock:
            self._options = options
            should_abort = cancel_event.is_set()
        if should_abort:
            options.abort()

    def clear(self) -> None:
        with self._lock:
            self._options = None

    def abort(self) -> None:
        with self._lock:
            options = self._options
        if options is not None:
            options.abort()


def validate_extracted_seq(
    request: ExportRequest,
    partial_path: Path,
    *,
    reader_factory: Callable[[Path], Any] = ScienceFileReader,
) -> None:
    partial = Path(partial_path)
    if not partial.is_file() or partial.stat().st_size == 0:
        raise ValueError("extracted SEQ is missing or empty")

    with reader_factory(request.source_path) as source:
        source_dimensions = (int(source.width), int(source.height))
        if int(source.num_frames) != request.source_num_frames:
            raise ValueError("source frame count changed")
        source_start_time = source.timestamp_at(request.source_start_frame)
        source_end_time = source.timestamp_at(request.source_end_frame)
        source_first_matrix = source.temperature_frame_at(
            request.source_start_frame
        ).temperature_c

    if source_start_time != request.start_timestamp:
        raise ValueError("source start timestamp changed")
    if source_end_time != request.end_timestamp:
        raise ValueError("source end timestamp changed")

    with reader_factory(partial) as output:
        output_dimensions = (int(output.width), int(output.height))
        if output_dimensions != source_dimensions:
            raise ValueError(
                f"output dimensions mismatch: {output_dimensions} != {source_dimensions}"
            )
        if int(output.num_frames) != request.frame_count:
            raise ValueError(
                f"output frame count mismatch: {output.num_frames} != "
                f"{request.frame_count}"
            )
        if output.timestamp_at(0) != request.start_timestamp:
            raise ValueError("output first timestamp mismatch")
        if output.timestamp_at(request.frame_count - 1) != request.end_timestamp:
            raise ValueError("output last timestamp mismatch")
        output_first_matrix = output.temperature_frame_at(0).temperature_c

    try:
        np.testing.assert_allclose(
            output_first_matrix,
            source_first_matrix,
            rtol=1e-6,
            atol=1e-4,
            equal_nan=True,
        )
    except AssertionError as exc:
        raise ValueError("output first temperature matrix mismatch") from exc

    stat = request.source_path.stat()
    if (
        stat.st_size != request.source_size_bytes
        or stat.st_mtime_ns != request.source_modified_time_ns
    ):
        raise ValueError("source file identity changed during validation")


def run_export(
    request: ExportRequest,
    *,
    progress: ExportProgress,
    cancel_event: threading.Event,
    imager_factory: Callable[[str], Any] = fnv.file.ImagerFile,
    options_factory: Callable[[], Any] = fnv.file.ImagerFileExtractOptions,
    validator: Callable[[ExportRequest, Path], None],
    abort_handle: ExportAbortHandle | None = None,
) -> ExportResult:
    partial: Path | None = None
    replaced = False
    progress.update(status=ExportStatus.RUNNING, message="Extracting")

    try:
        _validate_export_paths(request)
        if cancel_event.is_set():
            progress.update(status=ExportStatus.CANCELLED, message="Export cancelled")
            return ExportResult(
                ExportStatus.CANCELLED, request.target_path, "Export cancelled"
            )

        request.target_path.parent.mkdir(parents=True, exist_ok=True)
        partial_base = request.target_path.with_name(
            f"{request.target_path.stem}.{uuid.uuid4().hex}.partial"
        )
        partial = Path(f"{partial_base}.seq")
        options = options_factory()
        options.start_frame = request.source_start_frame
        options.end_frame = request.source_end_frame
        options.decimation = 1
        options.preset = -1
        options.preferredFormat = "SEQ-FFF"
        if abort_handle is not None:
            abort_handle.set_options(options, cancel_event)

        def on_progress(current: int, total: int) -> bool:
            progress.update(current=current, total=total)
            return cancel_event.is_set()

        options.progress_callback = on_progress
        imager = imager_factory(str(request.source_path))
        try:
            extracted = bool(imager.extract(str(partial_base), options))
        finally:
            imager.close()
            if abort_handle is not None:
                abort_handle.clear()

        if cancel_event.is_set():
            progress.update(status=ExportStatus.CANCELLED, message="Export cancelled")
            return ExportResult(
                ExportStatus.CANCELLED, request.target_path, "Export cancelled"
            )
        if not extracted:
            raise RuntimeError("FileSDK extract returned false")

        progress.update(message="Validating extracted SEQ")
        validator(request, partial)
        if cancel_event.is_set():
            progress.update(status=ExportStatus.CANCELLED, message="Export cancelled")
            return ExportResult(
                ExportStatus.CANCELLED, request.target_path, "Export cancelled"
            )
        current_source = request.source_path.stat()
        if (
            current_source.st_size != request.source_size_bytes
            or current_source.st_mtime_ns != request.source_modified_time_ns
        ):
            raise RuntimeError("source file identity changed during export")
        if TargetSnapshot.capture(request.target_path) != request.target_snapshot:
            raise RuntimeError(
                "target changed after export confirmation; refusing replacement"
            )
        os.replace(partial, request.target_path)
        replaced = True
        message = f"Exported: {request.target_path}"
        progress.update(
            status=ExportStatus.SUCCEEDED,
            current=request.frame_count,
            total=request.frame_count,
            message=message,
        )
        return ExportResult(ExportStatus.SUCCEEDED, request.target_path, message)
    except Exception as exc:
        if cancel_event.is_set():
            message = f"Export cancelled: {exc}"
            progress.update(status=ExportStatus.CANCELLED, message=message)
            return ExportResult(ExportStatus.CANCELLED, request.target_path, message)
        message = f"Export failed: {exc}"
        progress.update(status=ExportStatus.FAILED, message=message)
        return ExportResult(ExportStatus.FAILED, request.target_path, message)
    finally:
        if partial is not None and not replaced:
            partial.unlink(missing_ok=True)


class ExportJob:
    def __init__(
        self,
        request: ExportRequest,
        *,
        imager_factory: Callable[[str], Any] = fnv.file.ImagerFile,
        options_factory: Callable[[], Any] = fnv.file.ImagerFileExtractOptions,
        validator: Callable[[ExportRequest, Path], None] = validate_extracted_seq,
    ) -> None:
        self.request = request
        self.progress = ExportProgress()
        self.cancel_event = threading.Event()
        self.result: ExportResult | None = None
        self._abort_handle = ExportAbortHandle()
        self._imager_factory = imager_factory
        self._options_factory = options_factory
        self._validator = validator
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("export job has already been started")
        self._thread = threading.Thread(
            target=self._run,
            name="flir-seq-export",
            daemon=False,
        )
        self._thread.start()

    def _run(self) -> None:
        self.result = run_export(
            self.request,
            progress=self.progress,
            cancel_event=self.cancel_event,
            imager_factory=self._imager_factory,
            options_factory=self._options_factory,
            validator=self._validator,
            abort_handle=self._abort_handle,
        )

    def cancel(self) -> None:
        self.cancel_event.set()
        try:
            self._abort_handle.abort()
        except Exception as exc:
            self.progress.update(message=f"Cancellation requested: {exc}")

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
