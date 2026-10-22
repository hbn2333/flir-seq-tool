from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from flir_seq_tool.measurement_models import MeasurementSettings, Roi
from flir_seq_tool.measurement_state import AnalysisKey, MeasureState
from flir_seq_tool.selection import SourceIdentity
from flir_seq_tool.transform import FrameTransform


ROI = Roi("r1", "ROI 1", 0, 0, 2, 2, "#1f77b4", True)
SOURCE = SourceIdentity("sample.seq", 123, 456, 4, 3, 5, datetime(2026, 1, 1), datetime(2026, 1, 2))
TRANSFORM = FrameTransform.identity(4, 3)


def make_key(rois=(ROI,), **kwargs):
    return AnalysisKey.from_inputs(SOURCE, 0, 4, TRANSFORM, rois, **kwargs)


@pytest.mark.parametrize("changes", [{"name": "Renamed"}, {"color": "#ff0000"}, {"checked": False}])
def test_analysis_key_ignores_roi_display_fields(changes):
    assert make_key() == make_key((replace(ROI, **changes),))


@pytest.mark.parametrize("changes", [{"x": 1}, {"width": 1}])
def test_analysis_key_changes_with_geometry(changes):
    assert make_key() != make_key((replace(ROI, **changes),))


def test_analysis_key_sorts_roi_ids_and_serializes_canonically():
    other = replace(ROI, id="r2", name="ROI 2")
    assert make_key((ROI, other)) == make_key((other, ROI))
    payload = json.loads(make_key().to_json())
    assert payload["algorithm_version"] == "roi-stats-v1"
    assert json.loads(payload["source_token"]) == SOURCE.to_dict()


def test_analysis_key_covers_source_range_and_transform():
    key = make_key()
    assert key != AnalysisKey.from_inputs(replace(SOURCE, modified_time_ns=789), 0, 4, TRANSFORM, (ROI,))
    assert key != AnalysisKey.from_inputs(SOURCE, 1, 4, TRANSFORM, (ROI,))
    assert key != AnalysisKey.from_inputs(SOURCE, 0, 4, TRANSFORM.with_angle(90), (ROI,))


def test_invalidate_preserves_result_ownership_but_prevents_current_use():
    store = object()
    state = MeasureState(MeasurementSettings((ROI,), "r1"), make_key(), store, "complete")
    assert state.is_current(make_key())
    state.invalidate()
    assert state.status == "stale"
    assert state.result_store is store
    assert not state.is_current(make_key())


def test_clear_returns_store_to_caller_and_resets_settings():
    class Store:
        path = Path("owned.sqlite3")
    store = Store()
    state = MeasureState(MeasurementSettings((ROI,), "r1"), make_key(), store, "complete")
    assert state.result_path == store.path
    assert state.clear() is store
    assert state.settings == MeasurementSettings()
    assert state.result_key is state.result_store is state.result_path is None
    assert state.status == "empty"
    assert state.clear() is None


def test_complete_requires_result_and_matching_key():
    assert not MeasureState(result_key=make_key(), status="complete").is_current(make_key())
    state = MeasureState(result_key=make_key(), result_store=object(), status="complete")
    assert not state.is_current(replace(make_key(), source_start=1))
