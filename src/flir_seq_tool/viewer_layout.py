"""Shared figure-coordinate regions for the two viewer modes."""

import textwrap

from matplotlib.text import Text
from matplotlib.widgets import TextBox
from matplotlib.transforms import Bbox


NAV_RECTS = {"process": (0.08, 0.955, 0.13, 0.032), "measure": (0.22, 0.955, 0.13, 0.032),
             "open": (0.82, 0.955, 0.13, 0.032)}
MEASURE_IMAGE_RECT = (0.075, 0.62, 0.46, 0.19)
MEASURE_COLORBAR_RECT = (0.55, 0.62, 0.018, 0.19)
MEASURE_CURRENT_RECT = (0.15, 0.535, 0.48, 0.025)
MEASURE_RESULT_RECT = (0.12, 0.25, 0.76, 0.155)
MEASURE_CURVE_TEMP_RECT = (0.12, 0.320, 0.76, 0.085)
MEASURE_CURVE_DELTA_RECT = (0.12, 0.25, 0.76, 0.055)
PROCESS_IMAGE_RECT = (.075, .22, .585, .58)


def process_colorbar_locator(image_axes):
    """Follow the aspect-fitted image on every draw, including shape changes."""
    def locate(_axes, _renderer):
        # Compute the aspect fit even when queried before the image axes draw.
        image_axes.apply_aspect()
        box = image_axes.get_position()
        return Bbox.from_bounds(box.x1 + .012, box.y0, .014, box.height)
    return locate

# Shared controls; page integration positions the surrounding page regions.
COLOR_SCALE_RECTS = {
    "fixed": (.06, .895, .09, .035),
    "per_frame": (.16, .895, .11, .035),
    "minimum": (.28, .895, .17, .035),
    "maximum": (.46, .895, .17, .035),
    "apply": (.64, .895, .09, .035),
    "auto": (.74, .895, .13, .035),
    "cancel": (.88, .895, .08, .035),
}


NORMAL_RECTS = {
    "current": (.15, .135, .50, .025),
    "range": (.15, .095, .50, .025),
    "start": (.78, .77, .08, .035),
    "end": (.88, .77, .08, .035),
    "confirm": (.78, .715, .18, .035),
    "reset": (.78, .67, .18, .035),
    "save": (.78, .625, .18, .035),
    "load": (.78, .58, .18, .035),
    "edit": (.78, .535, .18, .035),
    "export": (.78, .48, .18, .035),
    "video": (.78, .435, .18, .035),
    "cancel": (.78, .39, .18, .035),
}
EDITOR_RECTS = {
    "angle_slider": (.78, .775, .13, .025),
    "angle_box": (.78, .70, .18, .035),
    "quick": tuple((x, y, .08, .035) for y in (.645, .60) for x in (.78, .88)),
    "crop": tuple((x, y, .08, .035) for y in (.525, .455) for x in (.78, .88)),
    "actions": tuple((.78, y, .18, .035) for y in (.40, .355, .31, .265)),
}
# All information is top-aligned: multi-line text grows into its own region.
TEXT_POSITIONS = {
    "color_scale": (.08, .880),
    "export": (.15, .235),
    "progress": (.15, .145),
    "editor_status": (.78, .24),
    "editor_error": (.78, .20),
    "transform": (.15, .125),
    "timeline": (.15, .102),
    "message": (.15, .052),
}
PROCESS_TEXT_POSITIONS = {
    "transform": (.08, .063), "timeline": (.08, .039),
    "message": (.78, .15), "progress": (.78, .275),
    "export": (.78, .37),
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
        self.right_edge = .95
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
        available = max(1, (self.right_edge - self.get_position()[0]) * self.figure.bbox.width)
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
