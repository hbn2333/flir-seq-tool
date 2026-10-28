from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.image import AxesImage
from matplotlib.text import Text
from .viewer_theme import (ThemedButton as Button, ThemedRangeSlider as RangeSlider,
    ThemedSlider as Slider, ThemedTextBox as TextBox, ViewerTheme, style_plot, MUTED)

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
from .measurement_state import AnalysisKey, MeasureState
from .measurement_panel import MeasurePanel, build_measure_panel
from .measurement_job import AnalysisJob, AnalysisRequest
from .measurement_export import AnalysisExportSnapshot, export_csv, export_png
from .reader import ScienceFileReader
from .selection import (
    DisplaySettings,
    SelectionDocument,
    SelectionStore,
    SourceIdentity,
    build_selection_document,
    validate_source_identity,
    transform_to_dict,
)
from .selection_dialogs import SelectionDialogs
from .file_safety import TargetSnapshot
from .export_bundle import sidecar_path, pending_path
from .timeline import TimelineState
from .transform import FrameTransform
from .transform_editor import TransformDraft, TransformEditorUI, build_transform_editor
from .viewer_layout import (BoundedMessage, NORMAL_RECTS, NAV_RECTS, MEASURE_IMAGE_RECT,
                            MEASURE_COLORBAR_RECT, MEASURE_CURRENT_RECT, MEASURE_RESULT_RECT,
                            MEASURE_CURVE_DELTA_RECT, label_above)
from .viewer_pages import PageCoordinator, PageGroup
from .video_controller import VideoController
from .video_export import VideoJob
from .runtime_paths import workspace_root


WORKSPACE_ROOT = workspace_root()


