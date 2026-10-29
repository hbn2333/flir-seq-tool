"""Frozen, complete height CSVs streamed and verified by an independent worker."""

from dataclasses import dataclass
from datetime import datetime
import csv
import json
import os
from pathlib import Path
import tempfile
import threading

from .file_safety import DEFAULT_RAW_ROOT, TargetSnapshot, assert_snapshot, validate_write_path
from .height_models import height_positions, zone_statistics
from .height_store import HEIGHT_ALGORITHM_VERSION, HeightResultStore
from .measurement_export import spreadsheet_text
from .measurement_job import AnalysisCancelled, AnalysisProgress
from .measurement_models import MeasurementSettings
from .measurement_state import AnalysisKey
from .provenance import origin_fields, validate_provenance
from .selection import SourceIdentity, transform_from_dict, transform_to_dict


COMMON_COLUMNS = (
    'format_version', 'algorithm_version', 'source_identity_json', 'transform_json',
    'source_start', 'source_end', 'time_origin_timestamp', 'source_frame', 'local_frame',
    'timestamp', 'relative_s', 'time_status', 'roi_id', 'roi_name', 'roi_x', 'roi_y',
    'roi_width', 'roi_height', 'coordinate_space', 'heated_edge', 'height_direction',
    'height_origin', 'mm_per_pixel', 'reference_height_px', 'reference_height_mm',
    'origin_source_identity_json', 'origin_frame', 'origin_scope', 'origin_depth', 'origin_status',
)
HEIGHT_COLUMNS = ('roi_row_index', 'cropped_y_pixel', 'height_pixel', 'height_mm',
                  'row_mean_c', 'valid_pixels', 'total_pixels', 'status')
ZONE_COLUMNS = ('cut_low_middle_fraction', 'cut_middle_high_fraction') + tuple(
    f'{zone}_{field}' for zone in ('low', 'middle', 'high') for field in
    ('name', 'lower_pixel', 'upper_pixel', 'lower_mm', 'upper_mm', 'mean_c',
     'valid_pixels', 'total_pixels', 'status')) + (
    'delta_zone_a', 'delta_zone_b', 'zone_delta_c', 'delta_a_valid_pixels',
    'delta_b_valid_pixels', 'roi_range_c')


@dataclass(frozen=True)
class HeightExportSnapshot:
    source_identity: SourceIdentity
    source_path: Path
    source_range: tuple[int, int]
    work_start_timestamp: datetime
    transform_json: str
    settings: MeasurementSettings
    result_store_path: Path
    provenance: tuple = ()
    analysis_key_json: str | None = None
    source_file_snapshot: TargetSnapshot | None = None
    result_file_snapshot: TargetSnapshot | None = None
    algorithm_version: str = HEIGHT_ALGORITHM_VERSION
    format_version: str = 'height-v1'

    def __post_init__(self):
        object.__setattr__(self, 'source_path', Path(self.source_path))
        object.__setattr__(self, 'result_store_path', Path(self.result_store_path))
        if (not isinstance(self.source_identity, SourceIdentity) or not isinstance(self.settings, MeasurementSettings)
                or not isinstance(self.work_start_timestamp, datetime)):
            raise ValueError('height snapshot requires identity, settings and time origin')
        if self.algorithm_version != HEIGHT_ALGORITHM_VERSION or self.format_version != 'height-v1':
            raise ValueError('unknown height export version')
        if (not isinstance(self.source_range, tuple) or len(self.source_range) != 2 or
                any(isinstance(v, bool) or not isinstance(v, int) for v in self.source_range) or
                not 0 <= self.source_range[0] <= self.source_range[1] < self.source_identity.num_frames):
            raise ValueError('invalid height export source range')
        payload = json.loads(self.transform_json)
        transform = transform_from_dict(payload, self.source_identity)
        object.__setattr__(self, 'transform_json', json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False))
        if not self.settings.rois:
            raise ValueError('height export requires ROI(s)')
        self.settings.validate_bounds(transform.crop.width, transform.crop.height)
        for roi in self.settings.rois:
            self.settings.height_for(roi.id).validate_height(roi.height)
        validate_provenance(self.source_identity, self.provenance)
        key = AnalysisKey.from_inputs(self.source_identity, *self.source_range, transform, self.settings.rois).to_json()
        if self.analysis_key_json is not None and self.analysis_key_json != key:
            raise ValueError('height snapshot key mismatch')
        object.__setattr__(self, 'analysis_key_json', key)
        for name, path in (('source_file_snapshot', self.source_path), ('result_file_snapshot', self.result_store_path)):
            if getattr(self, name) is None:
                object.__setattr__(self, name, TargetSnapshot.capture(path))

    @property
    def analysis_key(self):
        return AnalysisKey.from_inputs(self.source_identity, *self.source_range,
                                       transform_from_dict(json.loads(self.transform_json), self.source_identity),
                                       self.settings.rois)

    def expected_rows(self, kind):
        frames = self.source_range[1] - self.source_range[0] + 1
        if kind == 'height':
            return frames * sum(r.height for r in self.settings.rois)
        if kind == 'zones':
            return frames * sum(self.settings.height_for(r.id).zones_enabled for r in self.settings.rois)
        raise ValueError('unknown height export kind')


