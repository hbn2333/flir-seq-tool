"""A paged ROI editor with one active rectangle selector and no SEQ access."""

from dataclasses import replace
from uuid import uuid4

from matplotlib.patches import Rectangle
from matplotlib.widgets import CheckButtons, RectangleSelector
from .viewer_theme import (ThemedButton as Button, ThemedTextBox as TextBox, style_plot, style_border,
                           COMPACT_BUTTON_FONT_SIZE, LABEL_FONT_SIZE)

from .measurement_models import CurrentAnalysis, MeasurementSettings, Roi
from .height_models import HeightSettings
from .height_panel import HeightPanel
from .measurement_stats import histogram, profile, summarize
from .measurement_plots import draw_distribution, draw_frame_curves, draw_profile
from .pixel_geometry import rect_from_extents
from .viewer_layout import label_above, MEASURE_RESULT_RECT, MEASURE_CURVE_TEMP_RECT, MEASURE_CURVE_DELTA_RECT


COLORS = ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
          "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf")


def replace_roi(settings: MeasurementSettings, updated: Roi) -> MeasurementSettings:
    if not any(roi.id == updated.id for roi in settings.rois):
        raise ValueError("unknown ROI")
    config = settings.height_for(updated.id).for_height(updated.height)
    heights = tuple(config if h.roi_id == updated.id else h for h in settings.height_settings)
    if not any(h.roi_id == updated.id for h in heights):
        heights += (config,)
    return replace(settings, rois=tuple(updated if roi.id == updated.id else roi for roi in settings.rois),
                   height_settings=heights)


