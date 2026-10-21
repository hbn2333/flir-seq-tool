"""Filesystem identity checks shared by selection and native extraction."""

from dataclasses import dataclass
from pathlib import Path


DEFAULT_RAW_ROOT = Path(r"D:\carbonfoam\infrared\raw")


@dataclass(frozen=True)
class TargetSnapshot:
    exists: bool
    device: int | None = None
    inode: int | None = None
    size_bytes: int | None = None
    modified_time_ns: int | None = None
    created_time_ns: int | None = None

    @classmethod
    def capture(cls, path: Path) -> "TargetSnapshot":
        try:
            stat = Path(path).stat()
        except FileNotFoundError:
            return cls(exists=False)
        return cls(
            exists=True, device=stat.st_dev, inode=stat.st_ino,
            size_bytes=stat.st_size, modified_time_ns=stat.st_mtime_ns,
            created_time_ns=stat.st_ctime_ns,
        )


def assert_snapshot(path: Path, expected: TargetSnapshot) -> None:
    if TargetSnapshot.capture(path) != expected:
        raise RuntimeError(f"target changed after confirmation: {path}")


def validate_write_path(path: Path, *, source_path: Path, raw_root: Path) -> Path:
    target = Path(path).resolve()
    source = Path(source_path).resolve()
    protected = Path(raw_root).resolve()
    if target == source or target.is_relative_to(protected):
        raise ValueError(f"protected output path: {path}")
    if target.exists() and source.exists() and target.samefile(source):
        raise ValueError(f"output aliases source: {path}")
    if target.exists() and not target.is_file():
        raise ValueError(f"output is not a regular file: {path}")
    return target
