"""Transactional source changes exercise real widgets with disposable fake readers."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import matplotlib.pyplot as plt
import pytest
from matplotlib import colors

from test_viewer import FileBackedFakeReader, FakeSelectionDialogs, make_frames
from flir_seq_tool import viewer
from flir_seq_tool.selection import SelectionStore
from flir_seq_tool.viewer import ViewerSession, build_viewer
from flir_seq_tool.viewer_theme import ACCENT, SURFACE


def test_native_confirmation_blue_and_reset_on_invalidation(tmp_path):
    session = ViewerSession(FileBackedFakeReader(make_frames(5), tmp_path / "a.seq"))
    dialogs = FakeSelectionDialogs()
    dialogs.seq_target = tmp_path / "out.seq"
    ui = build_viewer(session, dialogs=dialogs)
    try:
        ui.export_button._observers.process("clicked", None)
        assert ui.export_button.label.get_text() == "Confirm export"
        assert ui.export_button.role == "primary"
        assert ui.export_button.ax.get_facecolor() == colors.to_rgba(ACCENT)
        ui.range_slider.set_val((1, 3))
        assert ui.export_button.role == "default"
        assert ui.export_button.ax.get_facecolor() == colors.to_rgba(SURFACE)
        ui.export_button._observers.process("clicked", None)
        assert ui.export_button.role == "primary"
        session.measurement.settings = replace(session.measurement.settings, height_view="zones")
        ui.measure_panel.on_display_change()
        assert ui.export_state.confirmation is None
        assert ui.export_button.role == "default"
    finally:
        plt.close(ui.figure)


class Reader(FileBackedFakeReader):
    def __init__(self, path, *, fail_frame=False):
        # The test owns these newly created, disposable sources.
        super().__init__(make_frames(5), path)
        self.closed = False
        self.fail_frame = fail_frame

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def temperature_frame_at(self, index):
        if self.fail_frame:
            raise RuntimeError("damaged first frame")
        return super().temperature_frame_at(index)


def make_window(tmp_path, *, builder=None):
    from flir_seq_tool.viewer_window import ViewerWindow
    readers = []
    def factory(path):
        reader = Reader(path, fail_frame=Path(path).stem == "bad")
        readers.append(reader)
        return reader
    dialogs = FakeSelectionDialogs()
    dialogs.choose_seq_open = lambda folder: None
    dialogs.choose_unsaved_action = lambda message: "discard"
    window = ViewerWindow(reader_factory=factory, builder=builder or build_viewer,
                          dialogs=dialogs,
                          session_factory=lambda reader: ViewerSession(reader,
                              selection_store=SelectionStore(tmp_path / "selections")))
    window.start(tmp_path / "first.seq")
    return window, readers, dialogs


def test_switch_reuses_figure_and_releases_old_resources_after_draw(tmp_path, monkeypatch):
    window, readers, _ = make_window(tmp_path)
    figure = window.ui.figure
    old = window.session
    titles = []
    monkeypatch.setattr(figure.canvas.manager, "set_window_title", titles.append)
    try:
        assert window.open_source(tmp_path / "second.seq")
        assert window.ui.figure is figure
        assert window.session is not old
        assert readers[0].closed and not readers[1].closed
        assert window.ui.pages.current == "process"
        assert window.ui.open_button.active
        assert "second.seq" in titles[-1]
    finally:
        window.close()
        plt.close(figure)
    assert readers[1].closed


def test_candidate_first_frame_failure_keeps_original_ui_results_and_reader(tmp_path):
    window, readers, _ = make_window(tmp_path)
    ui = window.ui
    marker = SimpleNamespace(cleanup=lambda: pytest.fail("old result was released"))
    window.session.measurement.result_store = marker
    window.session.measurement.status = "complete"
    try:
        assert not window.open_source(tmp_path / "bad.seq")
        assert window.ui is ui
        assert window.session.measurement.result_store is marker
        assert not readers[0].closed and readers[1].closed
        assert "damaged first frame" in ui.error_text.get_text()
    finally:
        window.session.measurement.result_store = None
        window.close()
        plt.close(ui.figure)


@pytest.mark.parametrize("failure", ["build", "draw"])
def test_failed_candidate_build_rolls_back_page_frame_and_results(tmp_path, failure):
    def builder(session, **kwargs):
        if Path(session.reader.path).stem == "second":
            # Failure after callbacks exist must not contaminate the rollback.
            ui = build_viewer(session, **kwargs)
            ui.figure.canvas.mpl_connect("button_press_event", lambda event: None)
            if failure == "build":
                raise RuntimeError("installation failed")
            draw = ui.figure.canvas.draw
            def fail_once(*args, **kwargs):
                ui.figure.canvas.draw = draw
                raise RuntimeError("first draw failed")
            ui.figure.canvas.draw = fail_once
            return ui
        return build_viewer(session, **kwargs)
    window, readers, _ = make_window(tmp_path, builder=builder)
    figure = window.ui.figure
    session = window.session
    window.ui.pages.switch("measure")
    window.ui.slider.set_val(2)
    marker = SimpleNamespace(cleanup=lambda: None)
    session.measurement.result_store = marker
    session.measurement.status = "stale"
    before = {name: len(callbacks) for name, callbacks in figure.canvas.callbacks.callbacks.items()}
    try:
        assert not window.open_source(tmp_path / "second.seq")
        assert window.session is session and not readers[0].closed and readers[1].closed
        assert window.ui.figure is figure
        assert window.ui.pages.current == "measure"
        assert session.timeline.current_local_frame == 2
        assert session.measurement.result_store is marker
        after = {name: len(callbacks) for name, callbacks in figure.canvas.callbacks.callbacks.items()}
        assert after == before
    finally:
        window.close()
        plt.close(figure)


def test_ui_dispose_preserves_state_and_disconnects_inputs_idempotently(tmp_path):
    session = ViewerSession(Reader(tmp_path / "a.seq"))
    figure = plt.figure()
    before = {name: set(callbacks) for name, callbacks in figure.canvas.callbacks.callbacks.items()}
    ui = build_viewer(session, figure=figure)
    marker = SimpleNamespace(cleanup=lambda: pytest.fail("detach cleared results"))
    session.measurement.result_store = marker
    try:
        ui.dispose(preserve_session=True)
        ui.dispose(preserve_session=True)
        assert session.measurement.result_store is marker
        assert session.measurement.settings is not None
        for name, callbacks in figure.canvas.callbacks.callbacks.items():
            assert set(callbacks) <= before.get(name, set())
        assert not figure.axes
    finally:
        session.measurement.result_store = None
        plt.close(figure)


@pytest.mark.parametrize("action", ["cancel", "save"])
def test_unsaved_cancel_and_cancelled_save_keep_old_session(tmp_path, action):
    window, readers, dialogs = make_window(tmp_path)
    session = window.session
    session.timeline.set_pending(1, 3)
    dialogs.choose_unsaved_action = lambda message: action
    try:
        assert not window.open_source(tmp_path / "second.seq")
        assert window.session is session and not readers[0].closed
        assert len(readers) == 1
    finally:
        window.close()
        plt.close(window.figure)


def test_window_save_uses_candidate_document_and_all_settings_without_clearing_measure_scope(tmp_path):
    from flir_seq_tool.viewer_window import save_window_selection
    session = ViewerSession(Reader(tmp_path / "a.seq"), selection_store=SelectionStore(tmp_path / "configs"))
    session.frame_at(0)
    session.timeline.set_pending(1, 3)
    session.measurement.settings = replace(session.measurement.settings, curve_metric="max")
    measure_signature = session.measure_saved_signature
    dialogs = FakeSelectionDialogs(save=tmp_path / "chosen.selection.json")
    outcome = save_window_selection(session, dialogs)
    assert outcome.status == "saved"
    assert outcome.signature == session.state_signature(scope="process")
    loaded = session.selection_store.load_path(outcome.path)
    assert loaded.selection.source_start_frame == 1 and loaded.selection.source_end_frame == 3
    assert loaded.measurement == session.measurement.settings
    assert session.measure_saved_signature == measure_signature
    assert session.has_unsaved_changes()


def test_open_is_blocked_during_busy_roi_draft_and_same_file_is_noop(tmp_path):
    window, readers, _ = make_window(tmp_path)
    ui = window.ui
    try:
        for kind in ("analysis", "analysis_export", "native_export", "video_export"):
            ui.pages.set_busy(kind)
            assert not ui.open_button.active
            assert not window.open_source(tmp_path / "second.seq")
        ui.pages.set_busy(None)
        ui.mode_state.editing_transform = True
        ui.pages.refresh()
        assert not ui.open_button.active
        assert not window.open_source(tmp_path / "second.seq")
        ui.mode_state.editing_transform = False
        ui.pages.switch("measure")
        ui.measure_panel.adding = True
        ui.pages.refresh()
        assert not ui.open_button.active
        assert not window.open_source(tmp_path / "second.seq")
        ui.measure_panel.adding = False
        assert not window.open_source(tmp_path / "." / "first.seq")
        assert len(readers) == 1 and window.ui is ui
    finally:
        window.close()
        plt.close(window.figure)


def test_process_signature_covers_measurement_and_provenance_and_roundtrips(tmp_path):
    from flir_seq_tool.selection import SourceLink
    session = ViewerSession(Reader(tmp_path / "a.seq"), selection_store=SelectionStore(tmp_path / "configs"))
    session.frame_at(0)
    initial = session.state_signature(scope="process")
    session.measurement.settings = replace(session.measurement.settings, height_view="zones")
    assert session.state_signature(scope="process") != initial
    changed = session.state_signature(scope="process")
    session.provenance = (SourceLink(session.source_identity(), 0, 5),)
    assert session.state_signature(scope="process") != changed
    path = session.save_selection()
    fresh = ViewerSession(session.reader, selection_store=session.selection_store)
    fresh.load_selection(path)
    assert fresh.provenance == session.provenance
    assert fresh.selection_document().provenance == session.provenance


class ControlledJob:
    def __init__(self, request=None, *args, **kwargs):
        from flir_seq_tool.measurement_job import AnalysisProgress
        self.request = request
        self.progress = AnalysisProgress()
        self.progress.update(0, 5, "waiting")
        self.outcome = None
        self.alive = self.cancelled = self.started = False

    def start(self):
        self.alive = self.started = True

    def is_alive(self):
        return self.alive

    def cancel(self):
        self.cancelled = True

    def join(self, timeout=None):
        self.alive = False


def test_height_mode_selects_height_job_and_routes_cancel(tmp_path):
    from test_viewer import measurement_settings
    session = ViewerSession(Reader(tmp_path / "a.seq"))
    session.measurement.settings = replace(measurement_settings(), result_view="height")
    jobs = []
    ui = build_viewer(session, height_job_factory=lambda request: jobs.append(ControlledJob(request)) or jobs[-1],
                      analysis_job_factory=lambda request: pytest.fail("standard analysis selected"))
    try:
        ui.pages.switch("measure")
        ui.measure_panel.on_calculate()
        assert len(jobs) == 1 and jobs[0].started
        assert ui.pages.busy == "analysis" and not ui.open_button.active
        ui.measure_panel.on_cancel()
        assert jobs[0].cancelled
    finally:
        ui.dispose(wait=True)
        plt.close(ui.figure)


def test_height_export_uses_frozen_background_job_and_close_cancels_it(tmp_path):
    from flir_seq_tool.height_job import HeightAnalysisJob
    from test_viewer import measurement_settings
    session = ViewerSession(Reader(tmp_path / "a.seq"))
    session.measurement.settings = replace(measurement_settings(), result_view="height")
    session.frame_at(0)
    from flir_seq_tool.measurement_job import AnalysisRequest
    source = session.source_identity()
    key = session.analysis_key()
    request = AnalysisRequest(session.reader.path, source, 0, 4, session.applied_transform,
                              session.measurement.settings.rois, key)
    job = HeightAnalysisJob(request, reader_factory=lambda path: session.reader, temp_root=tmp_path)
    job.start()
    job.join(5)
    assert job.outcome.status == "complete", job.outcome.error
    session.measurement.result_store = job.outcome.store
    session.measurement.result_key = key
    session.measurement.status = "complete"
    dialogs = FakeSelectionDialogs()
    target = tmp_path / "full.csv"
    dialogs.choose_analysis_export = lambda *args: target
    calls = []
    def factory(target, snapshot, **options):
        worker = ControlledJob()
        calls.append((target, snapshot, options, worker))
        return worker
    ui = build_viewer(session, dialogs=dialogs, height_export_job_factory=factory)
    try:
        ui.pages.switch("measure")
        assert ui.measure_panel.can_export_height("height")
        assert not ui.measure_panel.can_export_height("zones")
        ui.measure_panel.on_export_height("height")
        assert len(calls) == 1 and calls[0][3].started
        assert ui.pages.busy == "analysis_export"
        assert ui.measure_panel.cancel_button.active
        assert not ui.open_button.active
        assert not target.exists()
        ui.measure_panel.on_cancel()
        assert calls[0][3].cancelled
        calls[0][3].alive = False
        calls[0][3].outcome = SimpleNamespace(status="cancelled", error=None, path=None)
        ui.poll_height_export()
        assert ui.pages.busy is None
        assert session.measurement.status == "complete"
        ui.measure_panel.on_export_height("height")
        ui.dispose(wait=True)
        assert calls[-1][3].cancelled and not calls[-1][3].alive
    finally:
        ui.dispose(wait=True)
        plt.close(ui.figure)


def test_native_confirmation_cancel_and_factory_failure_restore_default_role(tmp_path):
    session = ViewerSession(Reader(tmp_path / "a.seq"))
    dialogs = FakeSelectionDialogs()
    dialogs.seq_target = tmp_path / "export.seq"
    def fail(request):
        raise RuntimeError("worker unavailable")
    ui = build_viewer(session, dialogs=dialogs, export_job_factory=fail)
    try:
        ui.export_button._observers.process("clicked", None)
        assert ui.cancel_button.active
        ui.cancel_button._observers.process("clicked", None)
        assert ui.export_state.confirmation is None
        assert ui.export_button.role == "default" and not ui.cancel_button.active
        ui.export_button._observers.process("clicked", None)
        ui.export_button._observers.process("clicked", None)
        assert ui.export_button.role == "default"
        assert ui.export_button.label.get_text() == "Export sub-SEQ"
        assert "worker unavailable" in ui.error_text.get_text()
    finally:
        ui.dispose()
        plt.close(ui.figure)


def test_cancel_and_hardlink_alias_do_not_prompt_or_open_reader(tmp_path):
    import os
    window, readers, dialogs = make_window(tmp_path)
    dialogs.choose_unsaved_action = lambda message: pytest.fail("same file prompted for changes")
    session = window.session
    session.timeline.set_pending(1, 3)
    alias = tmp_path / "alias.seq"
    os.link(session.reader.path, alias)
    try:
        assert not window.open_source()
        assert not window.open_source(alias)
        assert len(readers) == 1 and window.session is session
    finally:
        window.close()
        plt.close(window.figure)


def test_frozen_window_save_reports_failure_if_settings_change_during_dialog(tmp_path):
    from flir_seq_tool.viewer_window import save_window_selection
    session = ViewerSession(Reader(tmp_path / "a.seq"), selection_store=SelectionStore(tmp_path / "configs"))
    session.frame_at(0)
    original = session.measurement.settings
    original_signatures = session.process_saved_signature, session.measure_saved_signature
    dialogs = FakeSelectionDialogs()
    target = tmp_path / "frozen.selection.json"
    def choose(*args, **kwargs):
        session.measurement.settings = replace(original, curve_metric="max")
        return target
    dialogs.choose_save = choose
    result = save_window_selection(session, dialogs)
    assert result.status == "failed" and "changed" in result.error
    assert session.selection_store.load_path(target).measurement == original
    assert (session.process_saved_signature, session.measure_saved_signature) == original_signatures


def test_window_save_failure_is_explicit_and_preserves_dirty_signatures(tmp_path, monkeypatch):
    from flir_seq_tool.viewer_window import save_window_selection
    session = ViewerSession(Reader(tmp_path / "a.seq"), selection_store=SelectionStore(tmp_path / "configs"))
    session.frame_at(0)
    session.timeline.set_pending(1, 3)
    before = session.process_saved_signature, session.measure_saved_signature
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(session.selection_store, "save_to", fail)
    result = save_window_selection(session, FakeSelectionDialogs(save=tmp_path / "failed.selection.json"))
    assert result.status == "failed" and result.error == "disk full"
    assert before == (session.process_saved_signature, session.measure_saved_signature)
    assert session.has_unsaved_changes()


def test_build_failure_reclaims_new_widgets_and_callbacks(tmp_path, monkeypatch):
    figure = plt.figure()
    before = {name: set(callbacks) for name, callbacks in figure.canvas.callbacks.callbacks.items()}
    def fail(figure, *args, **kwargs):
        from flir_seq_tool.viewer_theme import ThemedButton
        ThemedButton(figure.add_axes((.1, .1, .1, .1)), "partial")
        figure.canvas.mpl_connect("key_press_event", lambda event: None)
        raise RuntimeError("panel construction failed")
    monkeypatch.setattr(viewer, "build_measure_panel", fail)
    try:
        with pytest.raises(RuntimeError, match="panel construction"):
            build_viewer(ViewerSession(Reader(tmp_path / "a.seq")), figure=figure)
        assert not figure.axes
        for name, callbacks in figure.canvas.callbacks.callbacks.items():
            assert set(callbacks) <= before.get(name, set())
        assert figure._viewer_resources == []
    finally:
        plt.close(figure)


def test_detach_does_not_submit_unfinished_text_or_change_retained_state(tmp_path):
    session = ViewerSession(Reader(tmp_path / "a.seq"))
    ui = build_viewer(session)
    before = session.state_signature(), session.timeline.pending_range
    ui.start_box.begin_typing()
    ui.start_box.text_disp.set_text("2")
    try:
        ui.dispose(preserve_session=True)
        assert not ui.start_box.capturekeystrokes
        assert (session.state_signature(), session.timeline.pending_range) == before
    finally:
        plt.close(ui.figure)


def test_ui_resource_cleanup_continues_after_one_component_fails():
    from flir_seq_tool.viewer_window import UIResources
    figure = plt.figure()
    resources = UIResources(figure)
    completed = []
    resources.add_cleanup(lambda: completed.append("remaining component"))
    def fail():
        raise RuntimeError("cleanup failed")
    resources.add_cleanup(fail)
    figure.add_axes((.1, .1, .1, .1))
    try:
        resources.dispose()
        assert completed == ["remaining component"]
        assert resources.errors == ["cleanup failed"]
        assert not figure.axes
    finally:
        plt.close(figure)


def test_initial_figure_creation_failure_closes_prepared_reader(tmp_path, monkeypatch):
    from flir_seq_tool.viewer_window import ViewerWindow
    readers = []
    def factory(path):
        readers.append(Reader(path))
        return readers[-1]
    def fail(*args, **kwargs):
        raise RuntimeError("backend unavailable")
    window = ViewerWindow(reader_factory=factory)
    monkeypatch.setattr(plt, "figure", fail)
    with pytest.raises(RuntimeError, match="backend unavailable"):
        window.start(tmp_path / "a.seq")
    assert readers[0].closed and window.candidate is None


def test_final_result_cleanup_waits_until_panel_reader_is_disposed(tmp_path):
    session = ViewerSession(Reader(tmp_path / "a.seq"))
    ui = build_viewer(session)
    cleaned = []
    def cleanup():
        assert ui.measure_panel._disposed, "result deleted while panel reader is open"
        cleaned.append(True)
    session.measurement.result_store = SimpleNamespace(cleanup=cleanup)
    try:
        ui.dispose(wait=True)
        assert cleaned == [True]
        assert session.measurement_cleanup_error is None
    finally:
        plt.close(ui.figure)


@pytest.mark.parametrize("recorded", [False, True])
def test_status_identifies_recorded_ancestor_or_unknown_without_opening_ancestors(tmp_path, recorded):
    from flir_seq_tool.selection import SourceLink
    session = ViewerSession(Reader(tmp_path / "a.seq"))
    if recorded:
        parent = replace(session.source_identity(), filename="known-parent.seq", num_frames=8)
        session.provenance = (SourceLink(parent, 2, 5),)
    ui = build_viewer(session)
    try:
        ui.slider.set_val(3)
        text = ui.status_text.get_text()
        if recorded:
            assert "Origin: known-parent.seq" in text
            assert "frame 5" in text and "depth 1" in text
            assert "not a verified original recording" in ui.message_details["origin"]
            assert "known-parent.seq" in ui.message_details["origin"]
        else:
            assert "Origin: unknown" in text
            assert "unknown" in ui.message_details["origin"]
    finally:
        ui.dispose(wait=True)
        plt.close(ui.figure)


def test_calculate_height_accepts_source_with_backwards_endpoint_time(tmp_path):
    from contextlib import nullcontext
    from flir_seq_tool.height_job import HeightAnalysisJob
    from test_viewer import measurement_settings
    reader = Reader(tmp_path / "backwards.seq")
    times = [frame.timestamp for frame in reader.frames][::-1]
    reader.frames = [replace(frame, timestamp=timestamp) for frame, timestamp in zip(reader.frames, times)]
    session = ViewerSession(reader)
    session.measurement.settings = replace(measurement_settings(), result_view="height")
    def factory(request):
        independent = SimpleNamespace(path=reader.path, width=reader.width, height=reader.height,
            num_frames=reader.num_frames, timestamp_at=lambda index: reader.frames[index].timestamp,
            temperature_frame_at=lambda index: reader.frames[index])
        return HeightAnalysisJob(request, reader_factory=lambda path: nullcontext(independent), temp_root=tmp_path)
    ui = build_viewer(session, height_job_factory=factory)
    try:
        ui.pages.switch("measure")
        ui.measure_panel.on_calculate()
        assert ui.analysis_job is not None
        ui.analysis_job.join(5)
        assert not ui.analysis_job.is_alive()
        ui.poll_analysis()
        assert session.measurement.status == "complete", ui.error_text.get_text()
        assert session.measurement.result_store.has_height
    finally:
        ui.dispose(wait=True)
        plt.close(ui.figure)
