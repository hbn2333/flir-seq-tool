from datetime import datetime, timedelta
import threading
import csv
import json
from PIL import Image
import pytest

from matplotlib.backend_bases import CloseEvent, KeyEvent, MouseEvent
import numpy as np

from flir_seq_tool.measurement_job import AnalysisJob, AnalysisProgress
from flir_seq_tool.measurement_models import MeasurementSettings, Roi
from flir_seq_tool.models import TemperatureFrame


class Gate:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()
        self.reads = []
        self.fail = False


class GateReader:
    width, height, num_frames = 8, 6, 5
    def __init__(self, path, gate):
        self.path, self.gate = path, gate

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.gate.closed.set()

    def timestamp_at(self, index):
        return datetime(2026, 10, 22) + timedelta(milliseconds=20 * index)

    def temperature_frame_at(self, index):
        self.gate.entered.set()
        if not self.gate.release.wait(30):
            raise TimeoutError("test gate was not released")
        self.gate.reads.append(index)
        if self.gate.fail:
            raise OSError("read failed")
        return TemperatureFrame(index, self.timestamp_at(index), np.arange(48.).reshape(6, 8) + 10 * index)


def click(ui, button):
    ui.figure.canvas.draw()
    px, py = button.ax.transAxes.transform((.5, .5))
    for kind in ("button_press_event", "button_release_event"):
        ui.figure.canvas.callbacks.process(kind, MouseEvent(kind, ui.figure.canvas, px, py, button=1))


def configured_ui(make_measure_ui, tmp_path, gate):
    jobs = []
    def factory(request):
        job = AnalysisJob(request, reader_factory=lambda path: GateReader(path, gate), temp_root=tmp_path)
        jobs.append(job)
        return job
    ui = make_measure_ui(analysis_job_factory=factory)
    ui.session.measurement.settings = MeasurementSettings((Roi("r1", "ROI 1", 0, 0, 2, 2, "#123456", True),), "r1")
    ui.pages.switch("measure")
    click(ui, ui.measure_panel.curves_button)
    return ui, jobs


