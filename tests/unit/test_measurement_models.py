from dataclasses import replace

import pytest

from flir_seq_tool.measurement_models import MeasurementSettings, Roi


ROI = Roi("r1", "ROI 1", 1, 2, 3, 4, "#1f77b4", True)


def test_settings_roundtrip_preserves_display_order_and_all_options():
    other = replace(ROI, id="r2", name="ROI 2", checked=False)
    settings = MeasurementSettings((other, ROI), "r1", "curves", "profile", "vertical", "max")
    assert MeasurementSettings.from_dict(settings.to_dict()) == settings
    assert settings.to_dict()["coordinate_space"] == "cropped_pixel"


@pytest.mark.parametrize("changes", [
    {"rois": (ROI, ROI)}, {"rois": (ROI, replace(ROI, id="r2"))},
    {"active_roi_id": "missing"}, {"active_roi_id": True},
    {"result_view": "bad"}, {"frame_view": "bad"},
    {"profile_direction": "bad"}, {"curve_metric": "std"}, {"rois": ("bad",)},
])
def test_settings_reject_invalid_construction(changes):
    with pytest.raises(ValueError):
        MeasurementSettings(**changes)


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(unknown=1), lambda p: p.pop("curve_metric"),
    lambda p: p.update(coordinate_space="source_pixel"),
    lambda p: p.update(rois={}), lambda p: p["rois"][0].update(x=True),
    lambda p: p["rois"][0].update(unknown=1), lambda p: p["rois"][0].pop("checked"),
    lambda p: p.update(active_roi_id=1), lambda p: p.update(result_view=[]),
])
def test_settings_reject_malformed_payload(mutate):
    payload = MeasurementSettings((ROI,), "r1").to_dict()
    mutate(payload)
    with pytest.raises(ValueError):
        MeasurementSettings.from_dict(payload)


def test_settings_validate_cropped_bounds():
    settings = MeasurementSettings((ROI,), "r1")
    settings.validate_bounds(4, 6)
    with pytest.raises(ValueError, match="bounds"):
        settings.validate_bounds(3, 6)
