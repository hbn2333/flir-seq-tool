"""Owned temporary SQLite results with streaming rows and bounded plot reads."""

from __future__ import annotations

import math
from pathlib import Path
import sqlite3
import tempfile

import numpy as np


SAMPLE_COLUMNS = ("roi_id", "local_frame", "source_frame", "timestamp", "relative_s", "mean_c",
                  "min_c", "max_c", "delta_c", "std_c", "valid_pixels", "total_pixels")
PLOT_METRICS = frozenset(("mean_c", "min_c", "max_c", "delta_c", "std_c"))


class ResultStore:
    def __init__(self, path, *, directory=None, writer=None):
        self.path = Path(path)
        self._directory = directory
        self._writer = writer
        self._reader = None
        self._complete = False

    @classmethod
    def create(cls, parent=None):
        directory = tempfile.TemporaryDirectory(prefix="flir-measure-", dir=parent)
        path = Path(directory.name) / "results.sqlite3"
        writer = None
        try:
            writer = sqlite3.connect(path)
            writer.executescript("""
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE samples (
                    roi_id TEXT NOT NULL, local_frame INTEGER NOT NULL,
                    source_frame INTEGER NOT NULL, timestamp TEXT NOT NULL, relative_s REAL NOT NULL,
                    mean_c REAL, min_c REAL, max_c REAL, delta_c REAL, std_c REAL,
                    valid_pixels INTEGER NOT NULL, total_pixels INTEGER NOT NULL,
                    PRIMARY KEY (roi_id, local_frame)
                );
                CREATE INDEX samples_frame_roi ON samples(local_frame, roi_id);
            """)
            return cls(path, directory=directory, writer=writer)
        except Exception:
            if writer is not None:
                writer.close()
            directory.cleanup()
            raise

    @classmethod
    def open_readonly(cls, path):
        store = cls(path)
        store._read_connection()
        return store

    def _read_connection(self):
        if self._reader is None:
            reader = sqlite3.connect(self.path.absolute().as_uri() + "?mode=ro", uri=True)
            reader.row_factory = sqlite3.Row
            try:
                row = reader.execute("SELECT value FROM metadata WHERE key='complete'").fetchone()
                if row is None or row[0] != "1":
                    raise ValueError("result store is not complete")
                metadata = dict(reader.execute("SELECT key, value FROM metadata"))
                actual_count = reader.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
                if not metadata.get("key_json") or metadata.get("row_count") != str(actual_count):
                    raise ValueError("result store completion metadata mismatch")
            except Exception:
                reader.close()
                raise
            self._reader = reader
        return self._reader

    def _write_connection(self):
        if self._complete:
            raise RuntimeError("result store is already complete")
        if self._writer is None:
            raise RuntimeError("result store writer is closed")
        return self._writer

    @property
    def key_json(self):
        return self._read_connection().execute("SELECT value FROM metadata WHERE key='key_json'").fetchone()[0]

    def append_rows(self, rows):
        writer = self._write_connection()
        def parameters():
            for row in rows:
                if set(row) != set(SAMPLE_COLUMNS):
                    raise ValueError("sample columns mismatch")
                yield tuple(row[name] for name in SAMPLE_COLUMNS)
        placeholders = ",".join("?" for name in SAMPLE_COLUMNS)
        with writer:
            writer.executemany(f"INSERT INTO samples VALUES ({placeholders})", parameters())

    def complete(self, *, expected_rows, key_json):
        writer = self._write_connection()
        if isinstance(expected_rows, bool) or not isinstance(expected_rows, int) or expected_rows < 0:
            raise ValueError("expected rows must be a nonnegative integer")
        if not isinstance(key_json, str) or not key_json:
            raise ValueError("key_json must be nonempty")
        with writer:
            actual = writer.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
            if actual != expected_rows:
                raise ValueError(f"result row count mismatch: {actual} != {expected_rows}")
            writer.executemany("INSERT INTO metadata VALUES (?, ?)",
                               (("key_json", key_json), ("row_count", str(actual)), ("complete", "1")))
        self._complete = True

    def iter_rows(self, roi_ids=None, chunk_size=1024):
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        query, parameters = "SELECT * FROM samples", ()
        if roi_ids is not None:
            parameters = tuple(roi_ids)
            if not parameters:
                return
            query += " WHERE roi_id IN (" + ",".join("?" for value in parameters) + ")"
        cursor = self._read_connection().execute(query + " ORDER BY local_frame, roi_id", parameters)
        try:
            while rows := cursor.fetchmany(chunk_size):
                for row in rows:
                    yield dict(row)
        finally:
            cursor.close()

    def iter_plot_segments(self, roi_id, metric, start, end, bucket_count=2000):
        if metric not in PLOT_METRICS:
            raise ValueError("unknown plot metric")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (start, end, bucket_count)):
            raise ValueError("plot bounds and bucket count must be integers")
        if start < 0 or end < start or bucket_count < 1:
            raise ValueError("invalid plot bounds or bucket count")
        length = end - start + 1
        buckets = min(bucket_count, length)
        cursor = self._read_connection().execute(
            f"SELECT local_frame, {metric} FROM samples WHERE roi_id=? AND local_frame BETWEEN ? AND ? ORDER BY local_frame",
            (roi_id, start, end))

        def bucket_summaries():
            current = None
            first = last = low = high = None
            last_frame = None
            has_missing = False

            def summary():
                lower = start + (current * length + buckets - 1) // buckets
                upper = start + ((current + 1) * length + buckets - 1) // buckets - 1
                missing = has_missing or first is None or first[0] != lower or last_frame != upper
                points = sorted({point[0]: point for point in (first, low, high, last) if point is not None}.values())
                return current, missing, points

            while rows := cursor.fetchmany(1024):
                for frame, value in rows:
                    bucket = (frame - start) * buckets // length
                    if current is not None and bucket != current:
                        yield summary()
                        first = last = low = high = None
                        last_frame = None
                        has_missing = False
                    current = bucket
                    if last_frame is not None and frame != last_frame + 1:
                        has_missing = True
                    last_frame = frame
                    if value is None or not math.isfinite(value):
                        has_missing = True
                        continue
                    point = (frame, value)
                    first = point if first is None else first
                    last = point
                    low = point if low is None or value < low[1] else low
                    high = point if high is None or value > high[1] else high
            if current is not None:
                yield summary()

        def arrays(points):
            return (np.asarray([p[0] for p in points], dtype=np.int64),
                    np.asarray([p[1] for p in points], dtype=np.float64))

        segment, previous = [], None
        try:
            for bucket, missing, points in bucket_summaries():
                if missing or (previous is not None and bucket != previous + 1):
                    if segment:
                        yield arrays(segment)
                        segment = []
                if missing:
                    for point in points:
                        yield arrays([point])
                else:
                    segment.extend(points)
                previous = bucket
            if segment:
                yield arrays(segment)
        finally:
            cursor.close()

    def close(self):
        if self._reader is not None:
            self._reader.close()
            self._reader = None
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    def cleanup(self):
        self.close()
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None
