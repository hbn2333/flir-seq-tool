from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from flir_seq_tool.selection import SourceIdentity, DisplaySettings, build_selection_document
from flir_seq_tool.transform import FrameTransform


START = datetime(2026, 8, 28, 9)


def view_document(source, count=5, angle=0.0):
    stat = source.stat()
    identity = SourceIdentity(source.name, stat.st_size, stat.st_mtime_ns, 20, 12, 20,
                              START, START + timedelta(seconds=19))
    return build_selection_document(
        source=identity, source_start_frame=3, source_end_frame=3 + count - 1,
        start_timestamp=START + timedelta(seconds=3),
        end_timestamp=START + timedelta(seconds=3 + count - 1),
        display=DisplaySettings("inferno", 20, 40),
        transform=FrameTransform.identity(20, 12).with_angle(angle),
    )


class MetadataReader:
    def __init__(self, document):
        self.width = document.source.width
        self.height = document.source.height
        self.num_frames = document.selection.frame_count
        self.start = document.selection.start_timestamp

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def timestamp_at(self, index):
        return self.start + timedelta(seconds=index)

    def temperature_frame_at(self, index):
        raise AssertionError("metadata builder must not request temperatures")


@pytest.mark.parametrize("count", [1, 5])
@pytest.mark.parametrize("angle", [0.0, -2.3])
def test_clip_document_uses_child_identity_and_preserves_view(tmp_path, count, angle):
    from flir_seq_tool.export_bundle import build_clip_document
    source = tmp_path / "source.seq"
    source.write_bytes(b"source")
    view = view_document(source, count, angle)
    partial = tmp_path / "unique.partial.seq"
    partial.write_bytes(b"child")
    target = tmp_path / "clip.seq"
    child = build_clip_document(partial, target, view, reader_factory=lambda _: MetadataReader(view))
    assert child.source.filename == "clip.seq"
    assert child.source.size_bytes == partial.stat().st_size
    assert child.source.modified_time_ns == partial.stat().st_mtime_ns
    assert (child.source.width, child.source.height) == (20, 12)
    assert child.source.num_frames == count
    assert (child.selection.source_start_frame, child.selection.source_end_frame) == (0, count - 1)
    assert child.selection.start_timestamp == view.selection.start_timestamp
    assert child.selection.end_timestamp == view.selection.end_timestamp
    assert child.transform == view.transform
    assert child.display == view.display


@pytest.mark.parametrize("field,value", [("width", 19), ("num_frames", 4), ("start", START)])
def test_clip_document_rejects_mismatched_output(tmp_path, field, value):
    from flir_seq_tool.export_bundle import build_clip_document
    source = tmp_path / "source.seq"
    source.write_bytes(b"source")
    view = view_document(source)
    reader = MetadataReader(view)
    setattr(reader, field, value)
    with pytest.raises(ValueError, match="mismatch"):
        build_clip_document(source, tmp_path / "clip.seq", view, reader_factory=lambda _: reader)


def paired_request(tmp_path):
    from flir_seq_tool.extraction import build_export_request
    source = tmp_path / "source.seq"
    source.write_bytes(b"source")
    view = view_document(source)
    return build_export_request(
        source_path=source, clips_root=tmp_path / "clips", clip_name="clip",
        source_start_frame=3, source_end_frame=7,
        start_timestamp=view.selection.start_timestamp, end_timestamp=view.selection.end_timestamp,
        source_num_frames=20, selection_document=view,
    )


def confirm(request):
    from flir_seq_tool.extraction import preflight_export
    return preflight_export(request, work_source_range=(0, 19),
                            writable_formats=lambda: ["SEQ-FFF"], free_bytes=lambda _: 10**9)


def test_pair_confirmation_includes_both_snapshots_and_frozen_view(tmp_path):
    from flir_seq_tool.export_bundle import sidecar_path
    request = paired_request(tmp_path)
    first = confirm(request)
    config = sidecar_path(request.target_path)
    config.write_bytes(b"external config")
    second = confirm(request)
    assert first.signature != second.signature
    assert str(config) in second.summary
    assert "OVERWRITE" in second.summary
    changed_view = replace(request.selection_document, display=DisplaySettings("inferno", 21, 41))
    third = confirm(replace(second.request, selection_document=changed_view))
    assert second.signature != third.signature