class MeasurePanel:
    PAGE_SIZE = 4

    def __init__(self, figure, image_axes, state, on_geometry_change, on_display_change, dialogs):
        self.figure, self.image_axes, self.state = figure, image_axes, state
        self.on_geometry_change, self.on_display_change = on_geometry_change, on_display_change
        self.dialogs = dialogs
        self.visible = self.busy = self.adding = self.updating = False
        self.page_index = 0
        self._frame = None
        self._analysis_key = None
        self._plot_key = None
        self.current_analysis = None
        self.result_axes = None
        self.delta_axes = None
        self.current_local_frame = 0
        self.frame_count = 1
        self.on_calculate = self.on_cancel = None
        self.on_save_setup = self.on_export = None
        self.can_export = lambda: False
        self.on_export_height = None
        self.can_export_height = lambda kind: False
        self.on_interaction_change = None
        self._reported_adding = False
        self._disposed = False
        self.resampled = False
        self.drag_pending = False
        self._drag_extents = None
        self._dragging = False
        self._drag_timer = figure.canvas.new_timer(interval=100)
        self._drag_timer.single_shot = True
        self._drag_timer.add_callback(self.flush_drag)
        self.patches = []
        self.control_axes, self.widgets, self.callback_ids = [], [], []

        def axes(rect):
            ax = figure.add_axes(rect)
            self.control_axes.append(ax)
            return ax

        self.row_buttons, self.row_checks = [], []
        for row in range(self.PAGE_SIZE):
            y = .875 - row * .033
            checkbox = CheckButtons(axes((.695, y, .026, .025)), [""], [False])
            button = Button(axes((.727, y, .225, .025)), "")
            button.label.set_parse_math(False)
            button.label.set_fontsize(COMPACT_BUTTON_FONT_SIZE)
            button.on_clicked(lambda event, row=row: self._select_row(row))
            checkbox.on_clicked(lambda label, row=row: self._check_row(row))
            self.row_buttons.append(button)
            self.row_checks.append(checkbox)
        self.previous_button = Button(axes((.695, .736, .118, .025)), "Previous")
        self.next_button = Button(axes((.830, .736, .122, .025)), "Next")
        self.add_button = Button(axes((.695, .696, .118, .027)), "Add ROI", role="primary")
        self.delete_button = Button(axes((.830, .696, .122, .027)), "Delete", role="danger")
        self.name_box = TextBox(axes((.695, .645, .257, .027)), "Name", initial="")
        self.name_box.text_disp.set_parse_math(False)
        self.name_box.text_disp.set_clip_on(True)
        self.name_box.text_disp.set_clip_path(self.name_box.ax.patch)
        self.geometry_boxes = {name: TextBox(axes((.695 + .067 * i, .592, .056, .025)), name, initial="")
                               for i, name in enumerate(("x", "y", "width", "height"))}
        for box in (self.name_box, *self.geometry_boxes.values()):
            label_above(box)
            box.label.set_fontsize(LABEL_FONT_SIZE)
        self.page_text = figure.text(.695, .915, "", fontsize=9, va="top")
        self.error_text = figure.text(.695, .585, "", color="crimson", fontsize=6.5, va="top")
        self.stats_text = figure.text(.695, .578, "Add an ROI to inspect temperatures", fontsize=8.5, va="top")
        self.analysis_note = figure.text(.075, .942, "", fontsize=9, va="top", parse_math=False)
        self.current_button = Button(axes((.08, .482, .12, .026)), "Current frame")
        self.curves_button = Button(axes((.21, .482, .12, .026)), "Frame curves")
        self.height_button = Button(axes((.34, .482, .145, .026)), "Height analysis")
        self.distribution_button = Button(axes((.51, .482, .11, .026)), "Distribution")
        self.profile_button = Button(axes((.63, .482, .095, .026)), "Profile")
        self.horizontal_button = Button(axes((.74, .482, .10, .026)), "Horizontal")
        self.vertical_button = Button(axes((.85, .482, .10, .026)), "Vertical")
        self.mean_button = Button(axes((.50, .482, .08, .026)), "Mean")
        self.min_button = Button(axes((.59, .482, .08, .026)), "Min")
        self.max_button = Button(axes((.68, .482, .08, .026)), "Max")
        self.calculate_button = Button(axes((.77, .482, .10, .026)), "Calculate", role="primary")
        self.cancel_button = Button(axes((.88, .482, .07, .026)), "Cancel")
        self.save_setup_button = Button(axes((.15, .16, .17, .027)), "Save setup")
        self.export_csv_button = Button(axes((.35, .16, .17, .027)), "Export CSV")
        self.export_png_button = Button(axes((.55, .16, .17, .027)), "Export PNG")
        self.output_controls = (self.save_setup_button, self.export_csv_button, self.export_png_button)
        self.save_setup_button.on_clicked(lambda event: self.on_save_setup() if self.on_save_setup else None)
        for button, kind in ((self.export_csv_button, "csv"), (self.export_png_button, "png")):
            button.on_clicked(lambda event, kind=kind: self.on_export(kind) if self.on_export else None)
        for button in self.output_controls:
            button.label.set_fontsize(COMPACT_BUTTON_FONT_SIZE)
        self.curve_controls = (self.mean_button, self.min_button, self.max_button,
                               self.calculate_button, self.cancel_button)
        for button in self.curve_controls:
            button.label.set_fontsize(COMPACT_BUTTON_FONT_SIZE)
        for button, value in ((self.mean_button, "mean"), (self.min_button, "min"), (self.max_button, "max")):
            button.on_clicked(lambda event, value=value: self._set_view("curve_metric", value))
        self.calculate_button.on_clicked(lambda event: self.on_calculate() if self.on_calculate else None)
        self.cancel_button.on_clicked(lambda event: self.on_cancel() if self.on_cancel else None)
        self.analysis_controls = (self.current_button, self.curves_button, self.height_button, self.distribution_button,
                                  self.profile_button, self.horizontal_button, self.vertical_button)
        for button, field, value in (
            (self.current_button, "result_view", "current"), (self.curves_button, "result_view", "curves"),
            (self.height_button, "result_view", "height"),
            (self.distribution_button, "frame_view", "distribution"), (self.profile_button, "frame_view", "profile"),
            (self.horizontal_button, "profile_direction", "horizontal"), (self.vertical_button, "profile_direction", "vertical"),
        ):
            button.label.set_fontsize(COMPACT_BUTTON_FONT_SIZE)
            button.on_clicked(lambda event, field=field, value=value: self._set_view(field, value))
        self.widgets = [*self.row_buttons, *self.row_checks, self.previous_button, self.next_button,
                        self.add_button, self.delete_button, self.name_box, *self.geometry_boxes.values(),
                        *self.analysis_controls, *self.curve_controls, *self.output_controls]
        self.previous_button.on_clicked(lambda event: self._turn_page(-1))
        self.next_button.on_clicked(lambda event: self._turn_page(1))
        self.add_button.on_clicked(lambda event: self.begin_add())
        self.delete_button.on_clicked(lambda event: self.delete_selected())
        self.name_box.on_submit(self._rename)
        for box in self.geometry_boxes.values():
            box.on_submit(self._submit_geometry)

        # Run before RectangleSelector's press handler, so a toolbar mode cannot
        # start a rectangle even when it was activated since the last refresh.
        self.callback_ids.append(figure.canvas.mpl_connect("button_press_event", self._before_press))
        self.selector = RectangleSelector(image_axes, self._on_select, interactive=True, useblit=False,
                                          button=[1], minspanx=1, minspany=1, spancoords="pixels")
        self.callback_ids.append(figure.canvas.mpl_connect("button_release_event", self._after_release))
        self.callback_ids.append(figure.canvas.mpl_connect("motion_notify_event", self._on_motion))
        self.callback_ids.append(figure.canvas.mpl_connect("key_press_event", self._on_key))
        self.height_panel = HeightPanel(self)
        self.control_axes.extend(self.height_panel.control_axes)
        self.widgets.extend(self.height_panel.widgets)
        self._sync()

    @property
    def selected(self):
        return next((roi for roi in self.state.settings.rois if roi.id == self.state.settings.active_roi_id), None)

    def _can_edit(self):
        return self.visible and not self.busy

    def _bounds(self):
        return self.image_axes.images[0].get_array().shape[::-1]

    def _publish(self, settings, *, geometry=False, sync=True):
        settings.validate_bounds(*self._bounds())
        if settings.height_unit == 'mm' and any(r.checked and settings.height_for(r.id).mm_per_pixel is None
                                                for r in settings.rois):
            settings = replace(settings, height_unit='px')
        self.state.settings = settings
        (self.on_geometry_change if geometry else self.on_display_change)()
        if self._frame is not None:
            self.refresh_frame(self._frame)
        if sync:
            self._sync()

    def begin_add(self):
        if not self._can_edit():
            return
        self.adding = True
        self.error_text.set_text("Drag a rectangle; Esc cancels")
        self.selector.clear()
        self._sync_selector()
        self._sync_analysis_controls()
        self.figure.canvas.draw_idle()

    def select(self, roi_id):
        if not self._can_edit():
            return
        index = next((i for i, roi in enumerate(self.state.settings.rois) if roi.id == roi_id), None)
        if index is None:
            raise ValueError("unknown ROI")
        self.adding = False
        self.page_index = index // self.PAGE_SIZE
        self._publish(replace(self.state.settings, active_roi_id=roi_id))

    def set_checked(self, roi_id, checked):
        if not self._can_edit():
            return
        roi = next(roi for roi in self.state.settings.rois if roi.id == roi_id)
        self._publish(replace_roi(self.state.settings, replace(roi, checked=checked)))

    def _select_row(self, row):
        index = self.page_index * self.PAGE_SIZE + row
        if self._can_edit() and index < len(self.state.settings.rois):
            self.select(self.state.settings.rois[index].id)

    def _check_row(self, row):
        if self.updating or not self._can_edit():
            return
        index = self.page_index * self.PAGE_SIZE + row
        if index < len(self.state.settings.rois):
            self.set_checked(self.state.settings.rois[index].id, bool(self.row_checks[row].get_status()[0]))

    def _turn_page(self, delta):
        if self._can_edit():
            self.page_index = max(0, min(self.page_index + delta, (len(self.state.settings.rois) - 1) // self.PAGE_SIZE))
            self._sync()

    def _rename(self, name):
        if self.updating or not self._can_edit() or self.selected is None:
            return
        try:
            self._publish(replace_roi(self.state.settings, replace(self.selected, name=name)))
            self.error_text.set_text("")
        except ValueError as exc:
            self._sync()
            self.error_text.set_text(str(exc)[:50])

    def _submit_geometry(self, text):
        if self.updating or not self._can_edit() or self.selected is None:
            return
        try:
            before = self.state.settings
            changes = {name: int(box.text.strip()) for name, box in self.geometry_boxes.items()}
            updated = replace(self.selected, **changes)
            if updated != self.selected:
                self._publish(replace_roi(self.state.settings, updated), geometry=True)
            self._geometry_notice(before)
        except ValueError as exc:
            self._sync()
            self.error_text.set_text(str(exc)[:50])

    def _geometry_notice(self, before):
        disabled = any(before.height_for(roi.id).zones_enabled and not self.state.settings.height_for(roi.id).zones_enabled
                       for roi in self.state.settings.rois if any(r.id == roi.id for r in before.rois))
        self.error_text.set_text('Height zones disabled: adjust cuts\nso every zone contains a row' if disabled else '')

    def delete_selected(self):
        if not self._can_edit() or self.selected is None:
            return
        selected = self.selected
        if not self.dialogs.confirm(f"Delete {selected.name}?"):
            return
        rois = self.state.settings.rois
        index = rois.index(selected)
        remaining = tuple(roi for roi in rois if roi.id != selected.id)
        active = remaining[min(index, len(remaining) - 1)].id if remaining else None
        self.adding = False
        self.page_index = min(index, max(0, len(remaining) - 1)) // self.PAGE_SIZE
        self._publish(replace(self.state.settings, rois=remaining, active_roi_id=active,
                              height_settings=tuple(h for h in self.state.settings.height_settings if h.roi_id != selected.id)),
                      geometry=True)

    def _before_press(self, event):
        self.selector.set_active(self._can_edit() and self.image_axes.get_navigate_mode() is None
                                 and (self.adding or self.selected is not None))
        self._dragging = self.selector.active and event.inaxes is self.image_axes and event.button == 1

    def _on_select(self, press, release):
        if not self._can_edit() or self.image_axes.get_navigate_mode() is not None:
            return
        x0, x1, y0, y1 = self.selector.extents
        p0, p1 = self.image_axes.transData.transform((x0, y0)), self.image_axes.transData.transform((x1, y1))
        if abs(p1[0] - p0[0]) <= self.selector.minspanx or abs(p1[1] - p0[1]) <= self.selector.minspany:
            return
        try:
            before = self.state.settings
            crop = rect_from_extents((x0, x1, y0, y1))
            if self.adding:
                number, names = 1, {roi.name for roi in self.state.settings.rois}
                while f"ROI {number}" in names:
                    number += 1
                roi = Roi(uuid4().hex, f"ROI {number}", crop.x, crop.y, crop.width, crop.height,
                          COLORS[len(self.state.settings.rois) % len(COLORS)], True)
                settings = replace(self.state.settings, rois=(*self.state.settings.rois, roi), active_roi_id=roi.id,
                                   height_settings=(*self.state.settings.height_settings, HeightSettings(roi.id)))
                settings.validate_bounds(*self._bounds())
                self.adding = False
                self.page_index = (len(settings.rois) - 1) // self.PAGE_SIZE
            elif self.selected is not None:
                roi = replace(self.selected, x=crop.x, y=crop.y, width=crop.width, height=crop.height)
                if roi == self.selected:
                    return
                settings = replace_roi(self.state.settings, roi)
            else:
                return
            self._publish(settings, geometry=True)
            self._geometry_notice(before)
        except ValueError as exc:
            self.error_text.set_text(str(exc)[:50])

    def _after_release(self, event):
        was_dragging = self._dragging
        self._dragging = False
        self._drag_timer.stop()
        self.drag_pending = False
        self._drag_extents = None
        if self.visible and not self.updating:
            if was_dragging:
                self._sync()
            else:
                self._sync_selector()
            if self._frame is not None:
                self.refresh_frame(self._frame)
            self.figure.canvas.draw_idle()

    def _on_motion(self, event):
        if not self._dragging or not self._can_edit() or self.adding or self.selected is None:
            return
        self._drag_extents = tuple(self.selector.extents)
        if not self.drag_pending:
            self.drag_pending = True
            self._drag_timer.start()

    def flush_drag(self):
        extents = self._drag_extents
        self._drag_extents = None
        self.drag_pending = False
        self._drag_timer.stop()
        if extents is None or not self._can_edit() or self.selected is None or self.adding:
            return
        try:
            rect = rect_from_extents(extents)
            roi = replace(self.selected, x=rect.x, y=rect.y, width=rect.width, height=rect.height)
            if roi != self.selected:
                before = self.state.settings
                self._publish(replace_roi(self.state.settings, roi), geometry=True, sync=False)
                self._geometry_notice(before)
        except ValueError as exc:
            self.error_text.set_text(str(exc)[:50])

    def clear_current_analysis(self):
        self._analysis_key = None
        self._plot_key = None
        self.current_analysis = None
        self._frame = None

    def set_result_axes(self, axes, delta_axes=None):
        self.result_axes = axes
        self.delta_axes = delta_axes
        self._plot_key = None

    def _set_view(self, field, value):
        if self._can_edit():
            self._publish(replace(self.state.settings, **{field: value}))

    def refresh_frame(self, frame):
        """Consume the viewer's transformed frame; never read the source reader."""
        self._frame = frame
        roi = self.selected
        key = None if roi is None else (frame.frame_index, id(frame.temperature_c), roi.id,
                                        roi.x, roi.y, roi.width, roi.height)
        if key != self._analysis_key or (roi is not None and self.current_analysis is None):
            self._analysis_key = key
            self.current_analysis = None if roi is None else CurrentAnalysis(
                frame.frame_index, roi.id, summarize(frame.temperature_c, roi),
                histogram(frame.temperature_c, roi), profile(frame.temperature_c, roi, "horizontal"),
                profile(frame.temperature_c, roi, "vertical"))
        self._render_analysis()

        if self.visible:
            self._sync_analysis_controls()

    def _render_analysis(self):
        if not self.visible or self.busy:
            return
        analysis = self.current_analysis
        if analysis is None:
            self.stats_text.set_text("Add an ROI to inspect temperatures" if not self.state.settings.rois
                                     else "Select an ROI to inspect temperatures")
            self.analysis_note.set_text("")
        else:
            stats = analysis.stats
            def number(value):
                return "N/A" if value is None else f"{value:.2f}"
            self.stats_text.set_text(
                f"Mean {number(stats.mean_c)} °C | Min {number(stats.min_c)} °C\n"
                f"Max {number(stats.max_c)} °C | ROI range {number(stats.delta_c)} °C\n"
                f"Std {number(stats.std_c)} °C (ddof=0)\n"
                f"Valid pixels {stats.valid_pixels}/{stats.total_pixels}"
                + (f" ({stats.total_pixels - stats.valid_pixels} excluded)" if stats.valid_pixels < stats.total_pixels else ""))
            self.analysis_note.set_text(self.selected.name[:34] + (
                " | Based on resampled temperatures" if self.resampled else " | Cropped pixel coordinates"))
        plot_key = (id(analysis), self.state.settings.result_view, self.state.settings.frame_view,
                    self.state.settings.profile_direction, self.state.settings.curve_metric,
                    self.state.status, id(self.state.result_store), self.current_local_frame,
                    self.state.settings)
        if self.result_axes is not None and plot_key != self._plot_key:
            self._plot_key = plot_key
            ax = self.result_axes
            curves = self.state.settings.result_view == "curves"
            ax.set_position(MEASURE_CURVE_TEMP_RECT if curves else MEASURE_RESULT_RECT)
            if self.delta_axes is not None:
                self.delta_axes.set_visible(curves and self.visible)
                self.delta_axes.set_position(MEASURE_CURVE_DELTA_RECT)
            if self.state.settings.result_view == "curves":
                if self.state.status == "complete" and self.state.result_store is not None and self.delta_axes is not None:
                    draw_frame_curves(ax, self.delta_axes, self.state.result_store, self.state.settings,
                                      self.current_local_frame, self.frame_count)
                else:
                    messages = {"stale": "Inputs changed — Calculate again", "cancelled": "Cancelled — Calculate again",
                                "failed": "Analysis failed — Calculate again"}
                    for result_ax in (ax, self.delta_axes):
                        if result_ax is not None:
                            result_ax.clear()
                            result_ax.set_axis_off()
                            result_ax.text(.5, .5, messages.get(self.state.status, "Not calculated"),
                                           transform=result_ax.transAxes, ha="center", va="center")
            elif self.state.settings.result_view == 'height':
                self.height_panel.draw(ax, self.delta_axes, self._frame)
            elif analysis is None:
                ax.clear()
                ax.set_axis_off()
                ax.text(.5, .5, "Select or add an ROI", transform=ax.transAxes, ha="center", va="center")
            elif self.state.settings.frame_view == "distribution":
                draw_distribution(ax, analysis.histogram)
            else:
                draw_profile(ax, getattr(analysis, self.state.settings.profile_direction))
            for result_ax in (ax, self.delta_axes):
                if result_ax is not None:
                    style_plot(result_ax)
        self.figure.canvas.draw_idle()

    def _sync_analysis_controls(self):
        settings = self.state.settings
        ready = self.visible and not self.busy and not self.adding
        self.save_setup_button.set_active(ready)
        for button in (self.export_csv_button, self.export_png_button):
            button.set_active(ready and self.can_export())
        height = settings.result_view == 'height'
        self.stats_text.set_visible(self.visible and not height)
        self.export_csv_button.ax.set_visible(self.visible and not height)
        self.export_csv_button.set_active(ready and not height and self.can_export())
        self.export_png_button.ax.set_visible(self.visible and not height)
        self.export_png_button.set_active(ready and not height and self.can_export())
        self.save_setup_button.ax.set_position((.09, .16, .13, .027) if height else (.15, .16, .17, .027))
        self.export_png_button.ax.set_position((.77, .16, .13, .027) if height else (.55, .16, .17, .027))
        if self.delta_axes is not None:
            self.delta_axes.set_visible(self.visible and (settings.result_view == "curves" or
                                                         height and settings.height_view == 'zones'))
        for button in self.analysis_controls:
            visible = self.visible
            if button in (self.distribution_button, self.profile_button):
                visible = visible and settings.result_view == "current"
            elif button in (self.horizontal_button, self.vertical_button):
                visible = visible and settings.result_view == "current" and settings.frame_view == "profile"
            button.ax.set_visible(visible)
            button.set_active(visible and not self.busy)
        for button, selected in ((self.current_button, settings.result_view == "current"),
                                  (self.curves_button, settings.result_view == "curves"),
                                  (self.height_button, height),
                                  (self.distribution_button, settings.frame_view == "distribution"),
                                  (self.profile_button, settings.frame_view == "profile"),
                                  (self.horizontal_button, settings.profile_direction == "horizontal"),
                                  (self.vertical_button, settings.profile_direction == "vertical")):
            button.set_selected(selected)
        for button in self.curve_controls:
            visible = self.visible and (settings.result_view == "curves" or height and
                                        button in (self.calculate_button, self.cancel_button))
            button.ax.set_visible(visible)
            button.set_active(visible and not self.busy)
        self.calculate_button.set_active(self.visible and settings.result_view in ("curves", 'height')
                                          and not self.busy and not self.adding and bool(settings.rois))
        self.cancel_button.set_active(self.visible and settings.result_view in ("curves", 'height') and self.busy)
        for button, value in ((self.mean_button, "mean"), (self.min_button, "min"), (self.max_button, "max")):
            button.set_selected(settings.curve_metric == value)
        self.height_panel.sync()
        if self._reported_adding != self.adding:
            self._reported_adding = self.adding
            if self.on_interaction_change:
                self.on_interaction_change()

    def _on_key(self, event):
        if self._can_edit() and event.key == "escape" and self.adding:
            self.adding = False
            self.error_text.set_text("")
            self._sync_selector()
            self._sync_analysis_controls()
            self.figure.canvas.draw_idle()

    def _sync_selector(self):
        selected = self.selected
        if selected is not None and not self.adding:
            self.selector.extents = (selected.x - .5, selected.x + selected.width - .5,
                                     selected.y - .5, selected.y + selected.height - .5)
            self.selector.set_props(edgecolor=selected.color)
        active = self._can_edit() and self.image_axes.get_navigate_mode() is None
        self.selector.set_active(active and (self.adding or selected is not None))
        self.selector.set_visible(self.visible and not self.adding and selected is not None)

    def _sync(self):
        if not self.visible:
            for widget in self.widgets:
                widget.active = False
            self.selector.set_active(False)
            self.selector.set_visible(False)
            for patch in self.patches:
                patch.set_visible(False)
            self.height_panel.sync()
            return
        self.updating = True
        try:
            count = len(self.state.settings.rois)
            self.page_index = max(0, min(self.page_index, max(0, count - 1) // self.PAGE_SIZE))
            enabled = self._can_edit()
            for i, (button, checkbox) in enumerate(zip(self.row_buttons, self.row_checks)):
                index = self.page_index * self.PAGE_SIZE + i
                exists = index < count
                button.set_active(enabled and exists)
                checkbox.active = enabled and exists
                roi = self.state.settings.rois[index] if exists else None
                label = roi.name if roi else ""
                button.label.set_text(label[:23] + ("…" if len(label) > 23 else ""))
                button.set_tint(roi.color if roi else None)
                button.set_selected(roi is not None and roi.id == self.state.settings.active_roi_id)
                style_border(checkbox.ax)
                status = bool(roi and roi.checked)
                if checkbox.get_status()[0] != status:
                    checkbox.set_active(0)
                    checkbox.active = enabled and exists
            selected = self.selected
            name = selected.name if selected else ""
            if self.name_box.text != name:
                self.name_box.set_val(name)
            self.name_box.set_active(enabled and selected is not None)
            for name, box in self.geometry_boxes.items():
                value = str(getattr(selected, name)) if selected else ""
                if box.text != value:
                    box.set_val(value)
                box.set_active(enabled and selected is not None)
            self.add_button.set_active(enabled)
            self.delete_button.set_active(enabled and selected is not None)
            self.previous_button.set_active(enabled and self.page_index > 0)
            self.next_button.set_active(enabled and (self.page_index + 1) * self.PAGE_SIZE < count)
            self.page_text.set_text(f"ROI list — {count} region(s), page {self.page_index + 1}")
            for patch in self.patches:
                patch.remove()
            self.patches = []
            for roi in self.state.settings.rois:
                patch = Rectangle((roi.x - .5, roi.y - .5), roi.width, roi.height,
                                  fill=False, edgecolor=roi.color, linewidth=1.5, visible=self.visible)
                self.image_axes.add_patch(patch)
                self.patches.append(patch)
            self._sync_selector()
            self._sync_analysis_controls()
            self._render_analysis()
        finally:
            self.updating = False
        self.figure.canvas.draw_idle()

    def set_visible(self, visible):
        self.visible = bool(visible)
        if not visible:
            self.adding = False
            self._drag_timer.stop()
            self.drag_pending = False
            self._drag_extents = None
            self._dragging = False
            for widget in self.widgets:
                if getattr(widget, 'capturekeystrokes', False):
                    widget.stop_typing()
                widget.active = False
            if self.figure.canvas.mouse_grabber in self.control_axes:
                self.figure.canvas.release_mouse(self.figure.canvas.mouse_grabber)
        for artist in (*self.control_axes, self.page_text, self.error_text, self.stats_text, self.analysis_note):
            artist.set_visible(self.visible)
        self._sync()

    def set_busy(self, busy):
        if self.busy == bool(busy):
            return
        self.busy = bool(busy)
        if self.busy:
            self._drag_timer.stop()
            self.drag_pending = False
            self._drag_extents = None
            self._dragging = False
            for widget in self.widgets:
                if getattr(widget, 'capturekeystrokes', False):
                    widget.stop_typing()
        self._sync()

    def dispose(self):
        if self._disposed:
            return
        for widget in self.widgets:
            widget.eventson = False
        self.set_visible(False)
        self.height_panel.dispose()
        self._drag_timer.remove_callback(self.flush_drag)
        self.selector.disconnect_events()
        for widget in self.widgets:
            widget.disconnect_events()
        for cid in self.callback_ids:
            self.figure.canvas.mpl_disconnect(cid)
        self._disposed = True


def build_measure_panel(figure, image_axes, state, on_geometry_change, on_display_change, dialogs):
    return MeasurePanel(figure, image_axes, state, on_geometry_change, on_display_change, dialogs)
