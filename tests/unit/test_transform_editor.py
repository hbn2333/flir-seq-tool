from dataclasses import fields, replace

import matplotlib
import pytest

matplotlib.use("Agg", force=True)
from matplotlib import pyplot as plt
from matplotlib.backend_bases import KeyEvent, MouseEvent

from flir_seq_tool.transform import CropRect, FrameTransform
from flir_seq_tool.transform_editor import (
    TransformDraft,
    TransformEditorUI,
    build_transform_editor,
)


def test_draft_starts_from_applied_and_cancel_does_not_mutate_it():
    applied = FrameTransform.identity(8, 6).with_angle(10.0)
    original_crop = applied.crop
    draft = TransformDraft.from_applied(applied)

    draft.set_crop(CropRect(applied.crop.x, applied.crop.y, 2, 2))

    assert draft.cancel() == applied
    assert applied.crop == original_crop


def test_angle_change_resets_crop_to_maximum_valid_area():
    draft = TransformDraft.from_applied(FrameTransform.identity(8, 6))
    draft.set_crop(CropRect(1, 1, 3, 2))

    draft.set_angle(10.0)

    expected = FrameTransform.identity(8, 6).with_angle(10.0)
    assert draft.angle_deg == expected.rotation.angle_deg
    assert draft.crop == expected.crop
    assert draft.message == "Rotation changed — crop reset"


def test_invalid_crop_keeps_draft_visible_but_blocks_build():
    draft = TransformDraft.from_applied(
        FrameTransform.identity(8, 6).with_angle(10.0)
    )
    invalid_crop = CropRect(0, 0, 8, 6)

    draft.set_crop(invalid_crop)

    assert draft.crop == invalid_crop
    assert draft.is_valid is False
    assert draft.message == "Crop includes invalid pixels"
    with pytest.raises(ValueError, match="crop"):
        draft.build()


def test_valid_crop_clears_message():
    draft = TransformDraft.from_applied(
        FrameTransform.identity(8, 6).with_angle(10.0)
    )

    draft.set_crop(draft.crop)

    assert draft.is_valid is True
    assert draft.message == ""


def test_reset_returns_identity_without_mutating_applied():
    applied = FrameTransform.identity(8, 6).with_angle(10.0)
    draft = TransformDraft.from_applied(applied)

    draft.reset()

    assert draft.build() == FrameTransform.identity(8, 6)
    assert draft.message == "Draft reset"
    assert applied.rotation.angle_deg == 10.0


def test_invalid_angle_leaves_draft_unchanged():
    draft = TransformDraft.from_applied(FrameTransform.identity(8, 6))
    draft.set_crop(CropRect(1, 1, 3, 2))
    before = (draft.angle_deg, draft.crop, draft.message)

    with pytest.raises(ValueError, match="within 15 degrees"):
        draft.set_angle(20.0)

    assert (draft.angle_deg, draft.crop, draft.message) == before


def test_equivalent_angle_is_no_op_after_validation():
    draft = TransformDraft.from_applied(FrameTransform.identity(8, 6))
    draft.set_crop(CropRect(1, 1, 3, 2))
    draft.message = "Keep current draft"
    before = (draft.angle_deg, draft.crop, draft.message)

    draft.set_angle(-0.0)

    assert (draft.angle_deg, draft.crop, draft.message) == before


def test_from_applied_requires_frame_transform():
    with pytest.raises(ValueError, match="FrameTransform"):
        TransformDraft.from_applied(object())


@pytest.mark.parametrize(
    ("angle_deg", "crop", "match"),
    [
        ("0.0", CropRect(0, 0, 8, 6), "angle_deg"),
        (0.0, object(), "crop"),
    ],
)
def test_direct_construction_rejects_invalid_field_types(angle_deg, crop, match):
    with pytest.raises(ValueError, match=match):
        TransformDraft(FrameTransform.identity(8, 6), angle_deg, crop)


def test_direct_invalid_draft_sets_crop_message_when_message_is_empty():
    draft = TransformDraft(
        FrameTransform.identity(8, 6),
        10.0,
        CropRect(0, 0, 8, 6),
    )

    assert draft.is_valid is False
    assert draft.message == "Crop includes invalid pixels"


