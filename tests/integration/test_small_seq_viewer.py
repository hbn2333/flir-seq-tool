from pathlib import Path

import matplotlib

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
import numpy as np
import pytest

from flir_seq_tool.reader import ScienceFileReader
from flir_seq_tool.selection import SelectionStore
from flir_seq_tool.transform import FrameTransform
from flir_seq_tool.viewer import ViewerSession, build_viewer


SMALL_SEQ = Path("raw/Rec-000366-240_09_26_37_152.seq")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.usefixtures("_preserve_small_seq_source"),
]


@pytest.fixture
def _preserve_small_seq_source():
    before = SMALL_SEQ.stat()
    yield
    after = SMALL_SEQ.stat()
    assert (before.st_size, before.st_mtime_ns) == (
        after.st_size,
        after.st_mtime_ns,
    )


def test_small_seq_viewer_moves_between_real_frames_without_modifying_source():
    with ScienceFileReader(SMALL_SEQ) as reader:
        session = ViewerSession(reader, cache_size=3)
        ui = build_viewer(session, initial_frame=0)
        try:
            initial_clim = ui.image.get_clim()

            ui.slider.set_val(64)

            displayed = np.asarray(ui.image.get_array())
            assert displayed.shape == (480, 640)
            assert ui.image.get_clim() == pytest.approx(initial_clim)
            assert "Local frame 64/64" in ui.axes.get_title()
            assert "Source frame 64/64" in ui.axes.get_title()
            assert len(session.cache) <= 3
        finally:
            plt.close(ui.figure)


def test_small_seq_time_range_uses_source_frames_and_real_timestamps():
    with ScienceFileReader(SMALL_SEQ) as reader:
        session = ViewerSession(reader, cache_size=3)
        ui = build_viewer(session, initial_frame=0)
        try:
            ui.range_slider.set_val((10, 50))
            ui.confirm_button._observers.process("clicked", None)

            assert (session.timeline.source_start, session.timeline.source_end) == (
                10,
                50,
            )
            assert session.timeline.current_local_frame == 0
            assert "Source frame 10/64" in ui.axes.get_title()
            start = reader.timestamp_at(10)
            end = reader.timestamp_at(50)
            assert session.work_duration_s() == pytest.approx(
                (end - start).total_seconds()
            )

            ui.range_slider.set_val((5, 20))
            ui.confirm_button._observers.process("clicked", None)
            assert (session.timeline.source_start, session.timeline.source_end) == (
                15,
                30,
            )

            ui.reset_button._observers.process("clicked", None)
            assert (session.timeline.source_start, session.timeline.source_end) == (
                0,
                64,
            )
            assert len(session.cache) <= 3
        finally:
            plt.close(ui.figure)


def test_small_seq_selection_round_trip_preserves_source(tmp_path):
    with ScienceFileReader(SMALL_SEQ) as reader:
        session = ViewerSession(
            reader,
            cache_size=3,
            selection_store=SelectionStore(tmp_path / "selections"),
        )
        ui = build_viewer(session, initial_frame=0)
        try:
            ui.range_slider.set_val((10, 20))
            ui.save_button._observers.process("clicked", None)
            ui.reset_button._observers.process("clicked", None)
            ui.load_button._observers.process("clicked", None)
            assert ui.load_button.label.get_text() == "Confirm load"
            ui.load_button._observers.process("clicked", None)

            assert (session.timeline.source_start, session.timeline.source_end) == (
                10,
                20,
            )
            assert session.timeline.pending_range == (0, 10)
            assert session.timeline.current_local_frame == 0
            assert "Source frame 10/64" in ui.axes.get_title()
            assert len(session.cache) <= 3
        finally:
            plt.close(ui.figure)


def test_small_seq_transform_round_trip_is_finite_cached_and_read_only(tmp_path):
    with ScienceFileReader(SMALL_SEQ) as reader:
        selection_root = tmp_path / "selections"
        session = ViewerSession(
            reader,
            cache_size=3,
            selection_store=SelectionStore(selection_root),
        )
        rotated = session.applied_transform.with_angle(10.0)
        session.apply_transform(rotated)

        assert reader.num_frames >= 4
        source_indices = (0, 1, reader.num_frames // 2, reader.num_frames - 1)
        assert len(set(source_indices)) == 4
        observed = []
        for source_index in source_indices:
            values = session.frame_at(source_index).temperature_c
            assert values.shape == rotated.output_shape
            assert np.isfinite(values).all()
            observed.append(values.copy())

        assert len(session.source_cache) == 3
        assert len(session.transformed_cache) == 3

        saved_path = session.save_selection()
        assert saved_path.parent == selection_root
        session.apply_transform(FrameTransform.identity(reader.width, reader.height))

        loaded = session.load_selection()
        assert loaded.transform == rotated
        assert session.applied_transform == rotated
        assert np.allclose(
            session.frame_at(source_indices[0]).temperature_c,
            observed[0],
        )
        assert len(session.source_cache) <= 3
        assert len(session.transformed_cache) <= 3
