from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import SeqInspection, TimestampSummary
from .reader import ScienceFileReader
from .timestamps import analyze_timestamps


def inspect_seq(
    path: Path,
    timestamp_mode: str = "endpoints",
    reader_factory=None,
) -> SeqInspection:
    if timestamp_mode not in {"endpoints", "full"}:
        raise ValueError(f"unknown timestamp mode: {timestamp_mode}")

    resolved = Path(path).resolve(strict=True)
    file_stat = resolved.stat()
    factory = reader_factory or ScienceFileReader

    with factory(resolved) as reader:
        if reader.num_frames < 1:
            raise RuntimeError("SEQ contains no frames")

        if timestamp_mode == "full":
            timestamp_summary = analyze_timestamps(list(reader.iter_timestamps()))
        else:
            first_time = reader.timestamp_at(0)
            last_time = (
                first_time
                if reader.num_frames == 1
                else reader.timestamp_at(reader.num_frames - 1)
            )
            timestamp_summary = TimestampSummary(
                frame_count=reader.num_frames,
                scanned_frame_count=1 if reader.num_frames == 1 else 2,
                continuity_checked=False,
                first_time=first_time,
                last_time=last_time,
                duration_s=(last_time - first_time).total_seconds(),
                median_interval_s=None,
                estimated_fps=None,
                duplicate_after_frames=(),
                backward_after_frames=(),
                gap_after_frames=(),
            )

        return SeqInspection(
            path=resolved,
            size_bytes=file_stat.st_size,
            modified_time_ns=file_stat.st_mtime_ns,
            width=reader.width,
            height=reader.height,
            num_frames=reader.num_frames,
            timestamp_mode=timestamp_mode,
            timestamps=timestamp_summary,
        )


def inspection_to_dict(result: SeqInspection) -> dict[str, Any]:
    return _json_safe(asdict(result))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
