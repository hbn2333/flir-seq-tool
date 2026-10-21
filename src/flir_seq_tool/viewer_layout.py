"""Shared figure-coordinate regions for the two viewer modes."""

from matplotlib.widgets import TextBox


NORMAL_RECTS = {
    "current": (0.15, 0.47, 0.65, 0.025),
    "range": (0.15, 0.425, 0.65, 0.025),
    "start": (0.15, 0.36, 0.12, 0.035),
    "end": (0.37, 0.36, 0.12, 0.035),
    "confirm": (0.57, 0.36, 0.13, 0.035),
    "reset": (0.72, 0.36, 0.16, 0.035),
    "save": (0.47, 0.31, 0.13, 0.035),
    "load": (0.61, 0.31, 0.13, 0.035),
    "edit": (0.75, 0.31, 0.13, 0.035),
    "clip": (0.24, 0.255, 0.31, 0.035),
    "export": (0.57, 0.255, 0.13, 0.035),
    "cancel": (0.72, 0.255, 0.16, 0.035),
}
EDITOR_RECTS = {
    "angle_slider": (0.15, 0.425, 0.40, 0.025),
    "angle_box": (0.69, 0.400, 0.13, 0.035),
    "quick": tuple((x, 0.345, 0.13, 0.040) for x in (0.15, 0.32, 0.49, 0.66)),
    "crop": tuple((x, 0.270, 0.13, 0.040) for x in (0.15, 0.32, 0.49, 0.66)),
    "actions": tuple((x, 0.210, 0.15, 0.040) for x in (0.15, 0.33, 0.51, 0.69)),
}
# All information is top-aligned: multi-line text grows into its own region.
TEXT_POSITIONS = {
    "export": (0.15, 0.235),
    "progress": (0.15, 0.163),
    "editor_status": (0.15, 0.190),
    "editor_error": (0.15, 0.163),
    "transform": (0.15, 0.125),
    "timeline": (0.15, 0.102),
    "message": (0.15, 0.052),
}


def label_above(box: TextBox) -> None:
    box.label.set_position((0.0, 1.12))
    box.label.set_horizontalalignment("left")
    box.label.set_verticalalignment("bottom")
