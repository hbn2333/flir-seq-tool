import sqlite3
from contextlib import closing

import numpy as np
import pytest

from flir_seq_tool.measurement_store import ResultStore


def sample(index=0, roi_id="r1", value=4.):
    return dict(roi_id=roi_id, local_frame=index, source_frame=index + 10,
                timestamp=f"2026-10-22T00:00:{index % 60:02d}", relative_s=index * .02,
                mean_c=value, min_c=value, max_c=value, delta_c=value, std_c=value,
                valid_pixels=0 if value is None else 4, total_pixels=4)


def test_store_preserves_complete_rows_and_missing_values(tmp_path):
    store = ResultStore.create(tmp_path)
    row = sample(value=None)
    store.append_rows([row])
    store.complete(expected_rows=1, key_json="key")
    assert store.key_json == "key"
    assert list(store.iter_rows()) == [row]
    path = store.path
    store.cleanup()
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_chunked_rows_are_complete_ordered_and_filtered(tmp_path):
    store = ResultStore.create(tmp_path)
    try:
        store.append_rows(sample(i, roi_id) for i in reversed(range(10)) for roi_id in ("r2", "r1"))
        store.complete(expected_rows=20, key_json="key")
        rows = list(store.iter_rows(chunk_size=1))
        assert [(r["local_frame"], r["roi_id"]) for r in rows] == [(i, roi_id) for i in range(10) for roi_id in ("r1", "r2")]
        assert len(list(store.iter_rows(("r2",), chunk_size=3))) == 10
        assert list(store.iter_rows(())) == []
    finally:
        store.cleanup()


def test_incomplete_and_duplicate_rows_never_publish(tmp_path):
    store = ResultStore.create(tmp_path)
    try:
        store.append_rows([sample()])
        with pytest.raises(sqlite3.IntegrityError):
            store.append_rows([sample()])
        with pytest.raises(ValueError, match="row"):
            store.complete(expected_rows=2, key_json="key")
        with pytest.raises(ValueError, match="complete"):
            ResultStore.open_readonly(store.path)
        with pytest.raises(ValueError, match="complete"):
            list(store.iter_rows())
        store.complete(expected_rows=1, key_json="key")
        with pytest.raises(RuntimeError, match="complete"):
            store.append_rows([sample(1)])
    finally:
        store.cleanup()


def test_readonly_close_and_cleanup_preserve_owner_directory(tmp_path):
    store = ResultStore.create(tmp_path)
    store.append_rows([sample()])
    store.complete(expected_rows=1, key_json="key")
    store.close()
    readonly = ResultStore.open_readonly(store.path)
    assert list(readonly.iter_rows()) == [sample()]
    readonly.cleanup()
    assert store.path.exists()
    # Reopened owner reads use a connection in the current consumer thread.
    assert list(store.iter_rows()) == [sample()]
    store.cleanup()
    assert not store.path.parent.exists()


def test_plot_segments_are_bounded_ordered_and_never_bridge_missing_values(tmp_path):
    store = ResultStore.create(tmp_path)
    count, buckets = 10003, 16
    missing = {3, 5000, 10000}
    def rows():
        for i in range(count):
            value = None if i in missing else (1000. if i == 1234 else -100. if i == 9876 else float(i % 10))
            yield sample(i, value=value)
    try:
        store.append_rows(rows())
        store.complete(expected_rows=count, key_json="key")
        assert sum(1 for row in store.iter_rows()) == count
        segments = list(store.iter_plot_segments("r1", "mean_c", 0, count - 1, bucket_count=buckets))
        assert sum(len(x) for x, y in segments) <= 8 * buckets
        points = {int(x): float(y) for xs, ys in segments for x, y in zip(xs, ys)}
        assert points[1234] == 1000.
        assert points[9876] == -100.
        previous = -1
        for xs, ys in segments:
            assert (np.diff(xs) > 0).all()
            assert int(xs[0]) > previous
            previous = int(xs[-1])
            assert not any(xs[0] < bad < xs[-1] for bad in missing)
        assert list(store.iter_plot_segments("missing", "mean_c", 0, count - 1)) == []
        one = list(store.iter_plot_segments("r1", "mean_c", 4, 4))
        assert len(one) == 1
        np.testing.assert_array_equal(one[0][0], [4])
    finally:
        store.cleanup()


def test_sparse_plot_rows_and_empty_buckets_are_not_connected(tmp_path):
    store = ResultStore.create(tmp_path)
    try:
        store.append_rows([sample(0), sample(1), sample(8), sample(9)])
        store.complete(expected_rows=4, key_json="key")
        segments = list(store.iter_plot_segments("r1", "mean_c", 0, 9, bucket_count=5))
        assert len(segments) == 2
        for x, y in segments:
            assert not (x[0] < 5 < x[-1])
        with pytest.raises(ValueError, match="metric"):
            list(store.iter_plot_segments("r1", "mean_c;DROP TABLE samples", 0, 9))
    finally:
        store.cleanup()


@pytest.mark.parametrize("field,value", [("row_count", "2"), ("key_json", "")])
def test_readonly_rejects_corrupt_completion_metadata(tmp_path, field, value):
    store = ResultStore.create(tmp_path)
    readonly = None
    try:
        store.append_rows([sample()])
        store.complete(expected_rows=1, key_json="key")
        store.close()
        with closing(sqlite3.connect(store.path)) as connection:
            with connection:
                connection.execute("UPDATE metadata SET value=? WHERE key=?", (value, field))
        with pytest.raises(ValueError, match="metadata"):
            readonly = ResultStore.open_readonly(store.path)
    finally:
        if readonly is not None:
            readonly.close()
        store.cleanup()
