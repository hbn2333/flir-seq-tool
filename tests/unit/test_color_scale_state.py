from dataclasses import FrozenInstanceError, replace
import gc
import weakref

import numpy as np
import pytest

from flir_seq_tool.color_scale import ColorLimits
from flir_seq_tool.color_scale_models import ColorScaleScope
from flir_seq_tool.color_scale_state import ColorScaleState
from flir_seq_tool.selection import DisplaySettings


SCOPE = ColorScaleScope('a' * 64, 0, 9, 'b' * 64)
OTHER = replace(SCOPE, source_end=8)


def auto_state():
    state = ColorScaleState(DisplaySettings('inferno', 20, 45, fixed_source='auto'))
    state.set_scope(SCOPE)
    return state


@pytest.mark.parametrize('loaded', [False, True])
def test_invalidate_auto_discards_applied_and_cached_scope_only(loaded):
    state = auto_state()
    state.accept_auto(SCOPE, ColorLimits(10, 60))
    if loaded:
        state = ColorScaleState(state.settings)
        state.set_scope(SCOPE)
    state.edit_bounds('draft', 'kept')
    before = state.settings
    state.invalidate_auto()
    assert state.settings == replace(before, auto_scope=None)
    assert state.draft_bounds == ('draft', 'kept')
    assert not state.fixed_ready
    state.restore_auto()
    assert not state.fixed_ready


def test_pending_auto_then_complete_and_stale_result():
    state = auto_state()
    original = state.settings
    assert state.observe_frame([[20, 25]]) == ColorLimits(20, 25)
    assert state.settings is original
    assert not state.fixed_ready
    assert state.render_snapshot().status == 'temporary'
    assert not state.accept_auto(OTHER, ColorLimits(20, 50))
    assert state.accept_auto(SCOPE, ColorLimits(20, 50))
    assert state.fixed_ready
    assert state.observe_frame([[22, 25]]) == ColorLimits(20, 50)
    assert state.render_snapshot().status == 'auto_ready'
    accepted = state.settings
    assert state.accept_auto(SCOPE, ColorLimits(20, 50))
    assert state.settings is accepted
    state.set_scope(OTHER)
    assert not state.fixed_ready
    assert state.settings.auto_scope is None
    assert state.observe_frame([[30, 40]]) == ColorLimits(30, 40)


def test_manual_apply_survives_context_change():
    state = auto_state()
    state.apply_manual(21, 46)
    state.set_scope(OTHER)
    assert state.fixed_ready
    assert state.settings.auto_scope is None
    assert not state.accept_auto(OTHER, ColorLimits(0, 100))
    assert state.observe_frame([[30, 50]]) == ColorLimits(21, 46)
    assert state.render_snapshot().status == 'manual'


def test_per_frame_browsing_preserves_manual_values_and_draft():
    state = ColorScaleState(DisplaySettings('inferno', 20, 45))
    state.edit_bounds('21', '46')
    state.select_mode('per_frame')
    saved = state.settings.to_dict()
    assert state.observe_frame([[22, 50]]) == ColorLimits(22, 50)
    assert state.render_snapshot().status == 'per_frame'
    assert state.settings.to_dict() == saved
    state.select_mode('fixed')
    assert state.draft_pending
    assert state.draft_bounds == ('21', '46')
    assert state.render_snapshot().limits == ColorLimits(20, 45)
    state.apply_draft()
    assert state.render_snapshot().limits == ColorLimits(21, 46)
    assert not state.draft_pending


@pytest.mark.parametrize('draft', [('21', '45'), ('20', '46'), (' 21.0 ', ''), ('NaN', '46')])
def test_completion_preserves_both_raw_draft_strings(draft):
    state = auto_state()
    state.edit_bounds(*draft)
    state.accept_auto(SCOPE, ColorLimits(20, 50))
    state.observe_frame([[1, 2]])
    assert state.draft_bounds == draft
    assert state.draft_pending


