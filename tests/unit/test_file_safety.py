import os

import pytest


@pytest.mark.parametrize("kind", ["source", "raw", "directory", "hardlink", "symlink"])
def test_protected_output_is_rejected(tmp_path, kind):
    from flir_seq_tool import file_safety

    source = tmp_path / "source.seq"
    source.write_bytes(b"source")
    raw = tmp_path / "raw"
    raw.mkdir()
    target = tmp_path / "config.selection.json"
    if kind == "source":
        target = source
    elif kind == "raw":
        target = raw / "new" / "config.selection.json"
    elif kind == "directory":
        target.mkdir()
    else:
        try:
            if kind == "hardlink":
                os.link(source, target)
            else:
                target.symlink_to(source)
        except OSError as exc:
            pytest.skip(f"cannot create {kind}: {exc}")
    with pytest.raises(ValueError):
        file_safety.validate_write_path(target, source_path=source, raw_root=raw)
    assert source.read_bytes() == b"source"
    assert not (raw / "new").exists()


def test_parent_alias_to_raw_is_rejected(tmp_path):
    from flir_seq_tool import file_safety

    raw = tmp_path / "raw"
    raw.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(raw, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"cannot create directory symlink: {exc}")
    with pytest.raises(ValueError):
        file_safety.validate_write_path(alias / "config.json", source_path=tmp_path / "source.seq", raw_root=raw)
