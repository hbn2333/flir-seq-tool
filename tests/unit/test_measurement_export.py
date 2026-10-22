from dataclasses import replace
from datetime import datetime, timedelta
import csv
import json
import os

import numpy as np
from PIL import Image
import pytest

from flir_seq_tool.file_safety import TargetSnapshot
from flir_seq_tool.measurement_export import (AnalysisExportSnapshot, COMMON_COLUMNS, HIST_COLUMNS,
                                             PROFILE_COLUMNS, STATS_COLUMNS, export_csv, export_png,
                                             publish_analysis_file, spreadsheet_text)
from flir_seq_tool.measurement_models import CurrentAnalysis, MeasurementSettings, Roi
from flir_seq_tool.measurement_state import AnalysisKey
from flir_seq_tool.measurement_stats import histogram, profile, summarize
from flir_seq_tool.measurement_store import ResultStore
from flir_seq_tool.selection import DisplaySettings, SourceIdentity, transform_to_dict
from flir_seq_tool.transform import FrameTransform


def current(values, roi):
    return CurrentAnalysis(2, roi.id, summarize(values, roi), histogram(values, roi),
                           profile(values, roi, "horizontal"), profile(values, roi, "vertical"))


@pytest.fixture
def snapshot(tmp_path):
    source = tmp_path / "source.seq"
    source.write_bytes(b"source")
    stat = source.stat()
    start = datetime(2026, 10, 22)
    identity = SourceIdentity(source.name, stat.st_size, stat.st_mtime_ns, 3, 2, 3,
                              start, start + timedelta(milliseconds=40))
    roi = Roi("r1", '=SUM(1,2),"zone"', 0, 0, 3, 2, "#123456", True)
    values = np.array([[1., np.nan, 3.], [4., 5., 6.]])
    return AnalysisExportSnapshot(identity, source, (1, 2), start + timedelta(milliseconds=20),
                                  json.dumps(transform_to_dict(FrameTransform.identity(3, 2))),
                                  DisplaySettings("inferno", 0., 10.), MeasurementSettings((roi,), "r1"),
                                  2, 1, start + timedelta(milliseconds=40), values, current(values, roi), None)


