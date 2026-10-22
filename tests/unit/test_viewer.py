from datetime import datetime, timedelta
import gc
import json
import weakref
from dataclasses import replace

import matplotlib

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
from matplotlib.text import Text
from matplotlib.backend_bases import CloseEvent, MouseEvent, KeyEvent, ResizeEvent
import numpy as np
import pytest

from flir_seq_tool.color_scale import ColorLimits
from flir_seq_tool.extraction import (
    ExportProgress,
    ExportResult,
    ExportStatus,
)
from flir_seq_tool.models import TemperatureFrame
from flir_seq_tool.measurement_models import MeasurementSettings, Roi
from flir_seq_tool.selection import DisplaySettings, SelectionStore
from flir_seq_tool.transform import CropRect, FrameTransform, RotationSettings
from flir_seq_tool import viewer
from flir_seq_tool.viewer import ViewerSession, build_viewer


class FakeTemperatureReader:
    def __init__(self, frames: list[TemperatureFrame]) -> None:
        self.frames = frames
        self.num_frames = len(frames)
        self.width = frames[0].temperature_c.shape[1]
        self.height = frames[0].temperature_c.shape[0]
        self.requested_frames: list[int] = []

    def temperature_frame_at(self, frame_index: int) -> TemperatureFrame:
        self.requested_frames.append(frame_index)
        return self.frames[frame_index]

    def timestamp_at(self, frame_index: int) -> datetime:
        return self.frames[frame_index].timestamp


class FileBackedFakeReader(FakeTemperatureReader):
    def __init__(self, frames: list[TemperatureFrame], path) -> None:
        super().__init__(frames)
        self.path = path
        self.path.write_bytes(b"fake-seq-source")
        self.width = frames[0].temperature_c.shape[1]
        self.height = frames[0].temperature_c.shape[0]


class FakeSelectionDialogs:
    def __init__(self, save=None, load=None, answer=True):
        self.save = save
        self.load = load
        self.answer = answer
        self.confirmations = []
        self.details = []
        self.choices = []

    def choose_save(self, initial_dir, suggested_name, **kwargs):
        self.choices.append(("save", initial_dir, suggested_name))
        return self.save

    def choose_load(self, initial_dir):
        self.choices.append(("load", initial_dir))
        return self.load

    def choose_analysis_export(self, kind, initial_dir, suggested_name):
        self.choices.append((kind, initial_dir, suggested_name))
        return None

    def confirm(self, message):
        self.confirmations.append(message)
        return self.answer

    def show_details(self, message):
        self.details.append(message)


def test_measure_scope_ignores_pending_range():
    session = ViewerSession(FakeTemperatureReader(make_frames(5)))
    session.timeline.set_pending(1, 3)
    assert session.measure_source_range() == (0, 4)
    session.confirm_range()
    assert session.measure_source_range() == (1, 3)


def test_measure_save_does_not_erase_unsaved_pending_range(tmp_path):
    session = ViewerSession(FileBackedFakeReader(make_frames(5), tmp_path / "s.seq"),
                            selection_store=SelectionStore(tmp_path / "configs"))
    session.frame_at(0)
    assert not session.has_unsaved_changes()
    session.timeline.set_pending(1, 3)
    assert session.selection_document(scope="measure").selection.frame_count == 5
    assert session.selection_document(scope="process").selection.frame_count == 3
    session.save_selection(scope="measure")
    assert session.has_unsaved_changes()
    assert session.measure_saved_signature == session.state_signature(scope="measure")
    assert session.process_saved_signature != session.state_signature(scope="process")


def test_roi_edits_are_dirty_before_any_save_and_browsing_is_not(tmp_path):
    session = ViewerSession(FileBackedFakeReader(make_frames(5), tmp_path / "s.seq"))
    session.frame_at(0)
    session.frame_at_local(3)
    assert not session.has_unsaved_changes()
    session.measurement.settings = MeasurementSettings((Roi("r1", "ROI 1", 0, 0, 2, 1, "#123456", True),), "r1")
    assert session.has_unsaved_changes()


@pytest.mark.parametrize("scope", ["measure", "process"])
def test_save_scope_io_and_clean_baselines_with_equal_ranges(tmp_path, scope):
    session = ViewerSession(FileBackedFakeReader(make_frames(5), tmp_path / "s.seq"),
                            selection_store=SelectionStore(tmp_path / "configs"))
    session.frame_at(0)
    settings = MeasurementSettings((Roi("r1", "ROI 1", 0, 0, 2, 1, "#123456", True),), "r1")
    session.measurement.settings = settings
    path = session.save_selection(scope=scope)
    assert not session.has_unsaved_changes()
    assert SelectionStore(tmp_path).load_path(path).measurement == settings
    session.measurement.settings = MeasurementSettings()
    session.load_selection(path)
    assert session.measurement.settings == settings
    assert session.measurement.status == "empty"
    assert not session.has_unsaved_changes()


def test_process_save_pending_scope_does_not_mark_measure_changes_saved(tmp_path):
    session = ViewerSession(FileBackedFakeReader(make_frames(5), tmp_path / "s.seq"),
                            selection_store=SelectionStore(tmp_path / "configs"))
    session.frame_at(0)
    session.timeline.set_pending(1, 3)
    session.measurement.settings = MeasurementSettings((Roi("r1", "ROI 1", 0, 0, 2, 1, "#123456", True),))
    session.save_selection()
    assert session.has_unsaved_changes()
    assert session.process_saved_signature == session.state_signature(scope="process")
    assert session.measure_saved_signature != session.state_signature(scope="measure")


def test_confirm_and_reset_invalidate_only_actual_work_range_changes():
    session = ViewerSession(FakeTemperatureReader(make_frames(5)))
    session.measurement.status = "complete"
    session.timeline.set_pending(1, 3)
    assert session.measurement.status == "complete"
    assert session.confirm_range()
    assert session.measurement.status == "stale"
    session.measurement.status = "complete"
    assert not session.confirm_range()
    assert session.measurement.status == "complete"
    session.reset_full()
    assert session.measurement.status == "stale"
    session.measurement.status = "complete"
    session.reset_full()
    assert session.measurement.status == "complete"


def test_unknown_save_scope_is_rejected_before_writing(tmp_path):
    session = ViewerSession(FileBackedFakeReader(make_frames(5), tmp_path / "s.seq"))
    session.frame_at(0)
    with pytest.raises(ValueError, match="scope"):
        session.selection_document(scope="bad")


def measurement_settings():
    return MeasurementSettings((Roi("r1", "ROI 1", 0, 0, 2, 1, "#123456", True),), "r1")


def test_measure_page_disables_process_range_input():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(5))))
    try:
        _drag_control(ui, ui.measure_button, .5, .5)
        assert ui.pages.current == "measure"
        assert not ui.range_slider.active
        assert not ui.range_slider.ax.get_visible()
        before = ui.session.timeline.pending_range
        ui.range_slider.set_val((1, 3))
        assert ui.session.timeline.pending_range == before
        _drag_control(ui, ui.process_button, .5, .5)
        assert ui.pages.current == "process"
        assert ui.range_slider.active
        assert ui.figure.canvas.mouse_grabber is None
    finally:
        plt.close(ui.figure)


def test_page_switch_does_not_accumulate_callbacks_or_change_frame():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(5))))
    try:
        ui.slider.set_val(2)
        counts = {kind: len(callbacks) for kind, callbacks in ui.figure.canvas.callbacks.callbacks.items()}
        for _ in range(20):
            assert ui.pages.switch("measure")
            assert ui.pages.switch("process")
        assert counts == {kind: len(callbacks) for kind, callbacks in ui.figure.canvas.callbacks.callbacks.items()}
        assert ui.session.timeline.current_local_frame == 2
        ui.edit_transform_button._observers.process("clicked", None)
        assert not ui.pages.switch("measure")
        assert not ui.measure_button.active
    finally:
        plt.close(ui.figure)


def test_single_frame_slider_remains_disabled_after_page_return():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(1))))
    try:
        ui.pages.switch("measure")
        assert not ui.slider.active
        ui.pages.switch("process")
        assert not ui.slider.active
        assert not ui.range_slider.active
    finally:
        plt.close(ui.figure)


def test_native_cancel_is_clickable_after_busy_page_refresh(tmp_path):
    session = ViewerSession(FileBackedFakeReader(make_frames(5), tmp_path / "s.seq"))
    jobs = []
    ui = build_viewer(session, clips_root=tmp_path / "clips",
                      export_job_factory=lambda request: jobs.append(FakeExportJob(request)) or jobs[-1])
    try:
        ui.clip_name_box.set_val("clip")
        _drag_control(ui, ui.export_button, .5, .5)
        _drag_control(ui, ui.export_button, .5, .5)
        ui.pages.refresh()
        assert ui.cancel_button.active
        _drag_control(ui, ui.cancel_button, .5, .5)
        assert jobs[0].cancelled
        jobs[0].progress.update(cancel_allowed=False)
        ui.pages.refresh()
        assert not ui.cancel_button.active
    finally:
        plt.close(ui.figure)


