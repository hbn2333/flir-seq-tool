"""Recorded contiguous parent spans; never infer an unrecorded original SEQ."""

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .selection import SourceIdentity


@dataclass(frozen=True)
class SourceLink:
    source: 'SourceIdentity'
    start_frame: int
    frame_count: int

    def __post_init__(self):
        from .selection import SourceIdentity
        if not isinstance(self.source, SourceIdentity):
            raise ValueError('provenance source must be a SourceIdentity')
        for value in (self.start_frame, self.frame_count):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError('provenance span must use integer frames')
        if self.start_frame < 0 or self.frame_count < 1 or self.start_frame + self.frame_count > self.source.num_frames:
            raise ValueError('provenance span exceeds parent source')

    def to_dict(self):
        return dict(source=self.source.to_dict(), start_frame=self.start_frame, frame_count=self.frame_count)

    @classmethod
    def from_dict(cls, payload):
        from .selection import SourceIdentity
        if not isinstance(payload, dict) or set(payload) != {'source', 'start_frame', 'frame_count'}:
            raise ValueError('provenance link keys mismatch')
        if not isinstance(payload['source'], dict):
            raise ValueError('provenance source must be an object')
        return cls(SourceIdentity.from_dict(payload['source']), payload['start_frame'], payload['frame_count'])


def validate_provenance(child, links):
    if not isinstance(links, tuple) or not all(isinstance(link, SourceLink) for link in links):
        raise ValueError('provenance must be a tuple of SourceLink records')
    current = child
    for link in links:
        if link.frame_count != current.num_frames:
            raise ValueError('provenance count does not describe the complete child')
        if (current.width, current.height) != (link.source.width, link.source.height):
            raise ValueError('provenance source dimensions mismatch')
        current = link.source


def origin_fields(links, source_frame):
    if isinstance(source_frame, bool) or not isinstance(source_frame, int) or source_frame < 0:
        raise ValueError('source frame must be a nonnegative integer')
    if not isinstance(links, tuple) or not all(isinstance(link, SourceLink) for link in links):
        raise ValueError('provenance must be a tuple of SourceLink records')
    if not links:
        return dict(origin_source_identity_json=None, origin_frame=None,
                    origin_scope='unknown', origin_depth=0, origin_status='unknown')
    frame = source_frame
    for previous, link in zip((None, *links[:-1]), links):
        if previous is not None and previous.source.num_frames != link.frame_count:
            raise ValueError('provenance count does not describe the complete parent')
        if frame >= link.frame_count:
            raise ValueError('frame exceeds recorded provenance span')
        frame += link.start_frame
    return dict(origin_source_identity_json=json.dumps(links[-1].source.to_dict(), sort_keys=True,
                    separators=(',', ':'), allow_nan=False), origin_frame=frame,
                origin_scope='earliest_recorded_source', origin_depth=len(links), origin_status='recorded_chain')
