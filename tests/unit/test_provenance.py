from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from flir_seq_tool.selection import SourceIdentity


def identity(name, frames):
    start = datetime(2026, 8, 28, 9)
    return SourceIdentity(name, 100, 200, 20, 12, frames, start,
                          start + timedelta(seconds=frames - 1))


def test_recorded_chain_maps_to_earliest_recorded_source():
    from flir_seq_tool.provenance import SourceLink, origin_fields, validate_provenance
    child, parent, ancestor = identity('child.seq', 3), identity('parent.seq', 10), identity('raw.seq', 100)
    links = (SourceLink(parent, 2, 3), SourceLink(ancestor, 40, 10))
    validate_provenance(child, links)
    fields = origin_fields(links, 1)
    assert fields['origin_frame'] == 43
    assert fields['origin_depth'] == 2
    assert fields['origin_scope'] == 'earliest_recorded_source'
    assert fields['origin_status'] == 'recorded_chain'
    assert 'raw.seq' in fields['origin_source_identity_json']
    assert SourceLink.from_dict(links[0].to_dict()) == links[0]


def test_missing_chain_never_invents_original_recording_index():
    from flir_seq_tool.provenance import origin_fields
    assert origin_fields((), 8) == dict(origin_source_identity_json=None, origin_frame=None,
        origin_scope='unknown', origin_depth=0, origin_status='unknown')


@pytest.mark.parametrize('start,count', [(-1, 3), (9, 3), (0, 0), (True, 3), (0, True)])
def test_link_rejects_invalid_source_span(start, count):
    from flir_seq_tool.provenance import SourceLink
    with pytest.raises(ValueError):
        SourceLink(identity('parent.seq', 10), start, count)


def test_chain_must_describe_complete_child_and_each_parent():
    from flir_seq_tool.provenance import SourceLink, validate_provenance
    child, parent, ancestor = identity('child.seq', 3), identity('parent.seq', 10), identity('raw.seq', 100)
    with pytest.raises(ValueError, match='count'):
        validate_provenance(child, (SourceLink(parent, 2, 2),))
    with pytest.raises(ValueError, match='count'):
        validate_provenance(child, (SourceLink(parent, 2, 3), SourceLink(ancestor, 40, 9)))
    with pytest.raises(ValueError, match='dimensions'):
        validate_provenance(child, (SourceLink(replace(parent, width=21), 2, 3),))


def test_invalid_payload_and_noninteger_frame_rejected():
    from flir_seq_tool.provenance import SourceLink, origin_fields
    with pytest.raises(ValueError):
        SourceLink.from_dict({'source': {}, 'start_frame': 0, 'frame_count': 3, 'extra': 1})
    for frame in (True, -1, 1.5):
        with pytest.raises(ValueError):
            origin_fields((), frame)
