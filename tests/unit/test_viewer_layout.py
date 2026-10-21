from itertools import combinations

import pytest

from flir_seq_tool.viewer_layout import EDITOR_RECTS, NORMAL_RECTS, TEXT_POSITIONS


def test_long_message_is_bounded_without_losing_detail_hint():
    from flir_seq_tool import viewer_layout

    original = "Save failed: D:/" + "nested/" * 60 + "a.selection.json"
    displayed = viewer_layout.fit_message(original, max_chars=60, max_lines=2)
    assert len(displayed.splitlines()) <= 2
    assert all(len(line) <= 60 for line in displayed.splitlines())
    assert "F1" in displayed


def test_short_message_and_line_breaks_are_preserved():
    from flir_seq_tool import viewer_layout

    assert viewer_layout.fit_message("one\ntwo", max_chars=60, max_lines=2) == "one\ntwo"
    assert viewer_layout.fit_message("", max_chars=60, max_lines=2) == ""


def test_long_message_fitting_does_not_measure_every_possible_width(monkeypatch):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from flir_seq_tool.viewer_layout import BoundedMessage

    figure = Figure(figsize=(10, 8))
    canvas = FigureCanvasAgg(figure)
    message = BoundedMessage(figure, "message", {}, max_lines=2)
    message.set_text("WideWWWdirectory/" * 50)
    renderer = canvas.get_renderer()
    original = renderer.get_text_width_height_descent
    calls = []

    def measure(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(renderer, "get_text_width_height_descent", measure)
    canvas.draw()
    assert len(calls) < 30


@pytest.mark.parametrize("editing", [False, True])
def test_shared_control_rectangles_are_inside_canvas_and_disjoint(editing):
    if editing:
        rects = [NORMAL_RECTS["current"], EDITOR_RECTS["angle_slider"], EDITOR_RECTS["angle_box"]]
        for name in ("quick", "crop", "actions"):
            rects.extend(EDITOR_RECTS[name])
    else:
        rects = list(NORMAL_RECTS.values())
    for x, y, width, height in rects:
        assert 0 <= x < x + width <= 1
        assert 0 <= y < y + height <= 1
    for first, second in combinations(rects, 2):
        x1, y1, w1, h1 = first
        x2, y2, w2, h2 = second
        assert x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1
    for x, y in TEXT_POSITIONS.values():
        assert 0 < x < 1 and 0 < y < 1
