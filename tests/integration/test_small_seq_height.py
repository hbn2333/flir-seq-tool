"""Real height CSV and window flow using three frames of the smallest read-only SEQ."""

import csv
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use('Agg', force=True)
import matplotlib.pyplot as plt
import numpy as np
import pytest

from flir_seq_tool.export_bundle import sidecar_path
from flir_seq_tool.extraction import ExportJob, ExportStatus, preflight_export
from flir_seq_tool.height_models import HeightSettings
from flir_seq_tool.measurement_models import MeasurementSettings, Roi
from flir_seq_tool.reader import ScienceFileReader
from flir_seq_tool.selection import SelectionStore
from flir_seq_tool.viewer import ViewerSession, build_viewer
from flir_seq_tool.viewer_window import ViewerWindow


SMALL_SEQ = min(Path('raw').glob('*.seq'), key=lambda path: path.stat().st_size,
                default=Path('raw/Rec-000366-240_09_26_37_152.seq'))
pytestmark = pytest.mark.integration


def finish(job):
    assert job is not None
    job.join(30)
    if job.is_alive():
        job.cancel()
        job.join(5)
    assert not job.is_alive(), 'real height test exceeded worker deadline'


def test_real_height_csv_calibration_sidecar_and_same_window_switch(tmp_path):
    setup = tmp_path/'height.selection.json'
    target = {'path': tmp_path/'height.csv'}
    dialogs = SimpleNamespace(choose_save=lambda *a, **k: setup,
        choose_analysis_export=lambda *a, **k: target['path'], confirm=lambda *a, **k: True,
        choose_unsaved_action=lambda *a: 'discard')
    with ScienceFileReader(SMALL_SEQ) as reader:
        original_num_frames = reader.num_frames
        session = ViewerSession(reader, selection_store=SelectionStore(tmp_path/'selections'))
        session.timeline.set_pending(10, 12)
        session.confirm_range()
        session.apply_transform(session.applied_transform.with_angle(-2.3))
        rois = (Roi('bottom', 'Bottom sample', 190, 305, 8, 6, '#2563eb', True),
                Roi('top', 'Top sample', 420, 305, 7, 9, '#f97316', False))
        settings = MeasurementSettings(rois, 'bottom', result_view='height')
        settings = settings.with_height(HeightSettings('bottom', zones_enabled=True).calibrate(6, .6))
        settings = settings.with_height(HeightSettings('top', heated_edge='top', zones_enabled=True).calibrate(9, 1.8))
        session.measurement.settings = settings
        ui = build_viewer(session, dialogs=dialogs)
        try:
            ui.pages.switch('measure')
            ui.measure_panel.on_calculate()
            finish(ui.analysis_job)
            ui.poll_analysis()
            assert session.measurement.status == 'complete', ui.message_details
            assert session.measurement.result_store.has_height
            source_key = session.measurement.result_key
            # Derived changes keep the completed row data; no new reader scan is needed.
            session.measurement.settings = settings.with_height(replace(settings.height_for('bottom'), delta_pair=('high', 'low')))
            assert session.measurement.is_current(source_key)
            exported = {}
            for kind in ('height', 'zones'):
                target['path'] = tmp_path/f'{kind}.csv'
                ui.measure_panel.on_export_height(kind)
                job = ui.height_export_state.job
                if job is not None:
                    finish(job)
                    outcome = job.outcome
                    ui.poll_height_export()
                    assert outcome.status == 'complete', outcome.error
                assert target['path'].is_file(), ui.message_details
                with target['path'].open(encoding='utf-8-sig', newline='') as stream:
                    exported[kind] = list(csv.DictReader(stream))
            assert len(exported['height']) == 3*(6+9)
            assert len(exported['zones']) == 3*2
            assert set(row['roi_id'] for row in exported['height']) == {'bottom', 'top'}
            t0 = reader.timestamp_at(10)
            for row in exported['height']:
                roi = next(r for r in rois if r.id == row['roi_id'])
                r = int(row['roi_row_index'])
                local = int(row['local_frame'])
                assert int(row['source_frame']) == local+10
                now = reader.timestamp_at(local+10)
                assert row['timestamp'] == now.isoformat()
                assert float(row['relative_s']) == pytest.approx((now-t0).total_seconds())
                block = session.frame_at_local(local).temperature_c[roi.y+r, roi.x:roi.x+roi.width]
                finite = block[np.isfinite(block)].astype(np.float64)
                assert int(row['valid_pixels']) == finite.size
                assert float(row['row_mean_c']) == pytest.approx(float(finite.mean()), abs=1e-6)
                z = roi.height-r-.5 if roi.id == 'bottom' else r+.5
                assert float(row['height_pixel']) == z
                assert float(row['height_mm']) == pytest.approx(z*(.1 if roi.id == 'bottom' else .2))
                assert row['origin_status'] == 'unknown' and row['origin_frame'] == ''
            for row in exported['zones']:
                assert float(row['zone_delta_c']) == pytest.approx(float(row[f"{row['delta_zone_a']}_mean_c"])-float(row[f"{row['delta_zone_b']}_mean_c"]))
                assert float(row['roi_range_c']) >= 0
            ui.measure_panel.on_save_setup()
            restored = session.selection_store.load_path(setup, session.source_identity())
            assert restored.measurement == session.measurement.settings
            ui.figure.savefig(tmp_path/'height-profile.png')
            ui.pages.switch('process')
            request = session.export_request('height-three-frames', tmp_path/'clips')
            confirmation = preflight_export(request, work_source_range=(10, 12))
            native = ExportJob(confirmation.request)
            native.start()
            finish(native)
            assert native.result.status is ExportStatus.SUCCEEDED, native.result.message
            child_path = request.target_path
            with ScienceFileReader(child_path) as child:
                child_session = ViewerSession(child, selection_store=SelectionStore(tmp_path/'child-selections'))
                doc = child_session.selection_store.load_path(sidecar_path(child_path), child_session.source_identity())
                assert doc.measurement == session.measurement.settings
                assert doc.provenance[0].source == session.source_identity()
                assert doc.provenance[0].start_frame == 10
                assert doc.provenance[0].frame_count == 3
        finally:
            ui.shutdown_analysis(wait=True)
            plt.close(ui.figure)
    window = ViewerWindow(dialogs=dialogs)
    try:
        window.start(child_path)
        figure = window.figure
        assert window.session.measurement.settings == restored.measurement
        assert window.session.provenance == doc.provenance
        assert window.open_source(SMALL_SEQ)
        assert window.figure is figure
        assert window.session.reader.num_frames == original_num_frames
        assert not window.session.measurement.settings.rois
        assert window.open_source(child_path)
        assert window.figure is figure
        assert window.session.measurement.settings == restored.measurement
    finally:
        window.close(wait=True)
        if window.figure is not None:
            plt.close(window.figure)
