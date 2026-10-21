from datetime import datetime, timedelta
import gc
import json
import weakref

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


def test_resubmitting_unchanged_endpoint_preserves_pending_load_confirmation(
    tmp_path,
):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(
        reader, selection_store=SelectionStore(tmp_path / "selections")
    )
    ui = build_viewer(session)
    session.save_selection()
    ui.range_slider.set_val((0, 1))
    ui.load_button._observers.process("clicked", None)
    confirmation = ui.mode_state.load_confirmation_signature
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
    assert ui.mode_state.load_confirmation_signature == confirmation
    assert ui.load_button.label.get_text() == "Confirm load"
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
    class FakeReader:
        num_frames = 1
        width = 3
        height = 2

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class ViewerToken:
        pass

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
    ui = build_viewer(session)
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

    assert ui.load_button.label.get_text() == "Confirm load"
    assert (session.timeline.source_start, session.timeline.source_end) == (0, 9)

    ui.load_button._observers.process("clicked", None)

    assert (session.timeline.source_start, session.timeline.source_end) == (2, 7)
    assert session.timeline.pending_range == (0, 5)
    assert session.timeline.current_local_frame == 0
    assert session.colormap == "inferno"
    assert ui.image.get_clim() == pytest.approx((1.05, 5.95))
    assert "Loaded selection" in ui.error_text.get_text()
    assert ui.error_text.get_color() == "forestgreen"
    plt.close(ui.figure)


def test_dirty_session_requires_second_load_click(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(
        reader, selection_store=SelectionStore(tmp_path / "selections")
    )
    ui = build_viewer(session)
    session.save_selection()
    session.apply_transform(session.applied_transform.with_angle(10.0))

    ui.load_button._observers.process("clicked", None)
    assert ui.load_button.label.get_text() == "Confirm load"
    assert session.applied_transform.rotation.angle_deg == 10.0
    assert "range, color scale, colormap, and transform" in ui.error_text.get_text()

    ui.load_button._observers.process("clicked", None)
    assert session.applied_transform.rotation.angle_deg == 0.0
    assert session.selection_dirty is False
    assert ui.load_button.label.get_text() == "Load selection"
    plt.close(ui.figure)


def test_state_change_invalidates_load_confirmation_but_browsing_does_not(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(
        reader, selection_store=SelectionStore(tmp_path / "selections")
    )
    ui = build_viewer(session)
    session.save_selection()
    ui.range_slider.set_val((0, 1))
    ui.load_button._observers.process("clicked", None)
    signature = ui.mode_state.load_confirmation_signature

    ui.slider.set_val(1)
    assert ui.mode_state.load_confirmation_signature == signature

    ui.range_slider.set_val((1, 1))
    assert ui.mode_state.load_confirmation_signature is None
    assert ui.load_button.label.get_text() == "Load selection"
    plt.close(ui.figure)


def test_save_clears_dirty_and_pending_load_confirmation(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(
        reader, selection_store=SelectionStore(tmp_path / "selections")
    )
    ui = build_viewer(session)
    ui.range_slider.set_val((0, 1))
    ui.load_button._observers.process("clicked", None)

    ui.save_button._observers.process("clicked", None)

    assert session.selection_dirty is False
    assert ui.mode_state.load_confirmation_signature is None
    assert ui.load_button.label.get_text() == "Load selection"
    assert "Transform: applied" in ui.transform_status_text.get_text()
    plt.close(ui.figure)


def test_save_failure_invalidates_pending_load_confirmation(tmp_path, monkeypatch):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(
        reader, selection_store=SelectionStore(tmp_path / "selections")
    )
    ui = build_viewer(session)
    session.save_selection()
    ui.range_slider.set_val((0, 1))
    ui.load_button._observers.process("clicked", None)
    assert ui.mode_state.load_confirmation_signature is not None

    def fail_save():
        raise OSError("disk full")

    monkeypatch.setattr(session, "save_selection", fail_save)
    ui.save_button._observers.process("clicked", None)

    assert session.selection_dirty is True
    assert ui.mode_state.load_confirmation_signature is None
    assert ui.load_button.label.get_text() == "Load selection"
    assert "Save selection failed: disk full" in ui.error_text.get_text()
    plt.close(ui.figure)


def test_native_export_request_ignores_applied_transform(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(reader)
    session.apply_transform(session.applied_transform.with_angle(10.0))

    request = session.export_request("raw_clip", tmp_path / "clips")

    assert not hasattr(request, "transform")
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
    job = FakeExportJob(session.export_request("useful", tmp_path / "clips"))
    ui = build_viewer(session)
    ui.export_state.job = job
    job.start()

    ui.edit_transform_button._observers.process("clicked", None)

    assert ui.mode_state.editing_transform is False
    assert "export" in ui.error_text.get_text().lower()
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
