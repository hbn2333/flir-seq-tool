import matplotlib
matplotlib.use("Agg", force=True)
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
import numpy as np

from flir_seq_tool.measurement_models import MeasurementSettings, Roi
from flir_seq_tool.measurement_stats import histogram, profile
from flir_seq_tool.measurement_plots import draw_distribution, draw_frame_curves, draw_profile
from flir_seq_tool.measurement_store import ResultStore


ROI = Roi("r1", "ROI 1", 0, 0, 2, 2, "#123456", True)


def test_distribution_plots_fractions_and_current_frame_bin_range():
    figure = Figure()
    FigureCanvasAgg(figure)
    ax = figure.subplots()
    result = histogram(np.array([[1., 2.], [3., 4.]]), ROI)
    draw_distribution(ax, result)
    assert len(ax.patches) == 32
    assert sum(patch.get_height() for patch in ax.patches) == 1.
    assert "automatic" in ax.get_title()
    draw_distribution(ax, histogram(np.full((2, 2), np.nan), ROI))
    assert not ax.patches
    assert "No finite" in ax.texts[0].get_text()
    figure.canvas.draw()


def test_profile_preserves_nan_gaps_and_downward_pixel_label():
    figure = Figure()
    FigureCanvasAgg(figure)
    ax = figure.subplots()
    result = profile(np.array([[np.nan, np.nan], [2., 4.]]), ROI, "vertical")
    draw_profile(ax, result)
    assert np.isnan(ax.lines[0].get_ydata()[0])
    assert "downward" in ax.get_xlabel()
    figure.canvas.draw()


def test_single_frame_curves_show_marker_cursor_and_unchecked_hint(tmp_path):
    from dataclasses import replace
    store = ResultStore.create(tmp_path)
    try:
        store.append_rows([dict(roi_id="r1", local_frame=0, source_frame=10, timestamp="2026-10-22T00:00:00",
                                relative_s=0., mean_c=4., min_c=1., max_c=7., delta_c=6., std_c=2.,
                                valid_pixels=4, total_pixels=4)])
        store.complete(expected_rows=1, key_json="key")
        figure = Figure()
        FigureCanvasAgg(figure)
        temp, delta = figure.subplots(2)
        settings = MeasurementSettings((ROI,), "r1", result_view="curves")
        draw_frame_curves(temp, delta, store, settings, 0, 1)
        assert temp.lines[0].get_marker() == "o"
        assert temp.get_xlim() == (-.5, .5)
        assert list(delta.lines[0].get_ydata()) == [6.]
        assert list(temp.lines[-1].get_xdata()) == [0, 0]
        literal = replace(settings, rois=(replace(ROI, name=r"$\unknowncommand$"),))
        draw_frame_curves(temp, delta, store, literal, 0, 1)
        figure.canvas.draw()
        draw_frame_curves(temp, delta, store, replace(settings, rois=(replace(ROI, checked=False),)), 0, 1)
        assert "Select" in temp.texts[0].get_text()
    finally:
        store.cleanup()
