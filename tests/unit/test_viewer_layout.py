from itertools import combinations

import pytest

from flir_seq_tool.viewer_layout import EDITOR_RECTS, NORMAL_RECTS, TEXT_POSITIONS


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
