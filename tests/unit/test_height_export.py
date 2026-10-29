from dataclasses import replace
from datetime import datetime, timedelta
import csv
import importlib
import json
import os
from types import SimpleNamespace

import numpy as np
import pytest

from flir_seq_tool.file_safety import TargetSnapshot
from flir_seq_tool.height_models import HeightSettings, row_profile
from flir_seq_tool.height_store import HeightResultStore
from flir_seq_tool.measurement_models import MeasurementSettings, Roi
from flir_seq_tool.measurement_state import AnalysisKey
from flir_seq_tool.measurement_stats import summarize
from flir_seq_tool.selection import SourceIdentity
from flir_seq_tool.transform import FrameTransform


def api():
    try:
        return importlib.import_module('flir_seq_tool.height_export')
    except ModuleNotFoundError:
        pytest.fail('height_export is not implemented')


@pytest.fixture
def session(tmp_path):
    source = tmp_path / 'source.seq'
    source.write_bytes(b'source')
    stat = source.stat()
    first = datetime(2026, 10, 30)
    identity = SourceIdentity(source.name, stat.st_size, stat.st_mtime_ns, 2, 3, 3, first, first + timedelta(seconds=2))
    rois = (Roi('b', '=B', 0, 0, 2, 3, '#123456', False), Roi('a', 'A', 1, 0, 1, 3, '#abcdef', True))
    settings = MeasurementSettings(rois).with_height(HeightSettings('b', zones_enabled=True).calibrate(3, 6))
    transform = FrameTransform.identity(2, 3)
    key = AnalysisKey.from_inputs(identity, 1, 2, transform, rois)
    store = HeightResultStore.create(tmp_path)
    from dataclasses import asdict
    for local in range(2):
        timestamp = first + timedelta(seconds=local + 1)
        store.append_frames([dict(local_frame=local, source_frame=local+1, timestamp=timestamp.isoformat(),
                                  relative_s=float(local), time_status='ok')])
        values = np.array([[1., 3.], [np.nan, np.inf], [5., 7.]])
        for roi in rois:
            p = row_profile(values, roi)
            store.append_rows([dict(roi_id=roi.id, local_frame=local, source_frame=local+1,
                                    timestamp=timestamp.isoformat(), relative_s=float(local), **asdict(summarize(values, roi)))])
            store.append_height_rows(dict(roi_id=roi.id, local_frame=local, roi_row_index=r,
                                          sum_c=float(p.sums_c[r]), valid_pixels=int(p.valid_counts[r]),
                                          row_mean_c=float(p.means_c[r]) if p.valid_counts[r] else None,
                                          total_pixels=roi.width) for r in range(roi.height))
    store.complete(expected_rows=4, expected_height_rows=12, expected_frames=2,
                   roi_shapes=tuple((r.id,r.width,r.height) for r in rois), key_json=key.to_json())
    store.close()
    s = SimpleNamespace(source_identity=lambda: identity, measure_source_range=lambda: (1,2),
                        work_timestamps=lambda: (first+timedelta(seconds=1), first+timedelta(seconds=2)),
                        applied_transform=transform, reader=SimpleNamespace(path=source), provenance=(),
                        measurement=SimpleNamespace(settings=settings, result_store=store, result_path=store.path))
    yield s
    store.cleanup()


def run_export(m, snapshot, target, kind='height', **kwargs):
    job = m.HeightExportJob(target, snapshot, kind, TargetSnapshot.capture(target), target.parent / 'raw')
    for key,value in kwargs.items():
        setattr(job,key,value)
    job.start()
    job.join(10)
    assert not job.is_alive()
    return job


def test_full_csv_all_rois_order_empty_rows_physical_positions_and_shared_time(session,tmp_path):
    m = api()
    snapshot = m.build_height_snapshot(session)
    # A live UI edit after the snapshot cannot change the frozen export.
    session.measurement.settings = MeasurementSettings()
    target = tmp_path/'height.csv'
    job = run_export(m,snapshot,target)
    assert job.outcome.status == 'complete', job.outcome
    assert target.read_bytes().startswith(b'\xef\xbb\xbf')
    with target.open(encoding='utf-8-sig',newline='') as f:
        rows = list(csv.DictReader(f))
    assert len(rows)==12
    assert [r['roi_id'] for r in rows[:6]]==['b']*3+['a']*3
    assert [r['roi_row_index'] for r in rows[:3]]==['2','1','0']
    assert rows[0]['roi_name']=="'=B"
    assert rows[0]['height_pixel']=='.5' or float(rows[0]['height_pixel'])==.5
    assert float(rows[0]['height_mm'])==1
    assert rows[1]['row_mean_c']=='' and rows[1]['valid_pixels']=='0' and rows[1]['status']=='no_finite_pixels'
    assert rows[3]['height_mm']=='' and rows[3]['mm_per_pixel']==''
    assert rows[0]['relative_s']=='0.0' and rows[6]['relative_s']=='1.0'
    assert rows[0]['source_frame']=='1' and rows[0]['local_frame']=='0'
    assert rows[0]['origin_scope']=='unknown' and rows[0]['origin_frame']==''
    for field in ('source_identity_json','transform_json','time_origin_timestamp','coordinate_space',
                  'height_direction','height_origin','reference_height_px','reference_height_mm'):
        assert field in rows[0]
    assert job.progress.snapshot().completed==12


