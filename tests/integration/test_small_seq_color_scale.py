"""Read-only real-SDK color scale acceptance, including the complete Q1 recording."""
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use('Agg', force=True)
import matplotlib.pyplot as plt
import numpy as np
import pytest

from flir_seq_tool.color_scale_job import ColorScaleJob, ColorScaleContext, ColorScaleRequest
from flir_seq_tool.export_bundle import sidecar_path
from flir_seq_tool.extraction import ExportJob, ExportStatus, preflight_export
from flir_seq_tool.frame_transformer import FrameTransformer
from flir_seq_tool.reader import ScienceFileReader
from flir_seq_tool.selection import SelectionStore
from flir_seq_tool.transform import FrameTransform, CropRect
from flir_seq_tool.viewer import ViewerSession, build_viewer

SMALL_SEQ = min(Path('raw').glob('*.seq'), key=lambda p: p.stat().st_size,
                default=Path('raw/missing.seq'))
pytestmark = pytest.mark.integration


def finish(job, timeout=60):
    try:
        job.join(timeout)
        assert not job.is_alive(), 'worker exceeded acceptance deadline'
    finally:
        if job.is_alive():
            job.cancel()
            job.join(10)
        assert not job.is_alive(), 'worker survived cancellation'


def wait_ui(ui, session):
    deadline = time.monotonic() + 60
    while session.color_controller.blocks_heavy_tasks:
        assert time.monotonic() < deadline, 'color scale exceeded acceptance deadline'
        ui.poll_color_scale()
        time.sleep(.01)
    assert session.color_controller.status == 'complete', session.color_controller.error


def reference(reader, start, end, transform):
    # Independent finite NumPy reduction; retain only one transformed frame at a time.
    low, high, hottest = float('inf'), float('-inf'), None
    transformer = FrameTransformer(transform)
    for index in range(start, end+1):
        values = transformer.apply(reader.temperature_frame_at(index).temperature_c)
        finite = values[np.isfinite(values)]
        if finite.size:
            low = min(low, float(finite.min()))
            peak = float(finite.max())
            if peak > high:
                high, hottest = peak, index
    assert np.isfinite(low) and np.isfinite(high)
    if low == high:
        low, high = low-.5, high+.5
    return (low, high), hottest


