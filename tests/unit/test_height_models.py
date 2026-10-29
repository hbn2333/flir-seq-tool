from dataclasses import replace
import importlib

import numpy as np
import pytest

from flir_seq_tool.measurement_models import Roi


def api():
    try:
        return importlib.import_module('flir_seq_tool.height_models')
    except ModuleNotFoundError:
        pytest.fail('height_models is not implemented')


def test_row_identity_invalid_rows_and_both_directions():
    m = api()
    roi = Roi('r', 'R', 0, 0, 2, 3, '#123456', False)
    p = m.row_profile(np.array([[1., 3.], [np.nan, np.inf], [7., np.nan]]), roi)
    np.testing.assert_equal(p.roi_row_indices, [0, 1, 2])
    np.testing.assert_equal(p.sums_c, [4., 0., 7.])
    np.testing.assert_equal(p.valid_counts, [2, 0, 1])
    np.testing.assert_equal(p.means_c, [2., np.nan, 7.])
    assert not p.means_c.flags.writeable and p.total_pixels == 2
    s = m.HeightSettings('r').calibrate(3, 12.)
    px, mm = m.height_positions(3, s)
    np.testing.assert_equal(px, [2.5, 1.5, .5])
    np.testing.assert_equal(mm, [10., 6., 2.])
    np.testing.assert_equal(m.height_positions(3, replace(s, heated_edge='top'))[0], [.5, 1.5, 2.5])


def test_weighted_zones_and_signed_difference():
    m = api()
    roi = Roi('r', 'R', 0, 0, 2, 6, '#123456', True)
    p = m.row_profile(np.array([[1, 3], [5, np.nan], [10, 10], [10, 10], [20, 20], [30, np.nan]]), roi)
    s = m.HeightSettings('r', heated_edge='top', zones_enabled=True)
    z = m.zone_statistics(p, s)
    assert [v.key for v in z.zones] == ['low', 'middle', 'high']
    assert z.zones[0].mean_c == 3. and z.zones[0].valid_pixels == 3
    assert z.zones[2].mean_c == pytest.approx(70 / 3)
    assert z.zone_delta_c == pytest.approx(3 - 70 / 3)
    assert z.zones[0].lower_px == 0 and z.zones[0].upper_px == 2


def test_settings_roundtrip_strict_and_geometry_preserves_cuts_and_calibration():
    m = api()
    s = m.HeightSettings('r', zones_enabled=True).calibrate(6, 12)
    assert m.HeightSettings.from_dict(s.to_dict()) == s
    assert s.for_height(2).zones_enabled is False
    assert s.for_height(2).cuts == s.cuts and s.for_height(2).mm_per_pixel == 2
    with pytest.raises(ValueError):
        s.validate_height(2)
    for change in ({'cuts': (0, .6)}, {'cuts': (float('nan'), .6)}, {'heated_edge': 'left'},
                   {'delta_pair': ('low', 'low')}, {'zones_enabled': 1}, {'mm_per_pixel': True},
                   {'mm_per_pixel': 2, 'reference_height_px': 6, 'reference_height_mm': 13}):
        with pytest.raises(ValueError):
            replace(s, **change)
    with pytest.raises(ValueError):
        m.HeightSettings.from_dict(s.to_dict() | {'extra': True})
    for length in (0, -1, float('inf'), True):
        with pytest.raises(ValueError):
            s.calibrate(6, length)


def test_small_disabled_rois_allow_profile_and_missing_zone_temperature():
    m = api()
    for h in (1, 2, 3):
        m.HeightSettings('r').validate_height(h)
    roi = Roi('r', 'R', 0, 0, 1, 3, '#123456', True)
    z = m.zone_statistics(m.row_profile(np.full((3, 1), np.nan), roi),
                          m.HeightSettings('r', zones_enabled=True))
    assert z.zone_delta_c is None
    assert all(v.mean_c is None and v.status == 'no_finite_pixels' for v in z.zones)


@pytest.mark.parametrize('indices,counts',[([0],[1.5]),([.5],[1]),([True],[1]),([0],[True])])
def test_profile_rejects_noninteger_row_identity_or_counts(indices,counts):
    m=api()
    with pytest.raises(ValueError):
        m.RowProfile(indices,[2.],counts,[2.],2)