def build_height_snapshot(session):
    """Freeze session inputs without importing the viewer or scanning the result table."""
    if session.measurement.result_path is None:
        raise ValueError('height export requires a complete height result')
    return HeightExportSnapshot(session.source_identity(), Path(session.reader.path),
                                tuple(session.measure_source_range()), session.work_timestamps()[0],
                                json.dumps(transform_to_dict(session.applied_transform)), session.measurement.settings,
                                Path(session.measurement.result_path), tuple(getattr(session, 'provenance', ())))


def _check_cancel(event):
    if event is not None and event.is_set():
        raise AnalysisCancelled()


def _check_inputs(snapshot):
    assert_snapshot(snapshot.source_path, snapshot.source_file_snapshot)
    stat = snapshot.source_path.stat()
    identity = snapshot.source_identity
    if (snapshot.source_path.name != identity.filename or stat.st_size != identity.size_bytes or
            stat.st_mtime_ns != identity.modified_time_ns):
        raise RuntimeError('source identity changed since height analysis')
    assert_snapshot(snapshot.result_store_path, snapshot.result_file_snapshot)


def _rows(snapshot, kind, cancel_event=None):
    if kind not in ('height', 'zones'):
        raise ValueError('unknown height export kind')
    store = HeightResultStore.open_readonly(snapshot.result_store_path, cancel_event=cancel_event)
    try:
        if store.key_json != snapshot.analysis_key_json:
            raise ValueError('height export result key does not match frozen inputs')
        first = store._read_connection().execute('SELECT timestamp FROM frames WHERE local_frame=0').fetchone()
        if first is None or first[0] != snapshot.work_start_timestamp.isoformat():
            raise ValueError('height export time origin does not match result')
        base = dict(format_version=snapshot.format_version, algorithm_version=snapshot.algorithm_version,
                    source_identity_json=json.dumps(snapshot.source_identity.to_dict(), sort_keys=True,
                                                    separators=(',', ':'), allow_nan=False),
                    transform_json=snapshot.transform_json, source_start=snapshot.source_range[0],
                    source_end=snapshot.source_range[1], time_origin_timestamp=snapshot.work_start_timestamp.isoformat(),
                    coordinate_space='cropped_pixel', height_origin='heated_roi_edge')
        frames = store._read_connection().execute('SELECT * FROM frames ORDER BY local_frame')
        try:
            for frame in frames:
                _check_cancel(cancel_event)
                for roi in snapshot.settings.rois:
                    config = snapshot.settings.height_for(roi.id)
                    if kind == 'zones' and not config.zones_enabled:
                        continue
                    p = store.height_profile(roi.id, frame['local_frame'])
                    row = dict(base, **dict(frame), roi_id=spreadsheet_text(roi.id),
                               roi_name=spreadsheet_text(roi.name), roi_x=roi.x, roi_y=roi.y,
                               roi_width=roi.width, roi_height=roi.height, heated_edge=config.heated_edge,
                               height_direction=config.height_direction, mm_per_pixel=config.mm_per_pixel,
                               reference_height_px=config.reference_height_px, reference_height_mm=config.reference_height_mm,
                               **origin_fields(snapshot.provenance, frame['source_frame']))
                    if kind == 'height':
                        px, mm = height_positions(roi.height, config)
                        indices = range(roi.height - 1, -1, -1) if config.heated_edge == 'bottom' else range(roi.height)
                        for r in indices:
                            if r % 1024 == 0:
                                _check_cancel(cancel_event)
                            count = int(p.valid_counts[r])
                            yield dict(row, roi_row_index=r, cropped_y_pixel=roi.y + r,
                                       height_pixel=float(px[r]), height_mm=None if mm is None else float(mm[r]),
                                       row_mean_c=float(p.means_c[r]) if count else None,
                                       valid_pixels=count, total_pixels=roi.width,
                                       status='no_finite_pixels' if not count else 'partial_invalid' if count < roi.width else 'ok')
                    else:
                        zones = zone_statistics(p, config)
                        row.update(cut_low_middle_fraction=config.cuts[0], cut_middle_high_fraction=config.cuts[1])
                        for zone in zones.zones:
                            row.update({f'{zone.key}_{key}': value for key,value in dict(
                                name=spreadsheet_text(zone.name), lower_pixel=zone.lower_px, upper_pixel=zone.upper_px,
                                lower_mm=zone.lower_mm, upper_mm=zone.upper_mm, mean_c=zone.mean_c,
                                valid_pixels=zone.valid_pixels, total_pixels=zone.total_pixels, status=zone.status).items()})
                        row.update(delta_zone_a=zones.delta_zone_a, delta_zone_b=zones.delta_zone_b,
                                   zone_delta_c=zones.zone_delta_c, delta_a_valid_pixels=zones.delta_a_valid_pixels,
                                   delta_b_valid_pixels=zones.delta_b_valid_pixels, roi_range_c=zones.roi_range_c)
                        yield row
        finally:
            frames.close()
    finally:
        store.close()


