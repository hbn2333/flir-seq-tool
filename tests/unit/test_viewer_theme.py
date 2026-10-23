import matplotlib
matplotlib.use('Agg', force=True)
import matplotlib.pyplot as plt
from matplotlib.backend_bases import CloseEvent

from flir_seq_tool.viewer import ViewerSession, build_viewer
from test_viewer import FakeTemperatureReader, make_frames


def test_light_theme_is_local_and_page_changes_preserve_input_lifecycle():
    from flir_seq_tool.viewer_theme import UI_BACKGROUND
    defaults = dict(matplotlib.rcParams)
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(5))))
    try:
        original = ui.image.get_array().copy()
        limits = ui.image.get_clim()
        assert ui.figure.get_facecolor() == matplotlib.colors.to_rgba(UI_BACKGROUND)
        assert ui.process_button.selected
        ui.pages.switch('measure')
        assert ui.measure_button.selected and not ui.process_button.selected
        assert not ui.confirm_button.active
        assert not ui.confirm_button.ax.get_visible()
        ui.pages.switch('process')
        assert ui.confirm_button.active
        ui.edit_transform_button._observers.process('clicked', None)
        assert not ui.confirm_button.active
        assert ui.transform_editor.apply_button.active
        ui.transform_editor.cancel_button._observers.process('clicked', None)
        assert ui.confirm_button.active
        assert ui.image.get_clim() == limits
        assert (ui.image.get_array() == original).all()
        assert dict(matplotlib.rcParams) == defaults
    finally:
        ui.figure.canvas.callbacks.process('close_event', CloseEvent('close_event', ui.figure.canvas))
        plt.close(ui.figure)