def test_request_rejects_inconsistent_view_and_pending(tmp_path):
    from flir_seq_tool.export_bundle import pending_path
    request = paired_request(tmp_path)
    with pytest.raises(ValueError):
        replace(request, source_start_frame=2)
    with pytest.raises(ValueError):
        replace(request, sidecar_snapshot=None)
    request.clips_root.mkdir()
    pending_path(request.target_path).write_text("untrusted pending")
    with pytest.raises(RuntimeError, match="pending"):
        confirm(request)


def bundle_inputs(tmp_path, old_seq=True, old_config=True):
    from flir_seq_tool.export_bundle import sidecar_path
    from flir_seq_tool.file_safety import TargetSnapshot
    source = tmp_path / "source.seq"
    source.write_bytes(b"source")
    target = tmp_path / "clip.seq"
    config = sidecar_path(target)
    if old_seq:
        target.write_bytes(b"old-seq")
    if old_config:
        config.write_bytes(b"old-config")
    seq_temp, config_temp = tmp_path / "unique.partial.seq", tmp_path / "unique.partial.json"
    seq_temp.write_bytes(b"new-seq")
    config_temp.write_bytes(b"new-config")
    return dict(source_path=source, raw_root=tmp_path / "raw", seq_temp=seq_temp,
                selection_temp=config_temp, seq_target=target,
                expected_seq=TargetSnapshot.capture(target), expected_selection=TargetSnapshot.capture(config))


