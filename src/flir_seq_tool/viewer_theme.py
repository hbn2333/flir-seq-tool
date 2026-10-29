"""Local light styling for interactive viewers; export figures are unaffected."""

from functools import lru_cache

from matplotlib import colors, font_manager
from matplotlib.patches import FancyBboxPatch
from matplotlib.text import Text
from matplotlib.widgets import Button, TextBox, Slider, RangeSlider


UI_BACKGROUND = "#f3f5f9"
SURFACE = "#ffffff"
INK = "#263449"
MUTED = "#64748b"
BORDER = "#cdd7e3"
ACCENT = "#2563eb"
SOFT_ACCENT = "#e8f0ff"
DISABLED = "#eef1f5"
DISABLED_INK = "#94a0b2"
BUTTON_FONT_SIZE = 11
COMPACT_BUTTON_FONT_SIZE = 10
INPUT_FONT_SIZE = 10
LABEL_FONT_SIZE = 9
NAV_FONT_SIZE = 12


@lru_cache(maxsize=1)
def ui_font():
    available = font_manager.get_font_names()
    return next((name for name in ("Segoe UI", "DejaVu Sans") if name in available), "sans-serif")


def style_text(text):
    text.set_fontfamily(ui_font())
    if colors.to_rgba(text.get_color()) == (0, 0, 0, 1):
        text.set_color(INK)


def style_border(axes, color=BORDER):
    for spine in axes.spines.values():
        spine.set_color(color)
        spine.set_linewidth(.7)


class _ButtonLabel(Text):
    """Keep the chosen font size and abbreviate long labels at the current DPI."""

    def set_text(self, text):
        if not getattr(self, "_drawing", False):
            self._full_text = str(text) if text is not None else ""
        return super().set_text(text)

    def draw(self, renderer):
        full = self._full_text
        available = max(0, self.axes.bbox.width - renderer.points_to_pixels(6))
        def width(text):
            return renderer.get_text_width_height_descent(text, self.get_fontproperties(), False)[0]
        displayed = full
        if width(full) > available:
            low, high = 0, len(full)
            while low < high:
                middle = (low + high + 1) // 2
                if width(full[:middle] + "…") <= available:
                    low = middle
                else:
                    high = middle - 1
            displayed = full[:low] + "…"
        super().set_text(displayed)
        self._drawing = True
        try:
            super().draw(renderer)
        finally:
            self._drawing = False


class ThemedButton(Button):
    def __init__(self, ax, label, *, role="default", **kwargs):
        self.role, self.selected, self.tint = role, False, None
        super().__init__(ax, label, **kwargs)
        original = self.label
        self.label = _ButtonLabel(*original.get_position(), text=original.get_text())
        self.label.update_from(original)
        original.remove()
        ax.add_artist(self.label)
        self.label.set_parse_math(False)
        self.label.set_fontsize(BUTTON_FONT_SIZE)
        self.label.set_fontfamily(ui_font())
        self.refresh_style()

    def set_active(self, active):
        super().set_active(active)
        if hasattr(self, "label"):
            self.refresh_style()

    def set_selected(self, selected):
        self.selected = bool(selected)
        self.refresh_style()

    def set_tint(self, color):
        self.tint = color
        self.refresh_style()

    def refresh_style(self):
        border = BORDER
        if not self.active:
            background, hover, foreground = DISABLED, DISABLED, self.tint or DISABLED_INK
        elif self.selected:
            background, hover, foreground = SOFT_ACCENT, "#dbe8ff", self.tint or ACCENT
            border = "#b8cff5"
        elif self.role == "primary":
            background, hover, foreground = ACCENT, "#1d4ed8", SURFACE
            border = ACCENT
        elif self.role == "danger":
            background, hover, foreground = SURFACE, "#fff1f2", "#b23c51"
        else:
            background, hover, foreground = SURFACE, "#f0f5ff", self.tint or INK
        self.color, self.hovercolor = background, hover
        self.ax.set_facecolor(background)
        self.label.set_color(foreground)
        self.label.set_weight("bold" if self.selected or self.role == "primary" else "normal")
        style_border(self.ax, border)


