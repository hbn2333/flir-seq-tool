from datetime import datetime, timedelta
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import threading

import numpy as np
import pytest

from flir_seq_tool.extraction import (
    ExportProgress,
    ExportAbortHandle,
    ExportJob,
    ExportRequest,
    ExportStatus,
    build_export_request,
    normalize_clip_name,
    preflight_export,
    required_free_bytes,
    run_export,
    TargetSnapshot,
    validate_extracted_seq,
)


START = datetime(2026, 8, 28, 9, 26, 37, 152000)


@pytest.mark.parametrize(
    "name",
    ["", " ", ".", "..", "a/b", r"a\b", "bad:name", "bad*name", "CON", "com1.txt", "tail."],
)
def test_clip_name_rejects_unsafe_windows_names(name):
    with pytest.raises(ValueError):
        normalize_clip_name(name)


def test_clip_name_accepts_custom_name_and_normalizes_one_extension():
    assert normalize_clip_name(" useful_run ") == "useful_run"
    assert normalize_clip_name("useful_run.seq") == "useful_run"


def make_request(tmp_path: Path, *, start: int = 3, end: int = 7) -> ExportRequest:
    source = tmp_path / "raw" / "source.seq"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b"x" * 1000)
    return build_export_request(
        source_path=source,
        clips_root=tmp_path / "clips",
        clip_name="useful_run",
        source_start_frame=start,
        source_end_frame=end,
        start_timestamp=START + timedelta(milliseconds=20 * start),
        end_timestamp=START + timedelta(milliseconds=20 * end),
        source_num_frames=20,
    )


def confirm_request(request: ExportRequest) -> ExportRequest:
    return preflight_export(
        request,
        work_source_range=(0, request.source_num_frames - 1),
        writable_formats=lambda: ["SEQ-FFF"],
        free_bytes=lambda _path: 10**9,
    ).request


def test_build_export_request_uses_closed_range_and_fixed_root(tmp_path: Path):
    request = make_request(tmp_path, start=5, end=9)

    assert request.target_path == tmp_path / "clips" / "useful_run.seq"
    assert request.frame_count == 5
    assert request.duration_s == pytest.approx(0.08)


def test_required_space_uses_average_frame_size_and_64_mib_floor():
    assert required_free_bytes(1000, 100, 10) == 64 * 1024 * 1024 + 100


def test_preflight_marks_existing_target_and_signature_changes_with_range(tmp_path):
    first = make_request(tmp_path, start=3, end=7)
    first.target_path.parent.mkdir(exist_ok=True)
    first.target_path.write_bytes(b"old")
    confirmation = preflight_export(
        first,
        work_source_range=(0, 19),
        writable_formats=lambda: ["SEQ-FFF"],
        free_bytes=lambda _path: 10**9,
    )
    second = make_request(tmp_path, start=4, end=7)
    other = preflight_export(
        second,
        work_source_range=(0, 19),
        writable_formats=lambda: ["SEQ-FFF"],
        free_bytes=lambda _path: 10**9,
    )

    assert confirmation.overwrite is True
    assert "OVERWRITE" in confirmation.summary
    assert confirmation.signature != other.signature


def test_confirmation_signature_changes_if_target_appears_before_second_click(tmp_path):
    request = make_request(tmp_path)
    first = preflight_export(
        request,
        work_source_range=(0, 19),
        writable_formats=lambda: ["SEQ-FFF"],
        free_bytes=lambda _path: 10**9,
    )
    request.target_path.write_bytes(b"external-target")

    second = preflight_export(
        request,
        work_source_range=(0, 19),
        writable_formats=lambda: ["SEQ-FFF"],
        free_bytes=lambda _path: 10**9,
    )

    assert first.overwrite is False
    assert second.overwrite is True
    assert first.signature != second.signature


def test_preflight_rejects_missing_seq_fff_and_low_space(tmp_path):
    request = make_request(tmp_path)
    with pytest.raises(RuntimeError, match="SEQ-FFF"):
        preflight_export(
            request,
            work_source_range=(0, 19),
            writable_formats=lambda: ["ATS"],
            free_bytes=lambda _path: 10**9,
        )
    with pytest.raises(RuntimeError, match="free space"):
        preflight_export(
            request,
            work_source_range=(0, 19),
            writable_formats=lambda: ["SEQ-FFF"],
            free_bytes=lambda _path: 0,
        )


