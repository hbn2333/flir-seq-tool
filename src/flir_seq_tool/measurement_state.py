"""Input identities and pure measurement state, independent of file and UI access."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import TYPE_CHECKING

from .measurement_models import MeasurementSettings, Roi
from .selection import SourceIdentity
from .transform import FrameTransform

if TYPE_CHECKING:
    from .measurement_store import ResultStore


@dataclass(frozen=True)
class AnalysisKey:
    source_token: str
    source_start: int
    source_end: int
    transform_fingerprint: str
    roi_geometry: tuple[tuple[str, int, int, int, int], ...]
    algorithm_version: str = "roi-stats-v1"

    @classmethod
    def from_inputs(cls, source: SourceIdentity, source_start: int, source_end: int,
                    transform: FrameTransform, rois: tuple[Roi, ...]) -> AnalysisKey:
        return cls(
            json.dumps(source.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False),
            source_start, source_end, transform.fingerprint,
            tuple(sorted((roi.id, roi.x, roi.y, roi.width, roi.height) for roi in rois)),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass
class MeasureState:
    settings: MeasurementSettings = field(default_factory=MeasurementSettings)
    result_key: AnalysisKey | None = None
    result_store: ResultStore | None = None
    status: str = "empty"

    @property
    def result_path(self) -> Path | None:
        return None if self.result_store is None else self.result_store.path

    def invalidate(self) -> None:
        self.status = "stale"

    def clear(self) -> ResultStore | None:
        """Reset state and transfer the previous store's cleanup ownership to caller."""
        previous = self.result_store
        self.settings = MeasurementSettings()
        self.result_key = None
        self.result_store = None
        self.status = "empty"
        return previous

    def is_current(self, key: AnalysisKey) -> bool:
        return self.status == "complete" and self.result_store is not None and self.result_key == key
