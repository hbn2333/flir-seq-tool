import hashlib
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from flir_seq_tool.color_scale_models import ColorScaleScope
from flir_seq_tool.transform import CropRect, FrameTransform
from test_selection import sample_source


def sample_scope(tmp_path):
    source = sample_source(tmp_path / "s.seq")
    transform = FrameTransform.identity(source.width, source.height)
    return ColorScaleScope.from_inputs(source, 0, 64, transform)


def test_scope_roundtrip_is_immutable_and_uses_canonical_source_digest(tmp_path):
    source = sample_source(tmp_path / "s.seq")
    transform = FrameTransform.identity(source.width, source.height)
    scope = ColorScaleScope.from_inputs(source, 0, 64, transform)
    expected = hashlib.sha256(json.dumps(source.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert scope.source_fingerprint == expected
    assert scope.transform_fingerprint == transform.fingerprint
    assert scope.algorithm_version == "finite_minmax_v1"
    assert ColorScaleScope.from_dict(scope.to_dict()) == scope
    with pytest.raises(FrozenInstanceError):
        scope.source_start = 1


def test_scope_changes_with_source_mtime_range_angle_or_crop(tmp_path):
    source = sample_source(tmp_path / "s.seq")
    transform = FrameTransform.identity(source.width, source.height)
    scope = ColorScaleScope.from_inputs(source, 0, 64, transform)
    changed = [
        ColorScaleScope.from_inputs(replace(source, modified_time_ns=source.modified_time_ns + 1), 0, 64, transform),
        ColorScaleScope.from_inputs(source, 1, 64, transform),
        ColorScaleScope.from_inputs(source, 0, 63, transform),
        ColorScaleScope.from_inputs(source, 0, 64, transform.with_angle(1.0)),
        ColorScaleScope.from_inputs(source, 0, 64, replace(transform, crop=CropRect(1, 1, 100, 100))),
    ]
    assert all(item != scope for item in changed)


@pytest.mark.parametrize("start,end", [(True, 2), (0, False), (-1, 2), (3, 2), (0, 65)])
def test_scope_from_inputs_rejects_invalid_source_range(tmp_path, start, end):
    source = sample_source(tmp_path / "s.seq")
    with pytest.raises(ValueError):
        ColorScaleScope.from_inputs(source, start, end, FrameTransform.identity(640, 480))


def test_scope_rejects_transform_dimensions_mismatch(tmp_path):
    with pytest.raises(ValueError, match="source dimensions"):
        ColorScaleScope.from_inputs(sample_source(tmp_path / "s.seq"), 0, 1, FrameTransform.identity(320, 240))


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(extra=True),
    lambda p: p.pop("source_start"),
    lambda p: p.update(source_start=True),
    lambda p: p.update(source_end=False),
    lambda p: p.update(source_start=-1),
    lambda p: p.update(source_start=65),
    lambda p: p.update(source_fingerprint="bad"),
    lambda p: p.update(source_fingerprint=1),
    lambda p: p.update(transform_fingerprint="g" * 64),
    lambda p: p.update(transform_fingerprint=None),
    lambda p: p.update(algorithm_version="unknown"),
])
def test_scope_rejects_invalid_payload(tmp_path, mutation):
    payload = sample_scope(tmp_path).to_dict()
    mutation(payload)
    with pytest.raises(ValueError):
        ColorScaleScope.from_dict(payload)


@pytest.mark.parametrize("payload", [None, [], "scope"])
def test_scope_requires_mapping(payload):
    with pytest.raises(ValueError):
        ColorScaleScope.from_dict(payload)


@pytest.mark.parametrize("changes", [
    {"source_start": True}, {"source_end": -1},
    {"source_fingerprint": "bad"}, {"transform_fingerprint": "bad"},
    {"algorithm_version": "unknown"},
])
def test_scope_constructor_validates_fields(tmp_path, changes):
    with pytest.raises(ValueError):
        replace(sample_scope(tmp_path), **changes)
