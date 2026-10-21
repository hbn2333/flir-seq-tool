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