def test_same_transform_and_cancel_keep_measurement_but_changed_transform_clears():
    session = ViewerSession(FakeTemperatureReader(make_frames(5)))
    session.measurement.settings = measurement_settings()
    session.measurement.status = "complete"
    session.apply_transform(session.applied_transform)
    assert session.measurement.settings == measurement_settings()
    assert session.measurement.status == "complete"
    session.apply_transform(cropped_transform())
    assert session.measurement.settings == MeasurementSettings()
    assert session.measurement.status == "empty"


@pytest.mark.parametrize("accept", [False, True])
def test_transform_change_requires_roi_count_confirmation_and_preserves_rejected_draft(accept):
    session = ViewerSession(FakeTemperatureReader(make_frames(5)))
    session.measurement.settings = measurement_settings()
    dialogs = FakeSelectionDialogs(answer=accept)
    ui = build_viewer(session, dialogs=dialogs)
    try:
        ui.edit_transform_button._observers.process("clicked", None)
        ui.transform_editor.set_angle(10.)
        ui.transform_editor.apply_button._observers.process("clicked", None)
        assert "1 ROI" in dialogs.confirmations[-1]
        if accept:
            assert session.measurement.settings == MeasurementSettings()
            assert session.applied_transform.rotation.angle_deg == 10.
            assert not ui.mode_state.editing_transform
        else:
            assert session.measurement.settings == measurement_settings()
            assert session.applied_transform.rotation.angle_deg == 0.
            assert ui.mode_state.editing_transform
            assert ui.transform_editor.draft.build().rotation.angle_deg == 10.
            ui.transform_editor.cancel_button._observers.process("clicked", None)
            assert session.measurement.settings == measurement_settings()
    finally:
        plt.close(ui.figure)


def test_invalid_measurement_load_is_atomic_and_valid_load_replaces_settings(tmp_path):
    session = ViewerSession(FileBackedFakeReader(make_frames(5), tmp_path / "s.seq"),
                            selection_store=SelectionStore(tmp_path / "configs"))
    session.frame_at(0)
    session.measurement.settings = measurement_settings()
    target = session.save_selection(scope="measure")
    session.measurement.settings = replace(measurement_settings(), curve_metric="max")
    session.measurement.status = "complete"
    before = (session.state_signature(scope="measure"), session.measurement.settings,
              session.measurement.status, session.process_saved_signature, session.measure_saved_signature)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["measurement"]["rois"][0]["width"] = 999
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="bounds"):
        session.load_selection(target)
    assert before == (session.state_signature(scope="measure"), session.measurement.settings,
                      session.measurement.status, session.process_saved_signature, session.measure_saved_signature)
    payload["measurement"]["rois"][0]["width"] = 2
    target.write_text(json.dumps(payload), encoding="utf-8")
    session.load_selection(target)
    assert session.measurement.settings == measurement_settings()
    assert session.measurement.status == "empty"
    assert not session.has_unsaved_changes()


def test_measure_save_failure_preserves_both_saved_signatures(tmp_path, monkeypatch):
    session = ViewerSession(FileBackedFakeReader(make_frames(5), tmp_path / "s.seq"),
                            selection_store=SelectionStore(tmp_path / "configs"))
    session.frame_at(0)
    session.timeline.set_pending(1, 3)
    session.measurement.settings = measurement_settings()
    before = (session.process_saved_signature, session.measure_saved_signature)
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(session.selection_store, "save", fail)
    with pytest.raises(OSError, match="disk full"):
        session.save_selection(scope="measure")
    assert before == (session.process_saved_signature, session.measure_saved_signature)
    assert session.has_unsaved_changes()


@pytest.mark.parametrize("operation", ["load", "transform"])
def test_old_store_cleanup_failure_keeps_new_state_and_ownership(tmp_path, operation):
    class Store:
        fails = True
        cleaned = False
        def cleanup(self):
            if self.fails:
                raise OSError("busy result")
            self.cleaned = True
    session = ViewerSession(FileBackedFakeReader(make_frames(5), tmp_path / "s.seq"),
                            selection_store=SelectionStore(tmp_path / "configs"))
    session.frame_at(0)
    saved = session.save_selection()
    store = Store()
    session.measurement.settings = measurement_settings()
    session.measurement.result_store = store
    if operation == "load":
        session.load_selection(saved)
        assert session.measurement.settings == MeasurementSettings()
        assert not session.has_unsaved_changes()
    else:
        session.apply_transform(cropped_transform())
        assert session.applied_transform == cropped_transform()
        assert session.measurement.settings == MeasurementSettings()
    assert "busy result" in session.measurement_cleanup_error
    store.fails = False
    session.cleanup_measurement_results()
    assert store.cleaned
    assert session.measurement_cleanup_error is None


@pytest.fixture(autouse=True)
def no_real_dialogs(monkeypatch):
    # All UI tests must opt into specific choices; never display an OS window.
    monkeypatch.setattr(viewer, "SelectionDialogs", lambda parent=None: FakeSelectionDialogs(), raising=False)


def test_save_dialog_cancel_keeps_dirty_state_and_writes_nothing(tmp_path):
    reader = FileBackedFakeReader(make_frames(4), tmp_path / "source.seq")
    session = ViewerSession(reader, selection_store=SelectionStore(tmp_path / "configs"))
    ui = build_viewer(session, dialogs=FakeSelectionDialogs())
    try:
        session.selection_dirty = True
        before = session.state_signature()
        ui.save_button._observers.process("clicked", None)
        assert session.selection_dirty
        assert session.state_signature() == before
        assert session.selection_path is None
        assert not (tmp_path / "configs").exists()
    finally:
        plt.close(ui.figure)


def test_file_dialogs_can_save_and_choose_two_configurations(tmp_path):
    reader = FileBackedFakeReader(make_frames(5), tmp_path / "source.seq")
    session = ViewerSession(reader, selection_store=SelectionStore(tmp_path / "configs"))
    first, second = tmp_path / "a.selection.json", tmp_path / "b.selection.json"
    dialogs = FakeSelectionDialogs(save=first, load=first)
    ui = build_viewer(session, dialogs=dialogs)
    try:
        ui.range_slider.set_val((1, 3))
        ui.save_button._observers.process("clicked", None)
        assert session.selection_path == first
        dialogs.save = second
        ui.range_slider.set_val((2, 4))
        ui.save_button._observers.process("clicked", None)
        assert session.selection_path == second
        ui.load_button._observers.process("clicked", None)
        assert session.selection_path == first
        assert (session.timeline.source_start, session.timeline.source_end) == (1, 3)
        assert not session.has_unsaved_changes()
        assert first.is_file() and second.is_file()
    finally:
        plt.close(ui.figure)


def make_frames(count: int = 2) -> list[TemperatureFrame]:
    start = datetime(2026, 8, 28, 9, 0, 0)
    return [
        TemperatureFrame(
            frame_index=index,
            timestamp=start + timedelta(milliseconds=20 * index),
            temperature_c=(
                np.arange(1, 7, dtype=np.float32).reshape(2, 3) + 10 * index
            ),
        )
        for index in range(count)
    ]


def _send_transform_mouse(ui, name, xdata, ydata):
    editor = ui.transform_editor
    x, y = editor.image_axes.transData.transform((xdata, ydata))
    event = MouseEvent(name, ui.figure.canvas, x, y, button=1)
    ui.figure.canvas.callbacks.process(name, event)


def _drag_control(ui, control, start, end):
    canvas = ui.figure.canvas
    canvas.draw()
    for name, value in (("button_press_event", start),
                        ("motion_notify_event", end),
                        ("button_release_event", end)):
        x, y = control.ax.transData.transform((value, 0.5))
        canvas.callbacks.process(name, MouseEvent(name, canvas, x, y, button=1))
        if name != "button_release_event":
            assert canvas.mouse_grabber is control.ax
    assert canvas.mouse_grabber is None


def test_range_mouse_drag_does_not_activate_hidden_angle_slider():
    session = ViewerSession(FakeTemperatureReader(make_frames(10)))
    ui = build_viewer(session)
    try:
        _drag_control(ui, ui.range_slider, 2, 3)
        assert session.timeline.pending_range == (3, 9)
        assert ui.transform_editor.draft.angle_deg == 0
    finally:
        plt.close(ui.figure)


