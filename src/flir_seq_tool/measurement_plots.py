"""Measurement plots shared by the live view and report figures."""

import numpy as np


def draw_distribution(ax, result):
    ax.clear()
    ax.set_axis_on()
    ax.set(xlabel="Temperature (°C)", ylabel="Valid pixel fraction")
    if not result.valid_pixels:
        ax.text(.5, .5, "No finite pixels", transform=ax.transAxes, ha="center")
        return
    ax.bar(result.edges[:-1], result.fractions, width=np.diff(result.edges), align="edge")
    ax.set_title("Current-frame automatic bin range", fontsize=9)


def draw_profile(ax, result):
    ax.clear()
    ax.set_axis_on()
    ax.plot(result.positions, result.means_c, marker="o" if len(result.positions) == 1 else None)
    label = "x (cropped pixel, rightward)" if result.direction == "horizontal" else "y (cropped pixel, downward)"
    ax.set(xlabel=label, ylabel="Mean temperature (°C)")


def draw_frame_curves(temp_ax, delta_ax, store, settings, current_local, frame_count):
    for ax in (temp_ax, delta_ax):
        ax.clear()
        ax.set_axis_on()
    checked = [roi for roi in settings.rois if roi.checked]
    if not checked:
        for ax in (temp_ax, delta_ax):
            ax.text(.5, .5, "Select ROI(s) for curves", transform=ax.transAxes, ha="center", va="center")
    for roi in checked:
        for ax, metric in ((temp_ax, settings.curve_metric + "_c"), (delta_ax, "delta_c")):
            first = True
            for x, y in store.iter_plot_segments(roi.id, metric, 0, frame_count - 1):
                ax.plot(x, y, color=roi.color, label=(roi.name[:24] if first else "_nolegend_"),
                        marker="o" if len(x) == 1 else None)
                first = False
    for ax in (temp_ax, delta_ax):
        ax.axvline(current_local, color="black", linestyle="--", linewidth=.8)
        ax.set_xlim((-.5, .5) if frame_count == 1 else (0, frame_count - 1))
        ax.tick_params(labelsize=8)
        if checked:
            handles, labels = ax.get_legend_handles_labels()
            if handles:
                legend = ax.legend(fontsize=7, loc="best")
                for text in legend.get_texts():
                    text.set_parse_math(False)
    temp_ax.set_ylabel("Temperature (°C)", fontsize=8)
    temp_ax.set_title(settings.curve_metric.capitalize() + " — decimated display; CSV retains all samples", fontsize=8)
    temp_ax.tick_params(labelbottom=False)
    delta_ax.set(xlabel="Local frame in confirmed range", ylabel="ΔT (°C)")
    delta_ax.xaxis.label.set_fontsize(8)
    delta_ax.yaxis.label.set_fontsize(8)


def build_report_figure(snapshot):
    """Build a standalone artifact from a frozen snapshot, without viewer widgets."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.patches import Rectangle
    from .measurement_store import ResultStore
    import textwrap

    figure = Figure(figsize=(11, 7), layout="constrained")
    FigureCanvasAgg(figure)
    grid = figure.add_gridspec(2, 2)
    image_ax = figure.add_subplot(grid[:, 0])
    image = image_ax.imshow(snapshot.current_values, origin="upper", cmap=snapshot.display.colormap,
                            vmin=snapshot.display.vmin_c, vmax=snapshot.display.vmax_c)
    figure.colorbar(image, ax=image_ax, label="Temperature (°C)")
    image_ax.set(xlabel="x (cropped pixel)", ylabel="y (cropped pixel, downward)")
    image_ax.set_title(f"Local {snapshot.current_local_frame} / source {snapshot.current_source_frame}", fontsize=10)
    for roi in snapshot.settings.rois:
        image_ax.add_patch(Rectangle((roi.x - .5, roi.y - .5), roi.width, roi.height,
                                     fill=False, edgecolor=roi.color, linewidth=1.5))
        image_ax.text(roi.x - .5, roi.y - .5, roi.name[:24], color=roi.color, fontsize=7,
                      parse_math=False, bbox=dict(facecolor="white", alpha=.7, edgecolor="none"))
    if snapshot.kind == "curves":
        temp_ax, delta_ax = figure.add_subplot(grid[0, 1]), figure.add_subplot(grid[1, 1])
        store = ResultStore.open_readonly(snapshot.result_store_path)
        try:
            if store.key_json != snapshot.analysis_key.to_json():
                raise ValueError("curve report result key does not match snapshot")
            draw_frame_curves(temp_ax, delta_ax, store, snapshot.settings, snapshot.current_local_frame,
                               snapshot.source_range[1] - snapshot.source_range[0] + 1)
        finally:
            store.close()
    else:
        ax = figure.add_subplot(grid[:, 1])
        if snapshot.kind == "distribution":
            draw_distribution(ax, snapshot.current_analysis.histogram)
        else:
            draw_profile(ax, getattr(snapshot.current_analysis, snapshot.settings.profile_direction))
        roi = next(roi for roi in snapshot.settings.rois if roi.id == snapshot.current_analysis.roi_id)
        ax.text(.5, 1.08, roi.name[:28] + ("…" if len(roi.name) > 28 else ""),
                transform=ax.transAxes, fontsize=9, ha="center", parse_math=False)
    title = textwrap.fill(f"{snapshot.source_path.name} | source frames {snapshot.source_range[0]}–{snapshot.source_range[1]}",
                          width=90, break_long_words=True, break_on_hyphens=False)
    figure.suptitle(f"{title}\n"
                    f"{snapshot.timestamp.isoformat()} | {snapshot.algorithm_version}", fontsize=10, parse_math=False)
    return figure