def test_preflight_rejects_clips_directory_nested_under_raw(tmp_path):
    source = tmp_path / "raw" / "source.seq"
    source.parent.mkdir()
    source.write_bytes(b"source")
    request = build_export_request(
        source_path=source,
        clips_root=source.parent / "clips",
        clip_name="bad_location",
        source_start_frame=0,
        source_end_frame=0,
        start_timestamp=START,
        end_timestamp=START,
        source_num_frames=1,
    )

    with pytest.raises(ValueError, match="raw"):
        preflight_export(
            request,
            work_source_range=(0, 0),
            writable_formats=lambda: ["SEQ-FFF"],
            free_bytes=lambda _path: 10**9,
            raw_root=source.parent,
        )
    assert not request.clips_root.exists()


@pytest.mark.parametrize("entry_point", ["direct", "job"])
@pytest.mark.parametrize("destination", ["source", "raw_nested", "outside_root"])
def test_export_worker_rejects_unsafe_paths_before_any_write(
    tmp_path, monkeypatch, entry_point, destination
):
    request = make_request(tmp_path)
    raw_root = request.source_path.parent
    monkeypatch.setattr("flir_seq_tool.extraction.DEFAULT_RAW_ROOT", raw_root)
    if destination == "source":
        root, target = raw_root, request.source_path
    elif destination == "raw_nested":
        root = raw_root / "nested"
        target = root / "output.seq"
    else:
        root = request.clips_root
        target = tmp_path / "elsewhere" / "output.seq"
    request = replace(
        request, clips_root=root, target_path=target,
        target_snapshot=TargetSnapshot.capture(target),
    )
    before = request.source_path.stat()
    fake = FakeImager()
    kwargs = dict(
        imager_factory=lambda _path: fake,
        options_factory=FakeOptions,
        validator=lambda *_args: None,
    )

    if entry_point == "job":
        job = ExportJob(request, **kwargs)
        job.start()
        job.join(timeout=2)
        assert not job.is_alive()
        result = job.result
    else:
        result = run_export(
            request, progress=ExportProgress(),
            cancel_event=threading.Event(), **kwargs,
        )

    assert result is not None
    assert result.status is ExportStatus.FAILED
    assert fake.options is None
    assert request.source_path.read_bytes() == b"x" * 1000
    assert request.source_path.stat().st_mtime_ns == before.st_mtime_ns
    assert list(raw_root.iterdir()) == [request.source_path]
    assert not (tmp_path / "elsewhere").exists()
    assert not (tmp_path / "clips").exists()


def test_preflight_allows_retrimming_source_already_in_clips(tmp_path):
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    clips = tmp_path / "clips"
    clips.mkdir()
    source = clips / "first_clip.seq"
    source.write_bytes(b"source")
    request = build_export_request(
        source_path=source,
        clips_root=clips,
        clip_name="second_clip",
        source_start_frame=0,
        source_end_frame=0,
        start_timestamp=START,
        end_timestamp=START,
        source_num_frames=1,
    )

    confirmation = preflight_export(
        request,
        work_source_range=(0, 0),
        writable_formats=lambda: ["SEQ-FFF"],
        free_bytes=lambda _path: 10**9,
        raw_root=raw_root,
    )

    assert confirmation.request.target_path == clips / "second_clip.seq"


class FakeOptions:
    def __init__(self):
        self.start_frame = 0
        self.end_frame = 0
        self.decimation = 0
        self.preset = 0
        self.preferredFormat = None
        self.progress_callback = None
        self.abort_called = False

    def abort(self):
        self.abort_called = True


class FakeImager:
    def __init__(self, *, succeed=True, error=None):
        self.succeed = succeed
        self.error = error
        self.options = None
        self.closed = False

    def extract(self, destination, options):
        self.options = options
        if self.error:
            raise self.error
        Path(f"{destination}.seq").write_bytes(b"new-seq")
        if options.progress_callback(1, 5):
            return False
        options.progress_callback(5, 5)
        return self.succeed

    def close(self):
        self.closed = True


