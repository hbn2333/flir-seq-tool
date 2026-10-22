"""Shared figure-coordinate regions for the two viewer modes."""

import textwrap

from matplotlib.text import Text
from matplotlib.widgets import TextBox


NAV_RECTS = {"process": (0.08, 0.955, 0.13, 0.032), "measure": (0.22, 0.955, 0.13, 0.032)}
MEASURE_IMAGE_RECT = (0.075, 0.61, 0.46, 0.29)
MEASURE_COLORBAR_RECT = (0.55, 0.61, 0.018, 0.29)
MEASURE_CURRENT_RECT = (0.15, 0.535, 0.48, 0.025)
MEASURE_RESULT_RECT = (0.12, 0.25, 0.76, 0.19)
MEASURE_CURVE_TEMP_RECT = (0.12, 0.355, 0.76, 0.085)
MEASURE_CURVE_DELTA_RECT = (0.12, 0.25, 0.76, 0.09)


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
    "export": (0.47, 0.255, 0.20, 0.035),
    "cancel": (0.69, 0.255, 0.19, 0.035),
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
    "progress": (0.15, 0.145),
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


def fit_message(text: str, *, max_chars: int, max_lines: int) -> str:
    if max_chars < 24 or max_lines < 1:
        raise ValueError("message area too small")
    lines = []
    for paragraph in text.splitlines() or [""]:
        lines.extend(textwrap.wrap(
            paragraph, width=max_chars, break_long_words=True,
            break_on_hyphens=False,
        ) or [""])
    if len(lines) <= max_lines:
        return "\n".join(lines)
    hint = "... [F1: details]"
    result = lines[:max_lines]
    result[-1] = result[-1][:max_chars - len(hint)] + hint
    return "\n".join(result)


class BoundedMessage(Text):
    """Fit at draw time, retaining the original message for details and resizing.

    Draw-time fitting uses the actual renderer (including DPI changes), without
    scheduling recursive draws or making callers manage truncated text.
    """

    def __init__(self, figure, key, details, *, max_lines, fontsize=8.5, color="black"):
        self.key = key
        self.details = details
        self.max_lines = max_lines
        self._drawing = False
        super().__init__(
            *TEXT_POSITIONS[key], text="", fontsize=fontsize, color=color,
            verticalalignment="top", transform=figure.transFigure,
            figure=figure, parse_math=False,
        )
        figure.add_artist(self)

    def set_text(self, text):
        original = str(text) if text is not None else ""
        if not self._drawing:
            self.details[self.key] = original
        super().set_text(original)

    def draw(self, renderer):
        if not self.get_visible():
            return
        original = self.details[self.key]
        available = max(1, (0.95 - self.get_position()[0]) * self.figure.bbox.width)
        # A fixed upper bound also keeps fitting predictable for huge messages.
        max_chars = min(max(24, int(available / 3)), max(24, len(original)))
        while True:
            displayed = fit_message(original, max_chars=max_chars, max_lines=self.max_lines)
            width = max((renderer.get_text_width_height_descent(
                line, self.get_fontproperties(), ismath=False,
            )[0] for line in displayed.splitlines()), default=0)
            if width <= available or max_chars <= 24:
                break
            max_chars = max(24, min(max_chars - 1, int(max_chars * available / width)))
        # Bypass our set_text: fitted content must never replace the full details.
        super().set_text(displayed)
        self._drawing = True
        try:
            super().draw(renderer)
        finally:
            self._drawing = False
