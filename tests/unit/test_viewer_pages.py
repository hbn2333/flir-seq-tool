import matplotlib
matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
from matplotlib.widgets import Button
import pytest

from flir_seq_tool.viewer_pages import PageCoordinator, PageGroup


def test_page_switch_hides_and_disables_inputs_and_releases_owned_mouse():
    figure = plt.figure()
    first, second = figure.add_axes((.1, .1, .2, .2)), figure.add_axes((.6, .6, .2, .2))
    button = Button(first, "Process")
    pages = PageCoordinator(figure.canvas, {"process": PageGroup((first,), (button,)),
                                          "measure": PageGroup((second,))})
    try:
        figure.canvas.grab_mouse(first)
        assert pages.switch("measure")
        assert not first.get_visible() and not button.active
        assert second.get_visible()
        assert figure.canvas.mouse_grabber is None
        assert pages.switch("process")
        assert button.active
    finally:
        pages.dispose()
        plt.close(figure)


def test_page_switch_refuses_busy_and_transform_editing():
    figure = plt.figure()
    editing = [False]
    pages = PageCoordinator(figure.canvas, {"process": PageGroup(), "measure": PageGroup()},
                            editing_transform=lambda: editing[0])
    try:
        for kind in ("analysis", "native_export", "analysis_export"):
            pages.set_busy(kind)
            assert not pages.switch("measure")
            assert pages.current == "process"
        pages.set_busy(None)
        editing[0] = True
        assert not pages.switch("measure")
        editing[0] = False
        assert pages.switch("measure")
        with pytest.raises(ValueError):
            pages.set_busy("unknown")
    finally:
        pages.dispose()
        plt.close(figure)


def test_group_availability_is_recomputed_on_return():
    figure = plt.figure()
    ax = figure.add_axes((.1, .1, .2, .2))
    button = Button(ax, "Disabled")
    pages = PageCoordinator(figure.canvas, {"process": PageGroup((ax,), (button,),
                            available=lambda widget: False), "measure": PageGroup()})
    try:
        assert not button.active
        pages.switch("measure")
        pages.switch("process")
        assert not button.active
    finally:
        pages.dispose()
        plt.close(figure)
