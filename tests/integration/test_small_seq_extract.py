from pathlib import Path
import threading

import pytest

from flir_seq_tool.extraction import (
    ExportProgress,
    ExportStatus,
    build_export_request,
    preflight_export,
    run_export,
    validate_extracted_seq,
)
from flir_seq_tool.reader import ScienceFileReader


SMALL_SEQ = Path("raw/Rec-000366-240_09_26_37_152.seq")


@pytest.mark.integration
def test_filesdk_extracts_valid_five_frame_seq_without_modifying_source(tmp_path):
    before = SMALL_SEQ.stat()
    with ScienceFileReader(SMALL_SEQ) as source:
        start_time = source.timestamp_at(10)
        end_time = source.timestamp_at(14)
        request = build_export_request(
            source_path=SMALL_SEQ,
            clips_root=tmp_path / "clips",
            clip_name="frames_10_14",
            source_start_frame=10,
            source_end_frame=14,
            start_timestamp=start_time,
            end_timestamp=end_time,
            source_num_frames=source.num_frames,
        )

    confirmation = preflight_export(request, work_source_range=(0, 64))
    assert confirmation.overwrite is False
    result = run_export(
        request,
        progress=ExportProgress(),
        cancel_event=threading.Event(),
        validator=validate_extracted_seq,
    )

    assert result.status is ExportStatus.SUCCEEDED, result.message
    assert request.target_path.is_file()
    with ScienceFileReader(request.target_path) as output:
        assert output.num_frames == 5
        assert (output.width, output.height) == (640, 480)
        assert output.timestamp_at(0) == start_time
        assert output.timestamp_at(4) == end_time
    assert list(request.target_path.parent.glob("*.partial.seq")) == []

    after = SMALL_SEQ.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)


@pytest.mark.integration
def test_real_paired_export_reopens_with_matching_transformed_temperatures(tmp_path):
    import numpy as np
    from flir_seq_tool.extraction import ExportJob
    from flir_seq_tool.export_bundle import sidecar_path, pending_path
    from flir_seq_tool.selection import SelectionStore
    from flir_seq_tool.viewer import ViewerSession

    with ScienceFileReader(SMALL_SEQ) as source:
        session = ViewerSession(source, cache_size=3, vmin=23, vmax=45, colormap="magma")
        session.frame_at(0)
        session.timeline.set_pending(10, 14)
        session.apply_transform(session.applied_transform.with_angle(-2.3))
        expected = [session.frame_at(index).temperature_c.copy() for index in (10, 12, 14)]
        request = session.export_request("real_five_frames", tmp_path / "clips")
        confirmation = preflight_export(request, work_source_range=(0, 64))
        job = ExportJob(confirmation.request)
        job.start()
        job.join(timeout=30)
        if job.is_alive():
            job.cancel()
            job.join(timeout=5)
        assert not job.is_alive(), "real paired export did not finish within the test deadline"
        assert job.result is not None
        assert job.result.status is ExportStatus.SUCCEEDED, job.result.message
        assert not pending_path(request.target_path).exists()
        document = SelectionStore(tmp_path).load_path(sidecar_path(request.target_path))
        assert document.source.filename == request.target_path.name
        assert document.source.filename != source.path.name
        assert document.source.size_bytes == request.target_path.stat().st_size
        assert document.source.modified_time_ns == request.target_path.stat().st_mtime_ns

        with ScienceFileReader(request.target_path) as child:
            restored = ViewerSession(child, cache_size=3)
            frame, notices = restored.restore_startup()
            assert frame == 0 and "Restored" in "\n".join(notices)
            assert child.num_frames == 5
            assert (child.width, child.height) == (640, 480)
            assert (restored.timeline.source_start, restored.timeline.source_end) == (0, 4)
            assert child.timestamp_at(0) == source.timestamp_at(10)
            assert child.timestamp_at(4) == source.timestamp_at(14)
            assert restored.applied_transform == session.applied_transform
            assert restored.color_limits == session.color_limits
            assert restored.colormap == session.colormap
            for index, reference in zip((0, 2, 4), expected, strict=True):
                np.testing.assert_allclose(restored.frame_at(index).temperature_c, reference,
                                           rtol=1e-6, atol=1e-4, equal_nan=True)
            assert len(restored.source_cache) <= 3
            assert len(restored.transformed_cache) <= 3
            assert restored.source_cache.maxsize == 3
            assert restored.transformed_cache.maxsize == 3
        assert len(session.source_cache) <= 3
        assert len(session.transformed_cache) <= 3
