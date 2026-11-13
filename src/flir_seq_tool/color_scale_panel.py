"""Shared Matplotlib scale controls, independent of viewer and worker timers."""

from .viewer_layout import BoundedMessage, COLOR_SCALE_RECTS, label_above
from .viewer_theme import MUTED, ThemedButton, ThemedTextBox, style_text


class ScaleTextBox(ThemedTextBox):
    """Clipped scrolling viewport that retains the exact numeric draft."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for artist in (self.text_disp, self.cursor):
            artist.set_clip_on(True)
            artist.set_clip_path(self.ax.patch)
        self.text_disp.set_parse_math(False)

    def reset_viewport(self):
        self.text_disp.set_x(.05)
        self.cursor.set_visible(False)

    def _rendercursor(self):
        # Measure without TextBox's synchronous canvas draw. Model refreshes
        # never call this method, preserving continuous slider performance.
        renderer = self.ax.figure.canvas.get_renderer()
        width = renderer.get_text_width_height_descent(
            self.text[:self.cursor_index], self.text_disp.get_fontproperties(),
            ismath=False)[0]
        left, right = self.ax.bbox.x0 + 4, self.ax.bbox.x1 - 4
        origin = self.text_disp.get_transform().transform(self.text_disp.get_position())[0]
        cursor_x = origin + width
        shift = min(0, right - cursor_x) if cursor_x > right else max(0, left - cursor_x)
        origin += shift
        self.text_disp.set_x((origin - self.ax.bbox.x0) / self.ax.bbox.width)
        bounds = self.text_disp.get_window_extent(renderer)
        bottom, top = bounds.y0, bounds.y1
        if bottom == top:
            height = renderer.get_text_width_height_descent(
                'lp', self.text_disp.get_fontproperties(), ismath=False)[1]
            bottom, top = bottom - height / 2, top + height / 2
        self.cursor.set(segments=[[(origin + width, bottom),
                                   (origin + width, top)]], visible=True)
        self.ax.figure.canvas.draw_idle()


class ColorScalePanel:
    def __init__(self, figure, state, controller, on_change, *, message_details=None):
        self.figure, self.state, self.controller = figure, state, controller
        self._on_change = on_change
        self._disposed = self._syncing = self._editing = self._busy = False
        self._error = None
        self._connections = []
        axes = {name: figure.add_axes(rect) for name, rect in COLOR_SCALE_RECTS.items()}
        self.control_axes = tuple(axes.values())
        self.min_box = ScaleTextBox(axes['minimum'], 'Min (°C)')
        self.max_box = ScaleTextBox(axes['maximum'], 'Max (°C)')
        for box in (self.min_box, self.max_box):
            label_above(box)
            self._connections.append((box, box.on_text_change(self._edit_bounds)))
            self._connections.append((box, box.on_submit(self._submitted)))
        self.fixed_button = ThemedButton(axes['fixed'], 'Fixed')
        self.per_frame_button = ThemedButton(axes['per_frame'], 'Per frame')
        self.apply_button = ThemedButton(axes['apply'], 'Apply')
        self.auto_button = ThemedButton(axes['auto'], 'Auto range')
        self.cancel_button = ThemedButton(axes['cancel'], 'Cancel')
        self.widgets = (self.min_box, self.max_box, self.fixed_button,
                        self.per_frame_button, self.apply_button, self.auto_button,
                        self.cancel_button)
        for button, callback in (
            (self.fixed_button, lambda event: self._select_mode('fixed')),
            (self.per_frame_button, lambda event: self._select_mode('per_frame')),
            (self.apply_button, self._apply), (self.auto_button, self._auto),
            (self.cancel_button, self._cancel),
        ):
            self._connections.append((button, button.on_clicked(callback)))
        self.message_details = {} if message_details is None else message_details
        self.status_text = BoundedMessage(figure, 'color_scale', self.message_details,
                                          max_lines=2, color=MUTED)
        style_text(self.status_text)
        self.sync()

    def _enabled(self):
        return (not self._disposed and not self._editing and not self._busy
                and self.state.render_snapshot().status != 'preview')

    def _editable(self):
        return self._enabled() and self.state.settings.scale_mode == 'fixed'

    def _changed(self):
        self.sync()
        self._on_change()

    def _edit_bounds(self, text):
        if self._syncing or not self._editable():
            return
        self.state.edit_bounds(self.min_box.text, self.max_box.text)
        self._error = None
        self._changed()

    def _select_mode(self, mode):
        if not self._enabled():
            return
        # Finishing actual user capture releases Matplotlib's keymap lock.
        self._finish_typing()
        self.state.select_mode(mode)
        self._error = None
        self.controller.reconcile()
        self._changed()

    def _submitted(self, text):
        # Enter and blur only refresh display; Apply alone commits manual bounds.
        if not self._disposed and not self._syncing:
            self.sync()

    def _apply(self, event):
        if not self._editable():
            return
        try:
            self.state.apply_draft()
        except (ValueError, OverflowError) as exc:
            self._error = f'Invalid bounds: {exc}'
        else:
            self._error = None
            self.controller.reconcile()
        self._changed()

    def _auto(self, event):
        if not self._editable():
            return
        self._finish_typing()
        self._error = None
        self.controller.request_auto()
        self._changed()

    def _cancel(self, event):
        if not self._enabled() or not self.controller.scanning:
            return
        self.controller.cancel()
        self._changed()

    def _finish_typing(self):
        for box in (self.min_box, self.max_box):
            if box.capturekeystrokes:
                box.stop_typing()

    def set_editing(self, editing):
        self._editing = bool(editing)
        self.sync()

    def set_busy(self, busy):
        self._busy = bool(busy)
        self.sync()

    def sync(self):
        """Read current state; never reconcile, submit text, or finish capture."""
        if self._disposed:
            return
        self._syncing = True
        try:
            snapshot = self.state.render_snapshot()
            fixed = self.state.settings.scale_mode == 'fixed'
            enabled, editable = self._enabled(), self._editable()
            self.fixed_button.set_selected(fixed)
            self.per_frame_button.set_selected(not fixed)
            for button in (self.fixed_button, self.per_frame_button):
                button.set_active(enabled)
            self.auto_button.set_active(editable)
            self.apply_button.set_active(editable)
            self.cancel_button.set_active(enabled and self.controller.scanning)
            for box in (self.min_box, self.max_box):
                box.set_active(editable)
            texts = (self.state.draft_bounds if fixed and self.state.draft_pending
                     else (str(float(snapshot.limits.vmin)), str(float(snapshot.limits.vmax))))
            capturing = fixed and any(box.capturekeystrokes
                                     for box in (self.min_box, self.max_box))
            for box, value in zip((self.min_box, self.max_box), texts):
                # Even a blurred, untouched sibling belongs to the pending pair.
                # Direct artist updates avoid TextBox.set_val's submit and draw.
                if not capturing and box.text != value:
                    box.text_disp.set_text(value)
                    box.reset_viewport()
            status = snapshot.status.replace('_', ' ')
            if self._error:
                status = self._error
            elif self.controller.error:
                status = f'Auto range failed: {self.controller.error}'
            elif self.controller.status in ('scanning', 'cancelling'):
                progress = self.controller.progress_snapshot
                status = (f'Auto range {self.controller.status}: '
                          f'{progress.completed}/{progress.total} {progress.message}')
            elif self.controller.status in ('cancelled', 'paused'):
                status = f'Auto range {self.controller.status}'
            if fixed and self.state.draft_pending:
                status = 'unapplied bounds — ' + status
            if snapshot.no_finite:
                status += ' — no finite pixels'
            self.status_text.set_text(status)
        finally:
            self._syncing = False
        self.figure.canvas.draw_idle()

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self._finish_typing()
        for widget, connection in self._connections:
            widget.disconnect(connection)
        self._connections.clear()
        for widget in self.widgets:
            widget.set_active(False)
            widget.disconnect_events()