def test_direct_invalid_draft_preserves_explicit_message():
    draft = TransformDraft(
        FrameTransform.identity(8, 6),
        10.0,
        CropRect(0, 0, 8, 6),
        message="Imported invalid draft",
    )

    assert draft.is_valid is False
    assert draft.message == "Imported invalid draft"


def _build_editor(
    initial: FrameTransform | None = None,
    *,
    previews: list[FrameTransform] | None = None,
    applied: list[FrameTransform] | None = None,
    cancelled: list[bool] | None = None,
) -> TransformEditorUI:
    figure, axes = plt.subplots()
    axes.set_xlim(-0.5, 7.5)
    axes.set_ylim(5.5, -0.5)
    preview_values = previews if previews is not None else []
    applied_values = applied if applied is not None else []
    cancelled_values = cancelled if cancelled is not None else []
    return build_transform_editor(
        figure,
        axes,
        initial or FrameTransform.identity(8, 6),
        preview_callback=preview_values.append,
        apply_callback=applied_values.append,
        cancel_callback=lambda: cancelled_values.append(True),
    )


def test_editor_builds_required_widgets_without_firing_callbacks():
    previews: list[FrameTransform] = []
    applied: list[FrameTransform] = []
    cancelled: list[bool] = []

    editor = _build_editor(
        previews=previews,
        applied=applied,
        cancelled=cancelled,
    )

    assert isinstance(editor, TransformEditorUI)
    assert set(editor.crop_boxes) == {"x", "y", "width", "height"}
    assert editor.selector.active is True
    assert previews == []
    assert applied == []
    assert cancelled == []
    plt.close(editor.figure)


def test_editor_callbacks_are_dataclass_fields_preserved_by_reconstruction():
    previews: list[FrameTransform] = []
    editor = _build_editor(previews=previews)

    callback_fields = {
        item.name: item for item in fields(TransformEditorUI)
    }
    for name in ("preview_callback", "apply_callback", "cancel_callback"):
        assert name in callback_fields
        assert callback_fields[name].repr is False

    reconstructed = replace(editor)
    reconstructed._publish_preview()

    assert previews == [FrameTransform.identity(8, 6)]
    plt.close(editor.figure)


def test_editor_angle_controls_reject_extra_precision_then_sync_valid_value():
    previews: list[FrameTransform] = []
    editor = _build_editor(previews=previews)

    editor.angle_box.set_val("10.04")

    assert editor.draft.angle_deg == 0.0
    assert editor.angle_box.text == "0.0"
    assert editor.error_text.get_text()
    assert editor.error_text.get_color() == "crimson"
    assert previews == []

    editor.angle_box.set_val("10.0")

    expected = FrameTransform.identity(8, 6).with_angle(10.0)
    assert editor.draft.angle_deg == 10.0
    assert editor.angle_slider.val == pytest.approx(10.0)
    assert editor.draft.crop == expected.crop
    assert previews == [expected]
    assert editor.error_text.get_text() == ""
    plt.close(editor.figure)


def test_invalid_crop_uses_public_selector_props_disables_apply_and_previews_rotation(
    monkeypatch,
):
    previews: list[FrameTransform] = []
    initial = FrameTransform.identity(8, 6).with_angle(10.0)
    editor = _build_editor(initial, previews=previews)

    prop_updates: list[dict[str, str]] = []
    monkeypatch.setattr(
        editor.selector,
        "set_props",
        lambda **props: prop_updates.append(props),
    )

    editor.set_crop(CropRect(0, 0, 8, 6))

    assert editor.draft.is_valid is False
    assert editor.apply_button.active is False
    assert prop_updates[-1] == {"edgecolor": "crimson"}
    assert editor.status_text.get_text() == "Crop includes invalid pixels"
    assert previews == [initial]
    plt.close(editor.figure)


@pytest.mark.parametrize(
    ("button_name", "expected"),
    [
        ("left_button", 100.0),
        ("right_button", -80.0),
        ("half_turn_button", -170.0),
        ("zero_button", 0.0),
    ],
)
def test_quick_rotation_buttons_use_total_angle(button_name, expected):
    editor = _build_editor(FrameTransform.identity(8, 6).with_angle(10.0))

    getattr(editor, button_name)._observers.process("clicked", None)

    assert editor.draft.angle_deg == expected
    plt.close(editor.figure)