class ThemedTextBox(TextBox):
    def __init__(self, ax, label, initial="", **kwargs):
        kwargs.setdefault("color", SURFACE)
        kwargs.setdefault("hovercolor", "#f8faff")
        super().__init__(ax, label, initial=initial, **kwargs)
        for text in (self.label, self.text_disp):
            style_text(text)
            text.set_fontsize(INPUT_FONT_SIZE)
        self.label.set_color(MUTED)
        self._style()

    def set_active(self, active):
        super().set_active(active)
        if hasattr(self, "text_disp"):
            self._style()

    def _style(self):
        self.color = SURFACE if self.active else DISABLED
        self.hovercolor = "#f8faff" if self.active else DISABLED
        self.ax.set_facecolor(self.color)
        self.text_disp.set_color(INK if self.active else DISABLED_INK)
        style_border(self.ax)


class ThemedSlider(Slider):
    def __init__(self, *args, **kwargs):
        _slider_defaults(kwargs)
        kwargs.setdefault("initcolor", "#b6c4d7")
        super().__init__(*args, **kwargs)
        _style_slider_text(self)


class ThemedRangeSlider(RangeSlider):
    def __init__(self, *args, **kwargs):
        _slider_defaults(kwargs)
        super().__init__(*args, **kwargs)
        _style_slider_text(self)


def _slider_defaults(kwargs):
    kwargs.setdefault("color", ACCENT)
    kwargs.setdefault("track_color", "#dfe6f0")
    kwargs.setdefault("handle_style", dict(facecolor=SURFACE, edgecolor=ACCENT, size=8))


def _style_slider_text(slider):
    for text in (slider.label, slider.valtext):
        style_text(text)
        text.set_fontsize(INPUT_FONT_SIZE)
    slider.label.set_color(MUTED)


def style_plot(axes):
    axes.set_facecolor(SURFACE)
    style_border(axes)
    axes.tick_params(colors=MUTED, labelsize=9, width=.7)
    for text in (axes.title, axes.xaxis.label, axes.yaxis.label,
                 *axes.get_xticklabels(), *axes.get_yticklabels(), *axes.texts):
        style_text(text)


class ViewerTheme:
    """Decorative figure artists without axes, callbacks, or input capture."""

    def __init__(self, figure):
        figure.set_facecolor(UI_BACKGROUND)
        self.cards = {}
        rects = {
            "header": (.055, .948, .91, .047),
            "process_image": (.055, .535, .91, .395),
            "process_controls": (.055, .245, .91, .275),
            "process_status": (.055, .025, .91, .215),
            "editor_controls": (.055, .203, .91, .317),
            "editor_status": (.055, .025, .91, .17),
            "measure_image": (.05, .525, .63, .405),
            "measure_roi": (.69, .525, .28, .405),
            "measure_analysis": (.055, .205, .915, .31),
            "measure_status": (.055, .025, .915, .172),
        }
        for name, (x, y, w, h) in rects.items():
            patch = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.003,rounding_size=0.008",
                transform=figure.transFigure, facecolor=SURFACE, edgecolor="#e0e6ef",
                linewidth=.7, zorder=-5, clip_on=False, picker=None)
            patch.set_in_layout(False)
            figure.add_artist(patch)
            self.cards[name] = patch
        self.show_page("process")

    def show_page(self, page, *, editing=False):
        prefix = "measure_" if page == "measure" else "editor_" if editing else "process_"
        for name, patch in self.cards.items():
            visible = name == "header" or name.startswith(prefix)
            if editing and name == "process_image":
                visible = True
            patch.set_visible(visible)

    def style_figure_text(self, figure):
        for text in figure.findobj(Text):
            style_text(text)