@pytest.mark.parametrize('minimum,maximum', [
    (float('nan'), 45), (20, float('inf')), (-float('inf'), 45),
    (45, 20), (20, 20), (True, 45), (20, False), ('20', 45),
])
def test_invalid_manual_is_atomic(minimum, maximum):
    state = auto_state()
    state.edit_bounds('21', '46')
    saved = state.settings
    with pytest.raises(ValueError):
        state.apply_manual(minimum, maximum)
    assert state.settings is saved
    assert state.draft_bounds == ('21', '46')


@pytest.mark.parametrize('draft', [('nan', '46'), ('21', 'inf'), ('46', '21'), ('21', '21'), ('', '46')])
def test_invalid_draft_is_atomic(draft):
    state = auto_state()
    state.edit_bounds(*draft)
    saved = state.settings
    with pytest.raises(ValueError):
        state.apply_draft()
    assert state.settings is saved
    assert state.draft_bounds == draft


@pytest.mark.parametrize('source', ['auto', 'manual'])
def test_mode_roundtrip_remembers_fixed_source(source):
    display = DisplaySettings('inferno', 20, 45, fixed_source=source)
    state = ColorScaleState(DisplaySettings.from_dict(display.to_dict()))
    state.select_mode('per_frame')
    state.observe_frame([[1, 99]])
    state.select_mode('fixed')
    assert state.settings == display


def test_auto_restores_only_matching_cached_result_and_clears_draft():
    state = auto_state()
    state.accept_auto(SCOPE, ColorLimits(20, 50))
    state.apply_manual(21, 46)
    state.select_mode('per_frame')
    state.edit_bounds('22', '47')
    state.restore_auto()
    assert state.settings.scale_mode == 'fixed'
    assert state.settings.fixed_source == 'auto'
    assert state.fixed_ready
    assert state.render_snapshot().limits == ColorLimits(20, 50)
    assert state.draft_bounds is None
    state.set_scope(OTHER)
    state.restore_auto()
    assert not state.fixed_ready


def test_invalid_frame_falls_back_to_latest_valid_actual_range():
    state = ColorScaleState()
    state.select_mode('per_frame')
    assert state.observe_frame([[np.nan, np.inf]]) == ColorLimits(0, 1)
    assert state.render_snapshot().no_finite
    assert state.observe_frame([[25, 25]]) == ColorLimits(24.5, 25.5)
    assert not state.render_snapshot().no_finite
    assert state.observe_frame([[-np.inf, np.nan]]) == ColorLimits(24.5, 25.5)
    assert state.render_snapshot().no_finite
    assert state.render_snapshot().status == 'per_frame'


def test_fixed_manual_invalid_frame_keeps_bounds_and_note():
    state = ColorScaleState(DisplaySettings('inferno', 20, 45))
    assert state.observe_frame([[np.nan]]) == ColorLimits(20, 45)
    assert state.render_snapshot().no_finite
    assert state.render_snapshot().status == 'manual'


def test_default_seeds_fixed_memory_once_from_first_valid_frame():
    state = ColorScaleState()
    assert (state.settings.scale_mode, state.settings.fixed_source) == ('fixed', 'auto')
    state.observe_frame([[np.nan]])
    state.observe_frame([[20, 25]])
    seeded = state.settings
    assert (seeded.vmin_c, seeded.vmax_c) == (20, 25)
    assert state.observe_frame([[30, 40]]) == ColorLimits(30, 40)
    assert state.settings is seeded


@pytest.mark.parametrize('mode', ['fixed', 'per_frame'])
def test_preview_freezes_entering_actual_range_and_resumes_current_frame(mode):
    state = auto_state()
    state.select_mode(mode)
    state.observe_frame([[20, 25]])
    state.freeze_preview()
    assert state.observe_frame([[0, 100]]) == ColorLimits(20, 25)
    assert state.render_snapshot().status == 'preview'
    state.freeze_preview()  # repeated entry cannot replace entering bounds
    assert state.observe_frame([[3, 90]]) == ColorLimits(20, 25)
    state.resume_preview()
    assert state.render_snapshot().limits == ColorLimits(3, 90)
    assert state.render_snapshot().status == ('temporary' if mode == 'fixed' else 'per_frame')