def test_run_export_configures_filesdk_and_replaces_only_after_validation(tmp_path):
    request = make_request(tmp_path)
    request.target_path.parent.mkdir(exist_ok=True)
    request.target_path.write_bytes(b"old")
    request = confirm_request(request)
    fake = FakeImager()
    options = FakeOptions()
    validated = []
    progress = ExportProgress()

    result = run_export(
        request,
        progress=progress,
        cancel_event=threading.Event(),
        imager_factory=lambda _path: fake,
        options_factory=lambda: options,
        validator=lambda req, partial: validated.append((req, partial)),
    )

    assert result.status is ExportStatus.SUCCEEDED
    assert request.target_path.read_bytes() == b"new-seq"
    assert options.start_frame == 3
    assert options.end_frame == 7
    assert options.decimation == 1
    assert options.preset == -1
    assert options.preferredFormat == "SEQ-FFF"
    assert validated and validated[0][0] == request
    assert fake.closed is True
    assert list(request.target_path.parent.glob("*.partial.seq")) == []


@pytest.mark.parametrize(
    "fake",
    [FakeImager(succeed=False), FakeImager(error=RuntimeError("SDK exploded"))],
)
def test_run_export_failure_keeps_existing_target_and_cleans_partial(tmp_path, fake):
    request = make_request(tmp_path)
    request.target_path.parent.mkdir(exist_ok=True)
    request.target_path.write_bytes(b"old")
    request = confirm_request(request)

    result = run_export(
        request,
        progress=ExportProgress(),
        cancel_event=threading.Event(),
        imager_factory=lambda _path: fake,
        options_factory=FakeOptions,
        validator=lambda *_args: None,
    )

    assert result.status is ExportStatus.FAILED
    assert request.target_path.read_bytes() == b"old"
    assert list(request.target_path.parent.glob("*.partial.seq")) == []


def test_run_export_cancel_callback_keeps_target_and_reports_cancelled(tmp_path):
    request = make_request(tmp_path)
    request.target_path.parent.mkdir(exist_ok=True)
    request.target_path.write_bytes(b"old")
    request = confirm_request(request)
    cancel = threading.Event()
    cancel.set()

    result = run_export(
        request,
        progress=ExportProgress(),
        cancel_event=cancel,
        imager_factory=lambda _path: FakeImager(),
        options_factory=FakeOptions,
        validator=lambda *_args: None,
    )

    assert result.status is ExportStatus.CANCELLED
    assert request.target_path.read_bytes() == b"old"
    assert list(request.target_path.parent.glob("*.partial.seq")) == []


def test_cancel_during_validation_prevents_formal_target_replacement(tmp_path):
    request = make_request(tmp_path)
    request.target_path.parent.mkdir(exist_ok=True)
    request.target_path.write_bytes(b"old")
    request = confirm_request(request)
    cancel = threading.Event()

    def cancel_in_validator(_request, _partial):
        cancel.set()

    result = run_export(
        request,
        progress=ExportProgress(),
        cancel_event=cancel,
        imager_factory=lambda _path: FakeImager(),
        options_factory=FakeOptions,
        validator=cancel_in_validator,
    )

    assert result.status is ExportStatus.CANCELLED
    assert request.target_path.read_bytes() == b"old"
    assert list(request.target_path.parent.glob("*.partial.seq")) == []


def test_target_created_during_validation_is_not_overwritten(tmp_path):
    request = make_request(tmp_path)

    def create_external_target(_request, _partial):
        request.target_path.write_bytes(b"external")

    result = run_export(
        request,
        progress=ExportProgress(),
        cancel_event=threading.Event(),
        imager_factory=lambda _path: FakeImager(),
        options_factory=FakeOptions,
        validator=create_external_target,
    )

    assert result.status is ExportStatus.FAILED
    assert "target changed" in result.message
    assert request.target_path.read_bytes() == b"external"


def test_confirmed_existing_target_changed_during_validation_is_not_overwritten(tmp_path):
    source = tmp_path / "raw" / "source.seq"
    source.parent.mkdir()
    source.write_bytes(b"x" * 1000)
    clips = tmp_path / "clips"
    clips.mkdir()
    target = clips / "useful_run.seq"
    target.write_bytes(b"confirmed-old")
    request = build_export_request(
        source_path=source,
        clips_root=clips,
        clip_name="useful_run",
        source_start_frame=3,
        source_end_frame=7,
        start_timestamp=START,
        end_timestamp=START + timedelta(milliseconds=80),
        source_num_frames=20,
    )

    result = run_export(
        request,
        progress=ExportProgress(),
        cancel_event=threading.Event(),
        imager_factory=lambda _path: FakeImager(),
        options_factory=FakeOptions,
        validator=lambda *_args: target.write_bytes(b"external-new"),
    )

    assert result.status is ExportStatus.FAILED
    assert "target changed" in result.message
    assert target.read_bytes() == b"external-new"