def _explicit_color_limits(vmin: float | None, vmax: float | None) -> ColorLimits | None:
    if (vmin is None) != (vmax is None):
        raise ValueError("vmin and vmax must be provided together")
    if vmin is None:
        return None
    if not math.isfinite(vmin) or not math.isfinite(vmax):
        raise ValueError("vmin and vmax must be finite")
    if vmin >= vmax:
        raise ValueError("vmin must be less than vmax")
    return ColorLimits(vmin=float(vmin), vmax=float(vmax))


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
        explicit_limits = _explicit_color_limits(vmin, vmax)
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
        self.process_saved_signature: str | None = None
        self.measure_saved_signature: str | None = None
        self.measurement = MeasureState()
        self._retired_measurement_stores: list[Any] = []
        self.measurement_cleanup_error: str | None = None
        self.timeline = TimelineState(reader.num_frames)
        self._work_time_key: tuple[int, int] | None = None
        self._work_time_value: tuple[datetime, datetime] | None = None
        self.color_limits = explicit_limits
        if self.color_limits is not None:
            self._establish_saved_signatures()

    def _check_pending(self) -> None:
        marker = pending_path(Path(self.reader.path))
        if marker.exists() or marker.is_symlink():
            raise RuntimeError(f"unresolved export pending marker; view configuration blocked: {marker}")

    def restore_startup(
        self, *, initial_frame: int | None = None,
        vmin: float | None = None, vmax: float | None = None,
    ) -> tuple[int, tuple[str, ...]]:
        """Restore only the source's sibling sidecar, before the first frame read."""
        limits = _explicit_color_limits(vmin, vmax)
        if initial_frame is not None and not 0 <= initial_frame < self.reader.num_frames:
            raise IndexError(f"frame index out of range: {initial_frame}")
        config = sidecar_path(Path(self.reader.path))
        notices: list[str] = []
        try:
            self._check_pending()
            if config.exists():
                self.load_selection(config)
                notices.append(f"Restored view configuration: {config}")
        except (OSError, ValueError, RuntimeError) as exc:
            notices.append(f"View configuration not restored: {config}\n{exc}")
        if limits is not None:
            if self.selection_path is not None and limits != self.color_limits:
                self.selection_dirty = True
            self.color_limits = limits
        local_frame = 0
        if initial_frame is not None:
            try:
                local_frame = self.timeline.local_frame_at(initial_frame)
            except IndexError:
                notices.append(f"Requested source frame {initial_frame} is outside restored range; showing its first frame.")
        self.timeline.set_current(local_frame)
        return local_frame, tuple(notices)

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
        if self.process_saved_signature is None:
            self._establish_saved_signatures()
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
        self.release_measurement_store(self.measurement.clear())
        self.selection_dirty = True

    def release_measurement_store(self, store: Any | None) -> None:
        """Retain cleanup ownership on failure without undoing a committed state change."""
        if store is None:
            return
        self._retired_measurement_stores.append(store)
        self._cleanup_retired_measurement_stores()

    def _cleanup_retired_measurement_stores(self) -> None:
        self.measurement_cleanup_error = None
        for store in tuple(self._retired_measurement_stores):
            try:
                store.cleanup()
            except Exception as exc:
                self.measurement_cleanup_error = str(exc)
            else:
                self._retired_measurement_stores.remove(store)

    def cleanup_measurement_results(self) -> None:
        previous = self.measurement.clear()
        if previous is not None:
            self._retired_measurement_stores.append(previous)
        self._cleanup_retired_measurement_stores()

    def frame_at_local(self, local_frame: int) -> TemperatureFrame:
        frame = self.frame_at(self.timeline.source_frame_at(local_frame))
        self.timeline.set_current(local_frame)
        return frame

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

    def measure_source_range(self) -> tuple[int, int]:
        return self.timeline.source_start, self.timeline.source_end

    def selection_source_range(self, scope: str) -> tuple[int, int]:
        if scope == "measure":
            return self.measure_source_range()
        if scope == "process":
            return (self.timeline.source_frame_at(self.timeline.pending_start),
                    self.timeline.source_frame_at(self.timeline.pending_end))
        raise ValueError("unknown selection scope")

    def confirm_range(self) -> bool:
        changed = self.timeline.confirm_range()
        if changed:
            self.measurement.invalidate()
        return changed

    def reset_full(self) -> None:
        before = self.measure_source_range()
        self.timeline.reset_full()
        if self.measure_source_range() != before:
            self.measurement.invalidate()

    def analysis_key(self) -> AnalysisKey:
        return AnalysisKey.from_inputs(self.source_identity(), *self.measure_source_range(),
                                       self.applied_transform, self.measurement.settings.rois)

    def selection_document(self, *, scope: str = "process") -> SelectionDocument:
        if self.color_limits is None:
            raise RuntimeError("viewer color limits were not initialized")
        source_start, source_end = self.selection_source_range(scope)
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
            measurement=self.measurement.settings,
        )

    def save_selection(
        self, target: Path | None = None, *,
        scope: str = "process",
        expected_target: TargetSnapshot | None = None,
        expected_backup: TargetSnapshot | None = None,
    ) -> Path:
        if target is None:
            target = self.selection_store.save(Path(self.reader.path), self.selection_document(scope=scope))
        else:
            if expected_target is None or expected_backup is None:
                raise ValueError("explicit selection save requires confirmed target snapshots")
            target = self.selection_store.save_to(
                target, self.selection_document(scope=scope), source_path=Path(self.reader.path),
                expected_target=expected_target, expected_backup=expected_backup,
            )
        self.selection_path = target
        self.selection_dirty = False
        if scope == "process":
            self.process_saved_signature = self.state_signature(scope="process")
        else:
            self.measure_saved_signature = self.state_signature(scope="measure")
        if self.selection_source_range("process") == self.measure_source_range():
            self._establish_saved_signatures()
        return target

    def load_selection(self, target: Path | None = None) -> SelectionDocument:
        self._check_pending()
        target = self.selection_store.path_for(Path(self.reader.path)) if target is None else Path(target)
        document = self.selection_store.load_path(target, self.source_identity())
        self.apply_selection_document(document, path=target)
        return document

    def _prepare_selection(self, document: SelectionDocument):
        self._check_pending()
        validate_source_identity(self.source_identity(), document.source)
        if document.display.colormap not in plt.colormaps():
            raise ValueError(f"unknown colormap: {document.display.colormap}")
        self._validate_transform(document.transform)
        document.measurement.validate_bounds(document.transform.crop.width, document.transform.crop.height)
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
        previous = self.measurement.clear()
        self.measurement.settings = document.measurement
        self.selection_dirty = False
        self._establish_saved_signatures()
        self.selection_path = Path(path)
        self.release_measurement_store(previous)

    def _establish_saved_signatures(self) -> None:
        self.process_saved_signature = self.state_signature(scope="process")
        self.measure_saved_signature = self.state_signature(scope="measure")

    def state_signature(self, *, scope: str = "process") -> str:
        limits = self.color_limits
        payload = {
            "source_range": self.selection_source_range(scope),
            "color_limits": None
            if limits is None
            else [limits.vmin, limits.vmax],
            "colormap": self.colormap,
            "transform": self.applied_transform.fingerprint,
        }
        if scope == "measure":
            payload["measurement"] = self.measurement.settings.to_dict()
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def has_unsaved_changes(self) -> bool:
        return self.selection_dirty or (
            self.process_saved_signature is not None
            and self.state_signature(scope="process") != self.process_saved_signature
        ) or (
            self.measure_saved_signature is not None
            and self.state_signature(scope="measure") != self.measure_saved_signature
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


@dataclass
class ViewerAnalysisState:
    job: Any | None = None
    request: AnalysisRequest | None = None
    timer: Any | None = None
    closed: bool = False


@dataclass(frozen=True)
class ViewerUI:
    figure: Figure
    axes: Axes
    image: AxesImage
    slider: Slider
    current_frame_box: TextBox
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
    process_button: Button
    measure_button: Button
    pages: PageCoordinator
    measure_panel: MeasurePanel
    analysis_state: ViewerAnalysisState
    poll_analysis: Callable[[], None]
    shutdown_analysis: Callable[..., None]
    video_button: Button
    video_controller: VideoController
    poll_video: Callable[[], None]

    @property
    def analysis_job(self):
        return self.analysis_state.job


def transform_status(transform: FrameTransform, dirty: bool) -> str:
    crop = transform.crop
    state = "applied, unsaved" if dirty else "applied"
    return (
        f"Transform: {state} | angle {transform.rotation.angle_deg:.1f}° | "
        f"crop {crop.x},{crop.y},{crop.width}×{crop.height}"
    )


def launch_viewer(
    path: Path,
    initial_frame: int | None = None,
    vmin: float | None = None,
    vmax: float | None = None,
) -> None:
    with ScienceFileReader(path) as reader:
        session = ViewerSession(reader)
        local_frame, notices = session.restore_startup(initial_frame=initial_frame, vmin=vmin, vmax=vmax)
        ui = build_viewer(session, initial_frame=local_frame)
        if notices:
            message = "\n".join(notices)
            ui.message_details["startup"] = message
            ui.error_text.set_text(message)
            print(message)
        try:
            plt.show()
        finally:
            ui.video_controller.close()
            ui.shutdown_analysis(wait=True)


def build_viewer(
    session: ViewerSession,
    initial_frame: int = 0,
    clips_root: Path | None = None,
    export_job_factory: Callable[[ExportRequest], Any] = ExportJob,
    dialogs: SelectionDialogs | None = None,
    analysis_job_factory: Callable[[AnalysisRequest], Any] = AnalysisJob,
    video_job_factory: Callable = VideoJob,
) -> ViewerUI:
    frame = session.frame_at_local(initial_frame)
    current_frame = frame
    limits = session.color_limits
    if limits is None:
        raise RuntimeError("viewer color limits were not initialized")

    clips_root = clips_root or (WORKSPACE_ROOT / "clips")
    figure, axes = plt.subplots(figsize=(12, 10))
    theme = ViewerTheme(figure)
    figure.canvas.manager.set_window_title("FLIR SEQ Tool")
    if dialogs is None:
        dialogs = SelectionDialogs(getattr(figure.canvas.manager, "window", None))
    figure.subplots_adjust(bottom=0.56, right=0.87, top=0.90)
    image = axes.imshow(
        frame.temperature_c,
        cmap=session.colormap,
        vmin=limits.vmin,
        vmax=limits.vmax,
        origin="upper",
    )
    colorbar = figure.colorbar(image, ax=axes)
    colorbar.set_label("Temperature (°C)")
    process_image_rect = axes.get_position(original=True).frozen()
    process_colorbar_rect = colorbar.ax.get_position(original=True).frozen()
    process_button = Button(figure.add_axes(NAV_RECTS["process"]), "Process")
    measure_button = Button(figure.add_axes(NAV_RECTS["measure"]), "Measure")
    page_title = figure.text(.39, .972, "", fontsize=9, va="center", parse_math=False)
    measure_placeholder = figure.add_axes(MEASURE_RESULT_RECT)
    measure_delta_axes = figure.add_axes(MEASURE_CURVE_DELTA_RECT)
    measure_delta_axes.set_visible(False)
    measure_placeholder.set_axis_off()
    measure_placeholder.text(.5, .5, "Measurement uses the confirmed work range",
                             ha="center", va="center", transform=measure_placeholder.transAxes)
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
    export_axes = figure.add_axes(NORMAL_RECTS["export"])
    video_axes = figure.add_axes(NORMAL_RECTS["video"])
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
    current_frame_box = TextBox(figure.add_axes((.64, .535, .04, .025)), "Frame", initial=str(initial_frame))
    label_above(current_frame_box)
    current_frame_box.label.set_fontsize(8)
    current_frame_box.text_disp.set_clip_on(True)
    current_frame_box.text_disp.set_clip_path(current_frame_box.ax.patch)
    end_box = TextBox(
        end_axes,
        "End frame",
        initial=str(session.timeline.local_num_frames - 1),
    )
    confirm_button = Button(confirm_axes, "Confirm range", role="primary")
    reset_button = Button(reset_axes, "Reset full")
    save_button = Button(save_axes, "Save selection")
    load_button = Button(load_axes, "Load selection")
    edit_transform_button = Button(edit_transform_axes, "Edit transform")
    export_button = Button(export_axes, "Export sub-SEQ")
    video_button = Button(video_axes, "Export MP4")
    cancel_button = Button(cancel_axes, "Cancel export")
    cancel_button.set_active(False)
    message_details: dict[str, str] = {}
    export_text = BoundedMessage(figure, "export", message_details, max_lines=5, fontsize=7.5)
    progress_text = BoundedMessage(figure, "progress", message_details, max_lines=1)
    transform_status_text = BoundedMessage(figure, "transform", message_details, max_lines=1)
    status_text = BoundedMessage(figure, "timeline", message_details, max_lines=2)
    error_text = BoundedMessage(figure, "message", message_details, max_lines=2, color="crimson")
    size_warning_text = figure.text(
        0.5, 0.945, "Enlarge window to at least 1000 x 800 logical pixels",
        ha="center", va="top", fontsize=9, color="crimson", visible=False,
    )
    details_notice = figure.text(0.15, 0.016, "", fontsize=7, va="top")
    export_state = ViewerExportState()
    mode_state = ViewerModeState()
    analysis_state = ViewerAnalysisState()
    updating = False
    transform_editor: TransformEditorUI
    pages: PageCoordinator

    normal_control_axes = (
        range_axes,
        start_axes,
        end_axes,
        confirm_axes,
        reset_axes,
        save_axes,
        load_axes,
        edit_transform_axes,
        export_axes,
        video_axes,
        cancel_axes,
    )

    def process_input_available() -> bool:
        if pages.current != "process" or pages.busy is not None:
            set_error("Process controls are unavailable on this page or during a task")
            return False
        return True

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
        nonlocal updating, current_frame
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
        current_frame = next_frame
        if pages.current == "measure":
            measure_panel.current_local_frame = local_frame
            measure_panel.frame_count = session.timeline.local_num_frames
            measure_panel.refresh_frame(next_frame)
        _set_frame_title(
            axes,
            next_frame,
            session.reader.num_frames,
            local_frame=local_frame,
            local_num_frames=session.timeline.local_num_frames,
        )
        refresh_status(next_frame)
        refresh_transform_status()
        updating = True
        try:
            current_frame_box.set_val(str(local_frame))
        finally:
            updating = False
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
            current_frame_box.set_val(str(session.timeline.current_local_frame))
            range_slider.set_val(session.timeline.pending_range)
            start_box.set_val(str(session.timeline.pending_start))
            end_box.set_val(str(session.timeline.pending_end))
        finally:
            updating = False
        if mode_state.editing_transform:
            slider.ax.set_visible(True)
            range_slider.set_active(False)
            range_slider.ax.set_visible(False)
        pages.refresh()

    def mark_selection_dirty() -> None:
        session.selection_dirty = True
        refresh_transform_status()

    def on_current(value: float) -> None:
        if not updating and pages.busy is None:
            show_local(int(round(value)), sync_slider=False)

    def on_current_frame_submit(text):
        nonlocal updating
        if updating or pages.current != "measure" or pages.busy is not None or mode_state.editing_transform:
            return
        try:
            value = int(text.strip())
            if not 0 <= value < session.timeline.local_num_frames:
                raise ValueError("Frame is outside the confirmed range")
            show_local(value)
        except (ValueError, OSError, RuntimeError) as exc:
            updating = True
            try:
                current_frame_box.set_val(str(session.timeline.current_local_frame))
            finally:
                updating = False
            set_error(str(exc))

    def on_range(values: tuple[float, float]) -> None:
        if updating:
            return
        if mode_state.editing_transform:
            set_error("Cannot change the frame range while editing transform")
            return
        if not process_input_available():
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
        if not process_input_available():
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
        if not process_input_available():
            return
        if session.confirm_range():
            invalidate_export_confirmation()
            mark_selection_dirty()
            sync_all_controls()
            show_local(0)

    def on_reset(_event: Any) -> None:
        if mode_state.editing_transform:
            set_error("Cannot reset the frame range while editing transform")
            return
        if not process_input_available():
            return
        before = session.state_signature()
        session.reset_full()
        invalidate_export_confirmation()
        if session.state_signature() != before:
            mark_selection_dirty()
        sync_all_controls()
        show_local(0)

    def on_save(_event: Any = None, *, scope="process") -> None:
        if mode_state.editing_transform:
            set_error("Cannot save a selection while editing transform")
            return
        if scope == "process" and not process_input_available():
            return
        if scope == "measure" and (pages.current != "measure" or pages.busy is not None):
            return
        target = None
        try:
            options = {} if scope == "process" else {"title": f"Save setup: confirmed source {session.measure_source_range()}"}
            target = dialogs.choose_save(
                session.selection_store.root,
                session.selection_store.path_for(Path(session.reader.path)).name,
                **options,
            )
            if target is None:
                return
            expected_target = TargetSnapshot.capture(target)
            expected_backup = TargetSnapshot.capture(target.with_name(target.name + ".bak"))
            if expected_target.exists and not dialogs.confirm(f"Overwrite selection?\n{target}"):
                return
            scope_options = {} if scope == "process" else {"scope": "measure"}
            target = session.save_selection(
                target, expected_target=expected_target, expected_backup=expected_backup,
                **scope_options,
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
        if not process_input_available():
            return
        target = None
        try:
            target = dialogs.choose_load(session.selection_store.root)
            if target is None:
                return
            document = session.selection_store.load_path(target, session.source_identity())
            session._prepare_selection(document)
            if session.has_unsaved_changes() and not dialogs.confirm(
                "Replace unsaved range, color scale, colormap, and transform? (Includes ROI setup.)\n"
                f"Load: {target}"
            ):
                return
            session.apply_selection_document(document, path=target)
            measure_panel.clear_current_analysis()
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
        # Normal/editor controls share screen space. Visibility alone does not
        # stop Matplotlib widgets from grabbing mouse input in the other mode.
        for control in (
            start_box, end_box, confirm_button, reset_button, save_button,
            load_button, edit_transform_button, export_button, video_button,
        ):
            control.set_active(visible)
        range_slider.set_active(visible and session.timeline.local_num_frames > 1)
        # Mode changes are only allowed when no export is running.
        cancel_button.set_active(False)
        export_text.set_visible(visible)
        progress_text.set_visible(visible)
        pages.refresh()

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
            if transform != session.applied_transform and session.measurement.settings.rois:
                count = len(session.measurement.settings.rois)
                if not dialogs.confirm(f"Apply changed transform and clear {count} ROI(s) and measurement results?"):
                    return
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
        if not process_input_available():
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
        export_button.set_active(active)
        cancel_button.set_active(not active)
        pages.set_busy(None if active else "native_export")

    def current_confirmation(target: Path) -> ExportConfirmation:
        request = session.export_request(target.name, target.parent)
        request = replace(request, target_path=target)
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
        if not process_input_available():
            return
        if export_state.job is not None and export_state.job.is_alive():
            return
        try:
            if export_state.confirmation is None:
                source_start = session.timeline.source_frame_at(session.timeline.pending_start)
                source_end = session.timeline.source_frame_at(session.timeline.pending_end)
                suggested = f"{Path(session.reader.path).stem}-{source_start}-{source_end}.seq"
                target = dialogs.choose_seq_export(Path(clips_root), suggested)
                if target is None:
                    return
                target = Path(target).absolute()
            else:
                target = export_state.confirmation.request.target_path
            confirmation = current_confirmation(target)
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
        if pages.busy == "video_export":
            video_controller.cancel()
            return
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
        if pages.busy in ("analysis", "analysis_export", "video_export"):
            return
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
        video_timer.stop()
        video_controller.close()
        shutdown_analysis(wait=False)
        pages.dispose()
        measure_panel.dispose()
        job = export_state.job
        if job is not None and job.is_alive():
            job.cancel()
            job.join(timeout=0.25)

    def restore_analysis_status(current_key, *, fallback):
        state = session.measurement
        if state.result_store is not None and state.result_key == current_key:
            state.status = "complete"
        else:
            state.status = "stale" if state.result_store is not None else fallback

    def on_calculate():
        if (pages.current != "measure" or pages.busy is not None or mode_state.editing_transform
                or measure_panel.adding or not session.measurement.settings.rois or analysis_state.closed):
            return
        try:
            source = session.source_identity()
            start, end = session.measure_source_range()
            key = AnalysisKey.from_inputs(source, start, end, session.applied_transform, session.measurement.settings.rois)
            request = AnalysisRequest(Path(session.reader.path), source, start, end, session.applied_transform,
                                      session.measurement.settings.rois, key)
            job = analysis_job_factory(request)
        except Exception as exc:
            set_error(f"Analysis could not start: {exc}")
            return
        analysis_state.request, analysis_state.job = request, job
        session.measurement.status = "running"
        measure_panel.set_busy(True)
        pages.set_busy("analysis")
        progress_text.set_visible(True)
        try:
            job.start()
        except Exception as exc:
            analysis_state.job = None
            restore_analysis_status(key, fallback="failed")
            pages.set_busy(None)
            sync_all_controls()
            set_error(f"Analysis start failed: {exc}")
            return
        analysis_state.timer.start()
        poll_analysis()

    def on_cancel_analysis():
        job = analysis_state.job
        if pages.busy == "analysis" and job is not None and job.is_alive():
            job.cancel()
            progress_text.set_text("Cancelling analysis; waiting for the current SDK call")
            figure.canvas.draw_idle()

    def poll_analysis():
        job = analysis_state.job
        if job is None or analysis_state.closed:
            return
        snapshot = job.progress.snapshot()
        progress_text.set_text(f"Analysis: {snapshot.completed}/{snapshot.total} | {snapshot.message}")
        if job.is_alive():
            measure_panel.cancel_button.set_active(True)
            figure.canvas.draw_idle()
            return
        analysis_state.timer.stop()
        outcome = job.outcome
        state = session.measurement
        current_key = None
        error = None
        try:
            # This is intentionally after is_alive() is false: never read the UI
            # SDK reader while the worker still owns its independent reader.
            current_key = session.analysis_key()
        except Exception as exc:
            error = str(exc)
        candidate = None if outcome is None else outcome.store
        valid = outcome is not None and outcome.status == "complete" and outcome.key == current_key
        if valid:
            try:
                valid = candidate.key_json == current_key.to_json()
            except Exception as exc:
                valid, error = False, str(exc)
        if valid:
            previous = state.result_store
            state.result_store, state.result_key, state.status = candidate, outcome.key, "complete"
            if previous is not candidate:
                session.release_measurement_store(previous)
            progress_text.set_text(f"Analysis complete: {snapshot.total} frames")
            set_error("Analysis complete", error=False)
        else:
            if candidate is not None:
                session.release_measurement_store(candidate)
            fallback = "failed" if outcome is None or outcome.status == "complete" else outcome.status
            restore_analysis_status(current_key, fallback=fallback)
            error = error or ("Worker ended without an outcome" if outcome is None else outcome.error)
            if outcome is not None and outcome.status == "complete":
                error = error or "Analysis inputs changed before publication"
            set_error(error or "Analysis cancelled", error=fallback != "cancelled")
        if outcome is not None:
            session.release_measurement_store(outcome.cleanup_store)
        analysis_state.job = None
        analysis_state.request = None
        pages.set_busy(None)
        sync_all_controls()
        if session.measurement_cleanup_error:
            set_error(f"Result cleanup pending: {session.measurement_cleanup_error}")
        figure.canvas.draw_idle()

    def shutdown_analysis(*, wait=False):
        analysis_state.closed = True
        if analysis_state.timer is not None:
            analysis_state.timer.stop()
        job = analysis_state.job
        if job is not None:
            if job.is_alive():
                job.cancel()
            if wait:
                job.join()
            if job.is_alive():
                return
            outcome = job.outcome
            if outcome is not None:
                if outcome.store is not session.measurement.result_store:
                    session.release_measurement_store(outcome.store)
                session.release_measurement_store(outcome.cleanup_store)
        session.cleanup_measurement_results()

    def on_resize(_event: Any) -> None:
        width, height = figure.get_size_inches()
        size_warning_text.set_visible(width < 10 or height < 8)
        figure.canvas.draw_idle()

    def on_key(event: Any) -> None:
        if event.key != "f1":
            return
        message_details["ROIs"] = "\n".join(f"{roi.id}: {roi.name}" for roi in session.measurement.settings.rois)
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

    def on_roi_geometry_change():
        session.measurement.invalidate()
        refresh_transform_status()

    measure_panel = build_measure_panel(figure, axes, session.measurement,
                                        on_roi_geometry_change, refresh_transform_status, dialogs)
    measure_panel.set_visible(False)
    measure_panel.set_result_axes(measure_placeholder, measure_delta_axes)
    measure_panel.on_calculate = on_calculate
    measure_panel.on_cancel = on_cancel_analysis
    measure_panel.on_save_setup = lambda: on_save(scope="measure")

    def can_export_analysis():
        # Availability never touches the SDK reader: analysis owns it while busy.
        if pages.busy is not None or measure_panel.adding:
            return False
        state = session.measurement
        if state.settings.result_view == "current":
            return measure_panel.current_analysis is not None
        key = state.result_key
        return (any(roi.checked for roi in state.settings.rois) and key is not None and state.is_current(key)
                and (key.source_start, key.source_end) == session.measure_source_range()
                and key.transform_fingerprint == session.applied_transform.fingerprint
                and key.roi_geometry == tuple(sorted((roi.id, roi.x, roi.y, roi.width, roi.height)
                                                      for roi in state.settings.rois)))

    def on_analysis_export(kind):
        if pages.current != "measure" or not can_export_analysis():
            return
        target = None
        try:
            identity = session.source_identity()
            state = session.measurement
            if state.settings.result_view == "curves" and not state.is_current(session.analysis_key()):
                raise ValueError("Analysis inputs changed; Calculate again")
            limits = session.color_limits
            snapshot = AnalysisExportSnapshot(
                identity, Path(session.reader.path), session.measure_source_range(), session.work_timestamps()[0],
                json.dumps(transform_to_dict(session.applied_transform), allow_nan=False),
                DisplaySettings(session.colormap, limits.vmin, limits.vmax), state.settings,
                current_frame.frame_index, session.timeline.current_local_frame, current_frame.timestamp,
                current_frame.temperature_c, measure_panel.current_analysis, state.result_path)
            measure_panel.set_busy(True)
            pages.set_busy("analysis_export")
            target = dialogs.choose_analysis_export(kind, WORKSPACE_ROOT / "analysis",
                        f"{Path(session.reader.path).stem}-{snapshot.source_range[0]}-{snapshot.source_range[1]}.{kind}")
            if target is None:
                return
            expected = TargetSnapshot.capture(target)
            if expected.exists and not dialogs.confirm(f"Overwrite {kind.upper()}?\n{target}"):
                return
            writer = export_csv if kind == "csv" else export_png
            writer(target, snapshot, expected=expected, raw_root=session.selection_store.raw_root)
            set_error(f"Exported {kind.upper()}: {target}", error=False)
        except (OSError, ValueError, RuntimeError) as exc:
            set_error(f"Analysis export failed: {exc}" + (f"\nFile: {target}" if target is not None else ""))
        finally:
            if pages.busy == "analysis_export":
                pages.set_busy(None)
                sync_all_controls()

    measure_panel.can_export = can_export_analysis
    measure_panel.on_export = on_analysis_export

    process_widgets = (range_slider, start_box, end_box, confirm_button, reset_button,
                       save_button, load_button, edit_transform_button,
                       export_button, video_button, cancel_button)
    def process_availability(widget) -> bool:
        if mode_state.editing_transform:
            return False
        if widget is range_slider:
            return session.timeline.local_num_frames > 1
        if widget is cancel_button:
            return False
        return True

    pages = PageCoordinator(figure.canvas, {
        "process": PageGroup(normal_control_axes, process_widgets, texts=(export_text,),
                             available=process_availability,
                             available_during_busy=lambda widget, kind: (
                                 widget is cancel_button and ((kind == "native_export"
                                 and export_state.job is not None and export_state.job.is_alive()
                                 and export_state.job.progress.snapshot().cancel_allowed)
                                 or (kind == "video_export" and video_controller.cancel_allowed)))),
        "measure": PageGroup((measure_placeholder, measure_delta_axes, current_frame_box.ax),
                             (current_frame_box,), available=lambda widget: session.timeline.local_num_frames > 1,
                             on_visibility=measure_panel.set_visible),
    }, editing_transform=lambda: mode_state.editing_transform, on_error=set_error)

    def refresh_page(page: str) -> None:
        measure = page == "measure"
        theme.show_page(page, editing=mode_state.editing_transform)
        measure_panel.set_busy(pages.busy is not None)
        measure_panel.cancel_button.set_active(pages.busy == "analysis" and analysis_state.job is not None
                                              and analysis_state.job.is_alive())
        measure_panel.current_local_frame = session.timeline.current_local_frame
        measure_panel.frame_count = session.timeline.local_num_frames
        if measure and pages.busy is None:
            message_details["ROIs"] = "\n".join(f"{roi.id}: {roi.name}" for roi in session.measurement.settings.rois)
            measure_panel.resampled = session.applied_transform.rotation.angle_deg % 90 != 0
            measure_panel.refresh_frame(current_frame)
        axes.set_position(MEASURE_IMAGE_RECT if measure else process_image_rect)
        colorbar.ax.set_position(MEASURE_COLORBAR_RECT if measure else process_colorbar_rect)
        slider.ax.set_position(MEASURE_CURRENT_RECT if measure else NORMAL_RECTS["current"])
        slider.valtext.set_visible(not measure)
        axes.title.set_fontsize(9 if measure else 12)
        slider.set_active(pages.busy is None and session.timeline.local_num_frames > 1)
        slider.ax.set_visible(session.timeline.local_num_frames > 1 or mode_state.editing_transform)
        range_slider.ax.set_visible(not measure and not mode_state.editing_transform
                                    and session.timeline.local_num_frames > 1)
        if mode_state.editing_transform:
            for control_axis in normal_control_axes:
                control_axis.set_visible(False)
            export_text.set_visible(False)
            progress_text.set_visible(False)
        nav_active = pages.busy is None and not mode_state.editing_transform
        process_button.set_active(nav_active)
        measure_button.set_active(nav_active)
        process_button.set_selected(not measure)
        measure_button.set_selected(measure)
        source_name = Path(getattr(session.reader, "path", "SEQ")).name
        page_title.set_text(f"{source_name[:38]} | work {session.timeline.source_start}–{session.timeline.source_end}")
        if measure and session.selection_source_range("process") != session.measure_source_range():
            set_error("Using confirmed range; the Process candidate range is not applied", error=False)
    pages.on_refresh = refresh_page

    def video_busy(busy):
        pages.set_busy("video_export" if busy else None)
        if busy:
            progress_text.set_text("")
        figure.canvas.draw_idle()

    def video_message(message):
        export_text.set_text(message)
        figure.canvas.draw_idle()

    video_controller = VideoController(session, dialogs, Path(clips_root), video_job_factory,
        on_busy=video_busy, on_message=video_message)
    video_timer = figure.canvas.new_timer(interval=100)

    def poll_video():
        video_controller.poll()
        if pages.busy == "video_export":
            cancel_button.set_active(video_controller.cancel_allowed)
        if video_controller.job is None:
            video_timer.stop()

    video_timer.add_callback(poll_video)

    def on_video(_event):
        if mode_state.editing_transform or not process_input_available():
            return
        invalidate_export_confirmation()
        video_controller.start()
        if video_controller.job is not None:
            video_timer.start()
            pages.refresh()

    video_button.on_clicked(on_video)
    process_button.on_clicked(lambda event: pages.switch("process"))
    measure_button.on_clicked(lambda event: pages.switch("measure"))

    slider.on_changed(on_current)
    current_frame_box.on_submit(on_current_frame_submit)
    range_slider.on_changed(on_range)
    start_box.on_submit(lambda text: submit_endpoint(text, "start"))
    end_box.on_submit(lambda text: submit_endpoint(text, "end"))
    confirm_button.on_clicked(on_confirm)
    reset_button.on_clicked(on_reset)
    save_button.on_clicked(on_save)
    load_button.on_clicked(on_load)
    edit_transform_button.on_clicked(on_edit_transform)
    export_button.on_clicked(on_export)
    cancel_button.on_clicked(on_cancel)
    figure.canvas.mpl_connect("close_event", on_close)
    figure.canvas.mpl_connect("resize_event", on_resize)
    figure.canvas.mpl_connect("key_press_event", on_key)
    export_state.timer = figure.canvas.new_timer(interval=100)
    export_state.timer.add_callback(poll_export)
    analysis_state.timer = figure.canvas.new_timer(interval=100)
    analysis_state.timer.add_callback(poll_analysis)
    theme.style_figure_text(figure)
    page_title.set_color(MUTED)
    style_plot(axes)
    style_plot(colorbar.ax)
    process_button.label.set_fontsize(10)
    measure_button.label.set_fontsize(10)
    sync_all_controls()
    show_local(initial_frame)

    return ViewerUI(
        figure=figure,
        axes=axes,
        image=image,
        slider=slider,
        current_frame_box=current_frame_box,
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
        process_button=process_button,
        measure_button=measure_button,
        pages=pages,
        measure_panel=measure_panel,
        analysis_state=analysis_state,
        poll_analysis=poll_analysis,
        shutdown_analysis=shutdown_analysis,
        video_button=video_button,
        video_controller=video_controller,
        poll_video=poll_video,
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