@pytest.mark.parametrize("exit_button", ["apply_button", "cancel_button"])
def test_mouse_drag_after_repeated_transform_mode_switches(exit_button):
    session = ViewerSession(FakeTemperatureReader(make_frames(10)))
    ui = build_viewer(session)
    try:
        for _ in range(2):
            _drag_control(ui, ui.edit_transform_button, 0.5, 0.5)
            _drag_control(ui, ui.transform_editor.angle_slider, 0, 5)
            assert ui.transform_editor.draft.angle_deg == pytest.approx(5, abs=0.1)
            assert session.timeline.pending_range == (0, 9)
            _drag_control(ui, ui.slider, 1, 2)
            assert session.timeline.current_local_frame == 2
            _drag_control(ui, getattr(ui.transform_editor, exit_button), 0.5, 0.5)
            _drag_control(ui, ui.range_slider, 2, 3)
            assert session.timeline.pending_range == (3, 9)
            ui.range_slider.set_val((0, 9))
    finally:
        plt.close(ui.figure)


@pytest.mark.parametrize("draw_first", [False, True])
@pytest.mark.parametrize("click_x", [5, 6])
def test_transform_single_click_keeps_crop_and_visible_selector(draw_first, click_x):
    frame = TemperatureFrame(
        frame_index=0, timestamp=datetime(2026, 8, 28),
        temperature_c=np.arange(48, dtype=np.float32).reshape(6, 8),
    )
    ui = build_viewer(ViewerSession(FakeTemperatureReader([frame])))
    try:
        ui.edit_transform_button._observers.process("clicked", None)
        ui.figure.canvas.draw()
        if draw_first:
            _send_transform_mouse(ui, "button_press_event", 1, 1)
            _send_transform_mouse(ui, "motion_notify_event", 4, 4)
            _send_transform_mouse(ui, "button_release_event", 4, 4)
        before = ui.transform_editor.draft.build()

        _send_transform_mouse(ui, "button_press_event", click_x, 1)
        _send_transform_mouse(ui, "button_release_event", click_x, 1)

        editor = ui.transform_editor
        assert editor.draft.build() == before
        assert editor.selector.get_visible()
        crop = before.crop
        assert editor.selector.extents == pytest.approx(
            (crop.x - 0.5, crop.x + crop.width - 0.5,
             crop.y - 0.5, crop.y + crop.height - 0.5)
        )
        editor.apply_button._observers.process("clicked", None)
        assert ui.session.applied_transform == before
    finally:
        plt.close(ui.figure)


@pytest.mark.parametrize("reverse", [False, True])
def test_transform_real_drag_preserves_integer_pixel_boundaries(reverse):
    frame = TemperatureFrame(
        frame_index=0, timestamp=datetime(2026, 8, 28),
        temperature_c=np.arange(48, dtype=np.float32).reshape(6, 8),
    )
    ui = build_viewer(ViewerSession(FakeTemperatureReader([frame])))
    try:
        ui.edit_transform_button._observers.process("clicked", None)
        ui.figure.canvas.draw()
        start, end = (4, 1) if reverse else (1, 4)
        _send_transform_mouse(ui, "button_press_event", start, start)
        _send_transform_mouse(ui, "motion_notify_event", end, end)
        _send_transform_mouse(ui, "button_release_event", end, end)

        assert ui.transform_editor.draft.crop == CropRect(1, 1, 4, 4)
    finally:
        plt.close(ui.figure)


def test_viewer_session_caches_frames_and_fixes_first_frame_color_limits():
    reader = FakeTemperatureReader(make_frames())
    session = ViewerSession(reader, cache_size=3)

    first = session.frame_at(0)
    second = session.frame_at(1)
    session.frame_at(0)

    assert first.frame_index == 0
    assert second.frame_index == 1
    assert reader.requested_frames == [0, 1]
    assert session.color_limits == ColorLimits(
        vmin=pytest.approx(1.05),
        vmax=pytest.approx(5.95),
    )
    assert len(session.cache) == 2


def cropped_transform(width: int = 3, height: int = 2) -> FrameTransform:
    return FrameTransform(
        source_width=width,
        source_height=height,
        rotation=RotationSettings(0.0),
        crop=CropRect(1, 0, width - 1, height),
    )


def test_viewer_session_transforms_one_frame_and_invalidates_only_derived_cache():
    reader = FakeTemperatureReader(make_frames())
    session = ViewerSession(reader)

    original = session.frame_at(0)
    session.apply_transform(cropped_transform())
    transformed = session.frame_at(0)
    session.frame_at(0)

    assert np.array_equal(original.temperature_c, reader.frames[0].temperature_c)
    assert np.array_equal(
        transformed.temperature_c,
        reader.frames[0].temperature_c[:, 1:],
    )
    assert transformed.frame_index == original.frame_index
    assert transformed.timestamp == original.timestamp
    assert reader.requested_frames == [0]
    assert len(session.source_cache) == 1
    assert len(session.transformed_cache) == 1


def test_viewer_session_reapplying_same_transform_is_a_noop():
    reader = FakeTemperatureReader(make_frames())
    session = ViewerSession(reader)
    transform = cropped_transform()
    session.apply_transform(transform)
    session.frame_at(0)
    session.selection_dirty = False
    cached = session.frame_at(0)

    session.apply_transform(transform)

    assert session.selection_dirty is False
    assert session.frame_at(0) is cached
    assert reader.requested_frames == [0]


def test_viewer_session_preview_is_uncropped_and_reuses_preview_transformer():
    reader = FakeTemperatureReader(make_frames())
    session = ViewerSession(reader)
    transform = cropped_transform()

    preview, first_mask = session.preview_frame(0, transform)
    first_transformer = session._preview_transformer
    second_preview, second_mask = session.preview_frame(0, transform)

    assert np.array_equal(preview.temperature_c, reader.frames[0].temperature_c)
    assert preview.temperature_c.shape != transform.output_shape
    assert np.array_equal(first_mask, np.ones((2, 3), dtype=bool))
    assert second_preview.frame_index == preview.frame_index
    assert second_preview.timestamp == preview.timestamp
    assert np.array_equal(second_preview.temperature_c, preview.temperature_c)
    assert np.array_equal(second_mask, first_mask)
    assert session._preview_transformer is first_transformer
    assert reader.requested_frames == [0]


def test_viewer_session_rejects_wrong_transform_dimensions_atomically():
    reader = FakeTemperatureReader(make_frames())
    session = ViewerSession(reader)
    session.frame_at(0)
    before_transform = session.applied_transform
    before_transformer = session.frame_transformer
    before_cache_length = len(session.transformed_cache)

    with pytest.raises(ValueError, match="source dimensions"):
        session.apply_transform(FrameTransform.identity(4, 2))

    assert session.applied_transform is before_transform
    assert session.frame_transformer is before_transformer
    assert len(session.transformed_cache) == before_cache_length
    assert session.selection_dirty is False


def test_viewer_session_reads_local_frame_from_confirmed_source_range():
    reader = FakeTemperatureReader(make_frames(count=2))
    session = ViewerSession(reader)
    session.timeline.set_pending(1, 1)
    session.timeline.confirm_range()

    frame = session.frame_at_local(0)

    assert frame.frame_index == 1
    assert reader.requested_frames == [1]


def test_viewer_session_uses_real_endpoint_timestamps_for_duration():
    reader = FakeTemperatureReader(make_frames(count=2))
    session = ViewerSession(reader)

    assert session.work_duration_s() == pytest.approx(0.02)


def test_build_viewer_slider_updates_frame_without_changing_color_limits():
    reader = FakeTemperatureReader(make_frames())
    session = ViewerSession(reader)
    ui = build_viewer(session, initial_frame=0)

    ui.slider.set_val(1)

    assert np.array_equal(ui.image.get_array(), reader.frames[1].temperature_c)
    assert ui.image.get_clim() == pytest.approx((1.05, 5.95))
    assert "Local frame 1/1" in ui.axes.get_title()
    assert "Source frame 1/1" in ui.axes.get_title()
    assert reader.frames[1].timestamp.isoformat() in ui.axes.get_title()
    plt.close(ui.figure)


def test_edit_transform_switches_single_window_controls_and_keeps_frame_slider():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(count=3))))

    ui.edit_transform_button._observers.process("clicked", None)

    assert ui.mode_state.editing_transform is True
    assert ui.transform_editor.control_axes[0].get_visible() is True
    assert ui.slider.ax.get_visible() is True
    assert ui.range_slider.ax.get_visible() is False
    assert ui.save_button.ax.get_visible() is False
    assert ui.transform_editor.draft.applied == ui.session.applied_transform
    plt.close(ui.figure)


