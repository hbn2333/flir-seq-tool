"""Paired native SEQ/view configuration helpers (no extraction dependency)."""

from pathlib import Path
from dataclasses import asdict, dataclass
import json
import os
import tempfile
import uuid
from typing import Any, Callable

from .selection import SelectionDocument, SourceIdentity, build_selection_document
from .file_safety import TargetSnapshot, assert_snapshot, validate_write_path


def sidecar_path(seq_path: Path) -> Path:
    return Path(seq_path).with_suffix(".selection.json")


def pending_path(seq_path: Path) -> Path:
    return Path(seq_path).with_suffix(".export-pending.json")


def read_seq_identity(path: Path, *, reader_factory: Callable[[Path], Any]) -> SourceIdentity:
    with reader_factory(path) as reader:
        stat = Path(path).stat()
        return SourceIdentity(
            filename=Path(path).name, size_bytes=stat.st_size, modified_time_ns=stat.st_mtime_ns,
            width=int(reader.width), height=int(reader.height), num_frames=int(reader.num_frames),
            first_timestamp=reader.timestamp_at(0), last_timestamp=reader.timestamp_at(reader.num_frames - 1),
        )


def build_clip_document(
    partial_path: Path, final_path: Path, view_document: SelectionDocument, *,
    reader_factory: Callable[[Path], Any],
) -> SelectionDocument:
    selection = view_document.selection
    with reader_factory(partial_path) as output:
        if (int(output.width), int(output.height)) != (view_document.source.width, view_document.source.height):
            raise ValueError("output dimensions mismatch")
        if int(output.num_frames) != selection.frame_count:
            raise ValueError("output frame count mismatch")
        first = output.timestamp_at(0)
        last = output.timestamp_at(output.num_frames - 1)
        if first != selection.start_timestamp or last != selection.end_timestamp:
            raise ValueError("output timestamps mismatch")
        stat = Path(partial_path).stat()
        source = SourceIdentity(
            filename=Path(final_path).name, size_bytes=stat.st_size,
            modified_time_ns=stat.st_mtime_ns, width=int(output.width), height=int(output.height),
            num_frames=int(output.num_frames), first_timestamp=first, last_timestamp=last,
        )
    return build_selection_document(
        source=source, source_start_frame=0, source_end_frame=source.num_frames - 1,
        start_timestamp=first, end_timestamp=last,
        display=view_document.display, transform=view_document.transform,
    )


@dataclass(frozen=True)
class BundlePublishResult:
    warnings: tuple[str, ...] = ()


class BundlePublishError(RuntimeError):
    def __init__(self, stage: str, cause: str, retained_paths: tuple[Path, ...]):
        self.stage = stage
        self.retained_paths = retained_paths
        retained = "\n".join(str(path) for path in retained_paths)
        super().__init__(f"paired export failed at {stage}: {cause}\nRetained paths:\n{retained}")


@dataclass
class _Entry:
    target: Path
    temporary: Path
    recovery: Path
    expected: TargetSnapshot
    prepared: TargetSnapshot
    protected: bool = False
    published: bool = False
    recovery_snapshot: TargetSnapshot | None = None
    published_snapshot: TargetSnapshot | None = None


def _assert_moved(path: Path, before: TargetSnapshot) -> None:
    """Rename can change ctime on Windows; compare stable identity at that boundary."""
    current = TargetSnapshot.capture(path)
    fields = ("exists", "device", "inode", "size_bytes", "modified_time_ns")
    if not current.exists or any(getattr(current, name) != getattr(before, name) for name in fields):
        raise RuntimeError(f"moved file changed: {path}")


def _assert_owned(path: Path, snapshot: TargetSnapshot | None, before_move: TargetSnapshot) -> None:
    if snapshot is None:
        _assert_moved(path, before_move)
    else:
        assert_snapshot(path, snapshot)


