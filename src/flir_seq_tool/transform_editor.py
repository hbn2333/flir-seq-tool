from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.text import Text
from matplotlib.widgets import Button, RectangleSelector, Slider, TextBox
from .viewer_layout import BoundedMessage, EDITOR_RECTS, label_above
from .pixel_geometry import rect_from_extents

from .transform import (
    CropRect,
    FrameTransform,
    RotationSettings,
    normalize_angle,
)


@dataclass
class TransformDraft:
    applied: FrameTransform
    angle_deg: float
    crop: CropRect
    message: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.applied, FrameTransform):
            raise ValueError("applied must be a FrameTransform instance")
        if not isinstance(self.crop, CropRect):
            raise ValueError("crop must be a CropRect instance")
        if not isinstance(self.message, str):
            raise ValueError("message must be a string")
        self.angle_deg = RotationSettings(self.angle_deg).angle_deg
        if not self.message and not self.is_valid:
            self.message = "Crop includes invalid pixels"

    @classmethod
    def from_applied(cls, transform: FrameTransform) -> TransformDraft:
        if not isinstance(transform, FrameTransform):
            raise ValueError("transform must be a FrameTransform instance")
        return cls(transform, transform.rotation.angle_deg, transform.crop)

    @property
    def is_valid(self) -> bool:
        try:
            self.build()
        except ValueError:
            return False
        return True

    def set_angle(self, angle_deg: float) -> None:
        rotation = RotationSettings(angle_deg)
        if rotation.angle_deg == self.angle_deg:
            return
        reset = FrameTransform.identity(
            self.applied.source_width,
            self.applied.source_height,
        ).with_angle(rotation.angle_deg)
        self.angle_deg = reset.rotation.angle_deg
        self.crop = reset.crop
        self.message = "Rotation changed — crop reset"

    def set_crop(self, crop: CropRect) -> None:
        if not isinstance(crop, CropRect):
            raise ValueError("crop must be a CropRect instance")
        self.crop = crop
        self.message = "" if self.is_valid else "Crop includes invalid pixels"

    def reset(self) -> None:
        identity = FrameTransform.identity(
            self.applied.source_width,
            self.applied.source_height,
        )
        self.angle_deg = identity.rotation.angle_deg
        self.crop = identity.crop
        self.message = "Draft reset"

    def build(self) -> FrameTransform:
        return FrameTransform(
            self.applied.source_width,
            self.applied.source_height,
            RotationSettings(self.angle_deg),
            self.crop,
        )

    def cancel(self) -> FrameTransform:
        return self.applied


@dataclass
class TransformEditorUI:
    figure: Figure
    image_axes: Axes
    draft: TransformDraft
    angle_slider: Slider
    angle_box: TextBox
    left_button: Button
    right_button: Button
    half_turn_button: Button
    zero_button: Button
    crop_boxes: dict[str, TextBox]
    maximum_button: Button
    reset_button: Button
    cancel_button: Button
    apply_button: Button
    selector: RectangleSelector
    status_text: Text
    error_text: Text
    control_axes: tuple[Axes, ...]
    preview_callback: Callable[[FrameTransform], None] = field(repr=False)
    apply_callback: Callable[[FrameTransform], None] = field(repr=False)
    cancel_callback: Callable[[], None] = field(repr=False)
    updating: bool = False

    def set_visible(self, visible: bool) -> None:
        for control_axis in self.control_axes:
            control_axis.set_visible(visible)
        # Hidden Axes still pass widget hit tests; disable their input as well.
        for control in (
            self.angle_slider, self.angle_box, *self.crop_boxes.values(),
            self.left_button, self.right_button, self.half_turn_button,
            self.zero_button, self.maximum_button, self.reset_button,
            self.cancel_button,
        ):
            control.set_active(visible)
        self.apply_button.set_active(visible and self.draft.is_valid)
        self.selector.set_active(visible)
        self.selector.set_visible(visible)
        self.status_text.set_visible(visible)
        self.error_text.set_visible(visible)
        self.figure.canvas.draw_idle()

    def set_angle(self, angle_deg: float) -> None:
        try:
            angle = RotationSettings(angle_deg).angle_deg
            self.draft.set_angle(angle)
        except (TypeError, ValueError) as exc:
            self._sync_controls()
            self._set_error(str(exc))
            return
        self._set_error("")
        self._sync_controls()
        self._publish_preview()

    def set_crop(self, crop: CropRect) -> None:
        try:
            self.draft.set_crop(crop)
        except (TypeError, ValueError) as exc:
            self._sync_controls()
            self._set_error(str(exc))
            return
        self._set_error("")
        self._sync_controls()
        self._publish_preview()

    def _set_error(self, message: str) -> None:
        self.error_text.set_color("crimson")
        self.error_text.set_text(message)
        self.figure.canvas.draw_idle()

    def _preview_transform(self) -> FrameTransform:
        if self.draft.is_valid:
            return self.draft.build()
        return FrameTransform.identity(
            self.draft.applied.source_width,
            self.draft.applied.source_height,
        ).with_angle(self.draft.angle_deg)

    def _publish_preview(self) -> None:
        self.preview_callback(self._preview_transform())

    def _sync_controls(self) -> None:
        self.updating = True
        try:
            fine = (self.draft.angle_deg + 45.0) % 90.0 - 45.0
            self.angle_slider.set_val(fine)
            self.angle_box.set_val(f"{self.draft.angle_deg:.1f}")
            crop = self.draft.crop
            for name, value in zip(
                ("x", "y", "width", "height"),
                (crop.x, crop.y, crop.width, crop.height),
                strict=True,
            ):
                self.crop_boxes[name].set_val(str(value))
            self.selector.extents = (
                crop.x - 0.5,
                crop.x + crop.width - 0.5,
                crop.y - 0.5,
                crop.y + crop.height - 0.5,
            )
            self.apply_button.set_active(
                self.apply_button.ax.get_visible() and self.draft.is_valid
            )
            color = "limegreen" if self.draft.is_valid else "crimson"
            self.selector.set_props(edgecolor=color)
            self.status_text.set_text(self.draft.message)
        finally:
            self.updating = False
        self.figure.canvas.draw_idle()