def test_edit_mode_hides_normal_export_text_and_marks_draft():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames())))
    try:
        ui.export_text.set_text("Previous export summary")
        ui.progress_text.set_text("Previous export progress")
        ui.edit_transform_button._observers.process("clicked", None)
        assert not ui.export_text.get_visible()
        assert not ui.progress_text.get_visible()
        assert ui.status_text.get_visible()
        assert "draft" in ui.transform_status_text.get_text().lower()
        ui.transform_editor.set_angle(-3.2)
        assert "-3.2" in ui.transform_status_text.get_text()
        ui.transform_editor.cancel_button._observers.process("clicked", None)
        assert ui.export_text.get_visible()
        assert ui.progress_text.get_visible()
        assert "applied" in ui.transform_status_text.get_text().lower()
    finally:
        plt.close(ui.figure)


@pytest.mark.parametrize("size", [(10, 8), (12, 10), (16, 10)])
@pytest.mark.parametrize("dpi", [100, 150, 200])
@pytest.mark.parametrize("editing", [False, True])
def test_controls_and_information_do_not_overlap(size, dpi, editing):
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames())))
    try:
        ui.figure.set_size_inches(*size)
        ui.figure.set_dpi(dpi)
        if editing:
            ui.edit_transform_button._observers.process("clicked", None)
            ui.transform_editor.set_angle(-3.2)
        ui.figure.canvas.draw()
        renderer = ui.figure.canvas.get_renderer()
        controls = [ui.slider.ax]
        controls += list(ui.transform_editor.control_axes if editing else ui.normal_control_axes)
        controls = [ax for ax in controls if ax.get_visible()]
        texts = [text for ax in controls for text in ax.texts]
        texts += [child for child in ui.figure.get_children() if isinstance(child, Text)]
        texts = [t for t in texts if t.get_visible() and t.get_text()]
        for text in texts:
            box = text.get_window_extent(renderer)
            assert ui.figure.bbox.contains(box.x0, box.y0), text.get_text()
            assert ui.figure.bbox.contains(box.x1, box.y1), text.get_text()
            for control in controls:
                if text.axes is control:
                    continue
                assert not box.overlaps(control.get_window_extent(renderer)), text.get_text()
        for index, text in enumerate(texts):
            for other in texts[index + 1:]:
                assert not text.get_window_extent(renderer).overlaps(
                    other.get_window_extent(renderer)
                ), (text.get_text(), other.get_text())
    finally:
        plt.close(ui.figure)


def test_editor_frame_navigation_keeps_draft_and_uses_full_preview():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(count=3))))
    ui.edit_transform_button._observers.process("clicked", None)
    ui.transform_editor.set_angle(10.0)
    fingerprint = ui.transform_editor.draft.build().fingerprint

    ui.slider.set_val(2)

    assert ui.transform_editor.draft.build().fingerprint == fingerprint
    assert ui.session.timeline.current_local_frame == 2
    assert np.asarray(ui.image.get_array()).shape == (
        ui.transform_editor.draft.build().rotated_shape
    )
    plt.close(ui.figure)


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("dpi", [100, 150, 200])
def test_long_messages_fit_and_full_details_survive_resize(editing, dpi, capsys):
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames())))
    ui.figure.set_dpi(dpi)
    original = "Save failed: D:/" + "WideWWWdirectory/" * 40 + "a.selection.json"
    try:
        if editing:
            ui.edit_transform_button._observers.process("clicked", None)
        ui.error_text.set_text(original)
        ui.export_text.set_text(original)
        ui.progress_text.set_text(original)
        ui.transform_editor.error_text.set_text(original)
        ui.transform_editor.status_text.set_text(original)
        for size in [(10, 8), (16, 10), (10, 8)]:
            ui.figure.set_size_inches(*size)
            ui.figure.canvas.callbacks.process("resize_event", ResizeEvent("resize_event", ui.figure.canvas))
            ui.figure.canvas.draw()
            renderer = ui.figure.canvas.get_renderer()
            texts = [ui.error_text, ui.status_text, ui.transform_status_text]
            texts += ([ui.transform_editor.error_text, ui.transform_editor.status_text]
                      if editing else [ui.export_text, ui.progress_text])
            for index, text in enumerate(texts):
                box = text.get_window_extent(renderer)
                assert ui.figure.bbox.contains(box.x0, box.y0), text.get_text()
                assert ui.figure.bbox.contains(box.x1, box.y1), text.get_text()
                for other in texts[index + 1:]:
                    assert not box.overlaps(other.get_window_extent(renderer))
        assert ui.message_details["message"] == original
        assert "F1" in ui.error_text.get_text()
        ui.figure.canvas.callbacks.process("key_press_event", KeyEvent("key_press_event", ui.figure.canvas, key="f1"))
        assert original in capsys.readouterr().out
        assert ui.message_details["message"] == original
    finally:
        plt.close(ui.figure)


def test_small_window_warning_is_removed_when_resized():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(count=1))))
    try:
        ui.figure.set_size_inches(8, 6)
        ui.figure.canvas.callbacks.process("resize_event", ResizeEvent("resize_event", ui.figure.canvas))
        ui.figure.canvas.draw()
        assert ui.size_warning_text.get_visible()
        assert "1000 x 800" in ui.size_warning_text.get_text()
        ui.figure.set_size_inches(10, 8)
        ui.figure.canvas.callbacks.process("resize_event", ResizeEvent("resize_event", ui.figure.canvas))
        assert not ui.size_warning_text.get_visible()
        assert not ui.slider.ax.get_visible()
    finally:
        plt.close(ui.figure)


def test_microsecond_timestamp_title_stays_inside_minimum_canvas():
    frame = TemperatureFrame(
        frame_index=0, timestamp=datetime(2026, 8, 28, 9, 26, 37, 152000),
        temperature_c=np.ones((480, 640)),
    )
    ui = build_viewer(ViewerSession(FakeTemperatureReader([frame] * 65)))
    try:
        ui.figure.set_size_inches(10, 8)
        ui.figure.canvas.draw()
        title = ui.axes.title.get_window_extent(ui.figure.canvas.get_renderer())
        assert ui.figure.bbox.contains(title.x0, title.y0)
        assert ui.figure.bbox.contains(title.x1, title.y1)
    finally:
        plt.close(ui.figure)


def test_cancel_restores_applied_view_and_apply_marks_unsaved():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(count=2))))
    original = ui.session.applied_transform
    ui.edit_transform_button._observers.process("clicked", None)
    ui.transform_editor.set_angle(10.0)
    ui.transform_editor.cancel_button._observers.process("clicked", None)
    assert ui.session.applied_transform == original
    assert ui.mode_state.editing_transform is False

    ui.edit_transform_button._observers.process("clicked", None)
    ui.transform_editor.set_angle(10.0)
    ui.transform_editor.apply_button._observers.process("clicked", None)
    assert ui.session.applied_transform.rotation.angle_deg == 10.0
    assert ui.session.selection_dirty is True
    assert "unsaved" in ui.transform_status_text.get_text()
    assert ui.mode_state.editing_transform is False
    plt.close(ui.figure)


def test_reentering_editor_starts_from_applied_not_cancelled_draft():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames())))
    ui.edit_transform_button._observers.process("clicked", None)
    ui.transform_editor.set_angle(10.0)
    ui.transform_editor.cancel_button._observers.process("clicked", None)

    ui.edit_transform_button._observers.process("clicked", None)

    assert ui.transform_editor.draft.build() == ui.session.applied_transform
    assert ui.transform_editor.draft.angle_deg == 0.0
    plt.close(ui.figure)


def test_apply_preview_failure_is_atomic_and_keeps_editor_open(monkeypatch):
    session = ViewerSession(FakeTemperatureReader(make_frames()))
    ui = build_viewer(session)
    ui.edit_transform_button._observers.process("clicked", None)
    ui.transform_editor.set_angle(10.0)
    before = session.applied_transform

    def fail_preview(*_args):
        raise RuntimeError("preview failed")

    monkeypatch.setattr(session, "preview_frame", fail_preview)
    ui.transform_editor.apply_button._observers.process("clicked", None)

    assert session.applied_transform == before
    assert ui.mode_state.editing_transform is True
    assert "preview failed" in ui.error_text.get_text()
    plt.close(ui.figure)


def test_image_extent_and_limits_follow_changed_transform_shape():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames())))
    ui.edit_transform_button._observers.process("clicked", None)
    ui.transform_editor.set_angle(90.0)

    height, width = np.asarray(ui.image.get_array()).shape
    assert tuple(ui.image.get_extent()) == pytest.approx(
        (-0.5, width - 0.5, height - 0.5, -0.5)
    )
    assert ui.axes.get_xlim() == pytest.approx((-0.5, width - 0.5))
    assert ui.axes.get_ylim() == pytest.approx((height - 0.5, -0.5))
    plt.close(ui.figure)


