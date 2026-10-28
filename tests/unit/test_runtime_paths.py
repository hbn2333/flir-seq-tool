from pathlib import Path
import sys

from flir_seq_tool import runtime_paths
from flir_seq_tool.file_safety import validate_write_path


def test_source_root_does_not_follow_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runtime_paths.application_root() == Path(__file__).resolve().parents[2]


def test_frozen_paths_and_cache_stay_beside_exe(tmp_path, monkeypatch):
    root = tmp_path / '中文 portable'
    root.mkdir()
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(root / 'FLIR_SEQ_Tool.exe'))
    monkeypatch.setattr(sys, '_MEIPASS', str(root / '_internal'), raising=False)
    assert runtime_paths.application_root() == root
    assert runtime_paths.workspace_root() == root / 'output'
    assert runtime_paths.resource_root() == root / '_internal'
    import os
    import tempfile
    monkeypatch.setattr(tempfile, 'tempdir', tempfile.tempdir)
    for name in ('TEMP', 'TMP', 'MPLCONFIGDIR', 'XDG_CACHE_HOME', 'PYTHONDONTWRITEBYTECODE'):
        monkeypatch.setenv(name, os.environ.get(name, ''))
    monkeypatch.setattr(sys, 'dont_write_bytecode', sys.dont_write_bytecode)
    runtime_paths.configure_portable_environment()
    assert Path(os.environ['TEMP']).is_relative_to(root)
    assert Path(os.environ['MPLCONFIGDIR']).is_relative_to(root)


def test_foreign_raw_directory_remains_protected(tmp_path):
    raw = tmp_path / 'foreign' / 'raw'
    raw.mkdir(parents=True)
    source = raw / 'source.seq'
    source.write_bytes(b'source')
    import pytest
    with pytest.raises(ValueError, match='protected'):
        validate_write_path(raw / 'other.seq', source_path=source, raw_root=tmp_path / 'app' / 'raw')


def test_frozen_setup_replaces_previously_cached_temp_directory(tmp_path, monkeypatch):
    import os
    import tempfile
    root = tmp_path / 'app'
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(root / 'FLIR_SEQ_Tool.exe'))
    monkeypatch.setattr(sys, 'dont_write_bytecode', sys.dont_write_bytecode)
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path / 'foreign-cache'))
    for name in ('TEMP', 'TMP', 'MPLCONFIGDIR', 'XDG_CACHE_HOME', 'PYTHONDONTWRITEBYTECODE'):
        monkeypatch.setenv(name, os.environ.get(name, ''))
    runtime_paths.configure_portable_environment()
    assert Path(tempfile.gettempdir()) == root / 'cache/tmp'
    with tempfile.TemporaryDirectory() as directory:
        assert Path(directory).is_relative_to(root / 'cache/tmp')
