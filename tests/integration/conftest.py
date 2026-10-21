from pathlib import Path

import pytest


RAW = Path(r"D:\carbonfoam\infrared\raw")


def raw_metadata():
    return {path.name: (path.stat().st_size, path.stat().st_mtime_ns) for path in RAW.glob("*.seq")}


@pytest.fixture(autouse=True)
def preserve_raw_sources():
    before = raw_metadata()
    try:
        yield
    finally:
        assert raw_metadata() == before, "raw SEQ metadata changed"


@pytest.fixture(autouse=True)
def forbid_native_dialogs(monkeypatch):
    from flir_seq_tool import viewer
    class ForbiddenDialogs:
        def __getattr__(self, name):
            raise AssertionError(f"Integration tests must inject a dialog choice: {name}")
    monkeypatch.setattr(viewer, "SelectionDialogs", lambda parent=None: ForbiddenDialogs())
