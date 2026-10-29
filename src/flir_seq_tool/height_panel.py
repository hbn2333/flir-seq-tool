"""Height controls and bounded previews; source readers stay with the viewer."""

from dataclasses import replace
from queue import SimpleQueue
from threading import Event, Thread
import textwrap

import numpy as np
from matplotlib.widgets import CheckButtons

from .height_models import ZONE_KEYS, height_positions, row_profile, zone_statistics
from .viewer_layout import label_above
from .viewer_theme import ThemedButton as Button, ThemedTextBox as TextBox, style_plot


PROFILE_RECT = (.10, .25, .51, .19)
TEMP_RECT = (.10, .355, .51, .085)
DELTA_RECT = (.10, .25, .51, .065)
ZONE_STYLES = ('-', '--', ':')


def sample_zone_curves(store, settings, frame_count, cancelled, buckets=500):
    """Reduce one frame at a time, retaining extrema and breaking unsafe links.

    A bucket containing gaps or nonmonotonic timestamps uses isolated points.
    At most six representative points per bucket and channel enter the GUI,
    including the first/last timestamp discontinuities in each bucket.
    """
    output = []
    any_reduced = any_time_break = False
    for roi in settings.rois:
        config = settings.height_for(roi.id)
        if not roi.checked or not config.zones_enabled:
            continue
        count = max(1, min(buckets, frame_count))
        summaries = [[None] * count for _ in range(4)]
        for frame, profile in store.height_series(roi.id):
            if cancelled.is_set():
                return None
            zone = zone_statistics(profile, config)
            local = frame['local_frame']
            slot = min(count - 1, local * count // max(1, frame_count))
            abnormal = frame.get('time_status', 'ok') != 'ok'
            any_time_break |= abnormal
            for channel, value in enumerate((*[z.mean_c for z in zone.zones], zone.zone_delta_c)):
                summary = summaries[channel][slot]
                if summary is None:
                    # first, low, high, last, has_gap, count, first/last time break
                    summary = [None, None, None, None, False, 0, None, None]
                    summaries[channel][slot] = summary
                summary[4] |= abnormal or value is None or not np.isfinite(value)
                summary[5] += 1
                if value is None or not np.isfinite(value):
                    continue
                point = (local, float(frame['relative_s']), value)
                if abnormal:
                    summary[6] = point if summary[6] is None else summary[6]
                    summary[7] = point
                summary[0] = point if summary[0] is None else summary[0]
                summary[1] = point if summary[1] is None or value < summary[1][2] else summary[1]
                summary[2] = point if summary[2] is None or value > summary[2][2] else summary[2]
                summary[3] = point
        channels = []
        for summaries_for_channel in summaries:
            points = []
            for summary in summaries_for_channel:
                if summary is None:
                    points.append((np.nan, np.nan))
                    continue
                selected = sorted({p[0]: p for p in (*summary[:4], *summary[6:]) if p is not None}.values())
                any_reduced |= summary[5] > len(selected)
                if summary[4]:
                    points.append((np.nan, np.nan))
                for _, x, y in selected:
                    points.append((x, y))
                    if summary[4]:
                        points.append((np.nan, np.nan))
            channels.append(np.asarray(points, dtype=float).reshape(-1, 2))
        output.append((roi, config, tuple(channels)))
    return tuple(output), any_reduced, any_time_break


class HeightPanel:
    """Owned widgets and a cancellable disk-to-preview worker for one panel."""

    def __init__(self, panel):
        self.panel, self.figure = panel, panel.figure
        self.widgets, self.control_axes = [], []
        self._updating = self._disposed = False
        self._worker = None
        self._cancel = Event()
        self._queue = SimpleQueue()
        self._generation = 0
        self._requested_key = self._data_key = None
        self._data = None
        self._error = None
        self._zone_edit_roi = None
        self.zone_lines = []
        self._spatial_artists = []
        self.height_arrow = self.direction_note = None
        self._timer = self.figure.canvas.new_timer(interval=75)
        self._timer.add_callback(self.poll)

        def button(rect, text, action, **kwargs):
            widget = Button(self._axes(rect), text, **kwargs)
            widget.label.set_fontsize(7)
            widget.on_clicked(lambda event: action())
            self.widgets.append(widget)
            return widget

        self.profile_button = button((.50, .482, .09, .026), 'Profile', lambda: self._view('height_view', 'profile'))
        self.zones_button = button((.595, .482, .08, .026), 'Zones', lambda: self._view('height_view', 'zones'))
        self.px_button = button((.68, .482, .04, .026), 'px', lambda: self._view('height_unit', 'px'))
        self.mm_button = button((.725, .482, .04, .026), 'mm', lambda: self._view('height_unit', 'mm'))
        self.bottom_button = button((.695, .415, .078, .023), 'Bottom hot', lambda: self._change(heated_edge='bottom'))
        self.top_button = button((.780, .415, .078, .023), 'Top hot', lambda: self._change(heated_edge='top'))
        self.clear_button = button((.865, .415, .087, .023), 'Clear mm', self._clear_calibration)
        self.height_mm_box = self._box((.695, .370, .090, .023), 'ROI height mm', self._calibrate)
        self.zones_check = CheckButtons(self._axes((.800, .365, .152, .031)), ['Enable zones'], [False])
        self.zones_check.labels[0].set_fontsize(7)
        self.zones_check.on_clicked(self._toggle_zones)
        self.widgets.append(self.zones_check)
        self.zone_name_boxes = tuple(self._box((.695 + i * .088, .325, .081, .022), name,
                                                lambda text, i=i: self._name(i, text))
                                     for i, name in enumerate(('Low name', 'Middle name', 'High name')))
        self.cut_boxes = tuple(self._box((.695 + i * .133, .280, .124, .022), name,
                                         lambda text: self._cuts())
                              for i, name in enumerate(('Low/middle (px)', 'Middle/high (px)')))
        self.pair_a_button = button((.695, .235, .124, .022), 'A: Low', lambda: self._cycle_pair(0))
        self.pair_b_button = button((.828, .235, .124, .022), 'B: High', lambda: self._cycle_pair(1))
        self.expanded = (*self.zone_name_boxes, *self.cut_boxes, self.pair_a_button, self.pair_b_button)
        self.export_height_button = button((.24, .16, .30, .027), 'Export full height CSV (all ROIs)',
                                           lambda: self._export('height'), role='primary')
        self.export_zones_button = button((.56, .16, .19, .027), 'Export zones CSV', lambda: self._export('zones'))
        self.note = self.figure.text(.10, .451, '', fontsize=6.5, va='center', parse_math=False)
        self.sync()

    def _axes(self, rect):
        ax = self.figure.add_axes(rect)
        self.control_axes.append(ax)
        return ax

    def _box(self, rect, label, callback):
        box = TextBox(self._axes(rect), label)
        label_above(box)
        box.label.set_fontsize(6.5)
        box.text_disp.set_fontsize(7)
        box.text_disp.set_parse_math(False)
        box.text_disp.set_clip_path(box.ax.patch)
        box.text_disp.set_clip_on(True)
        box.on_submit(callback)
        self.widgets.append(box)
        return box

    @property
    def visible(self):
        return self.panel.visible and self.panel.state.settings.result_view == 'height' and not self._disposed

    def _editable(self):
        return self.visible and self.panel._can_edit() and self.panel.selected is not None and not self._updating

    def _view(self, field, value):
        if not self.visible or not self.panel._can_edit():
            return
        if field == 'height_unit' and value == 'mm' and self.uncalibrated():
            return
        self.panel._set_view(field, value)

    def _publish_config(self, config):
        config.validate_height(self.panel.selected.height)
        if config.zones_enabled:
            self._zone_edit_roi = None
        self.panel._publish(self.panel.state.settings.with_height(config))
        self.panel.error_text.set_text('')

    def _try(self, operation):
        if not self._editable():
            return
        try:
            operation()
        except ValueError as exc:
            self.sync()
            self.panel.error_text.set_text(textwrap.fill(str(exc)[:110], width=48))

    def _change(self, **changes):
        self._try(lambda: self._publish_config(replace(self.panel.state.settings.height_for(self.panel.selected.id), **changes)))

    def _calibrate(self, text):
        def apply():
            config = self.panel.state.settings.height_for(self.panel.selected.id)
            self._publish_config(config.calibrate(self.panel.selected.height, float(text)))
        self._try(apply)

    def _clear_calibration(self):
        self._change(mm_per_pixel=None, reference_height_px=None, reference_height_mm=None)

    def _toggle_zones(self, label):
        if not self._editable():
            return
        enabled = bool(self.zones_check.get_status()[0])
        config = replace(self.panel.state.settings.height_for(self.panel.selected.id), zones_enabled=enabled)
        try:
            config.validate_height(self.panel.selected.height)
        except ValueError as exc:
            self._zone_edit_roi = self.panel.selected.id
            self.sync()
            self.panel.error_text.set_text(textwrap.fill(str(exc)[:110], width=48))
        else:
            self._zone_edit_roi = None
            self._publish_config(config)

    def _name(self, index, text):
        if self.panel.selected is None:
            return
        names = list(self.panel.state.settings.height_for(self.panel.selected.id).zone_names)
        names[index] = text.strip()
        self._change(zone_names=tuple(names))

    def _cuts(self):
        roi = self.panel.selected
        if roi is None:
            return
        settings = self.panel.state.settings
        config = settings.height_for(roi.id)
        span = roi.height * (config.mm_per_pixel if settings.height_unit == 'mm' else 1)
        self._try(lambda: self._publish_config(replace(self.panel.state.settings.height_for(self.panel.selected.id),
                                                     cuts=tuple(float(box.text) / span for box in self.cut_boxes))))

    def _cycle_pair(self, index):
        if not self._editable():
            return
        pair = list(self.panel.state.settings.height_for(self.panel.selected.id).delta_pair)
        value = (ZONE_KEYS.index(pair[index]) + 1) % 3
        if ZONE_KEYS[value] == pair[1 - index]:
            value = (value + 1) % 3
        pair[index] = ZONE_KEYS[value]
        self._change(delta_pair=tuple(pair))

    def _export(self, kind):
        if self.visible and not self.panel.busy and self.panel.can_export_height(kind) and self.panel.on_export_height:
            self.panel.on_export_height(kind)

    def uncalibrated(self):
        settings = self.panel.state.settings
        return [roi.name for roi in settings.rois if roi.checked and settings.height_for(roi.id).mm_per_pixel is None]

    def sync(self):
        visible, panel = self.visible, self.panel
        settings = panel.state.settings
        if visible and settings.height_unit == 'mm' and self.uncalibrated():
            settings = replace(settings, height_unit='px')
            panel.state.settings = settings
            panel._plot_key = None
            panel.on_display_change()
        roi = panel.selected
        config = None if roi is None else settings.height_for(roi.id)
        self._updating = True
        try:
            ready = visible and not panel.busy and not panel.adding
            for widget in self.widgets:
                shown = visible and (widget not in self.expanded or bool(config and
                         (config.zones_enabled or self._zone_edit_roi == roi.id)))
                widget.ax.set_visible(shown)
                if not shown and getattr(widget, 'capturekeystrokes', False):
                    widget.stop_typing()
                if isinstance(widget, CheckButtons):
                    widget.active = shown and ready and roi is not None
                else:
                    widget.set_active(shown and ready)
            for widget in (self.bottom_button, self.top_button, self.height_mm_box, self.clear_button, *self.expanded):
                widget.set_active(widget.ax.get_visible() and ready and roi is not None)
            self.note.set_visible(visible)
            self.mm_button.set_active(ready and bool(settings.rois) and not self.uncalibrated())
            self.profile_button.set_selected(settings.height_view == 'profile')
            self.zones_button.set_selected(settings.height_view == 'zones')
            self.px_button.set_selected(settings.height_unit == 'px')
            self.mm_button.set_selected(settings.height_unit == 'mm')
            self.export_height_button.set_active(ready and panel.can_export_height('height'))
            self.export_zones_button.set_active(ready and any(settings.height_for(r.id).zones_enabled for r in settings.rois)
                                                 and panel.can_export_height('zones'))
            if config:
                self.bottom_button.set_selected(config.heated_edge == 'bottom')
                self.top_button.set_selected(config.heated_edge == 'top')
                values = ((self.height_mm_box, '' if config.mm_per_pixel is None else f'{roi.height * config.mm_per_pixel:.12g}'),
                          *zip(self.zone_name_boxes, config.zone_names),
                          *zip(self.cut_boxes, (f'{q * roi.height * (config.mm_per_pixel if settings.height_unit == "mm" else 1):.12g}'
                                                for q in config.cuts)))
                for box, value in values:
                    if box.text != value:
                        box.set_val(value)
                if self.zones_check.get_status()[0] != config.zones_enabled:
                    self.zones_check.set_active(0)
                for button, label, key in zip((self.pair_a_button, self.pair_b_button), ('A', 'B'), config.delta_pair):
                    button.label.set_text(f'{label}: {config.zone_names[ZONE_KEYS.index(key)][:12]}')
                for box, label in zip(self.cut_boxes, ('Low/middle', 'Middle/high')):
                    box.label.set_text(f'{label} ({settings.height_unit})')
            self._sync_spatial()
            if not visible or panel.busy:
                self.cancel()
        finally:
            self._updating = False

    def _sync_spatial(self):
        for artist in self._spatial_artists:
            artist.remove()
        self._spatial_artists = []
        self.zone_lines = []
        self.height_arrow = self.direction_note = None
        roi = self.panel.selected
        if not self.visible or roi is None:
            return
        config = self.panel.state.settings.height_for(roi.id)
        ax = self.panel.image_axes
        hot_y = roi.y - .5 if config.heated_edge == 'top' else roi.y + roi.height - .5
        direction = 1 if config.heated_edge == 'top' else -1
        hot_line, = ax.plot([roi.x - .5, roi.x + roi.width - .5], [hot_y, hot_y],
                            color=roi.color, linewidth=2.5)
        arrow_x = roi.x + roi.width - .5 - min(.25, roi.width / 4)
        self.height_arrow = ax.annotate('', xy=(arrow_x, hot_y + direction * roi.height * .65),
                                       xytext=(arrow_x, hot_y + direction * roi.height * .10),
                                       arrowprops=dict(arrowstyle='-|>', color=roi.color, lw=1.5))
        self.direction_note = ax.text(.02, .98, f'Hot {config.heated_edge} | z {"↓" if direction == 1 else "↑"}',
                                      transform=ax.transAxes, ha='left', va='top', fontsize=7,
                                      bbox=dict(facecolor='white', alpha=.85, edgecolor='none', pad=2))
        self._spatial_artists = [hot_line, self.height_arrow, self.direction_note]
        if config.zones_enabled:
            for q, linestyle in zip(config.cuts, ('--', ':')):
                y = hot_y + direction * q * roi.height
                line, = ax.plot([roi.x - .5, roi.x + roi.width - .5], [y, y],
                                color=roi.color, linestyle=linestyle, linewidth=1.3)
                self.zone_lines.append(line)
                self._spatial_artists.append(line)

    def draw(self, ax, delta_ax, frame):
        settings = self.panel.state.settings
        zones = settings.height_view == 'zones'
        ax.set_position(TEMP_RECT if zones else PROFILE_RECT)
        if delta_ax is not None:
            delta_ax.set_position(DELTA_RECT)
            delta_ax.set_visible(zones and self.visible)
        ax.clear()
        ax.set_axis_on()
        uncalibrated = self.uncalibrated()
        self.note.set_text(('mm unavailable: ' + ', '.join(uncalibrated))[:110] if uncalibrated else
                           'Height from heated ROI edge; row centers; shared confirmed-range time origin')
        if zones:
            self._draw_zones(ax, delta_ax)
        elif frame is not None:
            self.cancel()
            for roi in settings.rois:
                if not roi.checked:
                    continue
                config = settings.height_for(roi.id)
                profile = row_profile(frame.temperature_c, roi)
                px, mm = height_positions(roi.height, config)
                z = mm if settings.height_unit == 'mm' and mm is not None else px
                order = np.argsort(z)
                ax.plot(z[order], profile.means_c[order], color=roi.color, label=self._short(roi.name, 26),
                        marker='o' if roi.height == 1 else '.', markersize=3)
            ax.set_xlabel(f'Height from heated edge ({settings.height_unit})', fontsize=8)
            ax.set_ylabel('Temperature (°C)', fontsize=8)
            if any(np.isfinite(line.get_ydata()).any() for line in ax.lines):
                ax.legend(fontsize=6, loc='best')
            elif ax.lines:
                self._empty(ax, 'No finite temperatures in checked ROIs')
            else:
                self._empty(ax, 'Check an ROI to preview its height profile')
        else:
            self._empty(ax, 'Select or add an ROI')
        for result_ax in (ax, delta_ax):
            if result_ax is not None:
                style_plot(result_ax)

    @staticmethod
    def _short(text, length):
        return text if len(text) <= length else text[:length - 1] + '…'

    @staticmethod
    def _empty(ax, text):
        ax.set_axis_off()
        ax.text(.5, .5, text, transform=ax.transAxes, ha='center', va='center', fontsize=8, wrap=True)

    def _key(self):
        p = self.panel
        return (id(p.state.result_store), p.state.settings, p.frame_count, p.state.status)

    def _draw_zones(self, ax, delta_ax):
        if delta_ax is None:
            return
        delta_ax.clear()
        delta_ax.set_axis_on()
        settings, state = self.panel.state.settings, self.panel.state
        if not any(r.checked and settings.height_for(r.id).zones_enabled for r in settings.rois):
            self.cancel()
            self._empty(ax, 'Check an ROI and enable its height zones')
            self._empty(delta_ax, 'Zone mean difference A−B')
            return
        if state.status != 'complete' or state.result_store is None or not getattr(state.result_store, 'has_height', False):
            self.cancel()
            self._empty(ax, 'Calculate height data' if state.status != 'stale' else 'Inputs changed — Calculate again')
            self._empty(delta_ax, 'Zone mean difference A−B')
            return
        key = self._key()
        if key != self._data_key:
            if key != self._requested_key:
                self._request(key)
            self._empty(ax, self._error or 'Preparing bounded zone preview…')
            self._empty(delta_ax, 'Zone mean difference A−B')
            return
        rows, reduced, time_break = self._data
        for roi, config, channels in rows:
            for i, points in enumerate(channels[:3]):
                ax.plot(points[:, 0], points[:, 1], color=roi.color, linestyle=ZONE_STYLES[i],
                        label=f'{self._short(roi.name, 18)}: {self._short(config.zone_names[i], 14)}', marker='.', markersize=2)
            points = channels[3]
            names = [config.zone_names[ZONE_KEYS.index(k)] for k in config.delta_pair]
            delta_ax.plot(points[:, 0], points[:, 1], color=roi.color,
                          label=f'{self._short(roi.name, 18)}: {self._short(names[0], 12)}−{self._short(names[1], 12)}',
                          marker='.', markersize=2)
        ax.set_ylabel('Zone mean (°C)', fontsize=7)
        ax.tick_params(labelbottom=False)
        delta_ax.set_xlabel('Time from confirmed-range first timestamp (s)', fontsize=7)
        delta_ax.set_ylabel('A−B (°C)', fontsize=7)
        if any(np.isfinite(line.get_ydata()).any() for line in ax.lines):
            ax.legend(fontsize=5.5, loc='best', ncol=2)
        else:
            self._empty(ax, 'No finite temperatures in enabled zones')
        if any(np.isfinite(line.get_ydata()).any() for line in delta_ax.lines):
            delta_ax.legend(fontsize=5.5, loc='best')
        else:
            self._empty(delta_ax, 'No finite zone mean differences A−B')
        if reduced or time_break:
            self.note.set_text(('Bounded preview: extrema preserved; CSV contains every sample. ' if reduced else '') +
                               ('Duplicate/backwards timestamps: connections broken.' if time_break else ''))

    def _request(self, key):
        self.cancel(wait=True)
        self._requested_key, self._error = key, None
        generation = self._generation
        cancelled = self._cancel = Event()
        store, settings, frame_count = self.panel.state.result_store, self.panel.state.settings, self.panel.frame_count
        def run():
            reader = None
            try:
                reader = type(store).open_readonly(store.path, cancel_event=cancelled)
                result = sample_zone_curves(reader, settings, frame_count, cancelled)
                if not cancelled.is_set():
                    self._queue.put((generation, key, result, None))
            except Exception as exc:
                if not cancelled.is_set():
                    self._queue.put((generation, key, None, str(exc)))
            finally:
                if reader is not None:
                    reader.close()
        self._worker = Thread(target=run, name='height-zone-preview', daemon=True)
        self._worker.start()
        self._timer.start()

    def poll(self):
        while not self._queue.empty():
            generation, key, result, error = self._queue.get()
            if self._disposed or generation != self._generation or key != self._key():
                continue
            self._data_key, self._data, self._error = (key if error is None else None), result, error
            self.panel._plot_key = None
            self.panel._render_analysis()
        if self._worker is None or not self._worker.is_alive():
            self._timer.stop()

    def cancel(self, wait=False):
        self._cancel.set()
        self._timer.stop()
        self._generation += 1
        self._requested_key = None
        if wait and self._worker is not None:
            self._worker.join()
            self._worker = None

    def dispose(self):
        if self._disposed:
            return
        self.cancel(wait=True)
        self._timer.remove_callback(self.poll)
        for widget in self.widgets:
            widget.eventson = False
            if getattr(widget, 'capturekeystrokes', False):
                widget.stop_typing()
            widget.active = False
            widget.disconnect_events()
            widget.ax.set_visible(False)
        self.note.set_visible(False)
        for artist in self._spatial_artists:
            artist.remove()
        self._spatial_artists = []
        self._disposed = True
