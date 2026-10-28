from pathlib import Path

from flir_seq_tool.desktop import launch_desktop


def test_no_args_cancel_does_not_import_or_launch_viewer():
    calls = []
    assert launch_desktop([], choose=lambda: None, run=lambda args: calls.append(args)) == 0
    assert not calls


def test_picker_and_dragged_seq_become_view_command():
    calls = []
    launch_desktop([], choose=lambda: Path('中文 sample.seq'), run=lambda args: calls.append(args))
    launch_desktop(['other.seq'], run=lambda args: calls.append(args))
    assert calls == [['view', '中文 sample.seq'], ['view', 'other.seq']]


def test_cli_inspect_is_preserved():
    calls = []
    launch_desktop(['inspect', 'input.seq', '--json'], run=lambda args: calls.append(args))
    assert calls == [['inspect', 'input.seq', '--json']]