@pytest.mark.parametrize("old_seq,old_config", [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize("reject", [False, True])
def test_pair_publication_and_verification_rollback(tmp_path, old_seq, old_config, reject):
    from flir_seq_tool.export_bundle import BundlePublishError, pending_path, publish_bundle, sidecar_path
    kwargs = bundle_inputs(tmp_path, old_seq, old_config)
    target = kwargs["seq_target"]
    config = sidecar_path(target)
    def verify(seq, selection):
        assert pending_path(target).is_file()
        assert seq.read_bytes() == b"new-seq"
        assert selection.read_bytes() == b"new-config"
        if reject:
            raise ValueError("injected verification failure")
    if reject:
        with pytest.raises(BundlePublishError, match="verify"):
            publish_bundle(**kwargs, verify_pair=verify)
        assert target.read_bytes() == b"old-seq" if old_seq else not target.exists()
        assert config.read_bytes() == b"old-config" if old_config else not config.exists()
    else:
        assert publish_bundle(**kwargs, verify_pair=verify).warnings == ()
        assert target.read_bytes() == b"new-seq"
        assert config.read_bytes() == b"new-config"
    assert not pending_path(target).exists()
    assert not list(tmp_path.glob("*.recovery"))
    assert not list(tmp_path.glob("*.partial.*"))


@pytest.mark.parametrize("stage", ["claim", "protect-0", "protect-1", "publish-0", "publish-1", "verify"])
def test_journal_failure_restores_old_pair(tmp_path, monkeypatch, stage):
    import flir_seq_tool.export_bundle as bundle
    kwargs = bundle_inputs(tmp_path)
    original = bundle._Journal.record
    failed = False
    def fail_once(self, state, entries):
        nonlocal failed
        if state == stage and not failed:
            failed = True
            raise OSError("journal failure")
        return original(self, state, entries)
    monkeypatch.setattr(bundle._Journal, "record", fail_once)
    with pytest.raises(bundle.BundlePublishError):
        bundle.publish_bundle(**kwargs, verify_pair=lambda *_: None)
    assert failed
    assert kwargs["seq_target"].read_bytes() == b"old-seq"
    assert bundle.sidecar_path(kwargs["seq_target"]).read_bytes() == b"old-config"
    assert not bundle.pending_path(kwargs["seq_target"]).exists()


@pytest.mark.parametrize("operation", ["protect-seq", "protect-json", "publish-seq", "publish-json"])
def test_rename_failure_restores_old_pair(tmp_path, monkeypatch, operation):
    import flir_seq_tool.export_bundle as bundle
    kwargs = bundle_inputs(tmp_path)
    target = kwargs["seq_target"]
    config = bundle.sidecar_path(target)
    original = bundle.os.replace
    failed = False
    def fail_once(src, dst):
        nonlocal failed
        src, dst = Path(src), Path(dst)
        hit = {"protect-seq": src == target and dst.suffix == ".recovery",
               "protect-json": src == config and dst.suffix == ".recovery",
               "publish-seq": src == kwargs["seq_temp"] and dst == target,
               "publish-json": src == kwargs["selection_temp"] and dst == config}[operation]
        if hit and not failed:
            failed = True
            raise OSError(operation)
        return original(src, dst)
    monkeypatch.setattr(bundle.os, "replace", fail_once)
    with pytest.raises(bundle.BundlePublishError):
        bundle.publish_bundle(**kwargs, verify_pair=lambda *_: None)
    assert failed
    assert target.read_bytes() == b"old-seq"
    assert config.read_bytes() == b"old-config"
    assert not bundle.pending_path(target).exists()


@pytest.mark.parametrize("changed", ["published", "recovery"])
def test_external_change_preserves_materials_and_pending(tmp_path, changed):
    import flir_seq_tool.export_bundle as bundle
    kwargs = bundle_inputs(tmp_path)
    target = kwargs["seq_target"]
    changed_path = None
    def reject(seq, config):
        nonlocal changed_path
        changed_path = seq if changed == "published" else next(tmp_path.glob("clip.seq.*.recovery"))
        changed_path.write_bytes(b"external bytes")
        raise ValueError("injected verify failure")
    with pytest.raises(bundle.BundlePublishError) as caught:
        bundle.publish_bundle(**kwargs, verify_pair=reject)
    assert changed_path.read_bytes() == b"external bytes"
    assert bundle.pending_path(target).exists()
    assert bundle.pending_path(target) in caught.value.retained_paths
    assert str(changed_path) in str(caught.value)


def test_existing_pending_is_never_interpreted_or_overwritten(tmp_path):
    import flir_seq_tool.export_bundle as bundle
    kwargs = bundle_inputs(tmp_path)
    marker = bundle.pending_path(kwargs["seq_target"])
    marker.write_bytes(b"untrusted journal")
    with pytest.raises(bundle.BundlePublishError):
        bundle.publish_bundle(**kwargs, verify_pair=lambda *_: None)
    assert marker.read_bytes() == b"untrusted journal"
    assert kwargs["seq_target"].read_bytes() == b"old-seq"


@pytest.mark.parametrize("which", ["recovery", "marker"])
def test_cleanup_failure_never_reverts_verified_pair(tmp_path, monkeypatch, which):
    import flir_seq_tool.export_bundle as bundle
    kwargs = bundle_inputs(tmp_path)
    target = kwargs["seq_target"]
    original = Path.unlink
    def fail_cleanup(self, *args, **kw):
        if (which == "recovery" and self.suffix == ".recovery") or (which == "marker" and self == bundle.pending_path(target)):
            raise OSError("cleanup failure")
        return original(self, *args, **kw)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    if which == "marker":
        with pytest.raises(bundle.BundlePublishError, match="finish"):
            bundle.publish_bundle(**kwargs, verify_pair=lambda *_: None)
        assert bundle.pending_path(target).exists()
    else:
        assert bundle.publish_bundle(**kwargs, verify_pair=lambda *_: None).warnings
        assert not bundle.pending_path(target).exists()
    assert target.read_bytes() == b"new-seq"
    assert bundle.sidecar_path(target).read_bytes() == b"new-config"


@pytest.mark.parametrize("bad", ["source", "outside", "alias"])
def test_pair_rejects_unsafe_temporary_paths_before_marker(tmp_path, bad):
    import flir_seq_tool.export_bundle as bundle
    kwargs = bundle_inputs(tmp_path)
    if bad == "source":
        kwargs["seq_temp"] = kwargs["source_path"]
    elif bad == "outside":
        outside = tmp_path / "other"
        outside.mkdir()
        kwargs["seq_temp"] = outside / "temp.seq"
        kwargs["seq_temp"].write_bytes(b"outside")
    else:
        kwargs["seq_temp"] = kwargs["selection_temp"]
    with pytest.raises(bundle.BundlePublishError):
        bundle.publish_bundle(**kwargs, verify_pair=lambda *_: None)
    assert kwargs["source_path"].read_bytes() == b"source"
    assert kwargs["seq_target"].read_bytes() == b"old-seq"
    assert not bundle.pending_path(kwargs["seq_target"]).exists()


def test_external_target_appearing_after_protection_is_not_overwritten(tmp_path, monkeypatch):
    import flir_seq_tool.export_bundle as bundle
    kwargs = bundle_inputs(tmp_path)
    original = bundle._Journal.record
    def insert_external(self, state, entries):
        original(self, state, entries)
        if state == "publish-0":
            kwargs["seq_target"].write_bytes(b"external target")
    monkeypatch.setattr(bundle._Journal, "record", insert_external)
    with pytest.raises(bundle.BundlePublishError) as caught:
        bundle.publish_bundle(**kwargs, verify_pair=lambda *_: None)
    assert kwargs["seq_target"].read_bytes() == b"external target"
    assert bundle.sidecar_path(kwargs["seq_target"]).read_bytes() == b"old-config"
    assert bundle.pending_path(kwargs["seq_target"]).exists()
    assert any(p.read_bytes() == b"old-seq" for p in caught.value.retained_paths if p.suffix == ".recovery")


def test_rollback_rename_failure_retains_old_and_new_materials(tmp_path, monkeypatch):
    import flir_seq_tool.export_bundle as bundle
    kwargs = bundle_inputs(tmp_path)
    original = bundle.os.replace
    def fail_restore(src, dst):
        if Path(src).suffix == ".recovery":
            raise OSError("restore denied")
        return original(src, dst)
    monkeypatch.setattr(bundle.os, "replace", fail_restore)
    def reject(*_):
        raise ValueError("verify rejected")
    with pytest.raises(bundle.BundlePublishError) as caught:
        bundle.publish_bundle(**kwargs, verify_pair=reject)
    assert "restore denied" in str(caught.value)
    assert bundle.pending_path(kwargs["seq_target"]).exists()
    assert not kwargs["seq_target"].exists()
    assert {p.read_bytes() for p in caught.value.retained_paths if p.suffix == ".recovery"} == {b"old-seq", b"old-config"}
    assert kwargs["seq_temp"].read_bytes() == b"new-seq"
    assert kwargs["selection_temp"].read_bytes() == b"new-config"


def test_stat_failure_after_protection_still_restores_old_pair(tmp_path, monkeypatch):
    import flir_seq_tool.export_bundle as bundle
    kwargs = bundle_inputs(tmp_path)
    original = bundle.TargetSnapshot.capture
    failed = False
    def fail_once(path):
        nonlocal failed
        if Path(path).suffix == ".recovery" and Path(path).exists() and not failed:
            failed = True
            raise OSError("stat unavailable")
        return original(path)
    monkeypatch.setattr(bundle.TargetSnapshot, "capture", fail_once)
    with pytest.raises(bundle.BundlePublishError):
        bundle.publish_bundle(**kwargs, verify_pair=lambda *_: None)
    assert failed
    assert kwargs["seq_target"].read_bytes() == b"old-seq"
    assert bundle.sidecar_path(kwargs["seq_target"]).read_bytes() == b"old-config"
    assert not bundle.pending_path(kwargs["seq_target"]).exists()


def test_marker_creation_failure_changes_no_formal_outputs(tmp_path, monkeypatch):
    import flir_seq_tool.export_bundle as bundle
    kwargs = bundle_inputs(tmp_path)
    original = Path.open
    marker = bundle.pending_path(kwargs["seq_target"])
    def fail_create(self, mode="r", *args, **kw):
        if self == marker and mode == "x":
            raise PermissionError("marker denied")
        return original(self, mode, *args, **kw)
    monkeypatch.setattr(Path, "open", fail_create)
    with pytest.raises(bundle.BundlePublishError, match="claim"):
        bundle.publish_bundle(**kwargs, verify_pair=lambda *_: None)
    assert not marker.exists()
    assert kwargs["seq_target"].read_bytes() == b"old-seq"
    assert bundle.sidecar_path(kwargs["seq_target"]).read_bytes() == b"old-config"


def test_changed_target_snapshot_rejected_before_claim(tmp_path):
    import flir_seq_tool.export_bundle as bundle
    kwargs = bundle_inputs(tmp_path)
    config = bundle.sidecar_path(kwargs["seq_target"])
    config.write_bytes(b"changed config")
    with pytest.raises(bundle.BundlePublishError, match="validate"):
        bundle.publish_bundle(**kwargs, verify_pair=lambda *_: None)
    assert config.read_bytes() == b"changed config"
    assert kwargs["seq_target"].read_bytes() == b"old-seq"
    assert not bundle.pending_path(kwargs["seq_target"]).exists()


def worker_kwargs(request):
    from types import SimpleNamespace
    import threading
    from flir_seq_tool.extraction import ExportProgress
    class Imager:
        def extract(self, destination, options):
            Path(f"{destination}.seq").write_bytes(b"native child")
            return not options.progress_callback(request.frame_count, request.frame_count)
        def close(self):
            pass
    def factory(path):
        reader = MetadataReader(request.selection_document)
        if Path(path) == request.source_path:
            reader.num_frames = request.source_num_frames
            reader.start = START
        return reader
    return dict(progress=ExportProgress(), cancel_event=threading.Event(),
                imager_factory=lambda _: Imager(), options_factory=lambda: SimpleNamespace(abort=lambda: None),
                validator=lambda *_: None, reader_factory=factory)


@pytest.mark.parametrize("count", [1, 5])
def test_worker_publishes_verified_pair(tmp_path, count):
    from flir_seq_tool import extraction
    from flir_seq_tool.export_bundle import sidecar_path, pending_path
    from flir_seq_tool.selection import SelectionStore
    request = paired_request(tmp_path)
    view = view_document(request.source_path, count=count, angle=-2.3)
    request = replace(request, selection_document=view, source_end_frame=3 + count - 1,
                      end_timestamp=view.selection.end_timestamp)
    result = extraction.run_export(request, **worker_kwargs(request))
    assert result.status is extraction.ExportStatus.SUCCEEDED, result.message
    assert str(sidecar_path(request.target_path)) in result.message
    saved = SelectionStore(tmp_path).load_path(sidecar_path(request.target_path))
    assert saved.source.filename == request.target_path.name
    assert saved.source.modified_time_ns == request.target_path.stat().st_mtime_ns
    assert saved.selection.frame_count == count
    assert saved.selection.source_start_frame == 0
    assert saved.transform == view.transform
    assert not pending_path(request.target_path).exists()
    assert len(list(request.clips_root.iterdir())) == 2


@pytest.mark.parametrize("failure", ["json", "external", "cancel"])
def test_worker_preparation_failure_keeps_formal_pair(tmp_path, monkeypatch, failure):
    from flir_seq_tool import extraction
    request = paired_request(tmp_path)
    request.clips_root.mkdir()
    request.target_path.write_bytes(b"old seq")
    config = request.target_path.with_suffix(".selection.json")
    config.write_bytes(b"old config")
    request = confirm(request).request
    kwargs = worker_kwargs(request)
    original = extraction.build_clip_document
    def prepare(*args, **kw):
        if failure == "json":
            raise OSError("json preparation denied")
        result = original(*args, **kw)
        if failure == "external":
            config.write_bytes(b"external config")
        else:
            kwargs["cancel_event"].set()
        return result
    monkeypatch.setattr(extraction, "build_clip_document", prepare)
    result = extraction.run_export(request, **kwargs)
    expected = extraction.ExportStatus.CANCELLED if failure == "cancel" else extraction.ExportStatus.FAILED
    assert result.status is expected, result.message
    assert request.target_path.read_bytes() == b"old seq"
    assert config.read_bytes() == (b"external config" if failure == "external" else b"old config")
    assert set(request.clips_root.iterdir()) == {request.target_path, config}


def test_worker_publish_failure_preserves_recovery_and_refuses_late_cancel(tmp_path, monkeypatch):
    from flir_seq_tool import extraction
    from flir_seq_tool.export_bundle import pending_path
    request = paired_request(tmp_path)
    kwargs = worker_kwargs(request)
    handle = extraction.ExportAbortHandle()
    original = extraction.publish_bundle
    def publish(**kw):
        assert kwargs["progress"].snapshot().cancel_allowed is False
        assert handle.request_cancel(kwargs["cancel_event"]) is False
        def reject(seq, config):
            seq.write_bytes(b"external")
            kwargs["cancel_event"].set()  # Direct legacy caller cannot relabel publishing as cancelled.
            raise ValueError("final validation failed")
        kw["verify_pair"] = reject
        return original(**kw)
    monkeypatch.setattr(extraction, "publish_bundle", publish)
    result = extraction.run_export(request, abort_handle=handle, **kwargs)
    assert result.status is extraction.ExportStatus.FAILED
    assert request.target_path.read_bytes() == b"external"
    assert pending_path(request.target_path).exists()
    assert "Retained paths" in result.message
    assert list(request.clips_root.glob("*.partial.json"))


def test_export_job_uses_injected_metadata_reader(tmp_path):
    from flir_seq_tool.extraction import ExportJob, ExportStatus
    request = paired_request(tmp_path)
    kwargs = worker_kwargs(request)
    kwargs.pop("progress")
    kwargs.pop("cancel_event")
    job = ExportJob(request, **kwargs)
    job.start()
    job.join(timeout=5)
    assert not job.is_alive()
    assert job.result.status is ExportStatus.SUCCEEDED, job.result.message


def test_worker_cleanup_failure_reports_retained_path_instead_of_crashing(tmp_path, monkeypatch):
    from flir_seq_tool import extraction
    request = paired_request(tmp_path)
    kwargs = worker_kwargs(request)
    def reject(*_):
        raise ValueError("validation rejected")
    kwargs["validator"] = reject
    original = Path.unlink
    def fail_cleanup(self, *args, **kw):
        if self.name.endswith(".partial.seq"):
            raise PermissionError("temporary file locked")
        return original(self, *args, **kw)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    result = extraction.run_export(request, **kwargs)
    assert result.status is extraction.ExportStatus.FAILED
    assert "validation rejected" in result.message
    retained = next(request.clips_root.glob("*.partial.seq"))
    assert str(retained) in result.message
    assert "temporary file locked" in result.message


def test_worker_rechecks_source_metadata_before_sdk(tmp_path):
    from flir_seq_tool import extraction
    request = paired_request(tmp_path)
    kwargs = worker_kwargs(request)
    original = kwargs["reader_factory"]
    def mismatched(path):
        reader = original(path)
        if Path(path) == request.source_path:
            reader.width += 1
        return reader
    kwargs["reader_factory"] = mismatched
    def never_open(_):
        raise AssertionError("must validate source before SDK extract")
    kwargs["imager_factory"] = never_open
    result = extraction.run_export(request, **kwargs)
    assert result.status is extraction.ExportStatus.FAILED
    assert "source mismatch: width" in result.message
    assert not request.clips_root.exists()


def test_worker_relative_paths_do_not_delete_retained_recovery_materials(tmp_path, monkeypatch):
    from flir_seq_tool import extraction
    import flir_seq_tool.export_bundle as bundle
    request = paired_request(tmp_path)
    request.clips_root.mkdir()
    request.target_path.write_bytes(b"old seq")
    request = confirm(request).request
    monkeypatch.chdir(tmp_path)
    request = replace(request, target_path=request.target_path.relative_to(tmp_path), clips_root=Path("clips"))
    kwargs = worker_kwargs(request)
    original_publish = extraction.publish_bundle
    original_replace = bundle.os.replace
    def fail_restore(src, dst):
        if Path(src).suffix == ".recovery":
            raise OSError("restore denied")
        return original_replace(src, dst)
    def publish(**kw):
        def reject(*_):
            raise ValueError("verify rejected")
        kw["verify_pair"] = reject
        return original_publish(**kw)
    monkeypatch.setattr(bundle.os, "replace", fail_restore)
    monkeypatch.setattr(extraction, "publish_bundle", publish)
    result = extraction.run_export(request, **kwargs)
    assert result.status is extraction.ExportStatus.FAILED
    assert bundle.pending_path(request.target_path).exists()
    retained_seq = list(request.clips_root.glob("*.partial.seq"))
    assert len(retained_seq) == 1
    assert retained_seq[0].read_bytes() == b"native child"