def test_selector_extents_follow_full_rotated_preview_after_axes_resize():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames())))
    ui.edit_transform_button._observers.process("clicked", None)

    ui.transform_editor.set_angle(90.0)

    assert ui.transform_editor.selector.extents == pytest.approx(
        (-0.5, 1.5, -0.5, 2.5)
    )
    plt.close(ui.figure)


def test_reentering_editor_restores_offset_crop_after_full_preview_resize():
    session = ViewerSession(FakeTemperatureReader(make_frames()))
    session.apply_transform(cropped_transform())
    ui = build_viewer(session)

    ui.edit_transform_button._observers.process("clicked", None)

    assert ui.transform_editor.selector.extents == pytest.approx(
        (0.5, 2.5, -0.5, 1.5)
    )
    plt.close(ui.figure)


def test_invalid_preview_pixels_are_masked_and_use_gray_bad_color():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames())))
    ui.edit_transform_button._observers.process("clicked", None)
    ui.transform_editor.set_angle(10.0)

    displayed = ui.image.get_array()
    assert np.ma.isMaskedArray(displayed)
    assert np.ma.getmaskarray(displayed).any()
    assert ui.image.get_cmap().get_bad() == pytest.approx(
        matplotlib.colors.to_rgba("0.35")
    )
    plt.close(ui.figure)


def test_browsing_current_frame_does_not_mark_selection_dirty():
    session = ViewerSession(FakeTemperatureReader(make_frames(count=3)))
    ui = build_viewer(session)

    ui.slider.set_val(2)

    assert session.selection_dirty is False
    plt.close(ui.figure)


def test_resubmitting_unchanged_endpoint_does_not_mark_selection_dirty():
    session = ViewerSession(FakeTemperatureReader(make_frames(count=3)))
    ui = build_viewer(session)

    ui.start_box._observers.process("submit", "0")

    assert session.selection_dirty is False
    plt.close(ui.figure)


def test_resubmitting_unchanged_endpoint_preserves_state_after_declining_load(
    tmp_path,
):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(
        reader, selection_store=SelectionStore(tmp_path / "selections")
    )
    dialogs = FakeSelectionDialogs(load=session.selection_store.path_for(reader.path), answer=False)
    ui = build_viewer(session, dialogs=dialogs)
    session.save_selection()
    ui.range_slider.set_val((0, 1))
    ui.load_button._observers.process("clicked", None)
    before = (
        session.timeline.pending_range,
        session.timeline.current_local_frame,
        session.selection_dirty,
    )

    ui.start_box._observers.process("submit", "0")

    assert (
        session.timeline.pending_range,
        session.timeline.current_local_frame,
        session.selection_dirty,
    ) == before
    assert len(dialogs.confirmations) == 1
    assert ui.load_button.label.get_text() == "Load selection"
    plt.close(ui.figure)


@pytest.mark.parametrize(
    ("vmin", "vmax", "message"),
    [
        (20.0, None, "vmin and vmax must be provided together"),
        (None, 40.0, "vmin and vmax must be provided together"),
        (40.0, 20.0, "vmin must be less than vmax"),
    ],
)
def test_viewer_session_rejects_invalid_explicit_limits(vmin, vmax, message):
    reader = FakeTemperatureReader(make_frames())

    with pytest.raises(ValueError, match=message):
        ViewerSession(reader, vmin=vmin, vmax=vmax)


def test_build_viewer_rejects_out_of_range_initial_frame():
    session = ViewerSession(FakeTemperatureReader(make_frames()))

    with pytest.raises(IndexError, match="local frame out of range"):
        build_viewer(session, initial_frame=2)


def test_build_viewer_hides_slider_for_single_frame():
    session = ViewerSession(FakeTemperatureReader(make_frames(count=1)))
    ui = build_viewer(session, initial_frame=0)

    assert ui.slider.active is False
    assert ui.slider.ax.get_visible() is False
    assert ui.range_slider.active is False
    assert ui.range_slider.ax.get_visible() is False
    plt.close(ui.figure)


def test_range_slider_updates_selection_and_previews_changed_endpoint():
    reader = FakeTemperatureReader(make_frames(count=10))
    ui = build_viewer(ViewerSession(reader))

    ui.range_slider.set_val((2, 7))

    assert ui.session.timeline.pending_range == (2, 7)
    assert ui.session.timeline.current_local_frame == 2
    assert ui.slider.val == pytest.approx(2)
    assert "Source frame 2/9" in ui.axes.get_title()
    plt.close(ui.figure)


def test_duplicate_range_event_keeps_preview_on_last_changed_endpoint():
    reader = FakeTemperatureReader(make_frames(count=10))
    ui = build_viewer(ViewerSession(reader))
    ui.range_slider.set_val((2, 7))
    assert ui.session.timeline.current_local_frame == 2

    ui.range_slider.set_val((2, 7))

    assert ui.session.timeline.pending_range == (2, 7)
    assert ui.session.timeline.current_local_frame == 2
    assert ui.slider.val == pytest.approx(2)
    plt.close(ui.figure)


def test_confirm_range_renumbers_both_sliders_and_shows_local_zero():
    reader = FakeTemperatureReader(make_frames(count=10))
    ui = build_viewer(ViewerSession(reader))
    ui.range_slider.set_val((2, 7))

    ui.confirm_button._observers.process("clicked", None)

    assert (ui.session.timeline.source_start, ui.session.timeline.source_end) == (2, 7)
    assert ui.session.timeline.current_local_frame == 0
    assert ui.slider.val == pytest.approx(0)
    assert ui.range_slider.val == pytest.approx((0, 5))
    assert "Source frame 2/9" in ui.axes.get_title()
    assert "Duration 0.100 s" in ui.status_text.get_text()
    plt.close(ui.figure)


def test_confirm_can_narrow_again_and_reset_returns_to_full_sequence():
    reader = FakeTemperatureReader(make_frames(count=10))
    ui = build_viewer(ViewerSession(reader))
    ui.range_slider.set_val((2, 7))
    ui.confirm_button._observers.process("clicked", None)
    ui.range_slider.set_val((1, 3))
    ui.confirm_button._observers.process("clicked", None)

    assert (ui.session.timeline.source_start, ui.session.timeline.source_end) == (3, 5)

    ui.reset_button._observers.process("clicked", None)

    assert (ui.session.timeline.source_start, ui.session.timeline.source_end) == (0, 9)
    assert ui.range_slider.val == pytest.approx((0, 9))
    plt.close(ui.figure)


def test_invalid_frame_text_restores_valid_value_and_shows_error():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(count=10))))

    ui.start_box.set_val("not-an-integer")
    ui.start_box._observers.process("submit", "not-an-integer")

    assert ui.session.timeline.pending_range == (0, 9)
    assert ui.start_box.text == "0"
    assert ui.error_text.get_text()
    assert ui.error_text.get_color() == "crimson"
    plt.close(ui.figure)


def test_valid_frame_text_updates_range_and_previews_each_endpoint():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(count=10))))

    ui.start_box.set_val("2")
    ui.start_box._observers.process("submit", "2")
    assert ui.session.timeline.pending_range == (2, 9)
    assert ui.session.timeline.current_local_frame == 2

    ui.end_box.set_val("7")
    ui.end_box._observers.process("submit", "7")
    assert ui.session.timeline.pending_range == (2, 7)
    assert ui.session.timeline.current_local_frame == 7
    assert ui.slider.val == pytest.approx(7)
    plt.close(ui.figure)


def test_confirming_single_frame_hides_sliders_until_full_reset():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(count=10))))
    ui.range_slider.set_val((4, 4))

    ui.confirm_button._observers.process("clicked", None)

    assert ui.session.timeline.local_num_frames == 1
    assert ui.slider.ax.get_visible() is False
    assert ui.range_slider.ax.get_visible() is False

    ui.reset_button._observers.process("clicked", None)

    assert ui.slider.ax.get_visible() is True
    assert ui.range_slider.ax.get_visible() is True
    plt.close(ui.figure)


