import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import flir_seq_tool.selection as selection_module
from flir_seq_tool.selection import (
    DisplaySettings,
    FrameSelection,
    SelectionDocument,
    SelectionStore,
    SourceIdentity,
    build_selection_document,
    validate_source_identity,
)
from flir_seq_tool.transform import CropRect, FrameTransform, RotationSettings
from flir_seq_tool.measurement_models import MeasurementSettings, Roi


START = datetime(2026, 8, 28, 9, 26, 37, 152000)


def sample_source(path: Path) -> SourceIdentity:
    return SourceIdentity(
        filename=path.name,
        size_bytes=123456,
        modified_time_ns=987654321,
        width=640,
        height=480,
        num_frames=65,
        first_timestamp=START,
        last_timestamp=START + timedelta(seconds=1.78),
    )


def sample_document(path: Path) -> SelectionDocument:
    return build_selection_document(
        source=sample_source(path),
        source_start_frame=16,
        source_end_frame=64,
        start_timestamp=START + timedelta(seconds=0.44),
        end_timestamp=START + timedelta(seconds=1.78),
        display=DisplaySettings("inferno", 22.5, 34.5),
    )


def test_v3_roundtrip_preserves_measurement(tmp_path):
    roi = Roi("r1", "ROI 1", 1, 2, 3, 4, "#1f77b4", True)
    settings = MeasurementSettings(rois=(roi,), active_roi_id="r1")
    document = replace(sample_document(tmp_path / "source.seq"), measurement=settings)
    encoded = document.to_dict()
    assert encoded["schema_version"] == 3
    assert encoded["measurement"]["coordinate_space"] == "cropped_pixel"
    assert SelectionDocument.from_dict(encoded) == document


@pytest.mark.parametrize("version", [1, 2])
def test_old_versions_read_as_empty_measurement_without_rewriting(tmp_path, version):
    source = tmp_path / "source.seq"
    store = SelectionStore(tmp_path / "selections")
    encoded = sample_document(source).to_dict()
    encoded["schema_version"] = version
    encoded.pop("measurement", None)
    if version == 1:
        encoded.pop("transform")
    target = store.path_for(source)
    target.parent.mkdir()
    target.write_text(json.dumps(encoded), encoding="utf-8")
    original = target.read_bytes()
    loaded = store.load(source)
    assert loaded.measurement == MeasurementSettings()
    assert loaded.schema_version == 3
    assert target.read_bytes() == original


def test_v3_rejects_roi_outside_cropped_dimensions(tmp_path):
    document = sample_document(tmp_path / "source.seq")
    roi = Roi("r1", "ROI 1", 0, 0, 4, 4, "#1f77b4", True)
    transform = replace(document.transform, crop=CropRect(0, 0, 2, 2))
    with pytest.raises(ValueError, match="bounds"):
        replace(document, transform=transform, measurement=MeasurementSettings((roi,)))


def test_partial_json_write_failure_cleans_its_temporary_file(tmp_path, monkeypatch):
    source = tmp_path / "sample.seq"
    source.write_bytes(b"source")
    store = SelectionStore(tmp_path / "selections")
    document = sample_document(source)
    target = store.save(source, document)
    original = target.read_bytes()
    def fail_dump(payload, stream, **kwargs):
        stream.write("partial")
        raise OSError("disk full")
    monkeypatch.setattr(selection_module.json, "dump", fail_dump)
    with pytest.raises(OSError, match="disk full"):
        store.save(source, document)
    assert target.read_bytes() == original
    assert list(store.root.iterdir()) == [target]


def test_selection_document_round_trip_uses_iso_timestamps(tmp_path: Path):
    document = sample_document(tmp_path / "sample.seq")

    payload = document.to_dict()

    assert set(payload) == {
        "schema_version",
        "source",
        "selection",
        "display",
        "transform",
        "measurement",
    }
    assert payload["schema_version"] == 3
    assert payload["source"]["first_timestamp"] == START.isoformat()
    assert payload["transform"] == {
        "rotation": {
            "angle_deg": 0.0,
            "center": "source_image_center",
            "canvas": "expand",
            "interpolation": "bilinear",
        },
        "crop": {"x": 0, "y": 0, "width": 640, "height": 480},
    }
    assert SelectionDocument.from_dict(payload) == document


