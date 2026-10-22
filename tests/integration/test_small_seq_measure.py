"""End-to-end measurement checks on only the smallest read-only real SEQ."""

import csv
from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
import pytest

from flir_seq_tool.export_bundle import sidecar_path
from flir_seq_tool.extraction import ExportJob, ExportStatus, preflight_export
from flir_seq_tool.measurement_models import MeasurementSettings, Roi
from flir_seq_tool.measurement_stats import profile, summarize
from flir_seq_tool.reader import ScienceFileReader
from flir_seq_tool.selection import SelectionStore
from flir_seq_tool.viewer import ViewerSession, build_viewer


SMALL_SEQ = Path("raw/Rec-000366-240_09_26_37_152.seq")
pytestmark = pytest.mark.integration


def test_real_measure_stats_job_setup_exports_and_child_seq(tmp_path):
    csv_path, png_path = tmp_path / "measure.csv", tmp_path / "measure.png"
    setup = tmp_path / "setup.selection.json"
    dialogs = SimpleNamespace(
        choose_save=lambda *args, **kwargs: setup,
        choose_load=lambda *args: setup,
        choose_analysis_export=lambda kind, *args: csv_path if kind == "csv" else png_path,
        confirm=lambda *args: True,
    )
    with ScienceFileReader(SMALL_SEQ) as reader:
        session = ViewerSession(reader, cache_size=3, vmin=23, vmax=45, colormap="magma",
                                selection_store=SelectionStore(tmp_path / "selections"))
        session.timeline.set_pending(10, 14)
        session.confirm_range()
        session.apply_transform(session.applied_transform.with_angle(-2.3))
        rois = (Roi("left", "Left sample", 180, 290, 30, 25, "#1f77b4", True),
                Roi("right", "Right sample", 420, 290, 30, 25, "#ff7f0e", True))
        settings = MeasurementSettings(rois, "left", result_view="curves")
        session.measurement.settings = settings
        ui = build_viewer(session, dialogs=dialogs)
        try:
            ui.figure.set_size_inches(10, 8)
            ui.pages.switch("measure")
            # Direct matrix comparisons are independent of the worker/store.
            references = {}
            for local in (0, 2, 4):
                values = session.frame_at_local(local).temperature_c
                for roi in rois:
                    block = values[roi.y:roi.y + roi.height, roi.x:roi.x + roi.width]
                    finite = block[np.isfinite(block)].astype(np.float64)
                    stats = summarize(values, roi)
                    assert stats.mean_c == pytest.approx(float(finite.mean()), rel=1e-6, abs=1e-4)
                    assert stats.delta_c == pytest.approx(float(finite.max() - finite.min()), rel=1e-6, abs=1e-4)
                    for direction, axis in (("horizontal", 0), ("vertical", 1)):
                        direct = np.nanmean(np.where(np.isfinite(block), block, np.nan).astype(np.float64), axis=axis)
                        actual = profile(values, roi, direction)
                        np.testing.assert_allclose(actual.means_c, direct, rtol=1e-6, atol=1e-4, equal_nan=True)
                    references[(local, roi.id)] = stats
            ui.slider.set_val(0)
            ui.measure_panel.on_calculate()
            job = ui.analysis_job
            assert job is not None
            job.join(30)
            if job.is_alive():
                job.cancel()
                job.join(5)
            assert not job.is_alive(), "real measure worker exceeded test deadline"
            ui.poll_analysis()
            assert session.measurement.status == "complete", ui.message_details
            rows = list(session.measurement.result_store.iter_rows())
            assert len(rows) == 10
            for row in rows:
                assert row["source_frame"] == row["local_frame"] + 10
                timestamp = reader.timestamp_at(row["source_frame"])
                assert row["timestamp"] == timestamp.isoformat()
                assert row["relative_s"] == pytest.approx((timestamp - reader.timestamp_at(10)).total_seconds())
                stats = references.get((row["local_frame"], row["roi_id"]))
                if stats is not None:
                    for field in ("mean_c", "min_c", "max_c", "delta_c", "std_c", "valid_pixels", "total_pixels"):
                        assert row[field] == pytest.approx(getattr(stats, field), rel=1e-6, abs=1e-4)
            ui.measure_panel.on_save_setup()
            document = session.selection_store.load_path(setup, session.source_identity())
            assert document.measurement == settings
            assert (document.selection.source_start_frame, document.selection.source_end_frame) == (10, 14)
            ui.measure_panel.on_export("csv")
            ui.measure_panel.on_export("png")
            with csv_path.open(encoding="utf-8-sig", newline="") as stream:
                exported = list(csv.DictReader(stream))
            assert len(exported) == 10
            assert [int(row["source_frame"]) for row in exported] == [frame for frame in range(10, 15) for _ in rois]
            with Image.open(png_path) as picture:
                picture.load()
                assert picture.format == "PNG" and min(picture.size) > 0
            ui.figure.canvas.draw()
            preview = tmp_path / "real-measure-complete-curves.png"
            ui.figure.savefig(preview)
            print(f"MEASURE_PREVIEW={preview}")

            # Process paired export freezes the same ROI setup, not its results.
            ui.pages.switch("process")
            request = session.export_request("measure_five_frames", tmp_path / "clips")
            confirmation = preflight_export(request, work_source_range=(10, 14))
            export_job = ExportJob(confirmation.request)
            export_job.start()
            export_job.join(30)
            if export_job.is_alive():
                export_job.cancel()
                export_job.join(5)
            assert not export_job.is_alive()
            assert export_job.result.status is ExportStatus.SUCCEEDED, export_job.result.message
            with ScienceFileReader(request.target_path) as child:
                child_session = ViewerSession(child, selection_store=SelectionStore(tmp_path / "child-selections"))
                document = child_session.selection_store.load_path(sidecar_path(request.target_path), child_session.source_identity())
                child_session.apply_selection_document(document, path=sidecar_path(request.target_path))
                assert child.num_frames == 5
                assert child_session.measurement.settings == settings
                assert child_session.measurement.status == "empty"
                assert child_session.measurement.result_store is None
                assert child_session.applied_transform == session.applied_transform
                assert child.timestamp_at(0) == reader.timestamp_at(10)
            session.load_selection(setup)
            assert session.measurement.settings == settings
            assert session.measurement.status == "empty"
            assert session.measurement.result_store is None
        finally:
            ui.shutdown_analysis(wait=True)
            plt.close(ui.figure)
