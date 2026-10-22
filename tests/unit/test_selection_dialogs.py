from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


@pytest.mark.parametrize("text, expected", [
    ("", None), ("experiment", Path("experiment.selection.json")),
    ("experiment.selection.json", Path("experiment.selection.json")),
    ("EXPERIMENT.SELECTION.JSON", Path("EXPERIMENT.SELECTION.JSON")),
])
def test_normalize_selection_path(text, expected):
    from flir_seq_tool.selection_dialogs import normalize_selection_path

    assert normalize_selection_path(text) == expected


@pytest.mark.parametrize("text", ["experiment.seq", "experiment.json", "experiment."])
def test_reject_incorrect_suffix(text):
    from flir_seq_tool.selection_dialogs import normalize_selection_path

    with pytest.raises(ValueError):
        normalize_selection_path(text)


@pytest.fixture
def fake_tk(monkeypatch):
    calls = []
    roots = []
    behavior = SimpleNamespace(value="", error=None)

    class Root:
        def __init__(self):
            self.destroyed = False
            self.hidden = False
            roots.append(self)

        def withdraw(self):
            self.hidden = True

        def destroy(self):
            self.destroyed = True

    class TclError(Exception):
        pass

    def dialog(**kwargs):
        calls.append(kwargs)
        if behavior.error:
            raise behavior.error
        return behavior.value

    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(
        Misc=Root, Tk=Root, TclError=TclError,
        filedialog=SimpleNamespace(asksaveasfilename=dialog, askopenfilename=dialog),
        messagebox=SimpleNamespace(askyesno=dialog, showinfo=dialog),
    ))
    return roots, calls, behavior, Root, TclError


@pytest.mark.parametrize("operation", ["save", "load", "confirm", "details"])
def test_dialog_parent_lifecycle_and_options(fake_tk, tmp_path, operation):
    from flir_seq_tool.selection_dialogs import SelectionDialogs

    roots, calls, behavior, Root, _ = fake_tk
    behavior.value = str(tmp_path / "chosen.selection.json")
    dialogs = SelectionDialogs()
    if operation == "save":
        assert dialogs.choose_save(tmp_path, "suggested.selection.json") == Path(behavior.value)
        assert calls[0]["confirmoverwrite"] is False
        assert calls[0]["initialfile"] == "suggested.selection.json"
        assert calls[0]["initialdir"] == str(tmp_path)
    elif operation == "load":
        assert dialogs.choose_load(tmp_path) == Path(behavior.value)
    elif operation == "confirm":
        behavior.value = False
        assert dialogs.confirm("Discard changes?") is False
    else:
        dialogs.show_details("full path")
        assert calls[0]["message"] == "full path"
    assert len(roots) == 1
    assert roots[0].hidden and roots[0].destroyed
    assert calls[0]["parent"] is roots[0]


def test_existing_tk_parent_is_not_destroyed(fake_tk, tmp_path):
    from flir_seq_tool.selection_dialogs import SelectionDialogs

    roots, calls, _, Root, _ = fake_tk
    parent = Root()
    assert SelectionDialogs(parent).choose_load(tmp_path) is None
    assert len(roots) == 1
    assert not parent.destroyed and not parent.hidden
    assert calls[0]["parent"] is parent


def test_cancel_and_backend_failure_create_no_files(fake_tk, tmp_path):
    from flir_seq_tool.selection_dialogs import SelectionDialogs

    roots, _, behavior, _, TclError = fake_tk
    initial = tmp_path / "not-created"
    dialogs = SelectionDialogs(parent=object())
    assert dialogs.choose_save(initial, "a.selection.json") is None
    behavior.error = TclError("backend unavailable")
    with pytest.raises(RuntimeError, match="backend unavailable"):
        dialogs.choose_load(initial)
    assert all(root.destroyed for root in roots)
    assert not initial.exists()


def test_worker_thread_cannot_open_dialog(fake_tk, tmp_path):
    from flir_seq_tool.selection_dialogs import SelectionDialogs

    roots, calls, _, _, _ = fake_tk
    with ThreadPoolExecutor(max_workers=1) as executor:
        job = executor.submit(SelectionDialogs().choose_load, tmp_path)
        with pytest.raises(RuntimeError, match="main thread"):
            job.result()
    assert not roots and not calls


@pytest.mark.parametrize("kind", ["csv", "png"])
def test_analysis_export_dialog_normalizes_suffix_and_preserves_parent_lifecycle(fake_tk, tmp_path, kind):
    from flir_seq_tool.selection_dialogs import SelectionDialogs
    roots, calls, behavior, _, _ = fake_tk
    behavior.value = str(tmp_path / "result")
    chosen = SelectionDialogs().choose_analysis_export(kind, tmp_path, f"suggested.{kind}")
    assert chosen == tmp_path / f"result.{kind}"
    assert calls[0]["confirmoverwrite"] is False
    assert calls[0]["initialfile"] == f"suggested.{kind}"
    assert roots[0].destroyed
    behavior.value = str(tmp_path / "bad.seq")
    with pytest.raises(ValueError, match="filename"):
        SelectionDialogs().choose_analysis_export(kind, tmp_path, "result")
    behavior.value = ""
    assert SelectionDialogs().choose_analysis_export(kind, tmp_path, "result") is None