def center_transform(reader):
    return replace(FrameTransform.identity(reader.width, reader.height),
        crop=CropRect(reader.width//4, reader.height//4,
                      max(1, reader.width//2), max(1, reader.height//2)))


def test_small_real_auto_per_frame_selection_and_native_child(tmp_path):
    dialogs = SimpleNamespace(confirm=lambda *a, **k: True,
                             choose_unsaved_action=lambda *a: 'discard')
    with ScienceFileReader(SMALL_SEQ) as reader:
        session = ViewerSession(reader, selection_store=SelectionStore(tmp_path/'selections'))
        start, end = 0, min(4, reader.num_frames-1)
        assert end-start+1 >= 3
        session.timeline.set_pending(start, end)
        session.confirm_range()
        session.apply_transform(center_transform(reader))
        ui = build_viewer(session, dialogs=dialogs)
        try:
            wait_ui(ui, session)
            expected, _ = reference(reader, start, end, session.applied_transform)
            limits = session.color_scale.render_snapshot().limits
            assert (limits.vmin, limits.vmax) == expected
            scope = session.color_scale.settings.auto_scope
            assert scope == session.color_controller.context.scope
            path = session.save_selection()
            assert json.loads(path.read_text(encoding='utf-8'))['schema_version'] == 5
            display = session.color_scale.settings
            session.color_scale.apply_manual(20, 40)
            session.load_selection(path)
            assert session.color_scale.settings == display
            assert session.color_scale.settings.auto_scope == scope
            session.color_scale.select_mode('per_frame')
            ui.poll_color_scale()
            for local in range(end-start+1):
                ui.slider.set_val(local)
                values = session.frame_at_local(local).temperature_c
                finite = values[np.isfinite(values)]
                bounds = (float(finite.min()), float(finite.max()))
                if bounds[0] == bounds[1]:
                    bounds = (bounds[0]-.5, bounds[1]+.5)
                assert ui.image.get_clim() == bounds
                assert (ui.image.colorbar.norm.vmin, ui.image.colorbar.norm.vmax) == bounds
            per_frame_path = session.save_selection()
            session.color_scale.apply_manual(20, 40)
            session.load_selection(per_frame_path)
            assert session.color_scale.settings.scale_mode == 'per_frame'
            assert session.color_scale.settings.auto_scope == scope
            # Return to auto before exporting: child must discard the parent cache scope.
            session.color_controller.request_auto()
            wait_ui(ui, session)
            request = session.export_request('scale-five-frames', tmp_path/'clips')
            native = ExportJob(preflight_export(request, work_source_range=(start, end)).request)
            try:
                native.start()
                finish(native)
                assert native.result.status is ExportStatus.SUCCEEDED, native.result.message
            finally:
                if native.is_alive():
                    native.cancel()
                    finish(native)
            with ScienceFileReader(request.target_path) as child:
                child_session = ViewerSession(child, selection_store=SelectionStore(tmp_path/'child'))
                document = child_session.selection_store.load_path(sidecar_path(request.target_path), child_session.source_identity())
                assert document.display.auto_scope is None
                assert document.display.scale_mode == display.scale_mode
                assert document.display.fixed_source == display.fixed_source
                assert document.transform == session.applied_transform
                assert document.provenance[0].source == session.source_identity()
                assert document.provenance[0].start_frame == start
                assert document.provenance[0].frame_count == end-start+1
                child_session.load_selection(sidecar_path(request.target_path))
                child_ui = build_viewer(child_session, dialogs=dialogs)
                try:
                    wait_ui(child_ui, child_session)
                    assert child_session.color_scale.settings.auto_scope == child_session.color_controller.context.scope
                    assert child_session.color_scale.settings.auto_scope != scope
                    actual = child_session.color_scale.render_snapshot().limits
                    child_expected, _ = reference(child, 0, child.num_frames-1, child_session.applied_transform)
                    assert (actual.vmin, actual.vmax) == child_expected
                finally:
                    child_ui.dispose(wait=True)
                    assert not child_session.color_controller.blocks_heavy_tasks
                    plt.close(child_ui.figure)
        finally:
            ui.dispose(wait=True)
            assert not session.color_controller.blocks_heavy_tasks
            plt.close(ui.figure)


def test_q1_complete_identity_and_new_crop_scope(tmp_path):
    source = Path('raw/Q1.seq')
    if not source.is_file():
        pytest.skip('Required complete Q1.seq is unavailable; minimum sample is not equivalent')
    before = source.stat()
    with ScienceFileReader(source) as reader:
        session = ViewerSession(reader, selection_store=SelectionStore(tmp_path/'selections'))
        identity = session.source_identity()
        transform = FrameTransform.identity(reader.width, reader.height)
        context = ColorScaleContext(source, identity, 0, reader.num_frames-1, transform)
        job = ColorScaleJob(ColorScaleRequest(context, 1, 'real-q1'))
        started = time.monotonic()
        try:
            job.start()
            concurrent = 0
            for index in (0, reader.num_frames//2, reader.num_frames-1):
                alive = job.is_alive()
                values = reader.temperature_frame_at(index).temperature_c
                assert np.isfinite(values).any()
                concurrent += int(alive)
                del values
            assert concurrent >= 1, 'main reader must overlap independent scan reader'
            finish(job)
            elapsed = time.monotonic()-started
            outcome = job.outcome
            assert outcome.status == 'complete', outcome.error
            assert outcome.completed == reader.num_frames
            reference_started = time.monotonic()
            with ScienceFileReader(source) as independent:
                expected, hottest = reference(independent, 0, independent.num_frames-1, transform)
            reference_seconds = time.monotonic()-reference_started
            assert (outcome.limits.vmin, outcome.limits.vmax) == expected
            assert hottest > 4, 'complete recording must include the late hottest frame'
            session.timeline.set_pending(1, 4)
            session.confirm_range()
            session.apply_transform(center_transform(reader))
            ui = build_viewer(session, dialogs=SimpleNamespace())
            try:
                wait_ui(ui, session)
                scoped, _ = reference(reader, 1, 4, session.applied_transform)
                limits = session.color_scale.render_snapshot().limits
                assert (limits.vmin, limits.vmax) == scoped
                assert session.color_scale.settings.auto_scope != context.scope
                (tmp_path/'q1-color-scale.json').write_text(json.dumps(dict(
                    frames=reader.num_frames, range_c=expected, hottest_frame=hottest,
                    worker_seconds=elapsed, reference_seconds=reference_seconds,
                    source_size=before.st_size, source_mtime_ns=before.st_mtime_ns,
                    scoped_range_c=scoped)), encoding='utf-8')
            finally:
                ui.dispose(wait=True)
                assert not session.color_controller.blocks_heavy_tasks
                plt.close(ui.figure)
        finally:
            if job.is_alive():
                job.cancel()
                finish(job)
            assert not job.is_alive()
            session.close_color_scale(wait=True)
    after = source.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