def test_selection_store_loads_v1_as_v3_without_rewriting_file(tmp_path: Path):
    source_path = tmp_path / "sample.seq"
    store = SelectionStore(tmp_path / "selections")
    payload = sample_document(source_path).to_dict()
    payload["schema_version"] = 1
    payload.pop("transform")
    payload.pop("measurement")
    target = store.path_for(source_path)
    target.parent.mkdir(parents=True)
    original_bytes = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    target.write_bytes(original_bytes)

    loaded = store.load(source_path)

    assert loaded.schema_version == 3
    assert loaded.transform == FrameTransform.identity(640, 480)
    assert target.read_bytes() == original_bytes


def test_selection_document_round_trips_nonzero_transform(tmp_path: Path):
    transform = FrameTransform.identity(640, 480).with_angle(-2.3)
    document = build_selection_document(
        source=sample_source(tmp_path / "sample.seq"),
        source_start_frame=16,
        source_end_frame=64,
        start_timestamp=START + timedelta(seconds=0.44),
        end_timestamp=START + timedelta(seconds=1.78),
        display=DisplaySettings("inferno", 22.5, 34.5),
        transform=transform,
    )

    payload = document.to_dict()

    assert payload["transform"] == {
        "rotation": {
            "angle_deg": -2.3,
            "center": "source_image_center",
            "canvas": "expand",
            "interpolation": "bilinear",
        },
        "crop": {
            "x": transform.crop.x,
            "y": transform.crop.y,
            "width": transform.crop.width,
            "height": transform.crop.height,
        },
    }
    assert SelectionDocument.from_dict(payload) == document


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: p.update(schema_version=4), "unsupported schema_version"),
        (lambda p: p["selection"].pop("frame_count"), "keys"),
        (lambda p: p["source"].update(num_frames=True), "num_frames"),
        (lambda p: p["selection"].update(source_start_frame=65), "range"),
        (lambda p: p["selection"].update(frame_count=48), "frame_count"),
        (lambda p: p["selection"].update(duration_s=99.0), "duration_s"),
        (lambda p: p["display"].update(vmin_c=float("nan")), "finite"),
        (lambda p: p["display"].update(vmin_c=40.0, vmax_c=20.0), "vmin_c"),
    ],
)
def test_selection_document_rejects_invalid_payload(tmp_path, mutation, message):
    payload = sample_document(tmp_path / "sample.seq").to_dict()
    mutation(payload)

    with pytest.raises(ValueError, match=message):
        SelectionDocument.from_dict(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: p["transform"].update(extra=True), "transform.*keys"),
        (
            lambda p: p["transform"]["rotation"].update(extra=True),
            "rotation.*keys",
        ),
        (
            lambda p: p["transform"]["crop"].update(extra=True),
            "crop.*keys",
        ),
        (
            lambda p: p["transform"]["rotation"].update(angle_deg=44.9),
            "within 15 degrees",
        ),
        (
            lambda p: p["transform"]["rotation"].update(center="custom"),
            "center",
        ),
        (
            lambda p: p["transform"]["rotation"].update(canvas="same"),
            "canvas",
        ),
        (
            lambda p: p["transform"]["rotation"].update(
                interpolation="nearest"
            ),
            "interpolation",
        ),
        (
            lambda p: p["transform"].update(
                rotation={
                    "angle_deg": 10.0,
                    "center": "source_image_center",
                    "canvas": "expand",
                    "interpolation": "bilinear",
                },
                crop={"x": 0, "y": 0, "width": 640, "height": 480},
            ),
            "invalid",
        ),
        (
            lambda p: p["transform"]["rotation"].update(center=7),
            "center.*string",
        ),
        (
            lambda p: p["transform"]["rotation"].update(canvas=False),
            "canvas.*string",
        ),
        (
            lambda p: p["transform"]["rotation"].update(interpolation=[]),
            "interpolation.*string",
        ),
        (lambda p: p["transform"]["crop"].update(x=True), "x.*integer"),
    ],
)
def test_selection_document_rejects_invalid_v2_transform(
    tmp_path, mutation, message
):
    payload = sample_document(tmp_path / "sample.seq").to_dict()
    mutation(payload)

    with pytest.raises(ValueError, match=message):
        SelectionDocument.from_dict(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: p.pop("schema_version"), "schema_version"),
        (lambda p: p.update(schema_version=True), "schema_version.*integer"),
    ],
)
def test_selection_document_rejects_missing_or_noninteger_schema_version(
    tmp_path, mutation, message
):
    payload = sample_document(tmp_path / "sample.seq").to_dict()
    mutation(payload)

    with pytest.raises(ValueError, match=message):
        SelectionDocument.from_dict(payload)