def test_external_change_after_confirmation_is_not_overwritten(tmp_path):
    source, target = tmp_path / "source.seq", tmp_path / "data.csv"
    source.write_bytes(b"source")
    target.write_text("old", encoding="utf-8")
    expected = TargetSnapshot.capture(target)
    target.write_text("external update", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed"):
        publish_analysis_file(target, source_path=source, raw_root=tmp_path / "raw", expected=expected,
                              writer=lambda path: path.write_text("new", encoding="utf-8"), validator=lambda path: None)
    assert target.read_text(encoding="utf-8") == "external update"


@pytest.mark.parametrize("stage", ["writer", "validator", "external-change"])
def test_failed_publication_never_deletes_or_overwrites_external_target(tmp_path, stage):
    source, target = tmp_path / "source.seq", tmp_path / "data.csv"
    source.write_bytes(b"source")
    target.write_bytes(b"old")
    expected = TargetSnapshot.capture(target)
    def writer(path):
        path.write_bytes(b"new")
        if stage == "writer":
            raise OSError("write failed")
        if stage == "external-change":
            target.write_bytes(b"external")
    def validator(path):
        if stage == "validator":
            raise ValueError("parse failed")
    with pytest.raises((OSError, ValueError, RuntimeError)):
        publish_analysis_file(target, source_path=source, raw_root=tmp_path / "raw", expected=expected,
                              writer=writer, validator=validator)
    assert target.read_bytes() == (b"external" if stage == "external-change" else b"old")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["data.csv", "source.seq"]


def test_current_distribution_csv_has_fixed_columns_full_precision_and_work_relative_time(snapshot, tmp_path):
    target = tmp_path / "data.csv"
    export_csv(target, snapshot, expected=TargetSnapshot.capture(target))
    assert target.read_bytes().startswith(b"\xef\xbb\xbf")
    with target.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert tuple(reader.fieldnames) == COMMON_COLUMNS + STATS_COLUMNS + HIST_COLUMNS
        rows = list(reader)
    assert len(rows) == 32
    assert rows[0]["roi_name"] == "'" + snapshot.settings.rois[0].name
    assert float(rows[0]["mean_c"]) == 3.8
    assert rows[0]["source_frame"] == "2" and rows[0]["local_frame"] == "1"
    assert float(rows[0]["relative_s"]) == .02
    assert sum(float(row["bin_fraction"]) for row in rows) == pytest.approx(1.)
    assert json.loads(rows[0]["source_identity_json"]) == snapshot.source_identity.to_dict()


def test_profile_csv_preserves_absolute_pixel_positions_and_missing_counts(snapshot, tmp_path):
    target = tmp_path / "profile.csv"
    snapshot = replace(snapshot, settings=replace(snapshot.settings, frame_view="profile"))
    export_csv(target, snapshot, expected=TargetSnapshot.capture(target))
    with target.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert tuple(reader.fieldnames) == COMMON_COLUMNS + STATS_COLUMNS + PROFILE_COLUMNS
        rows = list(reader)
    assert [float(row["position_mean_c"]) for row in rows] == [2.5, 5., 4.5]
    assert [int(row["position_valid_pixels"]) for row in rows] == [2, 1, 2]


def test_all_invalid_distribution_exports_missing_row_not_just_header(snapshot, tmp_path):
    roi = snapshot.settings.rois[0]
    values = np.full((2, 3), np.nan)
    snapshot = replace(snapshot, current_values=values, current_analysis=current(values, roi))
    target = tmp_path / "missing.csv"
    export_csv(target, snapshot, expected=TargetSnapshot.capture(target))
    with target.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["status"] == "no_finite_pixels"
    assert rows[0]["mean_c"] == rows[0]["bin_left_c"] == rows[0]["bin_count"] == ""


def test_snapshot_copies_values_and_png_is_real_decodable_image(snapshot, tmp_path):
    target = tmp_path / "report.png"
    assert not snapshot.current_values.flags.writeable
    export_png(target, snapshot, expected=TargetSnapshot.capture(target))
    with Image.open(target) as image:
        assert image.format == "PNG" and min(image.size) > 0
        image.verify()


@pytest.mark.parametrize("name", ["bad.txt", "report.seq", "raw/report.csv"])
def test_protected_or_wrong_suffix_target_is_rejected_before_mkdir(snapshot, tmp_path, name):
    target = tmp_path / name
    with pytest.raises(ValueError):
        export_csv(target, snapshot, expected=TargetSnapshot.capture(target), raw_root=tmp_path / "raw")
    assert not target.exists()
    if name.startswith("raw/"):
        assert not target.parent.exists()


@pytest.mark.parametrize("text", ["=1+1", "+formula", "-formula", "@formula", "\tname", " \n=1"])
def test_spreadsheet_formula_text_is_guarded(text):
    assert spreadsheet_text(text) == "'" + text
    assert spreadsheet_text("sample, quoted\" name") == 'sample, quoted" name'


@pytest.mark.parametrize("count", [2, 2503])
def test_curve_csv_exports_every_sample_in_roi_display_order(snapshot, tmp_path, monkeypatch, count):
    if count > 2:
        identity = replace(snapshot.source_identity, num_frames=count + 1,
                           last_timestamp=snapshot.work_start_timestamp + timedelta(milliseconds=20*(count-1)))
        snapshot = replace(snapshot, source_identity=identity, source_range=(1, count))
    first = snapshot.settings.rois[0]
    second = replace(first, id="r2", name="ROI 2", x=1, width=1)
    settings = replace(snapshot.settings, rois=(second, first), active_roi_id="r2", result_view="curves")
    store = ResultStore.create(tmp_path)
    key = AnalysisKey.from_inputs(snapshot.source_identity, 1, count, FrameTransform.identity(3, 2), settings.rois)
    try:
        store.append_rows(dict(roi_id=roi.id, local_frame=i, source_frame=1+i,
                                   timestamp=(snapshot.work_start_timestamp + timedelta(milliseconds=20*i)).isoformat(),
                                   relative_s=.02*i, mean_c=8.123456789, min_c=1., max_c=9., delta_c=8., std_c=2.,
                                   valid_pixels=roi.width*roi.height, total_pixels=roi.width*roi.height)
                          for i in range(count) for roi in settings.rois)
        store.complete(expected_rows=2*count, key_json=key.to_json())
        store.close()
        curve_snapshot = replace(snapshot, settings=settings, current_analysis=None, result_store_path=store.path)
        def forbidden_plot_read(*args, **kwargs):
            raise AssertionError("CSV must never read plot-decimated arrays")
        monkeypatch.setattr(ResultStore, "iter_plot_segments", forbidden_plot_read)
        target = tmp_path / "curves.csv"
        export_csv(target, curve_snapshot, expected=TargetSnapshot.capture(target))
        with target.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert [(row["local_frame"], row["roi_id"]) for row in rows] == [(str(i), roi_id) for i in range(count) for roi_id in ("r2", "r1")]
        assert all(row["mean_c"] == "8.123456789" for row in rows)
    finally:
        store.cleanup()


def test_source_hardlink_alias_is_rejected_by_export_entry(snapshot, tmp_path):
    target = tmp_path / "alias.csv"
    os.link(snapshot.source_path, target)
    with pytest.raises(ValueError, match="aliases"):
        export_csv(target, snapshot, expected=TargetSnapshot.capture(target))
    assert target.read_bytes() == snapshot.source_path.read_bytes() == b"source"


def test_snapshot_copy_and_unicode_csv_text_are_preserved(snapshot, tmp_path):
    values = snapshot.current_values.copy()
    roi = replace(snapshot.settings.rois[0], name='区域,"quoted"')
    frozen = replace(snapshot, current_values=values, settings=replace(snapshot.settings, rois=(roi,)))
    values[0, 0] = 999.
    assert frozen.current_values[0, 0] == 1.
    target = tmp_path / "unicode.csv"
    export_csv(target, frozen, expected=TargetSnapshot.capture(target))
    with target.open(encoding="utf-8-sig", newline="") as stream:
        assert next(csv.DictReader(stream))["roi_name"] == roi.name


@pytest.mark.parametrize("corrupt", ["key", "metadata"])
def test_curve_export_rejects_mismatched_or_incomplete_result(snapshot, tmp_path, corrupt):
    store = ResultStore.create(tmp_path)
    settings = replace(snapshot.settings, result_view="curves")
    try:
        if corrupt == "key":
            store.complete(expected_rows=0, key_json="wrong-key")
        store.close()
        target = tmp_path / "curve.csv"
        target.write_bytes(b"old")
        frozen = replace(snapshot, settings=settings, result_store_path=store.path)
        with pytest.raises(ValueError, match="key|complete"):
            export_csv(target, frozen, expected=TargetSnapshot.capture(target))
        assert target.read_bytes() == b"old"
    finally:
        store.cleanup()


def test_report_long_titles_fit_and_contain_no_ui_widgets(snapshot):
    from matplotlib.text import Text
    from flir_seq_tool.measurement_plots import build_report_figure
    roi = replace(snapshot.settings.rois[0], name="Long ROI " * 20)
    long_name = "Long-source-name-" * 12 + ".seq"
    snapshot = replace(snapshot, source_path=snapshot.source_path.with_name(long_name),
                       source_identity=replace(snapshot.source_identity, filename=long_name),
                       settings=replace(snapshot.settings, rois=(roi,)))
    figure = build_report_figure(snapshot)
    try:
        figure.canvas.draw()
        renderer = figure.canvas.get_renderer()
        titles = [figure._suptitle] + [text for ax in figure.axes for text in (*ax.texts, ax.title)]
        for text in titles:
            if text.get_visible() and text.get_text():
                box = text.get_window_extent(renderer)
                assert figure.bbox.contains(box.x0, box.y0)
                assert figure.bbox.contains(box.x1, box.y1)
        assert all(text.get_text() not in {"Calculate", "Save setup", "Export PNG"} for text in figure.findobj(Text))
    finally:
        figure.clear()


def test_snapshot_freezes_analysis_arrays_against_later_original_changes(snapshot, tmp_path):
    analysis = current(snapshot.current_values, snapshot.settings.rois[0])
    frozen = replace(snapshot, current_analysis=analysis)
    analysis.histogram.fractions[:] = 0
    analysis.horizontal.means_c[:] = 999
    assert frozen.current_analysis.histogram.fractions.sum() == pytest.approx(1.)
    assert frozen.current_analysis.horizontal.means_c[0] == 2.5
    assert not frozen.current_analysis.histogram.edges.flags.writeable


def test_png_report_previews_for_distribution_profile_and_curves(snapshot, tmp_path):
    store = ResultStore.create(tmp_path)
    roi = snapshot.settings.rois[0]
    try:
        store.append_rows(dict(roi_id=roi.id, local_frame=i, source_frame=1+i,
                               timestamp=(snapshot.work_start_timestamp + timedelta(milliseconds=20*i)).isoformat(),
                               relative_s=.02*i, mean_c=3.8+i, min_c=1.+i, max_c=6.+i,
                               delta_c=5., std_c=1., valid_pixels=5, total_pixels=6) for i in range(2))
        store.complete(expected_rows=2, key_json=snapshot.analysis_key.to_json())
        store.close()
        for kind in ("distribution", "profile", "curves"):
            settings = replace(snapshot.settings, result_view="curves" if kind == "curves" else "current",
                               frame_view="profile" if kind == "profile" else "distribution")
            frozen = replace(snapshot, settings=settings, result_store_path=store.path)
            path = tmp_path / f"report-{kind}.png"
            export_png(path, frozen, expected=TargetSnapshot.capture(path))
            with Image.open(path) as image:
                assert image.format == "PNG"
                image.load()
            print(f"REPORT_PREVIEW={path}")
    finally:
        store.cleanup()
