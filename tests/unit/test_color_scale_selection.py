import json
from dataclasses import replace

import pytest

from flir_seq_tool.color_scale_models import ColorScaleScope
from flir_seq_tool.selection import DisplaySettings, SelectionDocument, SelectionStore
from test_selection import sample_document


def matching_scope(document):
    return ColorScaleScope.from_inputs(
        document.source, document.selection.source_start_frame,
        document.selection.source_end_frame, document.transform,
    )


@pytest.mark.parametrize('mode', ['fixed', 'per_frame'])
@pytest.mark.parametrize('source', ['manual', 'auto'])
def test_v5_roundtrip_remembers_fixed_limits(tmp_path, mode, source):
    document = sample_document(tmp_path / 'source.seq')
    display = replace(document.display, scale_mode=mode, fixed_source=source)
    document = replace(document, display=display)
    encoded = document.to_dict()
    assert encoded['schema_version'] == 5
    restored = SelectionDocument.from_dict(encoded)
    assert restored == document
    assert (restored.display.vmin_c, restored.display.vmax_c) == (22.5, 34.5)
    assert restored.display.auto_scope is None


@pytest.mark.parametrize('mode', ['fixed', 'per_frame'])
def test_auto_scope_roundtrip(tmp_path, mode):
    document = sample_document(tmp_path / 'source.seq')
    scope = matching_scope(document)
    document = replace(document, display=replace(
        document.display, scale_mode=mode, fixed_source='auto', auto_scope=scope))
    encoded = document.to_dict()
    assert encoded['display']['auto_scope'] == scope.to_dict()
    assert SelectionDocument.from_dict(encoded) == document


def test_three_positional_arguments_keep_manual_semantics():
    display = DisplaySettings('inferno', 22.5, 34.5)
    assert (display.scale_mode, display.fixed_source, display.auto_scope) == ('fixed', 'manual', None)


@pytest.mark.parametrize('version', [1, 2, 3, 4])
def test_genuine_legacy_payload_loads_without_rewriting(tmp_path, version):
    document = sample_document(tmp_path / 'source.seq')
    payload = document.to_dict()
    payload['schema_version'] = version
    payload['display'] = dict(colormap='inferno', vmin_c=22.5, vmax_c=34.5)
    if version < 4:
        payload.pop('provenance')
        for key in ('height_settings', 'height_view', 'height_unit'):
            payload['measurement'].pop(key)
    if version < 3:
        payload.pop('measurement')
    if version == 1:
        payload.pop('transform')
    target = tmp_path / 'legacy.selection.json'
    original = (json.dumps(payload, indent=3) + '\n').encode('utf-8')
    target.write_bytes(original)
    loaded = SelectionStore(tmp_path).load_path(target)
    assert loaded.schema_version == 5
    assert loaded.display == DisplaySettings('inferno', 22.5, 34.5)
    assert loaded.display.auto_scope is None
    assert target.read_bytes() == original


@pytest.mark.parametrize('key', ['colormap', 'vmin_c', 'vmax_c', 'scale_mode', 'fixed_source', 'auto_scope'])
def test_current_display_requires_every_key(tmp_path, key):
    payload = sample_document(tmp_path / 'source.seq').to_dict()
    payload['display'].pop(key, None)
    with pytest.raises(ValueError, match='display.*keys'):
        SelectionDocument.from_dict(payload)


@pytest.mark.parametrize('changes', [
    {'extra': True}, {'scale_mode': 'adaptive'}, {'scale_mode': True},
    {'scale_mode': []}, {'fixed_source': 'pending'}, {'fixed_source': False},
    {'fixed_source': {}}, {'auto_scope': {}}, {'auto_scope': []}, {'auto_scope': True},
    {'vmin_c': True}, {'vmax_c': False}, {'vmin_c': '20'},
    {'vmin_c': float('nan')}, {'vmax_c': float('inf')},
    {'vmin_c': 34.5}, {'vmin_c': 40},
])
def test_current_display_rejects_invalid_structure(tmp_path, changes):
    payload = sample_document(tmp_path / 'source.seq').to_dict()
    payload['display'].update(changes)
    with pytest.raises(ValueError):
        SelectionDocument.from_dict(payload)


@pytest.mark.parametrize('changes', [
    {'vmin_c': True}, {'vmax_c': False}, {'vmin_c': '20'},
    {'scale_mode': []}, {'fixed_source': {}}, {'auto_scope': {}},
])
def test_display_constructor_validates_types(changes):
    with pytest.raises(ValueError):
        DisplaySettings(**dict(colormap='inferno', vmin_c=20, vmax_c=40) | changes)


def test_manual_scope_is_rejected_in_constructor_and_payload(tmp_path):
    document = sample_document(tmp_path / 'source.seq')
    scope = matching_scope(document)
    with pytest.raises(ValueError, match='manual'):
        replace(document.display, auto_scope=scope)
    payload = document.to_dict()
    payload['display']['auto_scope'] = scope.to_dict()
    with pytest.raises(ValueError, match='manual'):
        SelectionDocument.from_dict(payload)


def test_legacy_display_rejects_current_keys():
    with pytest.raises(ValueError, match='display.*keys'):
        DisplaySettings.from_dict(dict(colormap='inferno', vmin_c=20, vmax_c=40,
                                       scale_mode='fixed'), legacy=True)
