"""Paired native SEQ/view configuration helpers (no extraction dependency)."""

from pathlib import Path
from typing import Any, Callable

from .selection import SelectionDocument, SourceIdentity, build_selection_document


def sidecar_path(seq_path: Path) -> Path:
    return Path(seq_path).with_suffix(".selection.json")


def pending_path(seq_path: Path) -> Path:
    return Path(seq_path).with_suffix(".export-pending.json")


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
