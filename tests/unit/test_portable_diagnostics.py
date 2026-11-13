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


class ScanController:
    status = 'scanning'
    error = None
    blocks_heavy_tasks = True
    closed = False

    def poll(self):
        pass

    def close(self, wait=False):
        self.closed = wait
        self.blocks_heavy_tasks = False


def test_color_scan_timeout_retires_worker():
    controller = ScanController()
    with pytest.raises(RuntimeError, match='timed out'):
        portable_diagnostics._wait_color_scale(controller, lambda: None, timeout=0)
    assert controller.closed and not controller.blocks_heavy_tasks


def test_color_scan_failure_is_reported_and_closed():
    controller = ScanController()
    controller.status = 'failed'
    controller.error = 'no finite temperatures'
    controller.blocks_heavy_tasks = False
    with pytest.raises(RuntimeError, match='no finite temperatures'):
        portable_diagnostics._wait_color_scale(controller, lambda: None)
    assert controller.closed


def test_scale_assertion_detects_colorbar_drift():
    from types import SimpleNamespace
    import numpy as np
    image = SimpleNamespace(norm=SimpleNamespace(vmin=2., vmax=8.),
        colorbar=SimpleNamespace(norm=SimpleNamespace(vmin=2., vmax=9.)))
    with pytest.raises(AssertionError, match='colorbar'):
        portable_diagnostics._assert_frame_scale(image, np.array([[2., 8., np.nan]]))
    image.colorbar.norm.vmax = 8.
    assert portable_diagnostics._assert_frame_scale(image, np.array([[2., 8., np.inf]])) == (2., 8.)


def test_color_scan_success_polls_until_joined_without_closing_owner():
    controller = ScanController()
    polls = []
    def poll():
        polls.append(True)
        controller.status = 'complete'
        controller.blocks_heavy_tasks = False
    portable_diagnostics._wait_color_scale(controller, poll)
    assert polls == [True] and not controller.closed


def test_constant_frame_scale_uses_half_degree_padding():
    from types import SimpleNamespace
    import numpy as np
    norm = SimpleNamespace(vmin=6.5, vmax=7.5)
    image = SimpleNamespace(norm=norm, colorbar=SimpleNamespace(norm=norm))
    assert portable_diagnostics._assert_frame_scale(image, np.array([[7., 7.]])) == (6.5, 7.5)
