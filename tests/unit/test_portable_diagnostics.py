import sys

import pytest

from flir_seq_tool import portable_diagnostics


@pytest.mark.parametrize('name', ['source.seq', 'report.json'])
def test_diagnostics_cannot_write_report_into_source_raw(tmp_path, monkeypatch, name):
    raw = tmp_path / 'raw'
    raw.mkdir()
    source = raw / 'source.seq'
    source.write_bytes(b'original source')
    report = raw / name
    calls = []
    monkeypatch.setattr(portable_diagnostics, 'diagnose',
                        lambda *args: calls.append(args) or {'status': 'passed'})
    monkeypatch.setattr(sys, 'argv', ['FLIR_SEQ_Tool.exe', '--diagnose', str(source), '--report', str(report)])
    assert portable_diagnostics.main() == 1
    assert not calls
    assert source.read_bytes() == b'original source'
    assert name == 'source.seq' or not report.exists()