def test_launch_viewer_keeps_ui_alive_while_event_loop_runs(monkeypatch, tmp_path):
    shutdown_calls = []
    class FakeReader:
        path = tmp_path / "sample.seq"
        num_frames = 1
        width = 3
        height = 2

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class ViewerToken:
        def shutdown_analysis(self, *, wait=False):
            shutdown_calls.append(wait)

    token_reference = None

    def fake_build_viewer(_session, initial_frame=0):
        nonlocal token_reference
        token = ViewerToken()
        token_reference = weakref.ref(token)
        return token

    def fake_show():
        gc.collect()
        assert token_reference is not None
        assert token_reference() is not None

    monkeypatch.setattr(viewer, "ScienceFileReader", lambda _path: FakeReader())
    monkeypatch.setattr(viewer, "build_viewer", fake_build_viewer)
    monkeypatch.setattr(viewer.plt, "show", fake_show)

    viewer.launch_viewer(tmp_path / "sample.seq")
    assert shutdown_calls == [True]


def saved_startup_view(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=5), tmp_path / "sample.seq")
    session = ViewerSession(reader, vmin=20, vmax=40, selection_store=SelectionStore(tmp_path))
    session.timeline.set_pending(1, 3)
    session.apply_transform(cropped_transform())
    config = session.save_selection()
    return reader, config, session.selection_document()


def test_startup_without_same_directory_sidecar_does_not_scan_other_configs(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=5), tmp_path / "sample.seq")
    old = ViewerSession(reader, vmin=20, vmax=40, selection_store=SelectionStore(tmp_path / "elsewhere"))
    old.save_selection()
    session = ViewerSession(reader)
    assert session.restore_startup() == (0, ())
    assert session.color_limits is None
    assert reader.requested_frames == []


@pytest.mark.parametrize("initial_frame,local,warn", [(None, 0, False), (2, 1, False), (0, 0, True), (4, 0, True)])
def test_startup_restores_view_and_maps_explicit_source_frame(tmp_path, initial_frame, local, warn):
    reader, config, document = saved_startup_view(tmp_path)
    session = ViewerSession(reader)
    frame, notices = session.restore_startup(initial_frame=initial_frame)
    assert frame == local
    assert session.applied_transform == document.transform
    assert (session.timeline.source_start, session.timeline.source_end) == (1, 3)
    assert session.selection_path == config
    assert str(config) in "\n".join(notices)
    assert ("outside" in "\n".join(notices)) == warn
    assert not session.has_unsaved_changes()
    assert reader.requested_frames == []


@pytest.mark.parametrize("limits,dirty", [((None, None), False), ((20, 40), False), ((21, 41), True)])
def test_startup_cli_temperature_limits_override_without_false_dirty(tmp_path, limits, dirty):
    reader, _, _ = saved_startup_view(tmp_path)
    session = ViewerSession(reader)
    session.restore_startup(vmin=limits[0], vmax=limits[1])
    assert session.color_limits == ColorLimits(*(limits if limits[0] is not None else (20, 40)))
    assert session.has_unsaved_changes() is dirty


@pytest.mark.parametrize("options,error", [({"initial_frame": 5}, IndexError), ({"initial_frame": -1}, IndexError),
    ({"vmin": 20}, ValueError), ({"vmin": float("nan"), "vmax": 40}, ValueError),
    ({"vmin": 20, "vmax": float("inf")}, ValueError), ({"vmin": 40, "vmax": 20}, ValueError)])
def test_startup_rejects_invalid_cli_before_loading_config(tmp_path, monkeypatch, options, error):
    reader, _, _ = saved_startup_view(tmp_path)
    session = ViewerSession(reader)
    before = session.state_signature()
    def never_load(*_):
        raise AssertionError("invalid CLI must be rejected first")
    monkeypatch.setattr(session.selection_store, "load_path", never_load)
    with pytest.raises(error):
        session.restore_startup(**options)
    assert session.state_signature() == before


@pytest.mark.parametrize("invalid", ["json", "identity", "colormap", "transform"])
def test_startup_bad_sidecar_warns_and_keeps_original_view(tmp_path, invalid):
    reader, config, document = saved_startup_view(tmp_path)
    payload = document.to_dict()
    if invalid == "identity":
        payload["source"]["size_bytes"] += 1
    elif invalid == "colormap":
        payload["display"]["colormap"] = "missing-colormap"
    elif invalid == "transform":
        payload["transform"]["crop"]["width"] = 999
    config.write_text("{" if invalid == "json" else json.dumps(payload), encoding="utf-8")
    session = ViewerSession(reader)
    before = session.state_signature()
    local, notices = session.restore_startup()
    assert local == 0 and notices
    assert str(config) in "\n".join(notices)
    assert session.state_signature() == before
    assert session.selection_path is None


@pytest.mark.parametrize("content", ["", "untrusted paths"])
def test_startup_pending_blocks_auto_and_manual_loading(tmp_path, monkeypatch, content):
    reader, config, document = saved_startup_view(tmp_path)
    marker = reader.path.with_suffix(".export-pending.json")
    marker.write_text(content)
    session = ViewerSession(reader)
    before = session.state_signature()
    def never_load(*_):
        raise AssertionError("pending must block before reading config")
    monkeypatch.setattr(session.selection_store, "load_path", never_load)
    local, notices = session.restore_startup()
    assert local == 0 and "pending" in "\n".join(notices)
    with pytest.raises(RuntimeError, match="pending"):
        session.load_selection(config)
    with pytest.raises(RuntimeError, match="pending"):
        session.apply_selection_document(document, path=config)
    assert session.state_signature() == before


@pytest.mark.parametrize("version", [1, 2, 3])
def test_startup_launch_restores_before_first_draw_and_does_not_reload(tmp_path, monkeypatch, version):
    from contextlib import nullcontext
    reader, config, document = saved_startup_view(tmp_path)
    if version < 3:
        payload = document.to_dict()
        payload["schema_version"] = version
        del payload["measurement"]
        if version == 1:
            del payload["transform"]
        config.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(viewer, "ScienceFileReader", lambda _: nullcontext(reader))
    original_build = viewer.build_viewer
    def build(session, initial_frame):
        assert reader.requested_frames == []
        assert session.selection_path == config
        ui = original_build(session, initial_frame=initial_frame)
        assert reader.requested_frames == [1]
        expected = FrameTransform.identity(3, 2) if version == 1 else document.transform
        assert session.applied_transform == expected
        assert ui.image.get_clim() == (20, 40)
        config.write_text("now invalid", encoding="utf-8")
        ui.slider.set_val(1)
        ui.reset_button._observers.process("clicked", None)
        assert session.applied_transform == expected
        plt.close(ui.figure)
        return ui
    monkeypatch.setattr(viewer, "build_viewer", build)
    monkeypatch.setattr(viewer.plt, "show", lambda: None)
    viewer.launch_viewer(reader.path)


def test_session_saves_current_candidate_as_source_range(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=10), tmp_path / "sample.seq")
    store = SelectionStore(tmp_path / "selections")
    session = ViewerSession(reader, selection_store=store)
    session.frame_at(0)
    session.timeline.set_pending(2, 7)

    target = session.save_selection()
    saved = store.load(reader.path)

    assert target == tmp_path / "selections" / "sample.selection.json"
    assert saved.selection.source_start_frame == 2
    assert saved.selection.source_end_frame == 7
    assert saved.selection.frame_count == 6


def test_selection_document_contains_applied_transform(tmp_path):
    reader = FileBackedFakeReader(make_frames(), tmp_path / "sample.seq")
    session = ViewerSession(
        reader,
        selection_store=SelectionStore(tmp_path / "selections"),
    )
    session.frame_at(0)
    transform = cropped_transform()
    session.apply_transform(transform)

    assert session.selection_document().transform == transform


