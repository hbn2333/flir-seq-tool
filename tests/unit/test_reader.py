from array import array
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import fnv
import numpy as np
import pytest

from flir_seq_tool.reader import ScienceFileReader


class FakeImagerFile:
    def __init__(
        self,
        *,
        width: int,
        height: int,
        times: list[datetime],
        fail_indices: set[int] | None = None,
        final_values: list[float] | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self.times = times
        self.num_frames = len(times)
        self.fail_indices = fail_indices or set()
        self.frame_info = SimpleNamespace(time=None)
        self.final = array("f", final_values or [0.0] * (width * height))
        self.unit = fnv.Unit.COUNTS
        self.temp_type = fnv.TempType.CELSIUS
        self.object_parameters = SimpleNamespace(
            emissivity=0.95,
            reflected_temp=293.15,
            distance=1.0,
            atmosphere_temp=293.15,
            relative_humidity=0.5,
            atmospheric_transmission=1.0,
        )
        self.requested_frames: list[int] = []
        self.close_called = False

    def get_frame(self, frame_index: int) -> bool:
        self.requested_frames.append(frame_index)
        if frame_index in self.fail_indices:
            return False
        self.frame_info = SimpleNamespace(time=self.times[frame_index])
        return True

    def close(self) -> None:
        self.close_called = True


def create_seq(tmp_path: Path, name: str = "sample.seq") -> Path:
    path = tmp_path / name
    path.write_bytes(b"read-only-fixture")
    return path


def test_reader_reports_shape_count_and_timestamps(tmp_path: Path):
    path = create_seq(tmp_path)
    times = [
        datetime(2026, 8, 28, 9, 0, 0),
        datetime(2026, 8, 28, 9, 0, 1),
    ]
    fake = FakeImagerFile(width=640, height=480, times=times)

    with ScienceFileReader(path, imager_factory=lambda _: fake) as reader:
        assert reader.width == 640
        assert reader.height == 480
        assert reader.num_frames == 2
        assert list(reader.iter_timestamps()) == times

    assert fake.requested_frames == [0, 1]
    assert fake.close_called is True


def test_reader_rejects_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ScienceFileReader(tmp_path / "missing.seq")


def test_reader_rejects_non_seq_file(tmp_path: Path):
    path = create_seq(tmp_path, "sample.txt")

    with pytest.raises(ValueError, match=r"expected a \.seq file"):
        ScienceFileReader(path)


@pytest.mark.parametrize("frame_index", [-1, 2])
def test_reader_rejects_out_of_range_frame(tmp_path: Path, frame_index: int):
    path = create_seq(tmp_path)
    fake = FakeImagerFile(
        width=640,
        height=480,
        times=[datetime(2026, 8, 28, 9, 0, 0), datetime(2026, 8, 28, 9, 0, 1)],
    )

    with ScienceFileReader(path, imager_factory=lambda _: fake) as reader:
        with pytest.raises(IndexError, match="frame index out of range"):
            reader.timestamp_at(frame_index)


def test_reader_reports_filesdk_frame_failure(tmp_path: Path):
    path = create_seq(tmp_path)
    fake = FakeImagerFile(
        width=640,
        height=480,
        times=[datetime(2026, 8, 28, 9, 0, 0)],
        fail_indices={0},
    )

    with ScienceFileReader(path, imager_factory=lambda _: fake) as reader:
        with pytest.raises(RuntimeError, match="FileSDK could not read frame 0"):
            reader.timestamp_at(0)


def test_reader_returns_owned_factory_celsius_temperature_frame(tmp_path: Path):
    path = create_seq(tmp_path)
    timestamp = datetime(2026, 8, 28, 9, 0, 0)
    fake = FakeImagerFile(
        width=3,
        height=2,
        times=[timestamp],
        final_values=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    )

    with ScienceFileReader(path, imager_factory=lambda _: fake) as reader:
        frame = reader.temperature_frame_at(0)

    assert frame.frame_index == 0
    assert frame.timestamp == timestamp
    assert frame.temperature_c.shape == (2, 3)
    assert frame.temperature_c.dtype == np.float32
    assert frame.temperature_c.flags.owndata is True
    assert fake.unit == fnv.Unit.TEMPERATURE_FACTORY
    assert fake.temp_type == fnv.TempType.CELSIUS
    fake.final[0] = 999.0
    assert frame.temperature_c[0, 0] == pytest.approx(1.0)


def test_reader_rejects_unexpected_temperature_pixel_count(tmp_path: Path):
    path = create_seq(tmp_path)
    fake = FakeImagerFile(
        width=3,
        height=2,
        times=[datetime(2026, 8, 28, 9, 0, 0)],
        final_values=[1.0, 2.0],
    )

    with ScienceFileReader(path, imager_factory=lambda _: fake) as reader:
        with pytest.raises(RuntimeError, match="unexpected pixel count"):
            reader.temperature_frame_at(0)


def test_reader_snapshots_factory_object_parameters(tmp_path: Path):
    path = create_seq(tmp_path)
    fake = FakeImagerFile(
        width=1,
        height=1,
        times=[datetime(2026, 8, 28, 9, 0, 0)],
    )

    with ScienceFileReader(path, imager_factory=lambda _: fake) as reader:
        parameters = reader.object_parameters

    assert parameters.emissivity == pytest.approx(0.95)
    assert parameters.reflected_temp_k == pytest.approx(293.15)
    assert parameters.distance_m == pytest.approx(1.0)
    assert parameters.atmosphere_temp_k == pytest.approx(293.15)
    assert parameters.relative_humidity == pytest.approx(0.5)
    assert parameters.atmospheric_transmission == pytest.approx(1.0)
