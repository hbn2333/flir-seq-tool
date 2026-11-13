"""Isolated, fixed-size Agg rendering of already transformed temperatures."""

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from .color_scale import ColorLimits, frame_color_limits


VIDEO_WIDTH, VIDEO_HEIGHT = 1920, 1080


class VideoRenderer:
    def __init__(self, display, speed):
        self.display = display
        self.last_valid_limits = None
        self.figure = Figure(figsize=(19.2, 10.8), dpi=100, facecolor="white")
        self.canvas = FigureCanvasAgg(self.figure)
        self.axes = self.figure.add_axes((0.05, 0.1, 0.75, 0.8))
        self.axes.set_axis_off()
        self.image = self.axes.imshow([[0.0]], cmap=display.colormap,
            vmin=display.vmin_c, vmax=display.vmax_c, origin="upper",
            interpolation="nearest", aspect="equal")
        cax = self.figure.add_axes((1650 / 1920, 170 / 1080, 40 / 1920, 740 / 1080))
        self.colorbar = self.figure.colorbar(self.image, cax=cax)
        self.colorbar.set_label("Temperature (°C)", fontsize=20, labelpad=18)
        cax.tick_params(labelsize=18)
        self.time_text = self.figure.text(100 / 1920, 1020 / 1080, "", fontsize=24,
            va="top", parse_math=False)
        mode = ('Per-frame adaptive' if display.scale_mode == 'per_frame' else
                'Fixed automatic' if display.fixed_source == 'auto' else 'Fixed manual')
        self.mode_text = self.figure.text(1590 / 1920, 1040 / 1080, mode, fontsize=20,
            ha="right", va="top", parse_math=False)
        self.status_text = self.figure.text(1590 / 1920, 1005 / 1080, "", fontsize=16,
            ha="right", va="top", parse_math=False)
        self.speed = speed

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.figure.clear()

    def render(self, values, relative_s):
        current = frame_color_limits(values)
        if current is not None:
            self.last_valid_limits = current
        if self.display.scale_mode == 'per_frame':
            limits = current or self.last_valid_limits or ColorLimits(0.0, 1.0)
        else:
            limits = ColorLimits(self.display.vmin_c, self.display.vmax_c)
        if self.image.get_clim() != (limits.vmin, limits.vmax):
            self.image.set_clim(limits.vmin, limits.vmax)
        self.status_text.set_text('No finite temperatures' if current is None else '')
        height, width = values.shape
        scale = min(1450 / width, 860 / height)
        w, h = width * scale, height * scale
        self.axes.set_position(((100 + (1450 - w) / 2) / 1920,
                                (110 + (860 - h) / 2) / 1080, w / 1920, h / 1080))
        self.image.set_data(values)
        self.image.set_extent((-0.5, width - 0.5, height - 0.5, -0.5))
        self.axes.set_xlim(-0.5, width - 0.5)
        self.axes.set_ylim(height - 0.5, -0.5)
        self.time_text.set_text(f"t={relative_s:.2f}s   {self.speed}×")
        self.canvas.draw()
        return np.ascontiguousarray(np.asarray(self.canvas.buffer_rgba())[:, :, :3])