def test_slider_replaces_fine_angle_around_current_cardinal_direction():
    previews: list[FrameTransform] = []
    editor = _build_editor(
        FrameTransform.identity(8, 6).with_angle(100.0),
        previews=previews,
    )

    editor.angle_slider.set_val(-4.2)

    assert editor.draft.angle_deg == pytest.approx(85.8)
    assert editor.angle_box.text == "85.8"
    assert previews[-1].rotation.angle_deg == pytest.approx(85.8)
    plt.close(editor.figure)


def test_maximum_reset_cancel_and_apply_callbacks_are_transactional():
    previews: list[FrameTransform] = []
    applied: list[FrameTransform] = []
    cancelled: list[bool] = []
    initial = FrameTransform.identity(8, 6).with_angle(10.0)
    editor = _build_editor(
        initial,
        previews=previews,
        applied=applied,
        cancelled=cancelled,
    )
    editor.set_crop(CropRect(initial.crop.x, initial.crop.y, 2, 2))

    editor.maximum_button._observers.process("clicked", None)
    assert editor.draft.crop == initial.crop

    editor.reset_button._observers.process("clicked", None)
    assert editor.draft.build() == FrameTransform.identity(8, 6)

    editor.cancel_button._observers.process("clicked", None)
    assert cancelled == [True]
    assert applied == []

    editor.apply_button._observers.process("clicked", None)
    assert applied == [FrameTransform.identity(8, 6)]
    assert previews[-1] == FrameTransform.identity(8, 6)
    plt.close(editor.figure)


def test_invalid_apply_does_not_call_callback():
    applied: list[FrameTransform] = []
    initial = FrameTransform.identity(8, 6).with_angle(10.0)
    editor = _build_editor(initial, applied=applied)
    editor.set_crop(CropRect(0, 0, 8, 6))

    editor.apply_button._observers.process("clicked", None)

    assert applied == []
    plt.close(editor.figure)


@pytest.mark.parametrize(
    "extents",
    [
        (1.0, 4.0, 1.0, 4.0),
        (4.0, 1.0, 4.0, 1.0),
        (1.0 + 1e-14, 4.0 - 1e-14, 1.0 + 1e-14, 4.0 - 1e-14),
    ],
)
def test_selector_exact_ties_expand_outward_in_both_drag_directions(extents):
    editor = _build_editor()
    editor.selector.extents = extents

    editor.selector.onselect(None, None)

    assert editor.draft.crop == CropRect(1, 1, 4, 4)
    plt.close(editor.figure)


def test_selector_rounds_pixel_edges_and_uses_shared_crop_path():
    previews: list[FrameTransform] = []
    editor = _build_editor(previews=previews)
    editor.selector.extents = (0.48, 4.51, 0.49, 3.52)

    editor.selector.onselect(None, None)

    assert editor.draft.crop == CropRect(1, 1, 4, 3)
    assert previews[-1].crop == CropRect(1, 1, 4, 3)
    plt.close(editor.figure)


def _mouse_event(editor, name: str, xdata: float, ydata: float) -> MouseEvent:
    x, y = editor.image_axes.transData.transform((xdata, ydata))
    return MouseEvent(
        name,
        editor.figure.canvas,
        x,
        y,
        button=1,
    )


def test_disabled_apply_ignores_real_canvas_button_click():
    applied: list[FrameTransform] = []
    editor = _build_editor(
        FrameTransform.identity(8, 6).with_angle(10.0),
        applied=applied,
    )
    editor.set_crop(CropRect(0, 0, 8, 6))
    editor.figure.canvas.draw()
    center = editor.apply_button.ax.transAxes.transform((0.5, 0.5))

    editor.figure.canvas.callbacks.process(
        "button_press_event",
        MouseEvent(
            "button_press_event",
            editor.figure.canvas,
            *center,
            button=1,
        ),
    )
    editor.figure.canvas.callbacks.process(
        "button_release_event",
        MouseEvent(
            "button_release_event",
            editor.figure.canvas,
            *center,
            button=1,
        ),
    )

    assert applied == []
    plt.close(editor.figure)


def test_clicking_angle_box_does_not_discard_unsubmitted_text():
    editor = _build_editor()
    try:
        editor.figure.canvas.draw()
        canvas = editor.figure.canvas
        x, y = editor.angle_box.ax.transAxes.transform((0.5, 0.5))

        def click_box():
            for name in ("button_press_event", "button_release_event"):
                canvas.callbacks.process(
                    name, MouseEvent(name, canvas, x, y, button=1)
                )

        click_box()
        canvas.callbacks.process(
            "key_press_event", KeyEvent("key_press_event", canvas, key="1")
        )
        typed = editor.angle_box.text
        assert typed != "0.0"
        click_box()
        assert editor.angle_box.text == typed
        assert editor.draft.angle_deg == 0.0
    finally:
        plt.close(editor.figure)


