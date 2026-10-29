from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from flir_seq_tool.height_models import HeightSettings
from flir_seq_tool.measurement_models import MeasurementSettings, Roi
from flir_seq_tool.provenance import SourceLink
from flir_seq_tool.selection import SourceIdentity, DisplaySettings, SelectionDocument, build_selection_document


ROI = Roi('r1', 'Sample 1', 0, 0, 3, 6, '#2563eb', True)


def document():
    start = datetime(2026, 8, 28, 9)
    source = SourceIdentity('clip.seq', 100, 200, 20, 12, 5, start, start+timedelta(seconds=4))
    settings = MeasurementSettings((ROI,), 'r1', result_view='height')
    settings = settings.with_height(HeightSettings('r1', zones_enabled=True).calibrate(6, 1.2))
    return build_selection_document(source=source, source_start_frame=0, source_end_frame=4,
        start_timestamp=start, end_timestamp=source.last_timestamp, display=DisplaySettings('inferno', 20, 40),
        measurement=settings)


def test_height_settings_and_provenance_roundtrip_v4():
    original = document()
    parent = replace(original.source, filename='parent.seq', num_frames=20,
                     last_timestamp=original.source.first_timestamp+timedelta(seconds=19))
    original = replace(original, provenance=(SourceLink(parent, 3, 5),))
    payload = original.to_dict()
    assert payload['schema_version'] == 4
    restored = SelectionDocument.from_dict(payload)
    assert restored == original
    assert restored.measurement.height_for('r1').mm_per_pixel == pytest.approx(.2)
    assert restored.provenance[0].start_frame == 3


def test_genuine_v3_preserves_roi_but_defaults_height_and_origin():
    payload = document().to_dict()
    payload['schema_version'] = 3
    payload.pop('provenance')
    for field in ('height_settings', 'height_view', 'height_unit'):
        payload['measurement'].pop(field)
    payload['measurement']['result_view'] = 'curves'
    restored = SelectionDocument.from_dict(payload)
    assert restored.schema_version == 4
    assert restored.measurement.rois == (ROI,)
    assert restored.measurement.result_view == 'curves'
    assert restored.measurement.height_for('r1') == HeightSettings('r1')
    assert restored.provenance == ()


def test_height_settings_references_and_shape_are_strict():
    assert MeasurementSettings((ROI,)).height_for('r1') == HeightSettings('r1')
    with pytest.raises(ValueError, match='ROI'):
        MeasurementSettings((ROI,), height_settings=(HeightSettings('missing'),))
    with pytest.raises(ValueError):
        MeasurementSettings((ROI,), height_settings=(HeightSettings('r1'), HeightSettings('r1')))
    with pytest.raises(ValueError):
        MeasurementSettings.from_dict({'coordinate_space': 'cropped_pixel'})
    invalid = document().to_dict()
    invalid['provenance'] = None
    with pytest.raises(ValueError):
        SelectionDocument.from_dict(invalid)


def test_replacing_height_settings_keeps_stable_record_order():
    second = replace(ROI, id='r2', name='Sample 2')
    settings = MeasurementSettings((ROI, second)).with_height(HeightSettings('r1')).with_height(HeightSettings('r2'))
    updated = settings.with_height(HeightSettings('r1', heated_edge='top'))
    assert tuple(c.roi_id for c in updated.height_settings) == ('r1', 'r2')


def test_backwards_endpoint_timestamps_can_be_saved_without_reordering():
    original = document()
    last = original.source.first_timestamp-timedelta(seconds=1)
    source = replace(original.source, last_timestamp=last)
    saved = build_selection_document(source=source, source_start_frame=0, source_end_frame=4,
        start_timestamp=source.first_timestamp, end_timestamp=last, display=original.display,
        measurement=original.measurement)
    assert saved.selection.duration_s == -1
    assert SelectionDocument.from_dict(saved.to_dict()) == saved


def test_subseq_sidecar_keeps_height_and_records_direct_parent(tmp_path):
    from flir_seq_tool.export_bundle import build_clip_document
    view = document()
    partial = tmp_path/'partial.seq'
    partial.write_bytes(b'fake native result')
    class Reader:
        width, height, num_frames = 20, 12, 5
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def timestamp_at(self, i): return view.source.first_timestamp+timedelta(seconds=i)
    child = build_clip_document(partial, tmp_path/'output.seq', view, reader_factory=lambda _: Reader())
    assert child.measurement == view.measurement
    assert child.provenance == (SourceLink(view.source, 0, 5),)