def test_save_selection_clears_dirty_only_after_success(tmp_path, monkeypatch):
    reader = FileBackedFakeReader(make_frames(), tmp_path / "sample.seq")
    store = SelectionStore(tmp_path / "selections")
    session = ViewerSession(reader, selection_store=store)
    session.frame_at(0)
    session.apply_transform(cropped_transform())

    session.save_selection()

    assert session.selection_dirty is False
    session.apply_transform(FrameTransform.identity(3, 2))
    assert session.selection_dirty is True

    def fail_save(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(store, "save", fail_save)
    with pytest.raises(OSError, match="disk full"):
        session.save_selection()

    assert session.selection_dirty is True


def test_load_selection_applies_transform_and_clears_dirty(tmp_path):
    reader = FileBackedFakeReader(make_frames(), tmp_path / "sample.seq")
    store = SelectionStore(tmp_path / "selections")
    session = ViewerSession(reader, selection_store=store)
    session.frame_at(0)
    saved_transform = cropped_transform()
    session.apply_transform(saved_transform)
    session.save_selection()
    session.apply_transform(FrameTransform.identity(3, 2))

    loaded = session.load_selection()

    assert loaded.transform == saved_transform
    assert session.applied_transform == saved_transform
    assert session.selection_dirty is False
    assert np.array_equal(
        session.frame_at(0).temperature_c,
        reader.frames[0].temperature_c[:, 1:],
    )


def test_load_selection_rejects_invalid_display_without_partial_mutation(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=10), tmp_path / "sample.seq")
    store = SelectionStore(tmp_path / "selections")
    session = ViewerSession(reader, selection_store=store)
    session.frame_at(0)
    session.timeline.set_pending(2, 7)
    session.apply_transform(cropped_transform())
    session.save_selection()
    session.apply_transform(FrameTransform.identity(3, 2))
    session.frame_at(0)
    target = store.path_for(reader.path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["display"]["colormap"] = "not-a-real-colormap"
    target.write_text(json.dumps(payload), encoding="utf-8")
    before = (
        session.timeline.source_start,
        session.timeline.source_end,
        session.timeline.pending_range,
        session.color_limits,
        session.colormap,
        session.applied_transform,
        session.frame_transformer,
        len(session.transformed_cache),
        session.selection_dirty,
    )

    with pytest.raises(ValueError, match="unknown colormap"):
        session.load_selection()

    after = (
        session.timeline.source_start,
        session.timeline.source_end,
        session.timeline.pending_range,
        session.color_limits,
        session.colormap,
        session.applied_transform,
        session.frame_transformer,
        len(session.transformed_cache),
        session.selection_dirty,
    )
    assert after == before


def test_session_load_validates_source_before_mutating(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=10), tmp_path / "sample.seq")
    store = SelectionStore(tmp_path / "selections")
    session = ViewerSession(reader, selection_store=store)
    session.frame_at(0)
    session.timeline.set_pending(2, 7)
    session.save_selection()
    target = store.path_for(reader.path)
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["source"]["size_bytes"] += 1
    target.write_text(json.dumps(payload), encoding="utf-8")
    before = (
        session.timeline.source_start,
        session.timeline.source_end,
        session.timeline.pending_range,
        session.color_limits,
        session.colormap,
    )

    with pytest.raises(ValueError, match="size_bytes"):
        session.load_selection()

    after = (
        session.timeline.source_start,
        session.timeline.source_end,
        session.timeline.pending_range,
        session.color_limits,
        session.colormap,
    )
    assert after == before


def test_save_and_load_buttons_restore_confirmed_range_and_display(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=10), tmp_path / "sample.seq")
    store = SelectionStore(tmp_path / "selections")
    session = ViewerSession(reader, selection_store=store)
    target = store.path_for(reader.path)
    dialogs = FakeSelectionDialogs(save=target, load=target)
    ui = build_viewer(session, dialogs=dialogs)
    try:
        ui.range_slider.set_val((2, 7))
        ui.save_button._observers.process("clicked", None)
        assert "Saved selection" in ui.error_text.get_text()
        assert ui.error_text.get_color() == "forestgreen"
        session.timeline.reset_full()
        session.colormap = "viridis"
        session.color_limits = ColorLimits(50.0, 60.0)
        ui.image.set_cmap("viridis")
        ui.image.set_clim(50.0, 60.0)
        ui.load_button._observers.process("clicked", None)
        assert len(dialogs.confirmations) == 1
        assert (session.timeline.source_start, session.timeline.source_end) == (2, 7)
        assert session.timeline.pending_range == (0, 5)
        assert session.timeline.current_local_frame == 0
        assert session.colormap == "inferno"
        assert ui.image.get_clim() == pytest.approx((1.05, 5.95))
        assert "Loaded selection" in ui.error_text.get_text()
        assert ui.error_text.get_color() == "forestgreen"
    finally:
        plt.close(ui.figure)


def test_dirty_session_requires_explicit_confirmation(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(reader, selection_store=SelectionStore(tmp_path / "selections"))
    dialogs = FakeSelectionDialogs(load=session.selection_store.path_for(reader.path), answer=False)
    ui = build_viewer(session, dialogs=dialogs)
    try:
        session.save_selection()
        session.apply_transform(session.applied_transform.with_angle(10.0))
        ui.load_button._observers.process("clicked", None)
        assert session.applied_transform.rotation.angle_deg == 10.0
        assert "range, color scale, colormap, and transform" in dialogs.confirmations[0]
        assert session.selection_dirty
        dialogs.answer = True
        ui.load_button._observers.process("clicked", None)
        assert session.applied_transform.rotation.angle_deg == 0.0
        assert not session.selection_dirty
        assert ui.load_button.label.get_text() == "Load selection"
    finally:
        plt.close(ui.figure)


def test_load_cancel_keeps_all_state_and_does_not_confirm(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(reader)
    dialogs = FakeSelectionDialogs()
    ui = build_viewer(session, dialogs=dialogs)
    try:
        session.apply_transform(session.applied_transform.with_angle(10))
        before = (session.state_signature(), session.selection_path, session.selection_dirty)
        ui.load_button._observers.process("clicked", None)
        assert (session.state_signature(), session.selection_path, session.selection_dirty) == before
        assert not dialogs.confirmations
    finally:
        plt.close(ui.figure)


@pytest.mark.parametrize("kind", ["invalid-json", "invalid-colormap", "wrong-source"])
def test_invalid_load_never_confirms_or_mutates(tmp_path, kind):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(reader)
    target = tmp_path / "broken.selection.json"
    dialogs = FakeSelectionDialogs(load=target)
    ui = build_viewer(session, dialogs=dialogs)
    try:
        payload = session.selection_document().to_dict()
        if kind == "invalid-colormap":
            payload["display"]["colormap"] = "does-not-exist"
        elif kind == "wrong-source":
            payload["source"]["filename"] = "other.seq"
        target.write_text("{" if kind == "invalid-json" else json.dumps(payload), encoding="utf-8")
        session.selection_dirty = True
        before = session.state_signature()
        ui.load_button._observers.process("clicked", None)
        assert session.state_signature() == before and session.selection_dirty
        assert session.selection_path is None
        assert not dialogs.confirmations
        assert "Load selection failed" in ui.error_text.get_text()
        assert str(target) in ui.message_details["message"]
    finally:
        plt.close(ui.figure)


@pytest.mark.parametrize("kind", ["refuse", "external-change", "write-error"])
def test_save_overwrite_keeps_file_and_session_on_failure(tmp_path, monkeypatch, kind):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(reader, selection_store=SelectionStore(tmp_path / "selections"))
    target = session.selection_store.path_for(reader.path)
    dialogs = FakeSelectionDialogs(save=target)
    ui = build_viewer(session, dialogs=dialogs)
    try:
        session.save_selection()
        before_bytes = target.read_bytes()
        ui.range_slider.set_val((0, 1))
        before = session.state_signature()
        if kind == "refuse":
            dialogs.answer = False
        elif kind == "external-change":
            def change_during_confirmation(message):
                target.write_bytes(b"external")
                return True
            dialogs.confirm = change_during_confirmation
        else:
            def fail_save(*args, **kwargs):
                raise OSError("disk full")
            monkeypatch.setattr(session, "save_selection", fail_save)
        ui.save_button._observers.process("clicked", None)
        assert session.selection_dirty
        assert session.state_signature() == before
        assert target.read_bytes() == (b"external" if kind == "external-change" else before_bytes)
        if kind != "refuse":
            assert "Save selection failed" in ui.error_text.get_text()
            assert str(target) in ui.message_details["message"]
    finally:
        plt.close(ui.figure)


def test_load_dialog_backend_error_is_reported(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(reader)
    dialogs = FakeSelectionDialogs()
    def fail_choose(initial_dir):
        raise RuntimeError("backend unavailable")
    dialogs.choose_load = fail_choose
    ui = build_viewer(session, dialogs=dialogs)
    try:
        before = session.state_signature()
        ui.load_button._observers.process("clicked", None)
        assert session.state_signature() == before
        assert "backend unavailable" in ui.message_details["message"]
    finally:
        plt.close(ui.figure)


def test_f1_sends_untruncated_message_to_dialog(tmp_path):
    dialogs = FakeSelectionDialogs()
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames())), dialogs=dialogs)
    try:
        message = "D:/" + "long-path/" * 100
        ui.error_text.set_text(message)
        ui.figure.canvas.draw()
        ui.figure.canvas.callbacks.process("key_press_event", KeyEvent("key_press_event", ui.figure.canvas, key="f1"))
        assert message in dialogs.details[0]
        assert ui.message_details["message"] == message
    finally:
        plt.close(ui.figure)