def test_cancelled_sdk_exception_is_classified_as_cancelled(tmp_path):
    request = make_request(tmp_path)
    cancel = threading.Event()

    class AbortingImager(FakeImager):
        def extract(self, _destination, _options):
            cancel.set()
            raise RuntimeError("SDK abort exception")

    result = run_export(
        request,
        progress=ExportProgress(),
        cancel_event=cancel,
        imager_factory=lambda _path: AbortingImager(),
        options_factory=FakeOptions,
        validator=lambda *_args: None,
    )

    assert result.status is ExportStatus.CANCELLED
    assert "SDK abort exception" in result.message


def test_abort_handle_immediately_aborts_options_if_cancel_won_registration_race():
    handle = ExportAbortHandle()
    options = FakeOptions()
    cancel = threading.Event()
    cancel.set()

    handle.set_options(options, cancel)

    assert options.abort_called is True


class FakeValidationReader:
    def __init__(self, *, width=3, height=2, times=None, matrix=None):
        self.width = width
        self.height = height
        self.times = times or [START]
        self.num_frames = len(self.times)
        self.matrix = np.asarray(
            matrix if matrix is not None else np.arange(6).reshape(2, 3),
            dtype=np.float32,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def timestamp_at(self, index):
        return self.times[index]

    def temperature_frame_at(self, index):
        return SimpleNamespace(temperature_c=self.matrix)


def test_validate_extracted_seq_compares_shape_count_timestamps_and_matrix(tmp_path):
    request = make_request(tmp_path, start=3, end=7)
    partial = request.target_path.parent / "clip.partial.seq"
    partial.parent.mkdir(exist_ok=True)
    partial.write_bytes(b"partial")
    source_times = [START + timedelta(milliseconds=20 * i) for i in range(20)]
    source = FakeValidationReader(times=source_times)
    output = FakeValidationReader(
        times=source_times[3:8], matrix=source.matrix.copy()
    )

    validate_extracted_seq(
        request,
        partial,
        reader_factory=lambda path: source if Path(path) == request.source_path else output,
    )


@pytest.mark.parametrize(
    ("output", "message"),
    [
        (FakeValidationReader(width=4, times=[START] * 5), "dimensions"),
        (FakeValidationReader(times=[START] * 4), "frame count"),
        (FakeValidationReader(times=[START] * 5), "first timestamp"),
        (
            FakeValidationReader(
                times=[START + timedelta(milliseconds=20 * i) for i in range(3, 8)],
                matrix=np.ones((2, 3), dtype=np.float32) * 99,
            ),
            "temperature matrix",
        ),
    ],
)
def test_validate_extracted_seq_rejects_mismatches(tmp_path, output, message):
    request = make_request(tmp_path, start=3, end=7)
    partial = request.target_path.parent / "clip.partial.seq"
    partial.parent.mkdir(exist_ok=True)
    partial.write_bytes(b"partial")
    source = FakeValidationReader(
        times=[START + timedelta(milliseconds=20 * i) for i in range(20)]
    )

    with pytest.raises(ValueError, match=message):
        validate_extracted_seq(
            request,
            partial,
            reader_factory=lambda path: source
            if Path(path) == request.source_path
            else output,
        )


def test_export_job_runs_in_background_and_cancel_calls_filesdk_abort(tmp_path):
    request = make_request(tmp_path)
    started = threading.Event()
    options = FakeOptions()

    class BlockingImager(FakeImager):
        def extract(self, destination, active_options):
            self.options = active_options
            Path(f"{destination}.seq").write_bytes(b"partial")
            started.set()
            assert started.wait(timeout=1)
            while not active_options.abort_called:
                threading.Event().wait(0.005)
            return False

    job = ExportJob(
        request,
        imager_factory=lambda _path: BlockingImager(),
        options_factory=lambda: options,
        validator=lambda *_args: None,
    )

    job.start()
    assert started.wait(timeout=1)
    assert job.is_alive()
    job.cancel()
    job.join(timeout=2)

    assert options.abort_called is True
    assert job.result is not None
    assert job.result.status is ExportStatus.CANCELLED
    assert job.is_alive() is False