def build_transform_editor(
    figure: Figure,
    image_axes: Axes,
    initial_transform: FrameTransform,
    preview_callback: Callable[[FrameTransform], None],
    apply_callback: Callable[[FrameTransform], None],
    cancel_callback: Callable[[], None],
    message_details: dict[str, str] | None = None,
) -> TransformEditorUI:
    if not isinstance(figure, Figure):
        raise ValueError("figure must be a matplotlib Figure")
    if not isinstance(image_axes, Axes):
        raise ValueError("image_axes must be a matplotlib Axes")
    if not isinstance(initial_transform, FrameTransform):
        raise ValueError("initial_transform must be a FrameTransform")

    angle_slider_axes = figure.add_axes(EDITOR_RECTS["angle_slider"])
    angle_box_axes = figure.add_axes(EDITOR_RECTS["angle_box"])
    quick_axes = tuple(
        figure.add_axes(rect) for rect in EDITOR_RECTS["quick"]
    )
    crop_axes = tuple(
        figure.add_axes(rect) for rect in EDITOR_RECTS["crop"]
    )
    action_axes = tuple(
        figure.add_axes(rect) for rect in EDITOR_RECTS["actions"]
    )

    angle_slider = Slider(
        angle_slider_axes,
        "Fine angle",
        -15.0,
        15.0,
        valinit=0.0,
        valstep=0.1,
        valfmt="%.1f°",
    )
    angle_box = TextBox(angle_box_axes, "Total", initial="0.0")
    label_above(angle_box)
    left_button = Button(quick_axes[0], "+90°")
    right_button = Button(quick_axes[1], "-90°")
    half_turn_button = Button(quick_axes[2], "180°")
    zero_button = Button(quick_axes[3], "0°")
    crop_boxes = {
        name: TextBox(axes, name, initial="0")
        for name, axes in zip(
            ("x", "y", "width", "height"),
            crop_axes,
            strict=True,
        )
    }
    maximum_button = Button(action_axes[0], "Maximum")
    for box in crop_boxes.values():
        label_above(box)
    reset_button = Button(action_axes[1], "Reset")
    cancel_button = Button(action_axes[2], "Cancel")
    apply_button = Button(action_axes[3], "Apply")
    details = {} if message_details is None else message_details
    status_text = BoundedMessage(figure, "editor_status", details, max_lines=1, fontsize=9)
    error_text = BoundedMessage(
        figure, "editor_error", details, max_lines=1, fontsize=9, color="crimson",
    )

    editor: TransformEditorUI

    def on_select(_press_event, _release_event) -> None:
        if editor.updating:
            return
        x0, x1, y0, y1 = editor.selector.extents
        low_x, high_x = sorted((x0, x1))
        low_y, high_y = sorted((y0, y1))
        # RectangleSelector calls onselect even for a cleared, zero-span box.
        # Match its display-pixel threshold before converting to image pixels.
        display_low = image_axes.transData.transform((low_x, low_y))
        display_high = image_axes.transData.transform((high_x, high_y))
        if (
            abs(display_high[0] - display_low[0]) <= editor.selector.minspanx
            or abs(display_high[1] - display_low[1]) <= editor.selector.minspany
        ):
            return
        try:
            crop = rect_from_extents((low_x, high_x, low_y, high_y))
        except (TypeError, ValueError) as exc:
            editor._sync_controls()
            editor._set_error(str(exc))
            return
        editor.set_crop(crop)

    selector = RectangleSelector(
        image_axes,
        on_select,
        interactive=True,
        useblit=False,
        button=[1],
        minspanx=1,
        minspany=1,
        spancoords="pixels",
    )
    editor = TransformEditorUI(
        figure=figure,
        image_axes=image_axes,
        draft=TransformDraft.from_applied(initial_transform),
        angle_slider=angle_slider,
        angle_box=angle_box,
        left_button=left_button,
        right_button=right_button,
        half_turn_button=half_turn_button,
        zero_button=zero_button,
        crop_boxes=crop_boxes,
        maximum_button=maximum_button,
        reset_button=reset_button,
        cancel_button=cancel_button,
        apply_button=apply_button,
        selector=selector,
        status_text=status_text,
        error_text=error_text,
        control_axes=(
            angle_slider_axes,
            angle_box_axes,
            *quick_axes,
            *crop_axes,
            *action_axes,
        ),
        preview_callback=preview_callback,
        apply_callback=apply_callback,
        cancel_callback=cancel_callback,
    )

    selection_started_here = False

    def remember_selection_press(event) -> None:
        nonlocal selection_started_here
        selection_started_here = (
            selector.active and event.button == 1 and event.inaxes is image_axes
        )

    def restore_selector_after_release(event) -> None:
        nonlocal selection_started_here
        # Matplotlib clears the artist AFTER onselect. Restore from the draft
        # after its release handler, including clicks before the first drag.
        restore = selection_started_here and event.button == 1
        selection_started_here = False
        if restore and selector.active and not editor.updating:
            editor._sync_controls()
            selector.set_visible(True)
            figure.canvas.draw_idle()

    selector.connect_event("button_press_event", remember_selection_press)
    selector.connect_event("button_release_event", restore_selector_after_release)

    def on_slider(value: float) -> None:
        if editor.updating:
            return
        fine = (editor.draft.angle_deg + 45.0) % 90.0 - 45.0
        cardinal = editor.draft.angle_deg - fine
        editor.set_angle(normalize_angle(cardinal + round(float(value), 1)))

    def on_angle_submit(text: str) -> None:
        if editor.updating:
            return
        try:
            value = float(text)
        except (TypeError, ValueError) as exc:
            editor._sync_controls()
            editor._set_error(f"Invalid angle: {exc}")
            return
        editor.set_angle(value)

    def on_crop_submit(_text: str) -> None:
        if editor.updating:
            return
        try:
            crop = CropRect(
                *(int(editor.crop_boxes[name].text) for name in (
                    "x",
                    "y",
                    "width",
                    "height",
                ))
            )
        except (TypeError, ValueError) as exc:
            editor._sync_controls()
            editor._set_error(f"Invalid crop: {exc}")
            return
        editor.set_crop(crop)

    def rotate_by(delta: float) -> None:
        editor.set_angle(normalize_angle(editor.draft.angle_deg + delta))

    def unless_updating(
        callback: Callable[[object], None],
    ) -> Callable[[object], None]:
        def guarded(event: object) -> None:
            if editor.updating:
                return
            callback(event)

        return guarded

    angle_slider.on_changed(on_slider)
    angle_box.on_submit(on_angle_submit)
    for crop_box in crop_boxes.values():
        crop_box.on_submit(on_crop_submit)
    left_button.on_clicked(unless_updating(lambda _event: rotate_by(90.0)))
    right_button.on_clicked(unless_updating(lambda _event: rotate_by(-90.0)))
    half_turn_button.on_clicked(unless_updating(lambda _event: rotate_by(180.0)))
    zero_button.on_clicked(unless_updating(lambda _event: editor.set_angle(0.0)))

    def on_maximum(_event) -> None:
        maximum = FrameTransform.identity(
            editor.draft.applied.source_width,
            editor.draft.applied.source_height,
        ).with_angle(editor.draft.angle_deg)
        editor.set_crop(maximum.crop)

    def on_reset(_event) -> None:
        editor.draft.reset()
        editor._set_error("")
        editor._sync_controls()
        editor._publish_preview()

    def on_apply(_event) -> None:
        if editor.draft.is_valid:
            editor.apply_callback(editor.draft.build())

    maximum_button.on_clicked(unless_updating(on_maximum))
    reset_button.on_clicked(unless_updating(on_reset))
    cancel_button.on_clicked(
        unless_updating(lambda _event: editor.cancel_callback())
    )
    apply_button.on_clicked(unless_updating(on_apply))

    editor._sync_controls()
    return editor
