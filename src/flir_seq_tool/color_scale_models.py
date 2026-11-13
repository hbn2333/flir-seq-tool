from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .selection import SourceIdentity
    from .transform import FrameTransform


@dataclass(frozen=True)
class ColorScaleScope:
    """Identity of the source interval and transform used for finite extrema."""

    source_fingerprint: str
    source_start: int
    source_end: int
    transform_fingerprint: str
    algorithm_version: str = "finite_minmax_v1"

    def __post_init__(self) -> None:
        for name in ("source_fingerprint", "transform_fingerprint"):
            value = getattr(self, name)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"{name} must be a SHA256 hex digest")
        for name in ("source_start", "source_end"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        if self.source_start < 0 or self.source_end < self.source_start:
            raise ValueError("source range must be non-negative and ordered")
        if self.algorithm_version != "finite_minmax_v1":
            raise ValueError("unsupported algorithm_version")

    @classmethod
    def from_inputs(
        cls,
        source: SourceIdentity,
        start: int,
        end: int,
        transform: FrameTransform,
    ) -> ColorScaleScope:
        canonical = json.dumps(source.to_dict(), sort_keys=True, separators=(",", ":"))
        scope = cls(
            source_fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            source_start=start,
            source_end=end,
            transform_fingerprint=transform.fingerprint,
        )
        if end >= source.num_frames:
            raise ValueError("source range exceeds num_frames")
        if (transform.source_width, transform.source_height) != (source.width, source.height):
            raise ValueError("transform source dimensions must match source")
        return scope

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_fingerprint": self.source_fingerprint,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "transform_fingerprint": self.transform_fingerprint,
            "algorithm_version": self.algorithm_version,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ColorScaleScope:
        if not isinstance(payload, dict):
            raise ValueError("color scale scope must be an object")
        expected = {
            "source_fingerprint", "source_start", "source_end",
            "transform_fingerprint", "algorithm_version",
        }
        if set(payload) != expected:
            raise ValueError("color scale scope keys mismatch")
        return cls(**payload)