@pytest.mark.parametrize("schema_version", [2.0, True])
def test_selection_document_constructor_requires_integer_schema_version(
    tmp_path, schema_version
):
    document = sample_document(tmp_path / "sample.seq")

    with pytest.raises(ValueError, match="schema_version.*integer"):
        replace(document, schema_version=schema_version)

@pytest.mark.parametrize("schema_version", [1, 2, 3])
def test_selection_document_rejects_unknown_root_key(tmp_path, schema_version):
    payload = sample_document(tmp_path / "sample.seq").to_dict()
    payload["schema_version"] = schema_version
    if schema_version < 3:
        payload.pop("measurement")
    if schema_version == 1:
        payload.pop("transform")
    payload["extra"] = True

    with pytest.raises(ValueError, match="root keys"):
        SelectionDocument.from_dict(payload)


def test_selection_document_rejects_transform_source_size_mismatch(tmp_path):
    document = sample_document(tmp_path / "sample.seq")

    with pytest.raises(ValueError, match="source dimensions"):
        replace(document, transform=FrameTransform.identity(320, 240))


def test_build_document_derives_closed_range_count_and_duration(tmp_path: Path):
    document = build_selection_document(
        source=sample_source(tmp_path / "sample.seq"),
        source_start_frame=7,
        source_end_frame=9,
        start_timestamp=START + timedelta(seconds=0.2),
        end_timestamp=START + timedelta(seconds=0.26),
        display=DisplaySettings("inferno", 20.0, 40.0),
    )

    assert replace(document.selection, duration_s=0.06) == FrameSelection(
        source_start_frame=7,
        source_end_frame=9,
        start_timestamp=START + timedelta(seconds=0.2),
        end_timestamp=START + timedelta(seconds=0.26),
        frame_count=3,
        duration_s=0.06,
    )
    assert document.selection.duration_s == pytest.approx(0.06)


def test_build_document_accepts_explicit_transform_and_store_saves_it(tmp_path):
    source_path = tmp_path / "sample.seq"
    transform = FrameTransform(
        640,
        480,
        RotationSettings(0.0),
        CropRect(10, 20, 600, 440),
    )
    document = build_selection_document(
        source=sample_source(source_path),
        source_start_frame=7,
        source_end_frame=9,
        start_timestamp=START + timedelta(seconds=0.2),
        end_timestamp=START + timedelta(seconds=0.26),
        display=DisplaySettings("inferno", 20.0, 40.0),
        transform=transform,
    )

    store = SelectionStore(tmp_path / "selections")
    target = store.save(source_path, document)

    assert store.load(source_path).transform == transform
    assert json.loads(target.read_text(encoding="utf-8"))["transform"]["crop"] == {
        "x": 10,
        "y": 20,
        "width": 600,
        "height": 440,
    }


def test_build_document_rejects_explicit_transform_source_size_mismatch(tmp_path):
    with pytest.raises(ValueError, match="source dimensions"):
        build_selection_document(
            source=sample_source(tmp_path / "sample.seq"),
            source_start_frame=7,
            source_end_frame=9,
            start_timestamp=START + timedelta(seconds=0.2),
            end_timestamp=START + timedelta(seconds=0.26),
            display=DisplaySettings("inferno", 20.0, 40.0),
            transform=FrameTransform.identity(320, 240),
        )


def test_source_identity_reports_first_mismatch(tmp_path: Path):
    expected = sample_source(tmp_path / "sample.seq")
    actual = replace(expected, size_bytes=expected.size_bytes + 1)

    with pytest.raises(ValueError, match="size_bytes"):
        validate_source_identity(expected, actual)


def test_explicit_selection_path_round_trip(tmp_path):
    from flir_seq_tool.extraction import TargetSnapshot

    source = tmp_path / "sample.seq"
    source.write_bytes(b"source")
    doc = sample_document(source)
    store = SelectionStore(tmp_path / "defaults")
    target = tmp_path / "custom" / "experiment-a.selection.json"
    store.save_to(
        target, doc, source_path=source,
        expected_target=TargetSnapshot.capture(target),
        expected_backup=TargetSnapshot.capture(target.with_name(target.name + ".bak")),
    )
    assert store.load_path(target, doc.source) == doc
    assert not store.path_for(source).exists()