def test_selection_snapshot_preserves_memory_and_validates_metadata():
    state = auto_state()
    state.accept_auto(SCOPE, ColorLimits(20, 50))
    state.select_mode('per_frame')
    state.observe_frame([[3, 90]])
    assert state.settings_for_selection(SCOPE) == state.settings
    candidate = state.settings_for_selection(OTHER)
    assert candidate.auto_scope is None
    assert (candidate.vmin_c, candidate.vmax_c) == (20, 50)
    assert state.settings.auto_scope == SCOPE
    state.apply_manual(21, 46)
    assert state.settings_for_selection(SCOPE).auto_scope is None


def test_loaded_auto_only_ready_after_matching_scope_verified():
    display = DisplaySettings('inferno', 20, 50, fixed_source='auto', auto_scope=SCOPE)
    state = ColorScaleState(display)
    assert not state.fixed_ready
    state.set_scope(SCOPE)
    assert state.fixed_ready
    assert state.observe_frame([[3, 90]]) == ColorLimits(20, 50)
    other_state = ColorScaleState(display)
    other_state.set_scope(OTHER)
    assert not other_state.fixed_ready
    assert other_state.observe_frame([[3, 90]]) == ColorLimits(3, 90)


@pytest.mark.parametrize('scope,limits', [
    (None, ColorLimits(1, 2)), (OTHER, ColorLimits(1, 2)),
    (SCOPE, ColorLimits(np.nan, 2)), (SCOPE, ColorLimits(1, np.inf)),
    (SCOPE, ColorLimits(2, 1)), (SCOPE, ColorLimits(1, 1)),
    (SCOPE, ColorLimits(True, 2)), (SCOPE, (1, 2)),
])
def test_auto_rejects_invalid_results_without_mutation(scope, limits):
    state = auto_state()
    saved = state.settings
    assert not state.accept_auto(scope, limits)
    assert state.settings is saved


def test_late_auto_in_per_frame_does_not_overwrite_settings_or_cache():
    state = auto_state()
    state.accept_auto(SCOPE, ColorLimits(20, 50))
    state.select_mode('per_frame')
    saved = state.settings
    assert not state.accept_auto(SCOPE, ColorLimits(0, 100))
    assert state.settings is saved
    state.restore_auto()
    assert state.render_snapshot().limits == ColorLimits(20, 50)


def test_snapshot_is_immutable_and_state_retains_no_frame_array():
    state = auto_state()
    values = np.array([[20, 25]])
    reference = weakref.ref(values)
    state.observe_frame(values)
    snapshot = state.render_snapshot()
    del values
    gc.collect()
    assert reference() is None
    with pytest.raises(FrozenInstanceError):
        snapshot.status = 'manual'
    state.observe_frame([[30, 40]])
    assert snapshot.limits == ColorLimits(20, 25)


def test_invalid_mode_is_atomic_and_empty_apply_is_noop():
    state = auto_state()
    state.apply_draft()
    saved = state.settings
    with pytest.raises(ValueError):
        state.select_mode('other')
    assert state.settings is saved


@pytest.mark.parametrize('source', ['auto', 'manual'])
def test_discard_draft_preserves_policy_snapshot_and_cached_auto(source):
    state = auto_state()
    state.accept_auto(SCOPE, ColorLimits(20, 50))
    if source == 'manual':
        state.apply_manual(21, 46)
    state.observe_frame([[22, 40]])
    settings = state.settings
    snapshot = state.render_snapshot()
    state.edit_bounds('unfinished', ' 47 ')
    state.discard_draft()
    assert state.draft_bounds is None
    assert not state.draft_pending
    assert state.settings is settings
    assert state.render_snapshot() is snapshot
    state.discard_draft()  # Discarding an empty draft is also a no-op.
    assert state.settings is settings
    assert state.render_snapshot() is snapshot
    state.restore_auto()
    assert state.fixed_ready
    assert state.render_snapshot().limits == ColorLimits(20, 50)
