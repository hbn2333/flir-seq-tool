import json
from datetime import datetime
from pathlib import Path

from flir_seq_tool import cli
from flir_seq_tool.models import SeqInspection, TimestampSummary


def test_cli_distinguishes_omitted_frame_from_explicit_zero():
    parser = cli.build_parser()
    assert parser.parse_args(["view", "clip.seq"]).frame is None
    assert parser.parse_args(["view", "clip.seq", "--frame", "0"]).frame == 0


def sample_inspection(path: Path) -> SeqInspection:
    timestamp = datetime(2026, 8, 28, 9, 0, 0)
    return SeqInspection(
        path=path.resolve(),
        size_bytes=7,
        modified_time_ns=123,
        width=640,
        height=480,
        num_frames=65,
        timestamp_mode="full",
        timestamps=TimestampSummary(
            frame_count=65,
            scanned_frame_count=65,
            continuity_checked=True,
            first_time=timestamp,
            last_time=timestamp,
            duration_s=0.0,
            median_interval_s=0.02,
            estimated_fps=50.0,
            duplicate_after_frames=(),
            backward_after_frames=(),
            gap_after_frames=(),
        ),
    )


def test_cli_outputs_json(monkeypatch, capsys, tmp_path: Path):
    path = tmp_path / "sample.seq"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(cli, "inspect_seq", lambda *_args, **_kwargs: sample_inspection(path))

    exit_code = cli.main(
        ["inspect", str(path), "--timestamp-mode", "full", "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["width"] == 640
    assert payload["height"] == 480
    assert payload["num_frames"] == 65
    assert payload["timestamp_mode"] == "full"


def test_cli_reports_expected_errors(monkeypatch, capsys, tmp_path: Path):
    path = tmp_path / "missing.seq"
    monkeypatch.setattr(
        cli,
        "inspect_seq",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError(path)),
    )

    exit_code = cli.main(["inspect", str(path)])

    assert exit_code == 2
    assert "error:" in capsys.readouterr().err


def test_cli_dispatches_view_command(monkeypatch, tmp_path: Path):
    path = tmp_path / "sample.seq"
    path.write_bytes(b"fixture")
    calls = []
    monkeypatch.setattr(
        cli,
        "launch_viewer",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    exit_code = cli.main(
        [
            "view",
            str(path),
            "--frame",
            "2",
            "--vmin",
            "20",
            "--vmax",
            "40",
        ]
    )

    assert exit_code == 0
    assert calls == [
        ((path,), {"initial_frame": 2, "vmin": 20.0, "vmax": 40.0})
    ]


def test_cli_view_reports_expected_errors(monkeypatch, capsys, tmp_path: Path):
    path = tmp_path / "sample.seq"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(
        cli,
        "launch_viewer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            IndexError("frame index out of range: 99")
        ),
    )

    exit_code = cli.main(["view", str(path), "--frame", "99"])

    assert exit_code == 2
    assert "frame index out of range: 99" in capsys.readouterr().err