@pytest.mark.parametrize("changed", ["target", "backup"])
@pytest.mark.parametrize("existing", [False, True])
def test_explicit_save_rejects_changes_since_confirmation(tmp_path, changed, existing):
    from flir_seq_tool.extraction import TargetSnapshot

    source = tmp_path / "sample.seq"
    target = tmp_path / "experiment.selection.json"
    backup = target.with_name(target.name + ".bak")
    if existing:
        target.write_bytes(b"old-target")
        backup.write_bytes(b"old-backup")
    expected_target = TargetSnapshot.capture(target)
    expected_backup = TargetSnapshot.capture(backup)
    external = target if changed == "target" else backup
    external.write_bytes(b"external-change")
    with pytest.raises(RuntimeError, match="changed"):
        SelectionStore(tmp_path).save_to(
            target, sample_document(source), source_path=source,
            expected_target=expected_target, expected_backup=expected_backup,
        )
    assert external.read_bytes() == b"external-change"
    assert not list(tmp_path.glob("*.tmp"))


def test_save_rechecks_target_after_temp_validation(tmp_path, monkeypatch):
    source = tmp_path / "sample.seq"
    store = SelectionStore(tmp_path / "selections")
    target = store.path_for(source)
    original_dump = json.dump

    def change_target_during_write(*args, **kwargs):
        result = original_dump(*args, **kwargs)
        target.write_bytes(b"external-file")
        return result

    monkeypatch.setattr(json, "dump", change_target_during_write)
    with pytest.raises(RuntimeError, match="changed"):
        store.save(source, sample_document(source))
    assert target.read_bytes() == b"external-file"
    assert not target.with_name(target.name + ".bak").exists()


@pytest.mark.parametrize("location", ["target", "backup", "raw", "directory"])
def test_selection_write_protection_precedes_temp_creation(tmp_path, location):
    from flir_seq_tool.file_safety import TargetSnapshot

    source = tmp_path / "source.seq"
    source.write_bytes(b"source")
    raw = tmp_path / "raw"
    target = tmp_path / "config.selection.json"
    if location == "target":
        target = source
    elif location == "raw":
        target = raw / "nested" / "config.selection.json"
    elif location == "directory":
        target.mkdir()
    backup = target.with_name(target.name + ".bak")
    if location == "backup":
        os.link(source, backup)
    with pytest.raises(ValueError):
        SelectionStore(tmp_path, raw_root=raw).save_to(
            target, sample_document(source), source_path=source,
            expected_target=TargetSnapshot.capture(target),
            expected_backup=TargetSnapshot.capture(backup),
        )
    assert source.read_bytes() == b"source"
    assert not raw.exists()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("external_location", ["target", "backup"])
def test_failed_selection_replace_does_not_clobber_external_recovery_changes(
    tmp_path, monkeypatch, external_location,
):
    source = tmp_path / "source.seq"
    store = SelectionStore(tmp_path)
    first = sample_document(source)
    target = store.save(source, first)
    backup = target.with_name(target.name + ".bak")
    external = target if external_location == "target" else backup
    original_replace = os.replace

    def fail_publish_with_external_change(src, dest):
        if Path(src).suffix == ".tmp":
            external.write_bytes(b"external-change")
            raise OSError("injected publish failure")
        return original_replace(src, dest)

    monkeypatch.setattr(os, "replace", fail_publish_with_external_change)
    with pytest.raises(RuntimeError, match="restore failed") as caught:
        store.save(source, replace(first, display=DisplaySettings("viridis", 20, 40)))
    assert external.read_bytes() == b"external-change"
    assert str(target) in str(caught.value) and str(backup) in str(caught.value)


def test_save_rolls_back_if_post_move_snapshot_fails(tmp_path, monkeypatch):
    from flir_seq_tool.file_safety import TargetSnapshot

    source = tmp_path / "source.seq"
    store = SelectionStore(tmp_path)
    doc = sample_document(source)
    target = store.save(source, doc)
    original_bytes = target.read_bytes()
    backup = target.with_name(target.name + ".bak")
    capture = TargetSnapshot.capture
    failed = False

    def fail_once(path):
        nonlocal failed
        if Path(path) == backup and backup.exists() and not target.exists() and not failed:
            failed = True
            raise OSError("injected stat failure")
        return capture(path)

    monkeypatch.setattr(TargetSnapshot, "capture", fail_once)
    with pytest.raises(OSError, match="injected stat"):
        store.save(source, doc)
    assert target.read_bytes() == original_bytes