class _Journal:
    """An ownership-checked marker, never a source of executable recovery paths."""

    def __init__(self, path: Path, transaction_id: str):
        self.path = path
        self.transaction_id = transaction_id
        self.snapshot: TargetSnapshot | None = None

    def claim(self) -> None:
        with self.path.open("x", encoding="utf-8") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        self.snapshot = TargetSnapshot.capture(self.path)

    def record(self, state: str, entries: list[_Entry]) -> None:
        if self.snapshot is None:
            raise RuntimeError("marker ownership not established")
        payload = {"transaction_id": self.transaction_id, "state": state,
                   "entries": [asdict(entry) for entry in entries]}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".journal-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, default=str, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            assert_snapshot(self.path, self.snapshot)
            os.replace(temporary, self.path)
            temporary = None
            self.snapshot = TargetSnapshot.capture(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def clear(self) -> None:
        if self.snapshot is None:
            raise RuntimeError("marker ownership not established")
        assert_snapshot(self.path, self.snapshot)
        self.path.unlink()


def publish_bundle(
    *, source_path: Path, raw_root: Path, seq_temp: Path, selection_temp: Path,
    seq_target: Path, expected_seq: TargetSnapshot, expected_selection: TargetSnapshot,
    verify_pair: Callable[[Path, Path], None],
) -> BundlePublishResult:
    """Publish a checked pair with guarded rollback, not a two-file atomic commit.

    A crash leaves a pending marker for manual recovery. Files changed externally
    are never deliberately overwritten during rollback. Callers must retain paths
    reported by BundlePublishError; journals are not automatically replayed.
    """
    seq_target = Path(seq_target).absolute()
    config = sidecar_path(seq_target)
    marker = pending_path(seq_target)
    transaction_id = uuid.uuid4().hex
    root = seq_target.parent.resolve()
    entries: list[_Entry] = []
    paths = [seq_target, config, Path(seq_temp).absolute(), Path(selection_temp).absolute(), marker]
    journal = _Journal(marker, transaction_id)
    stage = "validate"
    verified = False

    def check_path(path: Path) -> None:
        resolved = validate_write_path(path, source_path=source_path, raw_root=raw_root)
        if resolved.parent != root or path.is_symlink():
            raise ValueError(f"bundle path must be a direct, non-symlink output: {path}")

    def record(state: str) -> None:
        journal.record(state, entries)

    def clean_temporary(entry: _Entry) -> None:
        if entry.temporary.exists():
            check_path(entry.temporary)
            _assert_moved(entry.temporary, entry.prepared)
            entry.temporary.unlink()

    try:
        for target, temporary, expected in ((seq_target, paths[2], expected_seq), (config, paths[3], expected_selection)):
            recovery = target.with_name(f"{target.name}.{transaction_id}.recovery")
            paths.append(recovery)
            entries.append(_Entry(target, temporary, recovery, expected, TargetSnapshot.capture(temporary)))
        for path in paths:
            check_path(path)
        if len({path.resolve() for path in paths}) != len(paths):
            raise ValueError("bundle paths must be distinct")
        existing = [path for path in paths if path.exists()]
        for index, path in enumerate(existing):
            if any(path.samefile(other) for other in existing[index + 1:]):
                raise ValueError("bundle paths alias each other")
        for entry in entries:
            if not entry.prepared.exists:
                raise ValueError(f"prepared file missing: {entry.temporary}")
            assert_snapshot(entry.target, entry.expected)
            assert_snapshot(entry.recovery, TargetSnapshot(False))
        stage = "claim"
        journal.claim()
        record(stage)
        for index, entry in enumerate(entries):
            stage = f"protect-{index}"
            record(stage)
            check_path(entry.target)
            check_path(entry.recovery)
            assert_snapshot(entry.target, entry.expected)
            assert_snapshot(entry.recovery, TargetSnapshot(False))
            if entry.expected.exists:
                os.replace(entry.target, entry.recovery)
                entry.protected = True  # Record ownership before any fallible stat/journal write.
                _assert_moved(entry.recovery, entry.expected)
                entry.recovery_snapshot = TargetSnapshot.capture(entry.recovery)
            record(f"{stage}-done")
        for index, entry in enumerate(entries):
            stage = f"publish-{index}"
            record(stage)
            check_path(entry.target)
            check_path(entry.temporary)
            assert_snapshot(entry.target, TargetSnapshot(False))
            assert_snapshot(entry.temporary, entry.prepared)
            os.replace(entry.temporary, entry.target)
            entry.published = True
            _assert_moved(entry.target, entry.prepared)
            entry.published_snapshot = TargetSnapshot.capture(entry.target)
            record(f"{stage}-done")
        stage = "verify"
        record(stage)
        verify_pair(seq_target, config)
        for entry in entries:
            _assert_owned(entry.target, entry.published_snapshot, entry.prepared)
        record("verified")
        verified = True
        stage = "finish"
        warnings = []
        for entry in entries:
            if entry.protected:
                try:
                    check_path(entry.recovery)
                    _assert_owned(entry.recovery, entry.recovery_snapshot, entry.expected)
                    entry.recovery.unlink()
                except (OSError, ValueError, RuntimeError) as exc:
                    warnings.append(f"Recovery file retained: {entry.recovery}: {exc}")
        journal.clear()
        return BundlePublishResult(tuple(warnings))
    except Exception as exc:
        failures = []
        if journal.snapshot is not None and not verified:
            for index, entry in reversed(list(enumerate(entries))):
                try:
                    if entry.published or entry.protected:
                        record(f"rollback-{index}")
                        check_path(entry.target)
                    if entry.published:
                        check_path(entry.temporary)
                        _assert_owned(entry.target, entry.published_snapshot, entry.prepared)
                        assert_snapshot(entry.temporary, TargetSnapshot(False))
                        os.replace(entry.target, entry.temporary)
                        entry.published = False
                    if entry.protected:
                        check_path(entry.recovery)
                        _assert_owned(entry.recovery, entry.recovery_snapshot, entry.expected)
                        assert_snapshot(entry.target, TargetSnapshot(False))
                        os.replace(entry.recovery, entry.target)
                        entry.protected = False
                    record(f"rollback-{index}-done")
                except Exception as rollback_error:
                    failures.append(str(rollback_error))
            if not failures:
                try:
                    for entry in entries:
                        clean_temporary(entry)
                    journal.clear()
                except Exception as cleanup_error:
                    failures.append(str(cleanup_error))
        retained = tuple(path for path in paths if path.exists())
        cause = str(exc) + ("; rollback incomplete: " + "; ".join(failures) if failures else "")
        raise BundlePublishError(stage, cause, retained) from exc
