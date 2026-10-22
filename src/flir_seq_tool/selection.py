from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .transform import CropRect, FrameTransform, RotationSettings
from .measurement_models import MeasurementSettings
from .file_safety import DEFAULT_RAW_ROOT, TargetSnapshot, assert_snapshot, validate_write_path


SCHEMA_VERSION = 3
_TIME_TOLERANCE_S = 1e-6


def _is_timezone_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _require_matching_timezone_awareness(
    first: datetime, second: datetime, label: str
) -> None:
    if _is_timezone_aware(first) != _is_timezone_aware(second):
        raise ValueError(f"{label} must use matching timezone awareness")


def _require_keys(payload: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(payload)
    if actual != expected:
        raise ValueError(
            f"{label} keys mismatch: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _require_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _require_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_datetime(value: Any, label: str) -> datetime:
    text = _require_string(value, label)
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO 8601 timestamp") from exc


def _validate_root_version_and_keys(
    payload: Any,
) -> tuple[dict[str, Any], int]:
    root = _require_mapping(payload, "root")
    if "schema_version" not in root:
        raise ValueError("schema_version is required")
    schema_version = _require_int(root["schema_version"], "schema_version")
    root_keys = {"schema_version", "source", "selection", "display"}
    expected = {1: root_keys, 2: root_keys | {"transform"},
                3: root_keys | {"transform", "measurement"}}
    if schema_version not in expected:
        raise ValueError(f"unsupported schema_version: {schema_version}")
    _require_keys(root, expected[schema_version], "root")
    return root, schema_version


def _read_payload(target: Path) -> dict[str, Any]:
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid selection JSON: {target}") from exc
    return _require_mapping(payload, "root")


@dataclass(frozen=True)
class SourceIdentity:
    filename: str
    size_bytes: int
    modified_time_ns: int
    width: int
    height: int
    num_frames: int
    first_timestamp: datetime
    last_timestamp: datetime

    def __post_init__(self) -> None:
        if not self.filename or Path(self.filename).name != self.filename:
            raise ValueError("filename must be a base filename")
        for name in ("size_bytes", "modified_time_ns", "width", "height", "num_frames"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        if self.size_bytes < 0 or self.modified_time_ns < 0:
            raise ValueError("source size and modified time must be non-negative")
        if self.width < 1 or self.height < 1 or self.num_frames < 1:
            raise ValueError("source dimensions and num_frames must be positive")
        _require_matching_timezone_awareness(
            self.first_timestamp, self.last_timestamp, "source timestamps"
        )
        if self.first_timestamp > self.last_timestamp:
            raise ValueError("source timestamps must be ordered")

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "modified_time_ns": self.modified_time_ns,
            "width": self.width,
            "height": self.height,
            "num_frames": self.num_frames,
            "first_timestamp": self.first_timestamp.isoformat(),
            "last_timestamp": self.last_timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SourceIdentity:
        _require_keys(
            payload,
            {
                "filename",
                "size_bytes",
                "modified_time_ns",
                "width",
                "height",
                "num_frames",
                "first_timestamp",
                "last_timestamp",
            },
            "source",
        )
        return cls(
            filename=_require_string(payload["filename"], "filename"),
            size_bytes=_require_int(payload["size_bytes"], "size_bytes"),
            modified_time_ns=_require_int(
                payload["modified_time_ns"], "modified_time_ns"
            ),
            width=_require_int(payload["width"], "width"),
            height=_require_int(payload["height"], "height"),
            num_frames=_require_int(payload["num_frames"], "num_frames"),
            first_timestamp=_require_datetime(
                payload["first_timestamp"], "first_timestamp"
            ),
            last_timestamp=_require_datetime(
                payload["last_timestamp"], "last_timestamp"
            ),
        )


@dataclass(frozen=True)
class FrameSelection:
    source_start_frame: int
    source_end_frame: int
    start_timestamp: datetime
    end_timestamp: datetime
    frame_count: int
    duration_s: float

    def __post_init__(self) -> None:
        if self.source_start_frame < 0 or self.source_end_frame < self.source_start_frame:
            raise ValueError("selection range is invalid")
        expected_count = self.source_end_frame - self.source_start_frame + 1
        if self.frame_count != expected_count:
            raise ValueError("frame_count does not match selection range")
        _require_matching_timezone_awareness(
            self.start_timestamp, self.end_timestamp, "selection timestamps"
        )
        if self.end_timestamp < self.start_timestamp:
            raise ValueError("selection timestamps must be ordered")
        expected_duration = (self.end_timestamp - self.start_timestamp).total_seconds()
        if not math.isfinite(self.duration_s):
            raise ValueError("duration_s must be finite")
        if abs(self.duration_s - expected_duration) > _TIME_TOLERANCE_S:
            raise ValueError("duration_s does not match selection timestamps")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_start_frame": self.source_start_frame,
            "source_end_frame": self.source_end_frame,
            "start_timestamp": self.start_timestamp.isoformat(),
            "end_timestamp": self.end_timestamp.isoformat(),
            "frame_count": self.frame_count,
            "duration_s": self.duration_s,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FrameSelection:
        _require_keys(
            payload,
            {
                "source_start_frame",
                "source_end_frame",
                "start_timestamp",
                "end_timestamp",
                "frame_count",
                "duration_s",
            },
            "selection",
        )
        return cls(
            source_start_frame=_require_int(
                payload["source_start_frame"], "source_start_frame"
            ),
            source_end_frame=_require_int(
                payload["source_end_frame"], "source_end_frame"
            ),
            start_timestamp=_require_datetime(
                payload["start_timestamp"], "start_timestamp"
            ),
            end_timestamp=_require_datetime(payload["end_timestamp"], "end_timestamp"),
            frame_count=_require_int(payload["frame_count"], "frame_count"),
            duration_s=_require_float(payload["duration_s"], "duration_s"),
        )


@dataclass(frozen=True)
class DisplaySettings:
    colormap: str
    vmin_c: float
    vmax_c: float

    def __post_init__(self) -> None:
        if not isinstance(self.colormap, str) or not self.colormap:
            raise ValueError("colormap must be a non-empty string")
        if not math.isfinite(self.vmin_c) or not math.isfinite(self.vmax_c):
            raise ValueError("display limits must be finite")
        if self.vmin_c >= self.vmax_c:
            raise ValueError("vmin_c must be less than vmax_c")

    def to_dict(self) -> dict[str, Any]:
        return {
            "colormap": self.colormap,
            "vmin_c": self.vmin_c,
            "vmax_c": self.vmax_c,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DisplaySettings:
        _require_keys(payload, {"colormap", "vmin_c", "vmax_c"}, "display")
        return cls(
            colormap=_require_string(payload["colormap"], "colormap"),
            vmin_c=_require_float(payload["vmin_c"], "vmin_c"),
            vmax_c=_require_float(payload["vmax_c"], "vmax_c"),
        )


def transform_to_dict(transform: FrameTransform) -> dict[str, Any]:
    if not isinstance(transform, FrameTransform):
        raise ValueError("transform must be a FrameTransform instance")
    return {
        "rotation": {
            "angle_deg": round(transform.rotation.angle_deg, 1),
            "center": "source_image_center",
            "canvas": "expand",
            "interpolation": "bilinear",
        },
        "crop": {
            "x": transform.crop.x,
            "y": transform.crop.y,
            "width": transform.crop.width,
            "height": transform.crop.height,
        },
    }


def transform_from_dict(
    payload: dict[str, Any], source: SourceIdentity
) -> FrameTransform:
    transform_payload = _require_mapping(payload, "transform")
    _require_keys(transform_payload, {"rotation", "crop"}, "transform")
    rotation_payload = _require_mapping(
        transform_payload["rotation"], "transform.rotation"
    )
    _require_keys(
        rotation_payload,
        {"angle_deg", "center", "canvas", "interpolation"},
        "transform.rotation",
    )
    crop_payload = _require_mapping(transform_payload["crop"], "transform.crop")
    _require_keys(crop_payload, {"x", "y", "width", "height"}, "transform.crop")

    center = _require_string(rotation_payload["center"], "center")
    canvas = _require_string(rotation_payload["canvas"], "canvas")
    interpolation = _require_string(
        rotation_payload["interpolation"], "interpolation"
    )
    if center != "source_image_center":
        raise ValueError("center must be source_image_center")
    if canvas != "expand":
        raise ValueError("canvas must be expand")
    if interpolation != "bilinear":
        raise ValueError("interpolation must be bilinear")

    rotation = RotationSettings(
        _require_float(rotation_payload["angle_deg"], "angle_deg")
    )
    crop = CropRect(
        x=_require_int(crop_payload["x"], "x"),
        y=_require_int(crop_payload["y"], "y"),
        width=_require_int(crop_payload["width"], "width"),
        height=_require_int(crop_payload["height"], "height"),
    )
    return FrameTransform(
        source_width=source.width,
        source_height=source.height,
        rotation=rotation,
        crop=crop,
    )


@dataclass(frozen=True)
class SelectionDocument:
    schema_version: int
    source: SourceIdentity
    selection: FrameSelection
    display: DisplaySettings
    transform: FrameTransform
    measurement: MeasurementSettings = field(default_factory=MeasurementSettings)

    def __post_init__(self) -> None:
        _require_int(self.schema_version, "schema_version")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")
        if self.selection.source_end_frame >= self.source.num_frames:
            raise ValueError("selection range exceeds source num_frames")
        if not isinstance(self.transform, FrameTransform):
            raise ValueError("transform must be a FrameTransform instance")
        if (
            self.transform.source_width != self.source.width
            or self.transform.source_height != self.source.height
        ):
            raise ValueError("transform source dimensions must match source")
        if not isinstance(self.measurement, MeasurementSettings):
            raise ValueError("measurement must be MeasurementSettings")
        self.measurement.validate_bounds(self.transform.crop.width, self.transform.crop.height)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source.to_dict(),
            "selection": self.selection.to_dict(),
            "display": self.display.to_dict(),
            "transform": transform_to_dict(self.transform),
            "measurement": self.measurement.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SelectionDocument:
        root, schema_version = _validate_root_version_and_keys(payload)

        source = SourceIdentity.from_dict(_require_mapping(root["source"], "source"))
        transform = (
            FrameTransform.identity(source.width, source.height)
            if schema_version == 1
            else transform_from_dict(root["transform"], source)
        )
        return cls(
            schema_version=SCHEMA_VERSION,
            source=source,
            selection=FrameSelection.from_dict(
                _require_mapping(root["selection"], "selection")
            ),
            display=DisplaySettings.from_dict(
                _require_mapping(root["display"], "display")
            ),
            transform=transform,
            measurement=(MeasurementSettings.from_dict(root["measurement"])
                         if schema_version == 3 else MeasurementSettings()),
        )


def build_selection_document(
    *,
    source: SourceIdentity,
    source_start_frame: int,
    source_end_frame: int,
    start_timestamp: datetime,
    end_timestamp: datetime,
    display: DisplaySettings,
    transform: FrameTransform | None = None,
    measurement: MeasurementSettings | None = None,
) -> SelectionDocument:
    return SelectionDocument(
        schema_version=SCHEMA_VERSION,
        source=source,
        selection=FrameSelection(
            source_start_frame=source_start_frame,
            source_end_frame=source_end_frame,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            frame_count=source_end_frame - source_start_frame + 1,
            duration_s=(end_timestamp - start_timestamp).total_seconds(),
        ),
        display=display,
        transform=(
            FrameTransform.identity(source.width, source.height)
            if transform is None
            else transform
        ),
        measurement=MeasurementSettings() if measurement is None else measurement,
    )


def validate_source_identity(expected: SourceIdentity, actual: SourceIdentity) -> None:
    for field_name in (
        "filename",
        "size_bytes",
        "modified_time_ns",
        "width",
        "height",
        "num_frames",
        "first_timestamp",
        "last_timestamp",
    ):
        if getattr(expected, field_name) != getattr(actual, field_name):
            raise ValueError(f"source mismatch: {field_name}")


class SelectionStore:
    def __init__(self, root: Path, *, raw_root: Path = DEFAULT_RAW_ROOT) -> None:
        self.root = Path(root)
        self.raw_root = Path(raw_root)

    def path_for(self, source_path: Path) -> Path:
        return self.root / f"{Path(source_path).stem}.selection.json"

    def load(self, source_path: Path) -> SelectionDocument:
        return self.load_path(self.path_for(source_path))

    def load_expected(
        self, source_path: Path, expected_source: SourceIdentity
    ) -> SelectionDocument:
        return self.load_path(self.path_for(source_path), expected_source)

    def load_path(
        self, target: Path, expected_source: SourceIdentity | None = None
    ) -> SelectionDocument:
        payload, _ = _validate_root_version_and_keys(_read_payload(Path(target)))
        configured_source = SourceIdentity.from_dict(
            _require_mapping(payload["source"], "source")
        )
        if expected_source is not None:
            validate_source_identity(expected_source, configured_source)
        return SelectionDocument.from_dict(payload)

    def save(self, source_path: Path, document: SelectionDocument) -> Path:
        target = self.path_for(source_path)
        return self.save_to(
            target, document, source_path=source_path,
            expected_target=TargetSnapshot.capture(target),
            expected_backup=TargetSnapshot.capture(target.with_name(target.name + ".bak")),
        )

    def save_to(
        self, target: Path, document: SelectionDocument, *, source_path: Path,
        expected_target: TargetSnapshot, expected_backup: TargetSnapshot,
    ) -> Path:
        target = Path(target)
        backup = target.with_name(target.name + ".bak")
        def check_paths() -> None:
            validate_write_path(target, source_path=source_path, raw_root=self.raw_root)
            validate_write_path(backup, source_path=source_path, raw_root=self.raw_root)

        check_paths()
        assert_snapshot(target, expected_target)
        assert_snapshot(backup, expected_backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        moved_old = False
        moved_snapshot: TargetSnapshot | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".tmp",
                prefix=target.name + ".",
                dir=target.parent,
                delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                json.dump(document.to_dict(), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())

            verified = SelectionDocument.from_dict(
                _require_mapping(
                    json.loads(temp_path.read_text(encoding="utf-8")), "root"
                )
            )
            if verified != document:
                raise ValueError("selection verification mismatch")

            check_paths()
            assert_snapshot(target, expected_target)
            assert_snapshot(backup, expected_backup)
            try:
                if expected_target.exists:
                    os.replace(target, backup)
                    moved_old = True
                    moved_snapshot = TargetSnapshot.capture(backup)
                assert_snapshot(target, TargetSnapshot(exists=False))
                os.replace(temp_path, target)
                temp_path = None
            except Exception as replace_error:
                if moved_old:
                    try:
                        check_paths()
                        assert_snapshot(target, TargetSnapshot(exists=False))
                        if moved_snapshot is not None:
                            assert_snapshot(backup, moved_snapshot)
                        else:
                            # Rename may change ctime on some filesystems. The
                            # original file identity/content must still match.
                            actual = TargetSnapshot.capture(backup)
                            for field in ("exists", "device", "inode", "size_bytes", "modified_time_ns"):
                                if getattr(actual, field) != getattr(expected_target, field):
                                    raise RuntimeError(f"backup changed during recovery: {backup}")
                        os.replace(backup, target)
                    except Exception as restore_error:
                        raise RuntimeError(
                            "selection replace and restore failed; "
                            f"target={target}; backup={backup}; "
                            f"backup exists={backup.exists()}; "
                            f"replace error={replace_error}; "
                            f"restore error={restore_error}"
                        ) from restore_error
                raise
            return target
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