def test_selection_store_replaces_config_and_keeps_one_backup(tmp_path: Path):
    source_path = tmp_path / "sample.seq"
    store = SelectionStore(tmp_path / "selections")
    first = sample_document(source_path)
    second = replace(first, display=DisplaySettings("viridis", 21.0, 41.0))

    target = store.save(source_path, first)
    store.save(source_path, second)

    assert target == tmp_path / "selections" / "sample.selection.json"
    assert store.load(source_path) == second
    backup_payload = json.loads(
        target.with_name(target.name + ".bak").read_text(encoding="utf-8")
    )
    assert SelectionDocument.from_dict(backup_payload) == first
    assert list(target.parent.glob("*.tmp")) == []


def test_load_expected_validates_source_before_constructing_transform(
    tmp_path: Path, monkeypatch
):
    source_path = tmp_path / "sample.seq"
    expected_source = sample_source(source_path)
    store = SelectionStore(tmp_path / "selections")
    target = store.save(source_path, sample_document(source_path))
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["source"]["size_bytes"] += 1
    payload["source"]["width"] = 10**400
    target.write_text(json.dumps(payload), encoding="utf-8")
    transform_called = False

    def fail_if_called(*args, **kwargs):
        nonlocal transform_called
        transform_called = True
        raise AssertionError("transform must not be constructed before source check")

    monkeypatch.setattr(selection_module, "transform_from_dict", fail_if_called)

    with pytest.raises(ValueError, match="source mismatch: size_bytes"):
        store.load_expected(source_path, expected_source)
    assert transform_called is False


def test_standalone_load_rejects_huge_transform_dimensions_as_value_error(tmp_path):
    source_path = tmp_path / "sample.seq"
    store = SelectionStore(tmp_path / "selections")
    target = store.save(source_path, sample_document(source_path))
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["source"]["width"] = 10**400
    payload["transform"]["crop"]["width"] = 10**400
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="source dimensions"):
        store.load(source_path)


def test_selection_store_restores_formal_file_if_final_replace_fails(
    tmp_path: Path, monkeypatch
):
    source_path = tmp_path / "sample.seq"
    store = SelectionStore(tmp_path / "selections")
    first = sample_document(source_path)
    second = replace(first, display=DisplaySettings("viridis", 21.0, 41.0))
    target = store.save(source_path, first)
    real_replace = os.replace

    def fail_temp_to_target(source, destination):
        if Path(destination) == target and Path(source).suffix == ".tmp":
            raise OSError("injected final replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_temp_to_target)

    with pytest.raises(OSError, match="injected"):
        store.save(source_path, second)

    assert store.load(source_path) == first
    assert list(target.parent.glob("*.tmp")) == []


def test_selection_rejects_mixed_timezone_awareness_as_value_error(tmp_path):
    payload = sample_document(tmp_path / "sample.seq").to_dict()
    payload["selection"]["start_timestamp"] = START.replace(
        tzinfo=timezone.utc
    ).isoformat()

    with pytest.raises(ValueError, match="timezone awareness"):
        SelectionDocument.from_dict(payload)


def test_selection_store_reports_target_and_backup_if_restore_also_fails(
    tmp_path: Path, monkeypatch
):
    source_path = tmp_path / "sample.seq"
    store = SelectionStore(tmp_path / "selections")
    first = sample_document(source_path)
    second = replace(first, display=DisplaySettings("viridis", 21.0, 41.0))
    target = store.save(source_path, first)
    backup = target.with_name(target.name + ".bak")
    real_replace = os.replace

    def fail_final_and_restore(source, destination):
        source_path_arg = Path(source)
        destination_path_arg = Path(destination)
        if destination_path_arg == target and (
            source_path_arg.suffix == ".tmp" or source_path_arg == backup
        ):
            raise OSError(f"injected replace failure: {source_path_arg.name}")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_final_and_restore)

    with pytest.raises(RuntimeError) as exc_info:
        store.save(source_path, second)

    message = str(exc_info.value)
    assert str(target) in message
    assert str(backup) in message
    assert "backup exists=True" in message