def test_analysis_locks_inputs_until_worker_releases_reader(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    ui.measure_panel.begin_add()
    assert ui.pages.busy is None
    assert not ui.measure_panel.calculate_button.active


def test_calculate_real_click_locks_inputs_and_publishes_complete_result(make_measure_ui, tmp_path, monkeypatch):
    gate = Gate()
    ui, jobs = configured_ui(make_measure_ui, tmp_path, gate)
    try:
        click(ui, ui.measure_panel.calculate_button)
        assert gate.entered.wait(5)
        assert ui.pages.busy == "analysis"
        assert not ui.slider.active and not ui.measure_button.active
        assert not ui.measure_panel.add_button.active
        assert ui.measure_panel.cancel_button.active
        assert not ui.pages.switch("process")
        before = ui.session.timeline.pending_range
        ui.confirm_button._observers.process("clicked", None)
        ui.range_slider.set_val((1, 3))
        assert ui.session.timeline.pending_range == before
        identity = ui.session.source_identity
        def forbidden():
            raise AssertionError("UI reader accessed during analysis")
        monkeypatch.setattr(ui.session, "source_identity", forbidden)
        ui.pages.refresh()
        ui.poll_analysis()
        monkeypatch.setattr(ui.session, "source_identity", identity)
        gate.release.set()
        jobs[0].join(5)
        assert not jobs[0].is_alive() and gate.closed.is_set()
        ui.poll_analysis()
        assert ui.pages.busy is None
        assert ui.session.measurement.status == "complete"
        assert ui.session.measurement.is_current(ui.session.analysis_key())
        assert len(list(ui.session.measurement.result_store.iter_rows())) == 5
        assert gate.reads == [0, 1, 2, 3, 4]
        assert ui.slider.active
        store = ui.session.measurement.result_store
        ui.measure_panel.name_box.set_val("Renamed")
        ui.measure_panel.set_checked("r1", False)
        assert ui.session.measurement.status == "complete"
        assert len(jobs) == 1 and store.path.exists()
        ui.measure_panel.geometry_boxes["width"].set_val("1")
        assert ui.session.measurement.status == "stale"
        ui.poll_analysis()
        assert store.path.exists()
        assert ui.session.measurement.status == "stale"
    finally:
        gate.release.set()
        ui.shutdown_analysis(wait=True)


def test_failed_recalculation_restores_old_matching_result(make_measure_ui, tmp_path):
    gate = Gate()
    gate.release.set()
    ui, jobs = configured_ui(make_measure_ui, tmp_path, gate)
    try:
        click(ui, ui.measure_panel.calculate_button)
        jobs[-1].join(5)
        ui.poll_analysis()
        old = ui.session.measurement.result_store
        gate.fail = True
        click(ui, ui.measure_panel.calculate_button)
        jobs[-1].join(5)
        ui.poll_analysis()
        assert ui.session.measurement.status == "complete"
        assert ui.session.measurement.result_store is old and old.path.exists()
        ui.measure_panel.geometry_boxes["width"].set_val("1")
        click(ui, ui.measure_panel.calculate_button)
        jobs[-1].join(5)
        ui.poll_analysis()
        assert ui.session.measurement.status == "stale"
        assert ui.session.measurement.result_store is old and old.path.exists()
    finally:
        ui.shutdown_analysis(wait=True)


def test_close_cancels_worker_and_shutdown_cleans_owned_results(make_measure_ui, tmp_path, monkeypatch):
    gate = Gate()
    ui, jobs = configured_ui(make_measure_ui, tmp_path, gate)
    try:
        click(ui, ui.measure_panel.calculate_button)
        assert gate.entered.wait(5)
        ui.figure.canvas.callbacks.process("close_event", CloseEvent("close_event", ui.figure.canvas))
        gate.release.set()
        ui.shutdown_analysis(wait=True)
        assert not jobs[0].is_alive()
        assert not list(tmp_path.glob("flir-measure-*"))
        monkeypatch.setattr(ui.figure.canvas, "draw_idle", lambda: (_ for _ in ()).throw(AssertionError("closed canvas")))
        ui.poll_analysis()
    finally:
        gate.release.set()
        ui.shutdown_analysis(wait=True)


def test_start_failure_restores_controls_and_does_not_leave_busy(make_measure_ui, tmp_path):
    class FailingJob:
        progress = AnalysisProgress()
        outcome = None
        def start(self):
            raise RuntimeError("start failed")
        def is_alive(self):
            return False
    ui = make_measure_ui(analysis_job_factory=lambda request: FailingJob())
    ui.session.measurement.settings = MeasurementSettings((Roi("r1", "ROI 1", 0, 0, 2, 2, "#123456", True),), "r1")
    ui.pages.switch("measure")
    click(ui, ui.measure_panel.curves_button)
    click(ui, ui.measure_panel.calculate_button)
    assert ui.pages.busy is None
    assert ui.measure_panel.calculate_button.active
    assert ui.slider.active
    assert "start failed" in ui.error_text.get_text()


def test_cancel_preserves_old_valid_plot_and_result(make_measure_ui, tmp_path):
    gate = Gate()
    gate.release.set()
    ui, jobs = configured_ui(make_measure_ui, tmp_path, gate)
    try:
        click(ui, ui.measure_panel.calculate_button)
        jobs[-1].join(5)
        ui.poll_analysis()
        old = ui.session.measurement.result_store
        lines = list(ui.measure_panel.result_axes.lines)
        gate.release.clear()
        gate.entered.clear()
        click(ui, ui.measure_panel.calculate_button)
        assert gate.entered.wait(5)
        assert list(ui.measure_panel.result_axes.lines) == lines
        click(ui, ui.measure_panel.cancel_button)
        gate.release.set()
        jobs[-1].join(5)
        ui.poll_analysis()
        assert ui.session.measurement.result_store is old
        assert ui.session.measurement.status == "complete"
        assert old.path.exists()
    finally:
        gate.release.set()
        ui.shutdown_analysis(wait=True)


def test_single_frame_analysis_keeps_slider_disabled_after_completion(make_measure_ui, tmp_path):
    gate = Gate()
    gate.release.set()
    ui, jobs = configured_ui(make_measure_ui, tmp_path, gate)
    try:
        ui.pages.switch("process")
        ui.range_slider.set_val((2, 2))
        click(ui, ui.confirm_button)
        ui.pages.switch("measure")
        click(ui, ui.measure_panel.calculate_button)
        jobs[-1].join(5)
        ui.poll_analysis()
        assert ui.session.measurement.status == "complete"
        assert not ui.slider.active
        assert gate.reads == [2]
        rows = list(ui.session.measurement.result_store.iter_rows())
        assert len(rows) == 1 and rows[0]["local_frame"] == 0 and rows[0]["source_frame"] == 2
    finally:
        ui.shutdown_analysis(wait=True)


def test_empty_worker_outcome_releases_busy_without_hanging(make_measure_ui):
    class EmptyJob:
        progress = AnalysisProgress()
        outcome = None
        def start(self):
            pass
        def is_alive(self):
            return False
    ui = make_measure_ui(analysis_job_factory=lambda request: EmptyJob())
    ui.session.measurement.settings = MeasurementSettings((Roi("r1", "ROI 1", 0, 0, 2, 2, "#123456", True),), "r1")
    ui.pages.switch("measure")
    click(ui, ui.measure_panel.curves_button)
    click(ui, ui.measure_panel.calculate_button)
    assert ui.pages.busy is None and ui.slider.active
    assert "without an outcome" in ui.error_text.get_text()


def test_analysis_and_native_export_are_mutually_exclusive(make_measure_ui, tmp_path):
    gate = Gate()
    ui, jobs = configured_ui(make_measure_ui, tmp_path, gate)
    try:
        ui.pages.switch("process")
        ui.pages.set_busy("native_export")
        ui.measure_panel.on_calculate()
        assert not jobs
        ui.pages.set_busy(None)
        ui.pages.switch("measure")
        click(ui, ui.measure_panel.calculate_button)
        assert gate.entered.wait(5)
        ui.export_button._observers.process("clicked", None)
        ui.load_button._observers.process("clicked", None)
        ui.save_button._observers.process("clicked", None)
        assert ui.export_state.job is None
        assert ui.pages.busy == "analysis"
        assert ui.session.selection_path is None
        # A queued stale native timer cannot unlock the analysis page.
        ui.export_state.job = object()
        ui.poll_export()
        assert ui.pages.busy == "analysis"
    finally:
        gate.release.set()
        ui.shutdown_analysis(wait=True)


def test_render_complete_curve_preview(make_measure_ui, tmp_path):
    from dataclasses import replace
    gate = Gate()
    gate.release.set()
    ui, jobs = configured_ui(make_measure_ui, tmp_path, gate)
    first = ui.session.measurement.settings.rois[0]
    second = Roi("r2", "ROI 2", 4, 2, 3, 2, "#ff7f0e", True)
    ui.session.measurement.settings = replace(ui.session.measurement.settings, rois=(first, second))
    ui.figure.set_size_inches(10, 8)
    ui.pages.refresh()
    try:
        click(ui, ui.measure_panel.calculate_button)
        jobs[-1].join(5)
        ui.poll_analysis()
        ui.slider.set_val(2)
        ui.figure.canvas.draw()
        path = tmp_path / "measure-complete-curves.png"
        ui.figure.savefig(path)
        assert path.stat().st_size > 0
        assert len(list(ui.session.measurement.result_store.iter_rows())) == 10
        print(f"MEASURE_PREVIEW={path}")
    finally:
        ui.shutdown_analysis(wait=True)


def test_save_setup_real_button_keeps_pending_dirty_and_load_restores_roi(make_measure_ui, tmp_path):
    ui = make_measure_ui()
    settings = MeasurementSettings((Roi("r1", "ROI 1", 0, 0, 2, 2, "#123456", True),), "r1")
    ui.session.measurement.settings = settings
    ui.range_slider.set_val((1, 3))
    ui.pages.switch("measure")
    target = tmp_path / "setup.selection.json"
    ui.measure_panel.dialogs.save = target
    click(ui, ui.measure_panel.save_setup_button)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["selection"]["source_start_frame"] == 0
    assert payload["selection"]["source_end_frame"] == 4
    assert payload["measurement"]["rois"][0]["id"] == "r1"
    assert ui.session.has_unsaved_changes()
    assert ui.session.measure_saved_signature == ui.session.state_signature(scope="measure")
    original = target.read_bytes()
    ui.measure_panel.name_box.set_val("Renamed")
    shown = []
    ui.measure_panel.dialogs.show_details = shown.append
    ui.figure.canvas.callbacks.process("key_press_event", KeyEvent("key_press_event", ui.figure.canvas, key="f1"))
    assert "Renamed" in ui.message_details["ROIs"]
    assert "Renamed" in shown[-1]
    click(ui, ui.measure_panel.save_setup_button)
    assert target.with_name(target.name + ".bak").read_bytes() == original
    ui.pages.switch("process")
    ui.measure_panel.dialogs.load = target
    click(ui, ui.load_button)
    assert ui.session.measurement.settings.rois[0].name == "Renamed"
    assert not ui.session.has_unsaved_changes()
    assert ui.session.measurement.status == "empty"


def test_current_frame_box_updates_frame_and_rejects_invalid_or_hidden_input(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    ui.current_frame_box.set_val("3")
    assert ui.session.timeline.current_local_frame == 3
    ui.current_frame_box.set_val("999")
    assert ui.session.timeline.current_local_frame == 3
    assert ui.current_frame_box.text == "3"
    ui.pages.switch("process")
    assert not ui.current_frame_box.active
    assert not ui.current_frame_box.ax.get_visible()
    ui.current_frame_box.set_val("1")
    assert ui.session.timeline.current_local_frame == 3


@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
def test_frame_box_reader_failure_is_shown_without_callback_exception(make_measure_ui, monkeypatch, error_type):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    def fail(*args):
        raise error_type("frame unavailable")
    monkeypatch.setattr(ui.session.reader, "temperature_frame_at", fail)
    ui.current_frame_box.set_val("2")
    assert "frame unavailable" in ui.message_details["message"]
    assert ui.current_frame_box.text == "0"
    assert ui.session.timeline.current_local_frame == 0
    assert ui.slider.val == 0


def test_current_csv_and_png_real_buttons_capture_frozen_frame(make_measure_ui, tmp_path):
    ui = make_measure_ui()
    ui.session.measurement.settings = MeasurementSettings((Roi("r1", "ROI 1", 0, 0, 2, 2, "#123456", True),), "r1")
    ui.pages.switch("measure")
    dialogs = ui.measure_panel.dialogs
    csv_target, png_target = tmp_path / "frame.csv", tmp_path / "frame.png"
    dialogs.analysis_paths.update(csv=csv_target, png=png_target)
    original_chooser = dialogs.choose_analysis_export
    def choose_while_trying_to_edit(*args):
        assert ui.pages.busy == "analysis_export"
        ui.slider.set_val(3)
        ui.measure_panel._set_view("frame_view", "profile")
        return original_chooser(*args)
    dialogs.choose_analysis_export = choose_while_trying_to_edit
    click(ui, ui.measure_panel.export_csv_button)
    with csv_target.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 32 and all(row["source_frame"] == "0" for row in rows)
    assert float(rows[0]["mean_c"]) == 4.5
    click(ui, ui.measure_panel.export_png_button)
    with Image.open(png_target) as picture:
        picture.load()
        assert picture.format == "PNG"
    assert ui.pages.busy is None
    assert ui.session.timeline.current_local_frame == 0


@pytest.mark.parametrize("choice", ["cancel", "refuse", "external-change"])
def test_measure_save_setup_cancel_and_failed_publish_preserve_dirty(make_measure_ui, tmp_path, choice):
    ui = make_measure_ui()
    ui.session.measurement.settings = MeasurementSettings((Roi("r1", "ROI 1", 0, 0, 2, 2, "#123456", True),), "r1")
    ui.pages.switch("measure")
    signature = ui.session.measure_saved_signature
    target = tmp_path / "existing.selection.json"
    target.write_bytes(b"old")
    dialogs = ui.measure_panel.dialogs
    dialogs.save = None if choice == "cancel" else target
    if choice == "refuse":
        dialogs.answer = False
    elif choice == "external-change":
        def confirm(message):
            target.write_bytes(b"external")
            return True
        dialogs.confirm = confirm
    click(ui, ui.measure_panel.save_setup_button)
    assert target.read_bytes() == (b"external" if choice == "external-change" else b"old")
    assert ui.session.measure_saved_signature == signature
    assert ui.session.has_unsaved_changes()
    assert not target.with_name(target.name + ".bak").exists()


@pytest.mark.parametrize("kind", ["cancel", "refuse", "backend-error", "external-change"])
def test_analysis_export_cancel_or_failure_keeps_files_and_unblocks(make_measure_ui, tmp_path, kind):
    ui = make_measure_ui()
    ui.session.measurement.settings = MeasurementSettings((Roi("r1", "ROI 1", 0, 0, 2, 2, "#123456", True),), "r1")
    ui.pages.switch("measure")
    dialogs = ui.measure_panel.dialogs
    target = tmp_path / "existing.csv"
    target.write_bytes(b"old")
    dialogs.analysis_paths["csv"] = None if kind == "cancel" else target
    if kind == "refuse":
        dialogs.answer = False
    elif kind == "backend-error":
        def fail(*args):
            raise RuntimeError("backend unavailable")
        dialogs.choose_analysis_export = fail
    elif kind == "external-change":
        def confirm(message):
            target.write_bytes(b"external")
            return True
        dialogs.confirm = confirm
    click(ui, ui.measure_panel.export_csv_button)
    assert target.read_bytes() == (b"external" if kind == "external-change" else b"old")
    assert ui.pages.busy is None and ui.measure_panel.export_csv_button.active
    if kind in ("backend-error", "external-change"):
        assert "failed" in ui.message_details["message"].lower()


def test_curve_export_requires_current_result_and_exports_all_rows(make_measure_ui, tmp_path):
    gate = Gate()
    gate.release.set()
    ui, jobs = configured_ui(make_measure_ui, tmp_path, gate)
    target = tmp_path / "curves.csv"
    ui.measure_panel.dialogs.analysis_paths["csv"] = target
    try:
        assert not ui.measure_panel.export_csv_button.active
        ui.measure_panel.on_export("csv")
        assert not target.exists()
        click(ui, ui.measure_panel.calculate_button)
        jobs[-1].join(5)
        ui.poll_analysis()
        assert ui.measure_panel.export_csv_button.active
        click(ui, ui.measure_panel.export_csv_button)
        with target.open(encoding="utf-8-sig", newline="") as stream:
            assert len(list(csv.DictReader(stream))) == 5
        ui.measure_panel.set_checked("r1", False)
        assert not ui.measure_panel.export_csv_button.active
        assert not ui.measure_panel.export_png_button.active
        choices_before = len(ui.measure_panel.dialogs.analysis_choices)
        ui.measure_panel.on_export("csv")
        assert len(ui.measure_panel.dialogs.analysis_choices) == choices_before
        ui.measure_panel.set_checked("r1", True)
        ui.measure_panel.geometry_boxes["width"].set_val("1")
        assert not ui.measure_panel.export_csv_button.active
    finally:
        ui.shutdown_analysis(wait=True)
