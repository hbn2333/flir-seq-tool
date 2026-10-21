import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .inspector import inspect_seq, inspection_to_dict


def launch_viewer(*args, **kwargs) -> None:
    """Import Matplotlib lazily so `inspect` remains lightweight."""
    from .viewer import launch_viewer as run_viewer

    run_viewer(*args, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flir-seq")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect", help="inspect a SEQ file")
    inspect_parser.add_argument("input", type=Path)
    inspect_parser.add_argument(
        "--timestamp-mode",
        choices=("endpoints", "full"),
        default="endpoints",
    )
    inspect_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="write machine-readable JSON to stdout",
    )
    view_parser = subparsers.add_parser("view", help="interactively view a SEQ file")
    view_parser.add_argument("input", type=Path)
    view_parser.add_argument("--frame", type=int, default=None, help="initial source frame index (default: restored range start)")
    view_parser.add_argument("--vmin", type=float, help="fixed color scale minimum (Celsius)")
    view_parser.add_argument("--vmax", type=float, help="fixed color scale maximum (Celsius)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "view":
            launch_viewer(
                args.input,
                initial_frame=args.frame,
                vmin=args.vmin,
                vmax=args.vmax,
            )
            return 0
        result = inspect_seq(args.input, timestamp_mode=args.timestamp_mode)
    except (FileNotFoundError, IndexError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.output_json:
        print(json.dumps(inspection_to_dict(result), ensure_ascii=False, indent=2))
    else:
        print(_format_human_readable(result))
    return 0


def _format_human_readable(result) -> str:
    timestamps = result.timestamps
    return "\n".join(
        (
            f"File: {result.path}",
            f"Size: {result.size_bytes} bytes",
            f"Dimensions: {result.width} x {result.height}",
            f"Frames: {result.num_frames}",
            f"First time: {timestamps.first_time.isoformat()}",
            f"Last time: {timestamps.last_time.isoformat()}",
            f"Timestamp continuity checked: {timestamps.continuity_checked}",
        )
    )
