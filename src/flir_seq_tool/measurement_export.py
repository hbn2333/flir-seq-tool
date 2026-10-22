"""Frozen, traceable CSV/PNG exports with guarded single-file publication."""

from dataclasses import asdict, dataclass
from datetime import datetime
import csv
from itertools import groupby
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np
from PIL import Image

from .file_safety import DEFAULT_RAW_ROOT, TargetSnapshot, assert_snapshot, validate_write_path
from .measurement_models import CurrentAnalysis, MeasurementSettings
from .measurement_state import AnalysisKey
from .measurement_store import ResultStore
from .measurement_stats import histogram, profile, summarize
from .selection import DisplaySettings, SourceIdentity, transform_from_dict


COMMON_COLUMNS = (
    "format_version", "algorithm_version", "source_identity_json", "transform_json",
    "source_start", "source_end", "roi_id", "roi_name", "roi_x", "roi_y",
    "roi_width", "roi_height", "source_frame", "local_frame", "timestamp", "relative_s",
)
STATS_COLUMNS = ("mean_c", "min_c", "max_c", "delta_c", "std_c", "valid_pixels", "total_pixels", "status")
HIST_COLUMNS = ("bin_left_c", "bin_right_c", "bin_count", "bin_fraction")
PROFILE_COLUMNS = ("direction", "position_pixel", "position_mean_c", "position_valid_pixels")


@dataclass(frozen=True)
class AnalysisExportSnapshot:
    source_identity: SourceIdentity
    source_path: Path
    source_range: tuple[int, int]
    work_start_timestamp: datetime
    transform_json: str
    display: DisplaySettings
    settings: MeasurementSettings
    current_source_frame: int
    current_local_frame: int
    timestamp: datetime
    current_values: np.ndarray
    current_analysis: CurrentAnalysis | None
    result_store_path: Path | None
    algorithm_version: str = "roi-stats-v1"
    format_version: str = "analysis-v1"

    def __post_init__(self):
        object.__setattr__(self, "source_path", Path(self.source_path))
        if self.result_store_path is not None:
            object.__setattr__(self, "result_store_path", Path(self.result_store_path))
        if self.algorithm_version != "roi-stats-v1" or self.format_version != "analysis-v1":
            raise ValueError("unsupported analysis export version")
        transform_payload = json.loads(self.transform_json)
        transform = transform_from_dict(transform_payload, self.source_identity)
        object.__setattr__(self, "transform_json", json.dumps(transform_payload, sort_keys=True,
                                                             separators=(",", ":"), allow_nan=False))
        self.settings.validate_bounds(transform.crop.width, transform.crop.height)
        if (len(self.source_range) != 2 or any(isinstance(v, bool) or not isinstance(v, int) for v in self.source_range)
                or not 0 <= self.source_range[0] <= self.source_range[1] < self.source_identity.num_frames):
            raise ValueError("invalid export source range")
        if (not self.source_range[0] <= self.current_source_frame <= self.source_range[1]
                or self.current_local_frame != self.current_source_frame - self.source_range[0]):
            raise ValueError("export current frame does not match source range")
        values = np.asarray(self.current_values)
        if (values.shape != (transform.crop.height, transform.crop.width)
                or not np.issubdtype(values.dtype, np.number) or np.iscomplexobj(values)):
            raise ValueError("export temperature must match the applied real 2-D image")
        frozen = np.array(values, copy=True)
        frozen.setflags(write=False)
        object.__setattr__(self, "current_values", frozen)
        if self.current_analysis is not None and (
            self.current_analysis.source_frame != self.current_source_frame
            or self.current_analysis.roi_id != self.settings.active_roi_id
        ):
            raise ValueError("export current analysis does not match selected ROI/frame")
        if self.current_analysis is not None:
            roi = next(roi for roi in self.settings.rois if roi.id == self.settings.active_roi_id)
            analysis = CurrentAnalysis(self.current_source_frame, roi.id, summarize(frozen, roi),
                                       histogram(frozen, roi), profile(frozen, roi, "horizontal"),
                                       profile(frozen, roi, "vertical"))
            for result in (analysis.histogram, analysis.horizontal, analysis.vertical):
                for value in vars(result).values():
                    if isinstance(value, np.ndarray):
                        value.setflags(write=False)
            object.__setattr__(self, "current_analysis", analysis)

    @property
    def analysis_key(self):
        return AnalysisKey.from_inputs(self.source_identity, *self.source_range,
                                       transform_from_dict(json.loads(self.transform_json), self.source_identity),
                                       self.settings.rois)

    @property
    def kind(self):
        return "curves" if self.settings.result_view == "curves" else self.settings.frame_view


def publish_analysis_file(target, *, source_path, raw_root, expected, writer, validator):
    target = Path(target)
    if target.suffix.lower() not in (".csv", ".png"):
        raise ValueError("analysis output must use .csv or .png")
    def check():
        validate_write_path(target, source_path=source_path, raw_root=raw_root)
        assert_snapshot(target, expected)
    check()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
        temporary = Path(name)
        os.close(fd)
        writer(temporary)
        validator(temporary)
        check()
        os.replace(temporary, target)
        temporary = None
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def spreadsheet_text(value):
    text = str(value)
    probe = text.lstrip()
    if text.startswith(("\t", "\r", "\n")) or probe.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _status(valid, total):
    return "no_finite_pixels" if not valid else "partial_invalid" if valid < total else "ok"