def test_rectangle_selector_real_reverse_drag_on_inverted_y_axis():
    previews: list[FrameTransform] = []
    editor = _build_editor(previews=previews)
    editor.figure.canvas.draw()

    for event in (
        _mouse_event(editor, "button_press_event", 4.0, 4.0),
        _mouse_event(editor, "motion_notify_event", 1.0, 1.0),
        _mouse_event(editor, "button_release_event", 1.0, 1.0),
    ):
        editor.figure.canvas.callbacks.process(event.name, event)

    assert editor.image_axes.yaxis_inverted()
    assert editor.draft.crop == CropRect(1, 1, 4, 4)
    assert previews[-1].crop == CropRect(1, 1, 4, 4)
    plt.close(editor.figure)


def test_crop_text_boxes_share_validation_and_restore_on_parse_error():
    previews: list[FrameTransform] = []
    editor = _build_editor(previews=previews)

    editor.crop_boxes["x"].set_val("1")
    assert editor.draft.crop == CropRect(1, 0, 8, 6)
    assert editor.draft.is_valid is False

    editor.crop_boxes["width"].set_val("bad")
    assert editor.crop_boxes["width"].text == "8"
    assert editor.error_text.get_text()
    assert editor.error_text.get_color() == "crimson"
    assert len(previews) == 1
    plt.close(editor.figure)


def test_set_angle_error_restores_all_controls_without_changing_draft():
    initial = FrameTransform.identity(8, 6).with_angle(10.0)
    editor = _build_editor(initial)
    before = (editor.draft.angle_deg, editor.draft.crop, editor.draft.message)

    editor.set_angle(20.0)

    assert (editor.draft.angle_deg, editor.draft.crop, editor.draft.message) == before
    assert editor.angle_box.text == "10.0"
    assert editor.angle_slider.val == pytest.approx(10.0)
    assert "15" in editor.error_text.get_text()
    plt.close(editor.figure)


def test_set_crop_rejects_wrong_type_and_restores_current_crop():
    editor = _build_editor()
    original = editor.draft.crop

    editor.set_crop(object())

    assert editor.draft.crop == original
    assert editor.crop_boxes["width"].text == "8"
    assert editor.error_text.get_text()
    plt.close(editor.figure)


def test_set_visible_synchronizes_axes_selector_and_text():
    editor = _build_editor()

    editor.set_visible(False)

    assert all(not axes.get_visible() for axes in editor.control_axes)
    assert editor.selector.active is False
    assert editor.selector.get_visible() is False
    assert editor.status_text.get_visible() is False
    assert editor.error_text.get_visible() is False

    editor.set_visible(True)

    assert all(axes.get_visible() for axes in editor.control_axes)
    assert editor.selector.active is True
    assert editor.selector.get_visible() is True
    assert editor.status_text.get_visible() is True
    assert editor.error_text.get_visible() is True
    plt.close(editor.figure)


@pytest.mark.parametrize(
    "button_name",
    [
        "left_button",
        "right_button",
        "half_turn_button",
        "zero_button",
        "maximum_button",
        "reset_button",
        "apply_button",
        "cancel_button",
    ],
)
def test_buttons_are_silent_while_controls_are_updating(button_name):
    previews: list[FrameTransform] = []
    applied: list[FrameTransform] = []
    cancelled: list[bool] = []
    initial = FrameTransform.identity(8, 6).with_angle(10.0)
    editor = _build_editor(
        initial,
        previews=previews,
        applied=applied,
        cancelled=cancelled,
    )
    editor.draft.set_crop(CropRect(initial.crop.x, initial.crop.y, 2, 2))
    before = (editor.draft.angle_deg, editor.draft.crop, editor.draft.message)
    editor.updating = True

    getattr(editor, button_name)._observers.process("clicked", None)

    assert (editor.draft.angle_deg, editor.draft.crop, editor.draft.message) == before
    assert previews == []
    assert applied == []
    assert cancelled == []
    plt.close(editor.figure)