def validate_height_csv(path, snapshot, kind, cancel_event=None):
    """Read back in bounded chunks and compare every field with independently queried rows."""
    columns = COMMON_COLUMNS + (HEIGHT_COLUMNS if kind == 'height' else ZONE_COLUMNS)
    expected = _rows(snapshot, kind, cancel_event)
    count = 0
    try:
        with Path(path).open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != columns:
                raise ValueError('height CSV header verification failed')
            for row in reader:
                if count % 1024 == 0:
                    _check_cancel(cancel_event)
                wanted = next(expected, None)
                if wanted is None or None in row or any(row[k] != ('' if wanted[k] is None else str(wanted[k])) for k in columns):
                    raise ValueError(f'height CSV data verification failed at row {count}')
                count += 1
            if next(expected, None) is not None or count != snapshot.expected_rows(kind):
                raise ValueError('height CSV row count verification failed')
    finally:
        expected.close()


def export_height_csv(target, snapshot, kind, *, expected, raw_root=DEFAULT_RAW_ROOT,
                      cancel_event=None, progress=None):
    target = Path(target)
    if target.suffix.lower() != '.csv':
        raise ValueError('height CSV output requires .csv')
    total = snapshot.expected_rows(kind)
    if total < 1:
        raise ValueError('zone export requires at least one enabled ROI')
    def check():
        _check_inputs(snapshot)
        validate_write_path(target, source_path=snapshot.source_path, raw_root=raw_root)
        if target.exists() and target.samefile(snapshot.result_store_path):
            raise ValueError('output aliases the height result store')
        assert_snapshot(target, expected)
    _check_cancel(cancel_event)
    check()
    if progress is not None:
        progress.update(0, total, 'Writing complete height CSV' if kind == 'height' else 'Writing zone CSV')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=target.name + '.', suffix='.tmp', dir=target.parent)
        temporary = Path(name)
        os.close(fd)
        columns = COMMON_COLUMNS + (HEIGHT_COLUMNS if kind == 'height' else ZONE_COLUMNS)
        rows = _rows(snapshot, kind, cancel_event)
        try:
            with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
                output = csv.DictWriter(stream, fieldnames=columns)
                output.writeheader()
                count = 0
                for count, row in enumerate(rows, 1):
                    output.writerow(row)
                    if count % 1024 == 0:
                        _check_cancel(cancel_event)
                        if progress is not None:
                            progress.update(count, total, 'Writing CSV')
                if count != total:
                    raise ValueError('height CSV source row count mismatch')
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            rows.close()
        if progress is not None:
            progress.update(total, total, 'Verifying CSV')
        validate_height_csv(temporary, snapshot, kind, cancel_event)
        _check_cancel(cancel_event)
        check()
        # Publication is a single uninterruptible operation after all final checks.
        os.replace(temporary, target)
        temporary = None
        if progress is not None:
            progress.update(total, total, 'CSV complete')
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class HeightExportOutcome:
    status: str
    error: str | None = None
    path: Path | None = None


class HeightExportJob:
    def __init__(self, target, snapshot, kind, expected, raw_root=DEFAULT_RAW_ROOT):
        self.target, self.snapshot, self.kind = Path(target), snapshot, kind
        self.expected, self.raw_root = expected, Path(raw_root)
        self.progress = AnalysisProgress()
        self._cancel = threading.Event()
        self._outcome = None
        self._thread = threading.Thread(target=self._run, daemon=False, name='flir-height-csv')

    def _run(self):
        try:
            path = export_height_csv(self.target, self.snapshot, self.kind, expected=self.expected,
                                     raw_root=self.raw_root, cancel_event=self._cancel, progress=self.progress)
            self._outcome = HeightExportOutcome('complete', path=path)
        except AnalysisCancelled:
            self._outcome = HeightExportOutcome('cancelled')
        except Exception as exc:
            self._outcome = HeightExportOutcome('failed', error=str(exc))

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
