import pytest

from flir_seq_tool.color_scale import ColorLimits
from flir_seq_tool.selection import SelectionStore
from flir_seq_tool.viewer import ViewerSession
from conftest import SyntheticReader
from test_color_scale_controller import ControlledColorJob


def session_at(tmp_path, **kwargs):
    return ViewerSession(SyntheticReader(tmp_path / 'source.seq'),
                         selection_store=SelectionStore(tmp_path / 'configs'), **kwargs)


def test_default_session_observes_extrema_without_starting_worker(tmp_path):
    session = session_at(tmp_path)
    session.frame_at(0)
    assert session.color_limits == ColorLimits(0, 47)
    assert session.color_scale.settings.fixed_source == 'auto'
    assert session._color_controller is None
    session.frame_at(1)
    assert session.color_limits == ColorLimits(10, 57)
    assert session.color_scale.settings.vmin_c == 0
    assert session.color_scale.settings.vmax_c == 47


def test_per_frame_bounds_do_not_change_configuration_or_analysis_key(tmp_path):
    session = session_at(tmp_path)
    session.frame_at(0)
    session.color_scale.select_mode('per_frame')
    signature, key = session.state_signature(), session.analysis_key()
    document = session.selection_document()
    session.frame_at(4)
    assert session.color_limits == ColorLimits(40, 87)
    assert session.state_signature() == signature
    assert session.analysis_key() == key
    assert session.selection_document().display == document.display


def test_manual_application_marks_configuration_only_and_compat_setter(tmp_path):
    session = session_at(tmp_path)
    session.frame_at(0)
    key = session.analysis_key()
    session.color_limits = ColorLimits(4, 20)
    assert session.has_unsaved_changes()
    assert session.analysis_key() == key
    assert session.color_scale.settings.fixed_source == 'manual'
    session.frame_at(4)
    assert session.color_limits == ColorLimits(4, 20)


def test_controller_is_lazy_and_candidate_ranges_do_not_change_context(tmp_path):
    session = session_at(tmp_path)
    jobs = []
    def factory(request):
        job = ControlledColorJob(request)
        jobs.append(job)
        return job
    controller = session.get_color_controller(job_factory=factory)
    assert jobs == []
    controller.activate()
    assert len(jobs) == 1
    first = controller.context
    session.timeline.set_pending(1, 3)
    assert controller.context == first
    session.confirm_range()
    assert controller.context.source_start == 1
    assert controller.context.source_end == 3
    assert jobs[0].cancelled
    jobs[0].finish()
    controller.poll()
    assert len(jobs) == 2
    controller.close(wait=True)


def test_load_preserves_scale_identity_and_restores_policy(tmp_path):
    session = session_at(tmp_path)
    session.frame_at(0)
    state = session.color_scale
    session.color_scale.select_mode('per_frame')
    document = session.selection_document()
    state.apply_manual(5, 8)
    session.apply_selection_document(document, path=tmp_path / 'saved.json')
    assert session.color_scale is state
    assert state.settings == document.display
    assert not session.has_unsaved_changes()
    session.frame_at(4)
    assert session.color_limits == ColorLimits(40, 87)
    assert not session.has_unsaved_changes()


def test_closing_color_scale_does_not_create_controller(tmp_path):
    session = session_at(tmp_path)
    session.close_color_scale(wait=True)
    assert session._color_controller is None


def test_context_identity_is_cached_for_browse_signatures(tmp_path, monkeypatch):
    session = session_at(tmp_path)
    def forbidden():
        raise AssertionError('source identity queried during frame refresh')
    monkeypatch.setattr(session, 'source_identity', forbidden)
    for index in range(5):
        session.frame_at(index)
        session.state_signature()
        session.has_unsaved_changes()


def test_saved_auto_round_trip_preserves_scope_and_controller_owner(tmp_path):
    session = session_at(tmp_path)
    session.frame_at(0)
    controller = session.get_color_controller(job_factory=ControlledColorJob)
    scope = controller.context.scope
    assert session.color_scale.accept_auto(scope, ColorLimits(-5, 95))
    document = session.selection_document()
    assert document.display.auto_scope == scope
    session.color_scale.apply_manual(3, 4)
    session.apply_selection_document(document, path=tmp_path / 'saved.json')
    assert session.get_color_controller() is controller
    assert session.color_scale.fixed_ready
    assert session.color_scale.settings == document.display
    session.frame_at(4)
    assert session.color_limits == ColorLimits(-5, 95)
    assert not session.has_unsaved_changes()


def test_candidate_document_uses_fixed_memory_and_excludes_wrong_auto_scope(tmp_path):
    session = session_at(tmp_path)
    session.frame_at(0)
    scope = session.get_color_controller().context.scope
    session.color_scale.accept_auto(scope, ColorLimits(-5, 95))
    session.color_scale.select_mode('per_frame')
    session.frame_at(4)
    session.timeline.set_pending(1, 3)
    display = session.selection_document().display
    assert (display.vmin_c, display.vmax_c) == (-5, 95)
    assert display.scale_mode == 'per_frame'
    assert display.auto_scope is None
    assert session.selection_document(scope='measure').display.auto_scope == scope