def test_f1_dialog_failure_remains_bounded_and_prints_cause(capsys):
    dialogs = FakeSelectionDialogs()
    failure = "unavailable " * 100
    def fail_details(message):
        raise RuntimeError(failure)
    dialogs.show_details = fail_details
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames())), dialogs=dialogs)
    try:
        ui.figure.set_size_inches(10, 8)
        ui.figure.canvas.callbacks.process("key_press_event", KeyEvent("key_press_event", ui.figure.canvas, key="f1"))
        assert failure in capsys.readouterr().out
        renderer = ui.figure.canvas.get_renderer()
        for text in ui.figure.texts:
            if text.get_visible() and text.get_text():
                box = text.get_window_extent(renderer)
                assert ui.figure.bbox.contains(box.x1, box.y1)
    finally:
        plt.close(ui.figure)


def test_native_export_request_freezes_view_without_changing_native_range(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(reader)
    session.apply_transform(session.applied_transform.with_angle(10.0))
    session.frame_at(0)

    request = session.export_request("raw_clip", tmp_path / "clips")

    frozen = request.selection_document
    assert frozen.transform == session.applied_transform
    session.apply_transform(FrameTransform.identity(reader.width, reader.height))
    assert request.selection_document == frozen
    assert request.selection_document.transform != session.applied_transform
    assert request.sidecar_snapshot.exists is False
    assert request.source_start_frame == 0
    assert request.source_end_frame == 2


class FakeExportJob:
    def __init__(self, request) -> None:
        self.request = request
        self.progress = ExportProgress()
        self.result = None
        self.started = False
        self.cancelled = False
        self.alive = False
        self.joined_timeout = None

    def start(self):
        self.started = True
        self.alive = True
        self.progress.update(
            status=ExportStatus.RUNNING, current=1, total=self.request.frame_count
        )

    def is_alive(self):
        return self.alive

    def cancel(self):
        self.cancelled = True

    def join(self, timeout=None):
        self.joined_timeout = timeout

    def finish(self, status=ExportStatus.SUCCEEDED):
        self.alive = False
        self.result = ExportResult(status, self.request.target_path, status.value)
        self.progress.update(
            status=status,
            current=self.request.frame_count,
            total=self.request.frame_count,
            message=status.value,
        )


def test_export_first_click_arms_second_click_and_range_change_invalidates(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=10), tmp_path / "sample.seq")
    session = ViewerSession(reader, selection_store=SelectionStore(tmp_path / "selections"))
    jobs = []
    ui = build_viewer(
        session,
        clips_root=tmp_path / "clips",
        export_job_factory=lambda request: jobs.append(FakeExportJob(request)) or jobs[-1],
    )
    ui.clip_name_box.set_val("useful")

    ui.export_button._observers.process("clicked", None)

    assert ui.export_button.label.get_text() == "Confirm export"
    assert "Target:" in ui.export_text.get_text()
    assert jobs == []

    ui.range_slider.set_val((2, 7))

    assert ui.export_button.label.get_text() == "Export sub-SEQ"
    assert ui.export_state.confirmation is None
    plt.close(ui.figure)


def test_export_second_click_starts_job_locks_controls_and_poll_restores(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=10), tmp_path / "sample.seq")
    session = ViewerSession(reader, selection_store=SelectionStore(tmp_path / "selections"))
    jobs = []
    ui = build_viewer(
        session,
        clips_root=tmp_path / "clips",
        export_job_factory=lambda request: jobs.append(FakeExportJob(request)) or jobs[-1],
    )
    ui.clip_name_box.set_val("useful")
    ui.range_slider.set_val((2, 7))
    ui.export_button._observers.process("clicked", None)
    ui.export_button._observers.process("clicked", None)

    assert len(jobs) == 1 and jobs[0].started
    assert ui.range_slider.active is False
    assert ui.clip_name_box.active is False
    assert ui.save_button.active is False
    assert ui.cancel_button.active is True

    jobs[0].finish()
    ui.poll_export()

    assert ui.range_slider.active is True
    assert ui.clip_name_box.active is True
    assert ui.save_button.active is True
    assert ui.cancel_button.active is False
    assert "succeeded" in ui.export_text.get_text()
    plt.close(ui.figure)


def test_running_export_refuses_transform_editing(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(reader)
    session.frame_at(0)
    job = FakeExportJob(session.export_request("useful", tmp_path / "clips"))
    ui = build_viewer(session)
    ui.export_state.job = job
    job.start()

    ui.edit_transform_button._observers.process("clicked", None)

    assert ui.mode_state.editing_transform is False
    assert "export" in ui.error_text.get_text().lower()
    plt.close(ui.figure)


def test_publishing_disables_cancel_and_shows_finalization_status(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(reader)
    ui = build_viewer(session)
    try:
        job = FakeExportJob(session.export_request("useful", tmp_path / "clips"))
        ui.export_state.job = job
        job.start()
        job.progress.update(message="Finalizing paired files; please wait", cancel_allowed=False)
        job.cancel = lambda: False
        ui.poll_export()
        assert not ui.cancel_button.active
        assert "Finalizing paired files" in ui.message_details["progress"]
        ui.cancel_button._observers.process("clicked", None)
        assert "Cancellation requested" not in ui.export_text.get_text()
    finally:
        plt.close(ui.figure)


def test_transform_editing_blocks_all_normal_mode_handlers_but_apply_works(
    tmp_path, monkeypatch
):
    reader = FileBackedFakeReader(make_frames(count=4), tmp_path / "sample.seq")
    session = ViewerSession(
        reader, selection_store=SelectionStore(tmp_path / "selections")
    )
    jobs = []
    ui = build_viewer(
        session,
        clips_root=tmp_path / "clips",
        export_job_factory=lambda request: jobs.append(FakeExportJob(request))
        or jobs[-1],
    )
    ui.range_slider.set_val((1, 2))
    session.selection_dirty = False
    before = (
        session.timeline.source_start,
        session.timeline.source_end,
        session.timeline.pending_range,
        session.timeline.current_local_frame,
    )
    save_calls = []
    load_calls = []
    monkeypatch.setattr(session, "save_selection", lambda: save_calls.append(True))
    monkeypatch.setattr(session, "load_selection", lambda: load_calls.append(True))

    ui.edit_transform_button._observers.process("clicked", None)
    ui.range_slider.set_val((0, 3))
    ui.start_box._observers.process("submit", "0")
    ui.end_box._observers.process("submit", "3")
    ui.confirm_button._observers.process("clicked", None)
    ui.reset_button._observers.process("clicked", None)
    ui.save_button._observers.process("clicked", None)
    ui.load_button._observers.process("clicked", None)
    ui.export_button._observers.process("clicked", None)
    ui.export_button._observers.process("clicked", None)

    assert (
        session.timeline.source_start,
        session.timeline.source_end,
        session.timeline.pending_range,
        session.timeline.current_local_frame,
    ) == before
    assert save_calls == []
    assert load_calls == []
    assert jobs == []
    assert session.selection_dirty is False
    assert ui.mode_state.editing_transform is True
    assert "transform" in ui.error_text.get_text().lower()

    ui.transform_editor.set_angle(10.0)
    ui.transform_editor.apply_button._observers.process("clicked", None)

    assert session.applied_transform.rotation.angle_deg == 10.0
    assert ui.mode_state.editing_transform is False
    plt.close(ui.figure)


def test_cancel_button_requests_running_export_cancel(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=10), tmp_path / "sample.seq")
    session = ViewerSession(reader, selection_store=SelectionStore(tmp_path / "selections"))
    jobs = []
    ui = build_viewer(
        session,
        clips_root=tmp_path / "clips",
        export_job_factory=lambda request: jobs.append(FakeExportJob(request)) or jobs[-1],
    )
    ui.clip_name_box.set_val("useful")
    ui.export_button._observers.process("clicked", None)
    ui.export_button._observers.process("clicked", None)

    ui.cancel_button._observers.process("clicked", None)

    assert jobs[0].cancelled is True
    assert "Cancellation requested" in ui.export_text.get_text()
    plt.close(ui.figure)


def test_window_close_requests_cancel_and_performs_bounded_join(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=10), tmp_path / "sample.seq")
    session = ViewerSession(reader, selection_store=SelectionStore(tmp_path / "selections"))
    jobs = []
    ui = build_viewer(
        session,
        clips_root=tmp_path / "clips",
        export_job_factory=lambda request: jobs.append(FakeExportJob(request)) or jobs[-1],
    )
    ui.clip_name_box.set_val("useful")
    ui.export_button._observers.process("clicked", None)
    ui.export_button._observers.process("clicked", None)

    ui.figure.canvas.callbacks.process(
        "close_event", CloseEvent("close_event", ui.figure.canvas)
    )

    assert jobs[0].cancelled is True
    assert jobs[0].joined_timeout == pytest.approx(0.25)
    plt.close(ui.figure)
