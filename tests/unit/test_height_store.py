import importlib
import json
import sqlite3
from contextlib import closing
import threading

import numpy as np
import pytest

from flir_seq_tool.measurement_store import ResultStore


def cls():
    try:
        return importlib.import_module('flir_seq_tool.height_store').HeightResultStore
    except ModuleNotFoundError:
        pytest.fail('HeightResultStore is not implemented')


def populate(store):
    store.append_rows([dict(roi_id='r', local_frame=0, source_frame=5,
                           timestamp='2026-10-30T12:00:00', relative_s=0.,
                           mean_c=3., min_c=1., max_c=5., delta_c=4., std_c=2.,
                           valid_pixels=2, total_pixels=6)])
    store.append_frames([dict(local_frame=0, source_frame=5, timestamp='2026-10-30T12:00:00',
                              relative_s=0., time_status='ok')])
    store.append_height_rows([dict(roi_id='r', local_frame=0, roi_row_index=i,
                                   sum_c=6. if i == 0 else 0., valid_pixels=2 if i == 0 else 0,
                                   row_mean_c=3. if i == 0 else None, total_pixels=2)
                              for i in range(3)])


def test_complete_height_store_keeps_standard_compatibility_and_all_rows(tmp_path):
    store = cls().create(tmp_path)
    try:
        populate(store)
        store.complete(expected_rows=1, expected_height_rows=3, expected_frames=1,
                       roi_shapes=(('r', 2, 3),), key_json='key')
        store.close()
        standard = ResultStore.open_readonly(store.path)
        assert list(standard.iter_rows())[0]['delta_c'] == 4.
        standard.close()
        assert store.has_height and store.key_json == 'key'
        assert len(list(store.iter_height_rows(chunk_size=1))) == 3
        assert len(list(store.iter_height_rows(('missing',)))) == 0
        p = store.height_profile('r', 0)
        np.testing.assert_equal(p.means_c, [3., np.nan, np.nan])
        assert p.roi_range_c == 4.
        frame, p = next(store.height_series('r'))
        assert frame['time_status'] == 'ok' and frame['source_frame'] == 5
        assert len(p.means_c) == 3
    finally:
        store.cleanup()


@pytest.mark.parametrize('damage', ['row', 'frame', 'metadata', 'row-key'])
def test_height_readonly_rejects_incomplete_or_inconsistent_rows(tmp_path, damage):
    store = cls().create(tmp_path)
    try:
        populate(store)
        store.complete(expected_rows=1, expected_height_rows=3, expected_frames=1,
                       roi_shapes=(('r', 2, 3),), key_json='key')
        store.close()
        with closing(sqlite3.connect(store.path)) as c, c:
            if damage == 'row':
                c.execute('DELETE FROM height_rows WHERE roi_row_index=2')
            elif damage == 'frame':
                c.execute('UPDATE frames SET source_frame=99')
            elif damage == 'metadata':
                c.execute("UPDATE metadata SET value='0' WHERE key='has_height'")
            else:
                c.execute('UPDATE height_rows SET roi_row_index=99 WHERE roi_row_index=2')
        with pytest.raises(ValueError):
            with closing(cls().open_readonly(store.path)):
                pass
    finally:
        store.cleanup()


def test_completion_rejects_wrong_geometry_and_partial_rows(tmp_path):
    store = cls().create(tmp_path)
    try:
        populate(store)
        with pytest.raises(ValueError):
            store.complete(expected_rows=1, expected_height_rows=3, expected_frames=1,
                           roi_shapes=(('r', 2, 4),), key_json='key')
        with pytest.raises(ValueError):
            with closing(cls().open_readonly(store.path)):
                pass
    finally:
        store.cleanup()


def test_verified_owner_reopens_without_full_table_scan_but_new_readers_validate(tmp_path, monkeypatch):
    store = cls().create(tmp_path)
    try:
        populate(store)
        store.complete(expected_rows=1, expected_height_rows=3, expected_frames=1,
                       roi_shapes=(('r', 2, 3),), key_json='key')
        store.close()
        statements = []
        connect = sqlite3.connect
        def traced(*args, **kwargs):
            c = connect(*args, **kwargs)
            c.set_trace_callback(statements.append)
            return c
        monkeypatch.setattr(sqlite3, 'connect', traced)
        assert store.has_height and store.key_json == 'key'
        assert not any('COUNT(*)' in s.upper() for s in statements)
        statements.clear()
        reader = cls().open_readonly(store.path)
        reader.close()
        assert any('COUNT(*)' in s.upper() for s in statements)
    finally:
        store.cleanup()


@pytest.mark.parametrize('damage', ['infinity', 'counts', 'mean', 'fractional-count'])
def test_height_integrity_rejects_nonfinite_or_inconsistent_reductions(tmp_path, damage):
    store = cls().create(tmp_path)
    try:
        populate(store)
        store.complete(expected_rows=1, expected_height_rows=3, expected_frames=1,
                       roi_shapes=(('r', 2, 3),), key_json='key')
        store.close()
        with closing(sqlite3.connect(store.path)) as c, c:
            if damage == 'infinity':
                c.execute('UPDATE height_rows SET sum_c=?,row_mean_c=? WHERE roi_row_index=0',
                          (float('inf'),float('inf')))
            elif damage == 'counts':
                c.execute('UPDATE samples SET valid_pixels=1')
            elif damage == 'mean':
                c.execute('UPDATE samples SET mean_c=999')
            else:
                c.execute('UPDATE height_rows SET valid_pixels=1.5,sum_c=4.5 WHERE roi_row_index=0')
                c.execute('UPDATE samples SET valid_pixels=1.5')
        with pytest.raises(ValueError):
            cls().open_readonly(store.path)
    finally:
        store.cleanup()


def test_background_readonly_integrity_scan_accepts_cancellation(tmp_path):
    from flir_seq_tool.measurement_job import AnalysisCancelled
    store = cls().create(tmp_path)
    try:
        populate(store)
        store.complete(expected_rows=1, expected_height_rows=3, expected_frames=1,
                       roi_shapes=(('r', 2, 3),), key_json='key')
        store.close()
        event = threading.Event()
        event.set()
        with pytest.raises(AnalysisCancelled):
            cls().open_readonly(store.path, cancel_event=event)
    finally:
        store.cleanup()


def test_frame_numbers_must_match_base_analysis_key_even_if_tables_agree(tmp_path):
    store=cls().create(tmp_path)
    try:
        populate(store)
        key=json.dumps(dict(source_start=5,source_end=5,roi_geometry=[['r',0,0,2,3]]))
        store.complete(expected_rows=1,expected_height_rows=3,expected_frames=1,
                       roi_shapes=(('r',2,3),),key_json=key)
        store.close()
        with closing(sqlite3.connect(store.path)) as c,c:
            c.execute('UPDATE samples SET source_frame=15')
            c.execute('UPDATE frames SET source_frame=15')
        with pytest.raises(ValueError):
            with closing(cls().open_readonly(store.path)):
                pass
    finally:
        store.cleanup()