def test_draft_does_not_dirty_applied_configuration_and_save_excludes_draft(tmp_path):
    session = session_at(tmp_path)
    session.frame_at(0)
    before = session.state_signature()
    session.color_scale.edit_bounds('invalid', '100')
    assert session.state_signature() == before
    assert not session.has_unsaved_changes()
    path = session.save_selection()
    document = session.selection_store.load_path(path, session.source_identity())
    assert document.display == session.color_scale.settings
    assert session.color_scale.draft_bounds == ('invalid', '100')


def test_invalid_palette_assignment_preserves_policy_and_drafts(tmp_path):
    session = session_at(tmp_path)
    session.color_scale.edit_bounds('1', '20')
    before = session.color_scale.settings
    with pytest.raises(ValueError, match='unknown colormap'):
        session.colormap = 'invalid-palette'
    assert session.color_scale.settings == before
    assert session.color_scale.draft_bounds == ('1', '20')



@pytest.mark.parametrize('operation', ['confirm', 'reset', 'transform', 'load'])
def test_failed_context_preparation_preserves_committed_state(tmp_path, operation):
    from types import SimpleNamespace
    from flir_seq_tool.transform import FrameTransform, CropRect, RotationSettings
    session = session_at(tmp_path)
    session.frame_at(0)
    document = session.selection_document()
    if operation == 'reset':
        session.timeline.set_pending(1, 3)
        session.confirm_range()
    else:
        session.timeline.set_pending(1, 3)
    cleaned = []
    session.measurement.result_store = SimpleNamespace(cleanup=lambda: cleaned.append(True))
    session.measurement.result_key = session.analysis_key()
    session.measurement.status = 'complete'
    before = (session.measure_source_range(), session.timeline.pending_range,
              session.applied_transform, session.color_scale.settings,
              session.state_signature(), session.measurement.status,
              session.measurement.result_store, session.measurement.result_key)
    session.reader.path.unlink()
    with pytest.raises(OSError):
        if operation == 'confirm':
            session.confirm_range()
        elif operation == 'reset':
            session.reset_full()
        elif operation == 'transform':
            session.apply_transform(FrameTransform(8, 6, RotationSettings(0), CropRect(1, 1, 6, 4)))
        else:
            session.apply_selection_document(document, path=tmp_path / 'saved.json')
    after = (session.measure_source_range(), session.timeline.pending_range,
             session.applied_transform, session.color_scale.settings,
             session.state_signature(), session.measurement.status,
             session.measurement.result_store, session.measurement.result_key)
    assert after == before
    assert not cleaned


def test_context_changes_only_for_applied_scale_inputs(tmp_path):
    from dataclasses import replace
    from flir_seq_tool.measurement_models import Roi
    from flir_seq_tool.transform import FrameTransform, CropRect, RotationSettings
    session = session_at(tmp_path)
    controller = session.get_color_controller(job_factory=ControlledColorJob)
    first = controller.context
    session.measurement.settings = replace(session.measurement.settings,
        rois=(Roi('a', 'A', 0, 0, 3, 3, '#ff0000', True),), result_view='height', height_view='zones')
    session.frame_at(4)
    assert controller.context == first
    session.timeline.set_pending(1, 3)
    session.confirm_range()
    assert controller.context.scope != first.scope
    session.reset_full()
    assert controller.context == first
    transform = FrameTransform(8, 6, RotationSettings(0), CropRect(1, 1, 6, 4))
    session.apply_transform(transform)
    assert controller.context.transform == transform
    assert controller.context.scope != first.scope
    assert not controller.blocks_heavy_tasks


def test_source_changes_are_rejected_without_adopting_new_identity(tmp_path):
    session = session_at(tmp_path)
    controller = session.get_color_controller(job_factory=ControlledColorJob)
    context = controller.context
    session.timeline.set_pending(1, 3)
    session.reader.path.write_bytes(b'changed-source-metadata-and-size')
    with pytest.raises(ValueError, match='source changed'):
        session.confirm_range()
    assert session.measure_source_range() == (0, 4)
    assert controller.context == context


def test_frozen_preview_retains_actual_bounds_until_applied_frame_is_observed(tmp_path):
    session = session_at(tmp_path)
    session.frame_at(0)
    session.frame_at(3)
    assert session.color_limits == ColorLimits(30, 77)
    session.color_scale.freeze_preview()
    session.frame_at(4)
    assert session.color_limits == ColorLimits(30, 77)
    session.color_scale.resume_preview()
    session.frame_at(1)
    assert session.color_limits == ColorLimits(10, 57)
