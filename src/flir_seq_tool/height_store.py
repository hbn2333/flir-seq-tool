"""Owned complete SQLite row profiles, retaining the standard samples interface."""

from datetime import datetime
import json
import math
import sqlite3

import numpy as np

from .height_models import RowProfile
from .measurement_store import ResultStore


HEIGHT_ALGORITHM_VERSION = 'roi-height-v1'
FRAME_COLUMNS = ('local_frame', 'source_frame', 'timestamp', 'relative_s', 'time_status')
HEIGHT_COLUMNS = ('roi_id', 'local_frame', 'roi_row_index', 'sum_c', 'valid_pixels',
                  'row_mean_c', 'total_pixels')


class HeightResultStore(ResultStore):
    @classmethod
    def open_readonly(cls, path, *, cancel_event=None):
        store = cls(path)
        store._validation_cancel_event = cancel_event
        store._read_connection()
        return store

    def _check_validation_cancel(self):
        event = getattr(self, '_validation_cancel_event', None)
        if event is not None and event.is_set():
            from .measurement_job import AnalysisCancelled
            raise AnalysisCancelled()

    @classmethod
    def create(cls, parent=None):
        store = super().create(parent)
        try:
            store._writer.executescript('''
                CREATE TABLE frames (
                    local_frame INTEGER PRIMARY KEY, source_frame INTEGER NOT NULL,
                    timestamp TEXT NOT NULL, relative_s REAL NOT NULL, time_status TEXT NOT NULL
                );
                CREATE TABLE height_rois (roi_id TEXT PRIMARY KEY, width INTEGER NOT NULL,
                                           height INTEGER NOT NULL);
                CREATE TABLE height_rows (
                    roi_id TEXT NOT NULL, local_frame INTEGER NOT NULL, roi_row_index INTEGER NOT NULL,
                    sum_c REAL NOT NULL, valid_pixels INTEGER NOT NULL,
                    row_mean_c REAL, total_pixels INTEGER NOT NULL,
                    PRIMARY KEY (roi_id, local_frame, roi_row_index)
                );
                CREATE INDEX height_frame_roi ON height_rows(local_frame, roi_id, roi_row_index);
            ''')
            return store
        except Exception:
            store.cleanup()
            raise

    def _append(self, table, columns, rows):
        writer = self._write_connection()
        def parameters():
            for row in rows:
                if set(row) != set(columns):
                    raise ValueError(f'{table} columns mismatch')
                yield tuple(row[k] for k in columns)
        with writer:
            writer.executemany(f'INSERT INTO {table} VALUES ({",".join("?" for _ in columns)})', parameters())

    def append_frames(self, rows):
        self._append('frames', FRAME_COLUMNS, rows)

    def append_height_rows(self, rows):
        self._append('height_rows', HEIGHT_COLUMNS, rows)

    def complete(self, *, expected_rows, expected_height_rows, expected_frames, roi_shapes, key_json):
        writer = self._write_connection()
        for count in (expected_rows, expected_height_rows, expected_frames):
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError('height expected counts must be positive integers')
        shapes = tuple(roi_shapes)
        if not shapes or len({s[0] for s in shapes}) != len(shapes):
            raise ValueError('height ROI shapes must be unique and nonempty')
        for roi_id, width, height in shapes:
            if (not isinstance(roi_id, str) or not roi_id or
                    any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in (width, height))):
                raise ValueError('invalid height ROI shape')
        if (expected_rows != expected_frames * len(shapes) or
                expected_height_rows != expected_frames * sum(h for _, _, h in shapes)):
            raise ValueError('height expected geometry row counts mismatch')
        height_key = json.dumps(dict(algorithm_version=HEIGHT_ALGORITHM_VERSION,
                                    base_key_json=key_json, roi_shapes=shapes),
                                sort_keys=True, separators=(',', ':'), allow_nan=False)
        metadata = {'has_height': '1', 'height_algorithm_version': HEIGHT_ALGORITHM_VERSION,
                    'height_key_json': height_key, 'height_row_count': str(expected_height_rows),
                    'frame_count': str(expected_frames)}
        with writer:
            writer.execute('DELETE FROM height_rois')
            writer.executemany('INSERT INTO height_rois VALUES (?,?,?)', shapes)
            self._validate_height(writer, metadata, key_json)
            writer.executemany('INSERT INTO metadata VALUES (?,?)', metadata.items())
        super().complete(expected_rows=expected_rows, key_json=key_json)
        self._remember_verified(writer)

    def _fingerprint(self):
        stat = self.path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def _remember_verified(self, connection):
        self._verified_metadata = dict(connection.execute('SELECT key,value FROM metadata'))
        self._verified_fingerprint = self._fingerprint()

    def _read_connection(self):
        self._check_validation_cancel()
        if self._reader is None and getattr(self, '_verified_fingerprint', None) == self._fingerprint():
            # The worker already proved completeness. The owned immutable file can be reopened
            # for a small current-frame UI query without another O(N*H) integrity scan.
            reader = sqlite3.connect(self.path.absolute().as_uri() + '?mode=ro', uri=True)
            reader.row_factory = sqlite3.Row
            try:
                if dict(reader.execute('SELECT key,value FROM metadata')) != self._verified_metadata:
                    raise ValueError('height completion metadata changed')
            except Exception:
                reader.close()
                raise
            self._reader = reader
            return reader
        if self._reader is None:
            reader = sqlite3.connect(self.path.absolute().as_uri() + '?mode=ro', uri=True)
            reader.row_factory = sqlite3.Row
            event = getattr(self, '_validation_cancel_event', None)
            if event is not None:
                reader.set_progress_handler(lambda: int(event.is_set()), 1000)
            try:
                meta = dict(reader.execute('SELECT key,value FROM metadata'))
                if meta.get('complete') != '1':
                    raise ValueError('result store is not complete')
                actual = reader.execute('SELECT COUNT(*) FROM samples').fetchone()[0]
                if not meta.get('key_json') or meta.get('row_count') != str(actual):
                    raise ValueError('result store completion metadata mismatch')
                self._validate_height(reader, meta, meta['key_json'])
                self._remember_verified(reader)
            except Exception:
                reader.close()
                self._check_validation_cancel()
                raise
            self._reader = reader
        return self._reader

    def _validate_height(self, c, meta, key_json):
        try:
            if meta.get('has_height') != '1' or meta.get('height_algorithm_version') != HEIGHT_ALGORITHM_VERSION:
                raise ValueError('result store has no complete height capability')
            count = int(meta['frame_count'])
            shapes = [tuple(r) for r in c.execute('SELECT * FROM height_rois ORDER BY roi_id')]
            if c.execute("SELECT 1 FROM height_rois WHERE typeof(width)!='integer' OR typeof(height)!='integer' OR width<1 OR height<1 LIMIT 1").fetchone():
                raise ValueError('height ROI dimensions must be positive integers')
            expected_key = json.dumps(dict(algorithm_version=HEIGHT_ALGORITHM_VERSION,
                                          base_key_json=key_json, roi_shapes=shapes),
                                      sort_keys=True, separators=(',', ':'), allow_nan=False)
            # Shape order in the capability key is canonical, independent of UI creation order.
            supplied = json.loads(meta['height_key_json'])
            supplied['roi_shapes'] = sorted(supplied['roi_shapes'])
            if json.dumps(supplied, sort_keys=True, separators=(',', ':'), allow_nan=False) != expected_key:
                raise ValueError('height key mismatch')
            if count < 1 or not shapes or int(meta['height_row_count']) != count * sum(s[2] for s in shapes):
                raise ValueError('height geometry counts mismatch')
            try:
                base = json.loads(key_json)
            except json.JSONDecodeError:
                base = None  # ResultStore also permits opaque legacy keys.
            expected_start = None
            if isinstance(base, dict):
                expected_start = base['source_start']
                expected_shapes = sorted((r[0], r[3], r[4]) for r in base['roi_geometry'])
                if base['source_end'] - expected_start + 1 != count or expected_shapes != shapes:
                    raise ValueError('height geometry does not match the base analysis key')
            if c.execute('SELECT COUNT(*) FROM frames').fetchone()[0] != count:
                raise ValueError('height frame count mismatch')
            if c.execute('SELECT COUNT(*) FROM height_rows').fetchone()[0] != int(meta['height_row_count']):
                raise ValueError('height row count mismatch')
            invalid = c.execute('''SELECT 1 FROM height_rows h
                LEFT JOIN height_rois r ON r.roi_id=h.roi_id
                LEFT JOIN frames f ON f.local_frame=h.local_frame
                WHERE r.roi_id IS NULL OR f.local_frame IS NULL OR
                typeof(h.local_frame)!='integer' OR typeof(h.roi_row_index)!='integer' OR
                typeof(h.valid_pixels)!='integer' OR typeof(h.total_pixels)!='integer' OR
                h.roi_row_index<0 OR h.roi_row_index>=r.height OR
                h.total_pixels!=r.width OR h.valid_pixels<0 OR h.valid_pixels>r.width OR
                (h.valid_pixels=0 AND (h.row_mean_c IS NOT NULL OR h.sum_c!=0)) OR
                (h.valid_pixels>0 AND (h.row_mean_c IS NULL OR
                    ABS(h.sum_c/h.valid_pixels-h.row_mean_c)>1e-10*MAX(1,ABS(h.row_mean_c)))) LIMIT 1''').fetchone()
            if invalid:
                raise ValueError('height row geometry or value mismatch')
            for roi_id, width, height in shapes:
                actual = c.execute('SELECT COUNT(*) FROM height_rows WHERE roi_id=?', (roi_id,)).fetchone()[0]
                if actual != height * count:
                    raise ValueError('height ROI row count mismatch')
            mismatch = c.execute('''SELECT 1 FROM samples s LEFT JOIN frames f USING(local_frame)
                LEFT JOIN height_rois r ON s.roi_id=r.roi_id
                WHERE f.local_frame IS NULL OR r.roi_id IS NULL OR
                typeof(s.local_frame)!='integer' OR typeof(s.source_frame)!='integer' OR
                typeof(s.valid_pixels)!='integer' OR typeof(s.total_pixels)!='integer' OR
                s.source_frame!=f.source_frame OR s.timestamp!=f.timestamp OR
                s.relative_s!=f.relative_s OR s.total_pixels!=r.width*r.height LIMIT 1''').fetchone()
            if mismatch or c.execute('SELECT COUNT(*) FROM samples').fetchone()[0] != count * len(shapes):
                raise ValueError('height samples/frame metadata mismatch')
            reductions = c.execute('''SELECT h.sum_c,h.valid_pixels,h.row_mean_c FROM height_rows h''')
            try:
                for i, (sum_c, valid, mean) in enumerate(reductions):
                    if i % 1024 == 0:
                        self._check_validation_cancel()
                    if not math.isfinite(sum_c) or (valid and not math.isfinite(mean)):
                        raise ValueError('height row reductions must be finite')
            finally:
                reductions.close()
            # Grouped SQL remains bounded in Python and checks that the standard capability
            # really describes the same pixels as the complete canonical row capability.
            aggregates = c.execute('''SELECT s.valid_pixels,s.mean_c,SUM(h.valid_pixels),SUM(h.sum_c)
                FROM samples s JOIN height_rows h ON h.roi_id=s.roi_id AND h.local_frame=s.local_frame
                GROUP BY s.roi_id,s.local_frame''')
            try:
                for i, (sample_count, sample_mean, row_count, row_sum) in enumerate(aggregates):
                    if i % 1024 == 0:
                        self._check_validation_cancel()
                    if (sample_count != row_count or
                            (not row_count and sample_mean is not None) or
                            (row_count and (sample_mean is None or not math.isfinite(sample_mean) or
                                            not math.isclose(sample_mean, row_sum/row_count, rel_tol=1e-10, abs_tol=1e-10)))):
                        raise ValueError('standard samples disagree with height reductions')
            finally:
                aggregates.close()
            first = previous = source_start = None
            for index, row in enumerate(c.execute('SELECT * FROM frames ORDER BY local_frame')):
                if index % 1024 == 0:
                    self._check_validation_cancel()
                local, source, timestamp, relative, status = tuple(row)
                if any(isinstance(v, bool) or not isinstance(v, int) for v in (local, source)):
                    raise ValueError('height frame indices must be integers')
                now = datetime.fromisoformat(timestamp)
                if first is None:
                    first, source_start = now, source
                expected_status = 'ok' if previous is None or now > previous else 'duplicate' if now == previous else 'backwards'
                if (local != index or source != source_start + index or
                        (expected_start is not None and source != expected_start + index) or status != expected_status or
                        not math.isfinite(relative) or relative != (now-first).total_seconds()):
                    raise ValueError('height frame time metadata mismatch')
                previous = now
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError('height completion metadata mismatch') from exc

    @property
    def has_height(self):
        self._read_connection()
        return True

    @property
    def height_key_json(self):
        return self._read_connection().execute("SELECT value FROM metadata WHERE key='height_key_json'").fetchone()[0]

    def iter_height_rows(self, roi_ids=None, chunk_size=1024, local_frame=None):
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size < 1:
            raise ValueError('chunk_size must be positive')
        query = 'SELECT h.*,f.source_frame,f.timestamp,f.relative_s,f.time_status FROM height_rows h JOIN frames f USING(local_frame)'
        clauses, parameters = [], []
        if roi_ids is not None:
            ids = tuple(roi_ids)
            if not ids:
                return
            clauses.append('h.roi_id IN (' + ','.join('?' for _ in ids) + ')')
            parameters.extend(ids)
        if local_frame is not None:
            if isinstance(local_frame, bool) or not isinstance(local_frame, int) or local_frame < 0:
                raise ValueError('local_frame must be a nonnegative integer')
            clauses.append('h.local_frame=?')
            parameters.append(local_frame)
        if clauses:
            query += ' WHERE ' + ' AND '.join(clauses)
        cursor = self._read_connection().execute(query + ' ORDER BY h.local_frame,h.roi_id,h.roi_row_index', parameters)
        try:
            while rows := cursor.fetchmany(chunk_size):
                for row in rows:
                    yield dict(row)
        finally:
            cursor.close()

    def height_profile(self, roi_id, local_frame):
        rows = list(self.iter_height_rows((roi_id,), local_frame=local_frame))
        if not rows:
            raise ValueError('height profile not found')
        sample = self._read_connection().execute('SELECT delta_c FROM samples WHERE roi_id=? AND local_frame=?',
                                                 (roi_id, local_frame)).fetchone()
        return RowProfile([r['roi_row_index'] for r in rows], [r['sum_c'] for r in rows],
                          [r['valid_pixels'] for r in rows],
                          [np.nan if r['row_mean_c'] is None else r['row_mean_c'] for r in rows],
                          rows[0]['total_pixels'], sample[0])

    def height_series(self, roi_id):
        cursor = self._read_connection().execute('SELECT * FROM frames ORDER BY local_frame')
        try:
            for frame in cursor:
                yield dict(frame), self.height_profile(roi_id, frame['local_frame'])
        finally:
            cursor.close()
