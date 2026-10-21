from datetime import datetime
from pathlib import Path

import pytest

from flir_seq_tool.inspector import inspect_seq
from flir_seq_tool.reader import ScienceFileReader


SMALL_SEQ = Path("raw/Rec-000366-240_09_26_37_152.seq")


@pytest.mark.integration
def test_small_seq_metadata_and_timestamps_are_stable():
    before = SMALL_SEQ.stat()

    result = inspect_seq(SMALL_SEQ, timestamp_mode="full")

    after = SMALL_SEQ.stat()
    assert (result.width, result.height) == (640, 480)
    assert result.num_frames == 65
    assert result.timestamps.first_time == datetime(2026, 8, 28, 9, 26, 37, 152000)
    assert result.timestamps.last_time == datetime(2026, 8, 28, 9, 26, 38, 932000)
    assert result.timestamps.median_interval_s == pytest.approx(0.021)
    assert result.timestamps.estimated_fps == pytest.approx(47.619047619)
    assert result.timestamps.duplicate_after_frames == ()
    assert result.timestamps.backward_after_frames == ()
    assert result.timestamps.gap_after_frames == ()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)


@pytest.mark.integration
def test_small_seq_first_temperature_frame_matches_factory_baseline():
    before = SMALL_SEQ.stat()

    with ScienceFileReader(SMALL_SEQ) as reader:
        frame = reader.temperature_frame_at(0)
        parameters = reader.object_parameters

    after = SMALL_SEQ.stat()
    assert frame.temperature_c.shape == (480, 640)
    assert frame.temperature_c.dtype.name == "float32"
    assert float(frame.temperature_c.min()) == pytest.approx(22.0858, abs=1e-4)
    assert float(frame.temperature_c.mean()) == pytest.approx(25.2810, abs=1e-4)
    assert float(frame.temperature_c.max()) == pytest.approx(42.1735, abs=1e-4)
    assert parameters.emissivity == pytest.approx(0.95, abs=1e-6)
    assert parameters.reflected_temp_k == pytest.approx(293.15, abs=1e-3)
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