def test_zones_wide_csv_includes_unchecked_enabled_roi_and_real_range(session,tmp_path):
    m=api()
    target=tmp_path/'zones.csv'
    job=run_export(m,m.build_height_snapshot(session),target,'zones')
    assert job.outcome.status=='complete',job.outcome
    with target.open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    assert len(rows)==2 and all(r['roi_id']=='b' for r in rows)
    assert float(rows[0]['low_mean_c'])==6 and float(rows[0]['high_mean_c'])==2
    assert float(rows[0]['zone_delta_c'])==4 and float(rows[0]['roi_range_c'])==6
    assert rows[0]['middle_status']=='no_finite_pixels' and rows[0]['middle_mean_c']==''


@pytest.mark.parametrize('failure',['cancel','source','target','validator','disk'])
def test_export_failure_or_cancel_preserves_previous_file_and_cleans_temp(session,tmp_path,monkeypatch,failure):
    m=api()
    snapshot=m.build_height_snapshot(session)
    target=tmp_path/'height.csv'
    target.write_bytes(b'old')
    expected=TargetSnapshot.capture(target)
    job=m.HeightExportJob(target,snapshot,'height',expected,tmp_path/'raw')
    if failure=='cancel':
        job.cancel()
    elif failure=='source':
        stat=session.reader.path.stat()
        os.utime(session.reader.path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
    elif failure=='target':
        target.write_bytes(b'external')
    elif failure=='validator':
        monkeypatch.setattr(m,'validate_height_csv',lambda *a,**k: (_ for _ in ()).throw(ValueError('bad CSV')))
    else:
        monkeypatch.setattr(m.os,'fsync',lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    job.start()
    job.join(10)
    assert job.outcome.status==('cancelled' if failure=='cancel' else 'failed')
    assert target.read_bytes()==(b'external' if failure=='target' else b'old')
    assert not list(tmp_path.glob('height.csv.*.tmp'))


def test_export_refuses_basic_result_store_mismatched_key_and_protected_target(session,tmp_path):
    m=api()
    snapshot=m.build_height_snapshot(session)
    target=tmp_path/'raw'/'bad.csv'
    job=m.HeightExportJob(target,snapshot,'height',TargetSnapshot.capture(target),tmp_path/'raw')
    job.start()
    job.join(10)
    assert job.outcome.status=='failed'
    with pytest.raises(ValueError):
        replace(snapshot,source_range=(0,2))


@pytest.mark.parametrize('change',['source','target','result','cancel','corrupt'])
def test_final_publication_checks_and_readback_preserve_old_target(session,tmp_path,monkeypatch,change):
    m=api()
    target=tmp_path/'height.csv'
    target.write_bytes(b'old')
    snapshot=m.build_height_snapshot(session)
    job=m.HeightExportJob(target,snapshot,'height',TargetSnapshot.capture(target),tmp_path/'raw')
    validate=m.validate_height_csv
    def altered(path,*args,**kwargs):
        if change=='corrupt':
            text=path.read_text(encoding='utf-8-sig')
            path.write_text(text.replace('no_finite_pixels','ok',1),encoding='utf-8-sig')
        validate(path,*args,**kwargs)
        if change=='source':
            session.reader.path.write_bytes(b'changed source')
        elif change=='target':
            target.write_bytes(b'external')
        elif change=='result':
            stat=snapshot.result_store_path.stat()
            os.utime(snapshot.result_store_path,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
        elif change=='cancel':
            job.cancel()
    monkeypatch.setattr(m,'validate_height_csv',altered)
    job.start()
    job.join(10)
    assert job.outcome.status==('cancelled' if change=='cancel' else 'failed')
    assert target.read_bytes()==(b'external' if change=='target' else b'old')
    assert not list(tmp_path.glob('height.csv.*.tmp'))


def test_derived_settings_and_recorded_origin_reuse_rows_without_source_reread(session,tmp_path):
    m=api()
    from flir_seq_tool.provenance import SourceLink
    source=session.source_identity()
    parent=replace(source,filename='parent.seq',num_frames=20,last_timestamp=source.last_timestamp+timedelta(seconds=20))
    session.provenance=(SourceLink(parent,4,source.num_frames),)
    session.measurement.settings=session.measurement.settings.with_height(
        HeightSettings('b',heated_edge='top',zones_enabled=True,delta_pair=('high','low')))
    target=tmp_path/'derived.csv'
    job=run_export(m,m.build_height_snapshot(session),target,'zones')
    assert job.outcome.status=='complete',job.outcome
    with target.open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    assert rows[0]['height_direction']=='down' and float(rows[0]['zone_delta_c'])==4
    assert rows[0]['mm_per_pixel']==''
    assert rows[0]['origin_frame']=='5' and rows[0]['origin_depth']=='1'
    assert rows[0]['origin_scope']=='earliest_recorded_source'
    assert json.loads(rows[0]['origin_source_identity_json'])['filename']=='parent.seq'


def test_statistic_only_and_wrong_key_stores_cannot_export_complete_height(session,tmp_path):
    m=api()
    from flir_seq_tool.measurement_store import ResultStore
    base=ResultStore.create(tmp_path)
    try:
        base.append_rows(session.measurement.result_store.iter_rows())
        base.complete(expected_rows=4,key_json=session.measurement.result_store.key_json)
        base.close()
        snapshot=replace(m.build_height_snapshot(session),result_store_path=base.path,result_file_snapshot=None)
        target=tmp_path/'basic.csv'
        assert run_export(m,snapshot,target).outcome.status=='failed'
        assert not target.exists()
    finally:
        base.cleanup()
    import sqlite3
    from contextlib import closing
    snapshot=m.build_height_snapshot(session)
    with closing(sqlite3.connect(snapshot.result_store_path)) as c,c:
        c.execute("UPDATE metadata SET value='other key' WHERE key='key_json'")
    snapshot=replace(snapshot,result_file_snapshot=None)
    assert run_export(m,snapshot,tmp_path/'wrong.csv').outcome.status=='failed'
