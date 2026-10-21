from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.image import AxesImage
from matplotlib.text import Text
from matplotlib.widgets import Button, RangeSlider, Slider, TextBox

from .color_scale import ColorLimits, compute_color_limits
from .frame_cache import FrameCache
from .frame_transformer import FrameTransformer
from .extraction import (
    ExportConfirmation,
    ExportJob,
    ExportRequest,
    ExportStatus,
    build_export_request,
    preflight_export,
)
from .models import TemperatureFrame
from .reader import ScienceFileReader
from .selection import (
    DisplaySettings,
    SelectionDocument,
    SelectionStore,
    SourceIdentity,
    build_selection_document,
    validate_source_identity,
)
from .selection_dialogs import SelectionDialogs
from .file_safety import TargetSnapshot
from .timeline import TimelineState
from .transform import FrameTransform
from .transform_editor import TransformDraft, TransformEditorUI, build_transform_editor
from .viewer_layout import BoundedMessage, NORMAL_RECTS


WORKSPACE_ROOT = Path(r"D:\carbonfoam\infrared")


class ViewerSession:
    def __init__(
        self,
        reader: Any,
        cache_size: int = 3,
        vmin: float | None = None,
        vmax: float | None = None,
        colormap: str = "inferno",
        selection_store: SelectionStore | None = None,
    ) -> None:
        if (vmin is None) != (vmax is None):
            raise ValueError("vmin and vmax must be provided together")
        if vmin is not None and vmax is not None and vmin >= vmax:
            raise ValueError("vmin must be less than vmax")
        if reader.num_frames < 1:
            raise ValueError("SEQ contains no frames")

        self.reader = reader
        self.colormap = colormap
        if colormap not in plt.colormaps():
            raise ValueError(f"unknown colormap: {colormap}")
        self.selection_store = selection_store or SelectionStore(
            WORKSPACE_ROOT / "selections"
        )
        self.source_cache = FrameCache[int](maxsize=cache_size)
        self.transformed_cache = FrameCache[tuple[int, str]](maxsize=cache_size)
        self.cache = self.source_cache
        self.applied_transform = FrameTransform.identity(
            int(reader.width),
            int(reader.height),
        )
        self.frame_transformer = FrameTransformer(self.applied_transform)
        self._preview_transformer_key: str | None = None
        self._preview_transformer: FrameTransformer | None = None
        self.selection_dirty = False
        self.selection_path: Path | None = None
        self._clean_state_signature: str | None = None
        self.timeline = TimelineState(reader.num_frames)
        self._work_time_key: tuple[int, int] | None = None
        self._work_time_value: tuple[datetime, datetime] | None = None
        self.color_limits = (
            None
            if vmin is None or vmax is None
            else ColorLimits(vmin=float(vmin), vmax=float(vmax))
        )

    def raw_frame_at(self, frame_index: int) -> TemperatureFrame:
        if not 0 <= frame_index < self.reader.num_frames:
            raise IndexError(f"frame index out of range: {frame_index}")
        return self.source_cache.get(frame_index, self.reader.temperature_frame_at)

    def frame_at(self, frame_index: int) -> TemperatureFrame:
        key = (frame_index, self.applied_transform.fingerprint)
        frame = self.transformed_cache.get(
            key,
            lambda cache_key: self._transform_frame(cache_key[0]),
        )
        if self.color_limits is None:
            self.color_limits = compute_color_limits(frame.temperature_c)
        return frame

    def _transform_frame(self, frame_index: int) -> TemperatureFrame:
        raw = self.raw_frame_at(frame_index)
        return TemperatureFrame(
            frame_index=raw.frame_index,
            timestamp=raw.timestamp,
            temperature_c=self.frame_transformer.apply(raw.temperature_c),
        )

    def _validate_transform(self, transform: FrameTransform) -> None:
        if not isinstance(transform, FrameTransform):
            raise ValueError("transform must be a FrameTransform instance")
        if (
            transform.source_width != int(self.reader.width)
            or transform.source_height != int(self.reader.height)
        ):
            raise ValueError("transform source dimensions must match reader")

    def preview_frame(
        self,
        frame_index: int,
        transform: FrameTransform,
    ) -> tuple[TemperatureFrame, np.ndarray]:
        self._validate_transform(transform)
        raw = self.raw_frame_at(frame_index)
        fingerprint = transform.fingerprint
        if (
            self._preview_transformer is None
            or self._preview_transformer_key != fingerprint
        ):
            self._preview_transformer = FrameTransformer(transform)
            self._preview_transformer_key = fingerprint
        values, valid_mask = self._preview_transformer.preview(raw.temperature_c)
        return (
            TemperatureFrame(
                frame_index=raw.frame_index,
                timestamp=raw.timestamp,
                temperature_c=values,
            ),
            valid_mask,
        )

    def apply_transform(self, transform: FrameTransform) -> None:
        self._validate_transform(transform)
        if transform == self.applied_transform:
            return
        transformer = FrameTransformer(transform)
        self.applied_transform = transform
        self.frame_transformer = transformer
        self.transformed_cache.clear()
        self._preview_transformer_key = None
        self._preview_transformer = None
        self.selection_dirty = True

    def frame_at_local(self, local_frame: int) -> TemperatureFrame:
        self.timeline.set_current(local_frame)
        return self.frame_at(self.timeline.source_frame_at(local_frame))

    def work_timestamps(self) -> tuple[datetime, datetime]:
        key = (self.timeline.source_start, self.timeline.source_end)
        if self._work_time_key != key or self._work_time_value is None:
            self._work_time_key = key
            self._work_time_value = (
                self.reader.timestamp_at(key[0]),
                self.reader.timestamp_at(key[1]),
            )
        return self._work_time_value

    def work_duration_s(self) -> float:
        start, end = self.work_timestamps()
        return (end - start).total_seconds()

    def source_identity(self) -> SourceIdentity:
        stat = Path(self.reader.path).stat()
        return SourceIdentity(
            filename=Path(self.reader.path).name,
            size_bytes=stat.st_size,
            modified_time_ns=stat.st_mtime_ns,
            width=int(self.reader.width),
            height=int(self.reader.height),
            num_frames=int(self.reader.num_frames),
            first_timestamp=self.reader.timestamp_at(0),
            last_timestamp=self.reader.timestamp_at(self.reader.num_frames - 1),
        )

    def selection_document(self) -> SelectionDocument:
        if self.color_limits is None:
            raise RuntimeError("viewer color limits were not initialized")
        source_start = self.timeline.source_frame_at(self.timeline.pending_start)
        source_end = self.timeline.source_frame_at(self.timeline.pending_end)
        return build_selection_document(
            source=self.source_identity(),
            source_start_frame=source_start,
            source_end_frame=source_end,
            start_timestamp=self.reader.timestamp_at(source_start),
            end_timestamp=self.reader.timestamp_at(source_end),
            display=DisplaySettings(
                colormap=self.colormap,
                vmin_c=self.color_limits.vmin,
                vmax_c=self.color_limits.vmax,
            ),
            transform=self.applied_transform,
        )

    def save_selection(
        self, target: Path | None = None, *,
        expected_target: TargetSnapshot | None = None,
        expected_backup: TargetSnapshot | None = None,
    ) -> Path:
        if target is None:
            target = self.selection_store.save(Path(self.reader.path), self.selection_document())
        else:
            if expected_target is None or expected_backup is None:
                raise ValueError("explicit selection save requires confirmed target snapshots")
            target = self.selection_store.save_to(
                target, self.selection_document(), source_path=Path(self.reader.path),
                expected_target=expected_target, expected_backup=expected_backup,
            )
        self.selection_path = target
        self.selection_dirty = False
        self._clean_state_signature = self.state_signature()
        return target

    def load_selection(self, target: Path | None = None) -> SelectionDocument:
        target = self.selection_store.path_for(Path(self.reader.path)) if target is None else Path(target)
        document = self.selection_store.load_path(target, self.source_identity())
        self.apply_selection_document(document, path=target)
        return document

    def _prepare_selection(self, document: SelectionDocument):
        validate_source_identity(self.source_identity(), document.source)
        if document.display.colormap not in plt.colormaps():
            raise ValueError(f"unknown colormap: {document.display.colormap}")
        self._validate_transform(document.transform)
        new_transformer = FrameTransformer(document.transform)
        new_color_limits = ColorLimits(
            vmin=document.display.vmin_c,
            vmax=document.display.vmax_c,
        )
        timeline = TimelineState(self.reader.num_frames)
        timeline.restore_source_range(
            document.selection.source_start_frame,
            document.selection.source_end_frame,
        )
        return timeline, new_transformer, new_color_limits

    def apply_selection_document(self, document: SelectionDocument, *, path: Path) -> None:
        timeline, new_transformer, new_color_limits = self._prepare_selection(document)
        self.timeline = timeline
        self.colormap = document.display.colormap
        self.color_limits = new_color_limits
        self.applied_transform = document.transform
        self.frame_transformer = new_transformer
        self.transformed_cache.clear()
        self._preview_transformer_key = None
        self._preview_transformer = None
        self._work_time_key = None
        self._work_time_value = None
        self.selection_dirty = False
        self._clean_state_signature = self.state_signature()
        self.selection_path = Path(path)

    def state_signature(self) -> str:
        limits = self.color_limits
        payload = {
            "source_range": [
                self.timeline.source_frame_at(self.timeline.pending_start),
                self.timeline.source_frame_at(self.timeline.pending_end),
            ],
            "color_limits": None
            if limits is None
            else [limits.vmin, limits.vmax],
            "colormap": self.colormap,
            "transform": self.applied_transform.fingerprint,
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def has_unsaved_changes(self) -> bool:
        return self.selection_dirty or (
            self._clean_state_signature is not None
            and self.state_signature() != self._clean_state_signature
        )

    def export_request(self, clip_name: str, clips_root: Path) -> ExportRequest:
        source_start = self.timeline.source_frame_at(self.timeline.pending_start)
        source_end = self.timeline.source_frame_at(self.timeline.pending_end)
        return build_export_request(
            source_path=Path(self.reader.path),
            clips_root=clips_root,
            clip_name=clip_name,
            source_start_frame=source_start,
            source_end_frame=source_end,
            start_timestamp=self.reader.timestamp_at(source_start),
            end_timestamp=self.reader.timestamp_at(source_end),
            source_num_frames=self.reader.num_frames,
            selection_document=self.selection_document(),
        )


@dataclass
class ViewerExportState:
    confirmation: ExportConfirmation | None = None
    job: Any | None = None
    timer: Any | None = None


@dataclass
class ViewerModeState:
    editing_transform: bool = False


@dataclass(frozen=True)
class ViewerUI:
    figure: Figure
    axes: Axes
    image: AxesImage
    slider: Slider
    range_slider: RangeSlider
    start_box: TextBox
    end_box: TextBox
    confirm_button: Button
    reset_button: Button
    save_button: Button
    load_button: Button
    edit_transform_button: Button
    transform_editor: TransformEditorUI
    transform_status_text: Text
    normal_control_axes: tuple[Axes, ...]
    mode_state: ViewerModeState
    clip_name_box: TextBox
    export_button: Button
    cancel_button: Button
    export_text: Text
    progress_text: Text
    export_state: ViewerExportState
    poll_export: Callable[[], None]
    status_text: Text
    error_text: Text
    session: ViewerSession
    message_details: dict[str, str]
    size_warning_text: Text


def transform_status(transform: FrameTransform, dirty: bool) -> str:
    crop = transform.crop
    state = "applied, unsaved" if dirty else "applied"
    return (
        f"Transform: {state} | angle {transform.rotation.angle_deg:.1f}° | "
        f"crop {crop.x},{crop.y},{crop.width}×{crop.height}"
    )


def launch_viewer(
    path: Path,
    initial_frame: int = 0,
    vmin: float | None = None,
    vmax: float | None = None,
) -> None:
    with ScienceFileReader(path) as reader:
        session = ViewerSession(reader, vmin=vmin, vmax=vmax)
        ui = build_viewer(session, initial_frame=initial_frame)
        plt.show()


def build_viewer(
    session: ViewerSession,
    initial_frame: int = 0,
    clips_root: Path | None = None,
    export_job_factory: Callable[[ExportRequest], Any] = ExportJob,
    dialogs: SelectionDialogs | None = None,
) -> ViewerUI:
    frame = session.frame_at_local(initial_frame)
    limits = session.color_limits
    if limits is None:
        raise RuntimeError("viewer color limits were not initialized")

    clips_root = clips_root or (WORKSPACE_ROOT / "clips")
    figure, axes = plt.subplots(figsize=(12, 10))
    if dialogs is None:
        dialogs = SelectionDialogs(getattr(figure.canvas.manager, "window", None))
    figure.subplots_adjust(bottom=0.56, right=0.87)
    image = axes.imshow(
        frame.temperature_c,
        cmap=session.colormap,
        vmin=limits.vmin,
        vmax=limits.vmax,
        origin="upper",
    )
    colorbar = figure.colorbar(image, ax=axes)
    colorbar.set_label("Temperature (°C)")
    axes.set_xlabel("x (pixel)")
    axes.set_ylabel("y (pixel)")
    _set_frame_title(
        axes,
        frame,
        session.reader.num_frames,
        local_frame=initial_frame,
        local_num_frames=session.timeline.local_num_frames,
    )

    current_axes = figure.add_axes(NORMAL_RECTS["current"])
    range_axes = figure.add_axes(NORMAL_RECTS["range"])
    start_axes = figure.add_axes(NORMAL_RECTS["start"])
    end_axes = figure.add_axes(NORMAL_RECTS["end"])
    confirm_axes = figure.add_axes(NORMAL_RECTS["confirm"])
    reset_axes = figure.add_axes(NORMAL_RECTS["reset"])
    save_axes = figure.add_axes(NORMAL_RECTS["save"])
    load_axes = figure.add_axes(NORMAL_RECTS["load"])
    edit_transform_axes = figure.add_axes(NORMAL_RECTS["edit"])
    clip_name_axes = figure.add_axes(NORMAL_RECTS["clip"])
    export_axes = figure.add_axes(NORMAL_RECTS["export"])
    cancel_axes = figure.add_axes(NORMAL_RECTS["cancel"])
    slider_max = max(1, session.reader.num_frames - 1)
    slider = Slider(
        ax=current_axes,
        label="Current frame",
        valmin=0,
        valmax=slider_max,
        valinit=initial_frame,
        valstep=1,
        valfmt="%0.0f",
    )
    range_slider = RangeSlider(
        ax=range_axes,
        label="Selected range",
        valmin=0,
        valmax=slider_max,
        valinit=(0, session.timeline.local_num_frames - 1),
        valstep=1,
        valfmt="%0.0f",
    )
    start_box = TextBox(start_axes, "Start frame", initial="0")
    end_box = TextBox(
        end_axes,
        "End frame",
        initial=str(session.timeline.local_num_frames - 1),
    )
    confirm_button = Button(confirm_axes, "Confirm range")
    reset_button = Button(reset_axes, "Reset full")
    save_button = Button(save_axes, "Save selection")
    load_button = Button(load_axes, "Load selection")
    edit_transform_button = Button(edit_transform_axes, "Edit transform")
    clip_name_box = TextBox(clip_name_axes, "Clip name", initial="")
    export_button = Button(export_axes, "Export sub-SEQ")
    cancel_button = Button(cancel_axes, "Cancel export")
    cancel_button.set_active(False)
    message_details: dict[str, str] = {}
    export_text = BoundedMessage(figure, "export", message_details, max_lines=3, fontsize=7.5)
    progress_text = BoundedMessage(figure, "progress", message_details, max_lines=1)
    transform_status_text = BoundedMessage(figure, "transform", message_details, max_lines=1)
    status_text = BoundedMessage(figure, "timeline", message_details, max_lines=2)
    error_text = BoundedMessage(figure, "message", message_details, max_lines=2, color="crimson")
    size_warning_text = figure.text(
        0.5, 0.98, "Enlarge window to at least 1000 x 800 logical pixels",
        ha="center", va="top", fontsize=9, color="crimson", visible=False,
    )
    details_notice = figure.text(0.15, 0.016, "", fontsize=7, va="top")
    export_state = ViewerExportState()
    mode_state = ViewerModeState()
    updating = False
    transform_editor: TransformEditorUI

    normal_control_axes = (
        range_axes,
        start_axes,
        end_axes,
        confirm_axes,
        reset_axes,
        save_axes,
        load_axes,
        edit_transform_axes,
        clip_name_axes,
        export_axes,
        cancel_axes,
    )

    def set_error(message: str = "", *, error: bool = True) -> None:
        details_notice.set_text("")
        error_text.set_text(message)
        error_text.set_color("crimson" if error else "forestgreen")
        figure.canvas.draw_idle()

    def set_widget_domain(widget: Slider | RangeSlider, local_max: int) -> None:
        visual_max = max(1, local_max)
        widget.valmin = 0
        widget.valmax = visual_max
        widget.ax.set_xlim(0, visual_max)
        widget.set_active(local_max > 0)
        widget.ax.set_visible(local_max > 0)

    def set_image_array(values: np.ndarray) -> None:
        height, width = values.shape
        image.set_data(values)
        image.set_extent((-0.5, width - 0.5, height - 0.5, -0.5))
        axes.set_xlim(-0.5, width - 0.5)
        axes.set_ylim(height - 0.5, -0.5)

    def refresh_transform_status() -> None:
        if mode_state.editing_transform:
            draft = transform_editor.draft
            crop = draft.crop
            transform_status_text.set_text(
                f"Transform: draft | angle {draft.angle_deg:.1f}° | "
                f"crop {crop.x},{crop.y},{crop.width}×{crop.height}"
            )
            return
        transform_status_text.set_text(
            transform_status(session.applied_transform, session.has_unsaved_changes())
        )

    def refresh_status(current_frame: TemperatureFrame) -> None:
        start_time, end_time = session.work_timestamps()
        relative_s = (current_frame.timestamp - start_time).total_seconds()
        status_text.set_text(
            f"Local frame {session.timeline.current_local_frame}/"
            f"{session.timeline.local_num_frames - 1} | "
            f"Source frame {current_frame.frame_index}/"
            f"{session.reader.num_frames - 1} | "
            f"Work source [{session.timeline.source_start}, "
            f"{session.timeline.source_end}]\n"
            f"Absolute {current_frame.timestamp.isoformat()} | "
            f"Relative {relative_s:.3f} s | "
            f"Duration {(end_time - start_time).total_seconds():.3f} s"
        )

    def show_local(
        local_frame: int,
        sync_slider: bool = True,
        preview_transform: FrameTransform | None = None,
    ) -> None:
        nonlocal updating
        if mode_state.editing_transform:
            session.timeline.set_current(local_frame)
            source_frame = session.timeline.source_frame_at(local_frame)
            transform = preview_transform or transform_editor._preview_transform()
            next_frame, valid_mask = session.preview_frame(source_frame, transform)
            values = np.ma.array(
                next_frame.temperature_c,
                mask=np.logical_or(~valid_mask, ~np.isfinite(next_frame.temperature_c)),
            )
            preview_cmap = plt.get_cmap(session.colormap).copy()
            preview_cmap.set_bad("0.35")
            image.set_cmap(preview_cmap)
            set_image_array(values)
            transform_editor._sync_controls()
        else:
            next_frame = session.frame_at_local(local_frame)
            image.set_cmap(session.colormap)
            set_image_array(next_frame.temperature_c)
        _set_frame_title(
            axes,
            next_frame,
            session.reader.num_frames,
            local_frame=local_frame,
            local_num_frames=session.timeline.local_num_frames,
        )
        refresh_status(next_frame)
        refresh_transform_status()
        if sync_slider:
            updating = True
            try:
                slider.set_val(local_frame)
            finally:
                updating = False
        set_error()
        figure.canvas.draw_idle()

    def sync_all_controls() -> None:
        nonlocal updating
        local_max = session.timeline.local_num_frames - 1
        updating = True
        try:
            set_widget_domain(slider, local_max)
            set_widget_domain(range_slider, local_max)
            slider.set_val(session.timeline.current_local_frame)
            range_slider.set_val(session.timeline.pending_range)
            start_box.set_val(str(session.timeline.pending_start))
            end_box.set_val(str(session.timeline.pending_end))
        finally:
            updating = False
        if mode_state.editing_transform:
            slider.ax.set_visible(True)

    def mark_selection_dirty() -> None:
        session.selection_dirty = True
        refresh_transform_status()

    def on_current(value: float) -> None:
        if not updating:
            show_local(int(round(value)), sync_slider=False)

    def on_range(values: tuple[float, float]) -> None:
        if updating:
            return
        if mode_state.editing_transform:
            set_error("Cannot change the frame range while editing transform")
            return
        old_start, old_end = session.timeline.pending_range
        new_start, new_end = int(round(values[0])), int(round(values[1]))
        if (new_start, new_end) == (old_start, old_end):
            return
        try:
            session.timeline.set_pending(new_start, new_end)
        except (IndexError, ValueError) as exc:
            set_error(str(exc))
            sync_all_controls()
            return
        target = new_start if new_start != old_start else new_end
        invalidate_export_confirmation()
        mark_selection_dirty()
        sync_all_controls()
        show_local(target)

    def submit_endpoint(text: str, endpoint: str) -> None:
        if updating:
            return
        if mode_state.editing_transform:
            set_error("Cannot change the frame range while editing transform")
            return
        before = session.timeline.pending_range
        try:
            value = int(text.strip())
            if endpoint == "start":
                session.timeline.set_pending_start(value)
            else:
                session.timeline.set_pending_end(value)
        except (IndexError, ValueError) as exc:
            set_error(str(exc))
            sync_all_controls()
            return
        if session.timeline.pending_range == before:
            sync_all_controls()
            return
        invalidate_export_confirmation()
        mark_selection_dirty()
        sync_all_controls()
        show_local(value)

    def on_confirm(_event: Any) -> None:
        if mode_state.editing_transform:
            set_error("Cannot confirm the frame range while editing transform")
            return
        if session.timeline.confirm_range():
            invalidate_export_confirmation()
            mark_selection_dirty()
            sync_all_controls()
            show_local(0)

    def on_reset(_event: Any) -> None:
        if mode_state.editing_transform:
            set_error("Cannot reset the frame range while editing transform")
            return
        before = session.state_signature()
        session.timeline.reset_full()
        invalidate_export_confirmation()
        if session.state_signature() != before:
            mark_selection_dirty()
        sync_all_controls()
        show_local(0)

    def on_save(_event: Any) -> None:
        if mode_state.editing_transform:
            set_error("Cannot save a selection while editing transform")
            return
        target = None
        try:
            target = dialogs.choose_save(
                session.selection_store.root,
                session.selection_store.path_for(Path(session.reader.path)).name,
            )
            if target is None:
                return
            expected_target = TargetSnapshot.capture(target)
            expected_backup = TargetSnapshot.capture(target.with_name(target.name + ".bak"))
            if expected_target.exists and not dialogs.confirm(f"Overwrite selection?\n{target}"):
                return
            target = session.save_selection(
                target, expected_target=expected_target, expected_backup=expected_backup,
            )
        except (OSError, ValueError, RuntimeError) as exc:
            set_error(f"Save selection failed: {exc}" + (f"\nFile: {target}" if target is not None else ""))
            return
        refresh_transform_status()
        set_error(f"Saved selection: {target}", error=False)

    def on_load(_event: Any) -> None:
        if mode_state.editing_transform:
            set_error("Cannot load a selection while editing transform")
            return
        target = None
        try:
            target = dialogs.choose_load(session.selection_store.root)
            if target is None:
                return
            document = session.selection_store.load_path(target, session.source_identity())
            session._prepare_selection(document)
            if session.has_unsaved_changes() and not dialogs.confirm(
                "Replace unsaved range, color scale, colormap, and transform?\n"
                f"Load: {target}"
            ):
                return
            session.apply_selection_document(document, path=target)
        except (OSError, ValueError, RuntimeError) as exc:
            set_error(f"Load selection failed: {exc}" + (f"\nFile: {target}" if target is not None else ""))
            return
        image.set_cmap(session.colormap)
        if session.color_limits is None:
            raise RuntimeError("loaded selection has no color limits")
        image.set_clim(session.color_limits.vmin, session.color_limits.vmax)
        invalidate_export_confirmation()
        if mode_state.editing_transform:
            leave_transform_mode(refresh=False)
        sync_all_controls()
        show_local(0)
        refresh_transform_status()
        set_error(f"Loaded selection: {target}", error=False)

    def invalidate_export_confirmation() -> None:
        if export_state.confirmation is None:
            return
        export_state.confirmation = None
        export_button.label.set_text("Export sub-SEQ")
        export_text.set_text("Export confirmation invalidated; review and click again.")
        figure.canvas.draw_idle()

    def set_normal_controls_visible(visible: bool) -> None:
        for control_axis in normal_control_axes:
            control_axis.set_visible(visible)
        export_text.set_visible(visible)
        progress_text.set_visible(visible)

    def leave_transform_mode(*, refresh: bool = True) -> None:
        mode_state.editing_transform = False
        transform_editor.set_visible(False)
        set_normal_controls_visible(True)
        sync_all_controls()
        if refresh:
            show_local(session.timeline.current_local_frame)

    def on_preview_transform(transform: FrameTransform) -> None:
        if not mode_state.editing_transform:
            return
        try:
            show_local(
                session.timeline.current_local_frame,
                preview_transform=transform,
            )
        except (IndexError, ValueError, RuntimeError) as exc:
            set_error(f"Transform preview failed: {exc}")

    def on_apply_transform(_transform: FrameTransform) -> None:
        try:
            transform = transform_editor.draft.build()
            source_frame = session.timeline.source_frame_at(
                session.timeline.current_local_frame
            )
            session.preview_frame(source_frame, transform)
            session.apply_transform(transform)
            invalidate_export_confirmation()
        except (IndexError, ValueError, RuntimeError) as exc:
            set_error(f"Apply transform failed: {exc}")
            return
        leave_transform_mode()
        refresh_transform_status()

    def on_cancel_transform() -> None:
        leave_transform_mode()

    def on_edit_transform(_event: Any) -> None:
        if mode_state.editing_transform:
            set_error("Transform editing is already active")
            return
        job = export_state.job
        if job is not None and job.is_alive():
            set_error("Cannot edit transform while export is running")
            return
        transform_editor.draft = TransformDraft.from_applied(
            session.applied_transform
        )
        transform_editor._sync_controls()
        mode_state.editing_transform = True
        set_normal_controls_visible(False)
        slider.ax.set_visible(True)
        transform_editor.set_visible(True)
        on_preview_transform(transform_editor._preview_transform())

    def set_export_controls_active(active: bool) -> None:
        local_max = session.timeline.local_num_frames - 1
        range_slider.set_active(active and local_max > 0)
        start_box.set_active(active)
        end_box.set_active(active)
        confirm_button.set_active(active)
        reset_button.set_active(active)
        save_button.set_active(active)
        load_button.set_active(active)
        edit_transform_button.set_active(active)
        clip_name_box.set_active(active)
        export_button.set_active(active)
        cancel_button.set_active(not active)

    def current_confirmation() -> ExportConfirmation:
        request = session.export_request(clip_name_box.text, Path(clips_root))
        return preflight_export(
            request,
            work_source_range=(
                session.timeline.source_start,
                session.timeline.source_end,
            ),
        )

    def on_export(_event: Any) -> None:
        if mode_state.editing_transform:
            set_error("Cannot export while editing transform")
            return
        if export_state.job is not None and export_state.job.is_alive():
            return
        try:
            confirmation = current_confirmation()
        except (OSError, ValueError, RuntimeError) as exc:
            invalidate_export_confirmation()
            set_error(f"Export preflight failed: {exc}")
            return

        if export_state.confirmation is None:
            export_state.confirmation = confirmation
            export_button.label.set_text("Confirm export")
            export_text.set_text(confirmation.summary)
            progress_text.set_text("")
            set_error()
            figure.canvas.draw_idle()
            return
        if export_state.confirmation.signature != confirmation.signature:
            export_state.confirmation = confirmation
            export_text.set_text(confirmation.summary)
            set_error("Export selection changed; confirm the updated summary.")
            return

        job = export_job_factory(confirmation.request)
        export_state.job = job
        export_state.confirmation = None
        export_button.label.set_text("Export sub-SEQ")
        set_export_controls_active(False)
        try:
            job.start()
        except Exception as exc:
            set_export_controls_active(True)
            export_state.job = None
            set_error(f"Export start failed: {exc}")
            return
        export_state.timer.start()
        poll_export()

    def on_cancel(_event: Any) -> None:
        if mode_state.editing_transform:
            set_error("Cannot cancel export while editing transform")
            return
        job = export_state.job
        if job is None or not job.is_alive():
            return
        if job.cancel() is False:
            export_text.set_text("Finalizing paired files; cancellation is no longer available.")
        else:
            export_text.set_text("Cancellation requested; waiting for FileSDK to stop.")
        figure.canvas.draw_idle()

    def poll_export() -> None:
        job = export_state.job
        if job is None:
            return
        snapshot = job.progress.snapshot()
        cancel_button.set_active(job.is_alive() and snapshot.cancel_allowed)
        if snapshot.total > 0:
            percent = 100.0 * snapshot.current / snapshot.total
            progress_text.set_text(
                f"Progress: {snapshot.current}/{snapshot.total} ({percent:.1f}%) | {snapshot.message}"
            )
        else:
            progress_text.set_text(f"Status: {snapshot.status.value} | {snapshot.message}")
        if job.is_alive():
            figure.canvas.draw_idle()
            return

        export_state.timer.stop()
        set_export_controls_active(True)
        cancel_button.set_active(False)
        result = job.result
        if result is None:
            export_text.set_text("Export failed: worker ended without a result")
            set_error("Export failed: worker ended without a result")
        else:
            export_text.set_text(result.message)
            if result.status is ExportStatus.SUCCEEDED:
                set_error()
            else:
                set_error(result.message)
        figure.canvas.draw_idle()

    def on_close(_event: Any) -> None:
        job = export_state.job
        if job is not None and job.is_alive():
            job.cancel()
            job.join(timeout=0.25)

    def on_resize(_event: Any) -> None:
        width, height = figure.get_size_inches()
        size_warning_text.set_visible(width < 10 or height < 8)
        figure.canvas.draw_idle()

    def on_key(event: Any) -> None:
        if event.key != "f1":
            return
        hidden = {"export", "progress"} if mode_state.editing_transform else {"editor_error", "editor_status"}
        details = "\n".join(
            f"{key}: {value}" for key, value in message_details.items()
            if key not in hidden and value
        )
        print(details)
        try:
            dialogs.show_details(details)
        except RuntimeError as exc:
            print(f"Details dialog failed: {exc}")
            details_notice.set_text("Dialog unavailable; full details printed to console")
        else:
            details_notice.set_text("Full details also printed to console")
        figure.canvas.draw_idle()

    transform_editor = build_transform_editor(
        figure=figure,
        image_axes=axes,
        initial_transform=session.applied_transform,
        preview_callback=on_preview_transform,
        apply_callback=on_apply_transform,
        cancel_callback=on_cancel_transform,
        message_details=message_details,
    )
    transform_editor.set_visible(False)

    slider.on_changed(on_current)
    range_slider.on_changed(on_range)
    start_box.on_submit(lambda text: submit_endpoint(text, "start"))
    end_box.on_submit(lambda text: submit_endpoint(text, "end"))
    confirm_button.on_clicked(on_confirm)
    reset_button.on_clicked(on_reset)
    save_button.on_clicked(on_save)
    load_button.on_clicked(on_load)
    edit_transform_button.on_clicked(on_edit_transform)
    def on_clip_name_submit(_text: str) -> None:
        if mode_state.editing_transform:
            set_error("Cannot change export settings while editing transform")
            return
        invalidate_export_confirmation()

    clip_name_box.on_submit(on_clip_name_submit)
    export_button.on_clicked(on_export)
    cancel_button.on_clicked(on_cancel)
    figure.canvas.mpl_connect("close_event", on_close)
    figure.canvas.mpl_connect("resize_event", on_resize)
    figure.canvas.mpl_connect("key_press_event", on_key)
    export_state.timer = figure.canvas.new_timer(interval=100)
    export_state.timer.add_callback(poll_export)
    sync_all_controls()
    show_local(initial_frame)

    return ViewerUI(
        figure=figure,
        axes=axes,
        image=image,
        slider=slider,
        range_slider=range_slider,
        start_box=start_box,
        end_box=end_box,
        confirm_button=confirm_button,
        reset_button=reset_button,
        save_button=save_button,
        load_button=load_button,
        edit_transform_button=edit_transform_button,
        transform_editor=transform_editor,
        transform_status_text=transform_status_text,
        normal_control_axes=normal_control_axes,
        mode_state=mode_state,
        clip_name_box=clip_name_box,
        export_button=export_button,
        cancel_button=cancel_button,
        export_text=export_text,
        progress_text=progress_text,
        export_state=export_state,
        poll_export=poll_export,
        status_text=status_text,
        error_text=error_text,
        session=session,
        message_details=message_details,
        size_warning_text=size_warning_text,
    )


def _set_frame_title(
    axes: Axes,
    frame: TemperatureFrame,
    source_num_frames: int,
    local_frame: int,
    local_num_frames: int,
) -> None:
    axes.set_title(
        f"Local frame {local_frame}/{local_num_frames - 1} | "
        f"Source frame {frame.frame_index}/{source_num_frames - 1} | "
        f"{frame.timestamp.isoformat()}"
    )
