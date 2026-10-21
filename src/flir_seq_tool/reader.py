from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any

import fnv.file
import fnv
import numpy as np

from .models import ObjectParameterSnapshot, TemperatureFrame


class ScienceFileReader:
    def __init__(
        self,
        path: Path,
        imager_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.path = Path(path).resolve(strict=True)
        if self.path.suffix.lower() != ".seq":
            raise ValueError(f"expected a .seq file: {self.path}")
        self._imager_factory = imager_factory or fnv.file.ImagerFile
        self._file: Any | None = None

    def __enter__(self) -> "ScienceFileReader":
        self._file = self._imager_factory(str(self.path))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def _require_open(self) -> Any:
        if self._file is None:
            raise RuntimeError("SEQ reader is not open")
        return self._file

    @property
    def width(self) -> int:
        return int(self._require_open().width)

    @property
    def height(self) -> int:
        return int(self._require_open().height)

    @property
    def num_frames(self) -> int:
        return int(self._require_open().num_frames)

    @property
    def object_parameters(self) -> ObjectParameterSnapshot:
        parameters = self._require_open().object_parameters
        return ObjectParameterSnapshot(
            emissivity=float(parameters.emissivity),
            reflected_temp_k=float(parameters.reflected_temp),
            distance_m=float(parameters.distance),
            atmosphere_temp_k=float(parameters.atmosphere_temp),
            relative_humidity=float(parameters.relative_humidity),
            atmospheric_transmission=float(parameters.atmospheric_transmission),
        )

    def timestamp_at(self, frame_index: int) -> datetime:
        if not 0 <= frame_index < self.num_frames:
            raise IndexError(f"frame index out of range: {frame_index}")
        imager = self._require_open()
        if not imager.get_frame(frame_index):
            raise RuntimeError(f"FileSDK could not read frame {frame_index}")
        timestamp = imager.frame_info.time
        if not isinstance(timestamp, datetime):
            raise RuntimeError(f"frame {frame_index} has no valid timestamp")
        return timestamp

    def iter_timestamps(self) -> Iterator[datetime]:
        for frame_index in range(self.num_frames):
            yield self.timestamp_at(frame_index)

    def temperature_frame_at(self, frame_index: int) -> TemperatureFrame:
        if not 0 <= frame_index < self.num_frames:
            raise IndexError(f"frame index out of range: {frame_index}")

        imager = self._require_open()
        imager.unit = fnv.Unit.TEMPERATURE_FACTORY
        imager.temp_type = fnv.TempType.CELSIUS
        if not imager.get_frame(frame_index):
            raise RuntimeError(f"FileSDK could not read frame {frame_index}")

        timestamp = imager.frame_info.time
        if not isinstance(timestamp, datetime):
            raise RuntimeError(f"frame {frame_index} has no valid timestamp")

        values = np.asarray(imager.final, dtype=np.float32)
        expected_pixels = self.width * self.height
        if values.size != expected_pixels:
            raise RuntimeError(
                f"unexpected pixel count for frame {frame_index}: "
                f"{values.size} != {expected_pixels}"
            )
        temperature_c = values.reshape(self.height, self.width).copy()
        return TemperatureFrame(
            frame_index=frame_index,
            timestamp=timestamp,
            temperature_c=temperature_c,
        )
