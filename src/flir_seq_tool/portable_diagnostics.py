"""Opt-in local acceptance checks for a frozen folder (never shipped test data)."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import traceback

from .runtime_paths import application_root, resource_root


def _wait_video(job, *, cancel_encoding=False):
    deadline = time.monotonic() + 90
    try:
        job.start()
        while time.monotonic() < deadline and job.result is None:
            for event in job.poll():
                if event['kind'] == 'ready':
                    job.confirm()
                if cancel_encoding and event.get('stage') == 'encoding':
                    job.cancel()
            time.sleep(.02)
        if job.result is None:
            raise RuntimeError('Frozen video acceptance timed out')
        expected = 'cancelled' if cancel_encoding else 'succeeded'
        if job.result['status'] != expected:
            raise RuntimeError(str(job.result))
        return job.result
    finally:
        job.close()


def _wait_color_scale(controller, poll_ui, *, timeout=30):
    """Agg has no running GUI timer: poll explicitly and retire on failure."""
    deadline = time.monotonic() + timeout
    try:
        while controller.blocks_heavy_tasks or controller.status == 'scanning':
            if time.monotonic() >= deadline:
                raise RuntimeError('Frozen color scale acceptance timed out')
            poll_ui()
            time.sleep(.01)
        if controller.status != 'complete':
            raise RuntimeError(f'Frozen color scale failed: {controller.error}')
    except BaseException:
        controller.close(wait=True)
        raise


def _assert_frame_scale(image, values):
    import numpy as np
    finite = np.asarray(values)[np.isfinite(values)]
    assert finite.size, 'diagnostic frame has no finite temperatures'
    low, high = float(finite.min()), float(finite.max())
    if low == high:
        low, high = low - .5, high + .5
    assert (image.norm.vmin, image.norm.vmax) == (low, high), 'image scale differs from frame'
    assert (image.colorbar.norm.vmin, image.colorbar.norm.vmax) == (low, high), 'colorbar scale differs from frame'
    return low, high


def _loaded_libraries():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetModuleFileNameW.argtypes = (wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD)
    psapi.EnumProcessModules.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD))
    modules = (wintypes.HMODULE * 4096)()
    needed = wintypes.DWORD()
    result = {}
    if not psapi.EnumProcessModules(kernel.GetCurrentProcess(), modules, ctypes.sizeof(modules), ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    for module in modules[:needed.value // ctypes.sizeof(wintypes.HMODULE)]:
        buffer = ctypes.create_unicode_buffer(32768)
        if kernel.GetModuleFileNameW(module, buffer, len(buffer)):
            path = Path(buffer.value)
            if any(name in path.name.lower() for name in ('fnv', 'vcruntime', 'msvcp', 'python313', 'tcl86', 'tk86')):
                result[path.name] = str(path)
    return result


def diagnose(source, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    import tkinter as tk
    import tempfile
    from dataclasses import replace
    import fnv
    from .reader import ScienceFileReader
    from .viewer import ViewerSession, build_viewer
    from .transform import FrameTransform, CropRect
    from .frame_transformer import FrameTransformer
    from .measurement_models import Roi, MeasurementSettings
    from .height_models import HeightSettings
    from .viewer_window import ViewerWindow
    from .selection import SelectionStore
    from .measurement_stats import summarize
    from .measurement_store import ResultStore
    from .extraction import (build_export_request, preflight_export, run_export,
                             ExportProgress, ExportStatus, validate_extracted_seq)
    from .video_export import VideoJob, build_video_request
    from .video_encoder import discover_encoder
    from .file_safety import DEFAULT_RAW_ROOT, validate_write_path
    import threading
    import csv
    from types import SimpleNamespace

    source = Path(source).resolve(strict=True)
    before = source.stat()
    validate_write_path(output / 'process.png', source_path=source, raw_root=DEFAULT_RAW_ROOT)
    output.mkdir(parents=True, exist_ok=True)
    report = dict(app_root=str(application_root()), resource_root=str(resource_root()),
        python=sys.version, sdk_module=str(fnv.__file__), ffmpeg=discover_encoder(),
        source=str(source), checks=[])
    try:
        report['temp_directory'] = tempfile.gettempdir()
        assert Path(tempfile.gettempdir()).is_relative_to(application_root() / 'cache/tmp')
        store = ResultStore.create()
        try:
            report['sqlite_path'] = str(store.path)
            assert store.path.is_relative_to(application_root() / 'cache/tmp')
        finally:
            store.cleanup()
        report['checks'].append('app_owned_temp_sqlite')
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        report['checks'].append('tkinter')
        with ScienceFileReader(source) as reader:
            first = min(10, reader.num_frames - 1)
            last = min(first + 4, reader.num_frames - 1)
            frame = reader.temperature_frame_at(first)
            report['frame'] = dict(index=first, shape=list(frame.temperature_c.shape),
                min_c=float(np.nanmin(frame.temperature_c)), max_c=float(np.nanmax(frame.temperature_c)),
                mean_c=float(np.nanmean(frame.temperature_c)), timestamp=frame.timestamp.isoformat())
            report['checks'].append('sdk_temperature')
            session = ViewerSession(reader, vmin=20, vmax=40)
            session.timeline.set_pending(first, last)
            session.confirm_range()
            session.apply_transform(FrameTransform.identity(reader.width, reader.height).with_angle(-2.3))
            transformed = FrameTransformer(session.applied_transform).apply(frame.temperature_c)
            roi = Roi('test', 'Test ROI', 0, 0, min(20, transformed.shape[1]), min(20, transformed.shape[0]), '#2563eb', True)
            stats = summarize(transformed, roi)
            assert stats.valid_pixels > 0
            session.measurement.settings = MeasurementSettings((roi,), roi.id)
            report['checks'].append('transform_roi')
            export_target = {'path': output / 'height.csv'}
            diagnostics_dialogs = SimpleNamespace(
                choose_analysis_export=lambda *a, **k: export_target['path'],
                confirm=lambda *a, **k: True, choose_unsaved_action=lambda *a: 'discard')
            ui = build_viewer(session, dialogs=diagnostics_dialogs)
            try:
                # Scope the automatic scan to this short interval and a real center crop.
                original_transform = session.applied_transform
                original_measurement = session.measurement.settings
                h, w = transformed.shape
                session.apply_transform(replace(original_transform,
                    crop=CropRect(w//4, h//4, max(1, w//2), max(1, h//2))))
                session.color_controller.request_auto()
                _wait_color_scale(session.color_controller, ui.poll_color_scale)
                lows, highs = [], []
                for index in range(first, last + 1):
                    values = session.frame_transformer.apply(reader.temperature_frame_at(index).temperature_c)
                    finite = values[np.isfinite(values)]
                    if finite.size:
                        lows.append(float(finite.min()))
                        highs.append(float(finite.max()))
                expected = (min(lows), max(highs))
                if expected[0] == expected[1]:
                    expected = (expected[0] - .5, expected[1] + .5)
                actual = session.color_scale.render_snapshot().limits
                assert (actual.vmin, actual.vmax) == expected
                report['color_scale_auto'] = dict(range_c=expected, frames=last-first+1,
                    scope=session.color_scale.settings.auto_scope.to_dict())
                report['checks'].append('color_scale_auto')
                session.selection_store = SelectionStore(output / 'selections')
                saved = session.save_selection()
                payload = json.loads(saved.read_text(encoding='utf-8'))
                assert payload['schema_version'] == 5
                display = session.color_scale.settings
                session.color_scale.apply_manual(20, 40)
                session.load_selection(saved)
                assert session.color_scale.settings == display
                report['checks'].append('selection_v5')
                session.color_scale.select_mode('per_frame')
                ui.poll_color_scale()
                ranges = []
                for local in range(last-first+1):
                    ui.slider.set_val(local)
                    ranges.append(_assert_frame_scale(ui.image,
                        session.frame_at_local(local).temperature_c))
                report['color_scale_per_frame'] = dict(ranges_c=ranges)
                report['checks'].append('color_scale_per_frame')
                adaptive_video_request = build_video_request(session, output / 'per-frame.mp4', 1)
                # Retain the manual diagnostic's ROI, range and video assumptions.
                session.color_scale.apply_manual(20, 40)
                session.apply_transform(original_transform)
                session.measurement.settings = original_measurement
                ui.slider.set_val(0)
                ui.poll_color_scale()
                ui.figure.savefig(output / 'process.png')
                ui.pages.switch('measure')
                ui.figure.savefig(output / 'measure.png')
                report['checks'].append('viewer_pages')
                session.measurement.settings = MeasurementSettings((roi,), roi.id, result_view='height').with_height(
                    HeightSettings(roi.id, zones_enabled=True).calibrate(roi.height, 10))
                ui.measure_panel.refresh_frame(session.frame_at_local(session.timeline.current_local_frame))
                ui.measure_panel.on_calculate()
                job = ui.analysis_job
                job.join(30)
                if job.is_alive():
                    job.cancel()
                    job.join()
                    raise RuntimeError('Frozen height analysis exceeded acceptance deadline')
                ui.poll_analysis()
                assert session.measurement.status == 'complete', ui.message_details
                for kind in ('height', 'zones'):
                    export_target['path'] = output / f'{kind}.csv'
                    ui.measure_panel.on_export_height(kind)
                    job = ui.height_export_state.job
                    if job is not None:
                        job.join(30)
                        if job.is_alive():
                            job.cancel()
                            job.join()
                            raise RuntimeError('Frozen height CSV exceeded acceptance deadline')
                        assert job.outcome.status == 'complete', job.outcome
                        ui.poll_height_export()
                    with export_target['path'].open(encoding='utf-8-sig', newline='') as stream:
                        rows = list(csv.DictReader(stream))
                    assert len(rows) == (last-first+1)*(roi.height if kind == 'height' else 1)
                ui.figure.savefig(output / 'height.png')
                report['checks'].append('height_analysis_full_and_zones_csv')
            finally:
                ui.dispose(wait=True)
                plt.close(ui.figure)
            video_request = build_video_request(session, output / 'five-frames.mp4', 1)
            # A full short sequence gives enough work to exercise encoding cancel.
            session.timeline.reset_full()
            cancel_request = build_video_request(session, output / 'cancelled.mp4', 1)
            request = build_export_request(source_path=source, clips_root=output, clip_name='five-frames',
                source_start_frame=first, source_end_frame=last, start_timestamp=reader.timestamp_at(first),
                end_timestamp=reader.timestamp_at(last), source_num_frames=reader.num_frames)
        confirmation = preflight_export(request, work_source_range=(first, last))
        extracted = run_export(confirmation.request, progress=ExportProgress(), cancel_event=threading.Event(),
                               validator=validate_extracted_seq)
        assert extracted.status is ExportStatus.SUCCEEDED, extracted.message
        with ScienceFileReader(request.target_path) as reader:
            assert reader.num_frames == last - first + 1
        report['checks'].append('native_seq')
        window = ViewerWindow(dialogs=diagnostics_dialogs)
        try:
            window.start(source)
            figure = window.figure
            assert window.open_source(request.target_path)
            assert window.figure is figure and window.session.reader.num_frames == last-first+1
            assert window.open_source(source) and window.figure is figure
            report['checks'].append('same_window_seq_switch')
        finally:
            window.close(wait=True)
            if window.figure is not None:
                plt.close(window.figure)
        report['adaptive_video'] = _wait_video(VideoJob(adaptive_video_request))
        report['checks'].append('video_spawn_per_frame_policy')
        report['video'] = _wait_video(VideoJob(video_request))
        report['checks'].append('video_spawn_encode_full_decode')
        report['cancel'] = _wait_video(VideoJob(cancel_request), cancel_encoding=True)
        assert not cancel_request.target_path.exists()
        report['checks'].append('video_cancel')
        report['loaded_libraries'] = _loaded_libraries()
        report['status'] = 'passed'
        return report
    finally:
        after = source.stat()
        assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), 'Source SEQ changed'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--diagnose', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    from .file_safety import DEFAULT_RAW_ROOT, validate_write_path
    try:
        args.report = validate_write_path(args.report, source_path=args.diagnose, raw_root=DEFAULT_RAW_ROOT)
    except (ValueError, OSError):
        traceback.print_exc()
        return 1
    report = dict(status='failed', time=datetime.now(timezone.utc).isoformat())
    try:
        report.update(diagnose(args.diagnose, args.report.parent / 'acceptance-output'))
    except Exception:
        report['error'] = traceback.format_exc()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if report['status'] == 'passed' else 1