def _shape(snapshot):
    if snapshot.kind == "curves":
        checked = [roi for roi in snapshot.settings.rois if roi.checked]
        if not checked or snapshot.result_store_path is None:
            raise ValueError("curve export requires checked ROI(s) and a complete current result")
        return COMMON_COLUMNS + STATS_COLUMNS, (snapshot.source_range[1] - snapshot.source_range[0] + 1) * len(checked)
    if snapshot.current_analysis is None:
        raise ValueError("current-frame export requires a selected ROI")
    if snapshot.kind == "distribution":
        return COMMON_COLUMNS + STATS_COLUMNS + HIST_COLUMNS, max(1, len(snapshot.current_analysis.histogram.counts))
    result = getattr(snapshot.current_analysis, snapshot.settings.profile_direction)
    return COMMON_COLUMNS + STATS_COLUMNS + PROFILE_COLUMNS, len(result.positions)


def _rows(snapshot):
    base = dict(format_version=snapshot.format_version, algorithm_version=snapshot.algorithm_version,
                source_identity_json=json.dumps(snapshot.source_identity.to_dict(), sort_keys=True,
                                                separators=(",", ":"), allow_nan=False),
                transform_json=snapshot.transform_json, source_start=snapshot.source_range[0],
                source_end=snapshot.source_range[1])
    roi_rows = {roi.id: dict(base, roi_id=spreadsheet_text(roi.id), roi_name=spreadsheet_text(roi.name),
                             roi_x=roi.x, roi_y=roi.y, roi_width=roi.width, roi_height=roi.height)
                for roi in snapshot.settings.rois}
    if snapshot.kind == "curves":
        order = {roi.id: i for i, roi in enumerate(snapshot.settings.rois) if roi.checked}
        store = ResultStore.open_readonly(snapshot.result_store_path)
        try:
            if store.key_json != snapshot.analysis_key.to_json():
                raise ValueError("curve export result key does not match snapshot")
            for local, frame_rows in groupby(store.iter_rows(tuple(order)), key=lambda row: row["local_frame"]):
                # At most one frame's R rows are held; never sort the complete result.
                for row in sorted(frame_rows, key=lambda row: order[row["roi_id"]]):
                    roi_id = row["roi_id"]
                    yield dict(roi_rows[roi_id], **{name: row[name] for name in (
                        "source_frame", "local_frame", "timestamp", "relative_s", *STATS_COLUMNS[:-1])},
                        status=_status(row["valid_pixels"], row["total_pixels"]))
        finally:
            store.close()
        return
    analysis = snapshot.current_analysis
    stats = asdict(analysis.stats)
    common = dict(roi_rows[analysis.roi_id], **stats,
                  status=_status(stats["valid_pixels"], stats["total_pixels"]),
                  source_frame=snapshot.current_source_frame, local_frame=snapshot.current_local_frame,
                  timestamp=snapshot.timestamp.isoformat(),
                  relative_s=(snapshot.timestamp - snapshot.work_start_timestamp).total_seconds())
    if snapshot.kind == "distribution":
        result = analysis.histogram
        if not result.valid_pixels:
            yield common | dict.fromkeys(HIST_COLUMNS)
        else:
            for i, count in enumerate(result.counts):
                yield dict(common, bin_left_c=float(result.edges[i]), bin_right_c=float(result.edges[i + 1]),
                           bin_count=int(count), bin_fraction=float(result.fractions[i]))
    else:
        result = getattr(analysis, snapshot.settings.profile_direction)
        for position, mean, count in zip(result.positions, result.means_c, result.valid_counts):
            yield dict(common, direction=result.direction, position_pixel=int(position),
                       position_mean_c=float(mean) if math.isfinite(mean) else None, position_valid_pixels=int(count))


def export_csv(target, snapshot, *, expected, raw_root=DEFAULT_RAW_ROOT):
    if Path(target).suffix.lower() != ".csv":
        raise ValueError("CSV output requires .csv")
    columns, expected_rows = _shape(snapshot)
    def writer(path):
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            output = csv.DictWriter(stream, fieldnames=columns)
            output.writeheader()
            for row in _rows(snapshot):
                output.writerow(row)
            stream.flush()
            os.fsync(stream.fileno())
    def validator(path):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != columns:
                raise ValueError("CSV header verification failed")
            count = 0
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("CSV column verification failed")
                count += 1
            if count != expected_rows:
                raise ValueError("CSV row count verification failed")
    return publish_analysis_file(target, source_path=snapshot.source_path, raw_root=raw_root,
                                  expected=expected, writer=writer, validator=validator)


def export_png(target, snapshot, *, expected, raw_root=DEFAULT_RAW_ROOT):
    if Path(target).suffix.lower() != ".png":
        raise ValueError("PNG output requires .png")
    _shape(snapshot)
    from .measurement_plots import build_report_figure
    def writer(path):
        figure = build_report_figure(snapshot)
        try:
            figure.savefig(path, format="png", dpi=150)
        finally:
            figure.clear()
    def validator(path):
        with Image.open(path) as picture:
            if picture.format != "PNG" or min(picture.size) < 1:
                raise ValueError("PNG verification failed")
            picture.load()
    return publish_analysis_file(target, source_path=snapshot.source_path, raw_root=raw_root,
                                  expected=expected, writer=writer, validator=validator)
