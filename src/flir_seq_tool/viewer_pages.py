"""Explicit page input lifecycles for a single Matplotlib figure."""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PageGroup:
    axes: tuple = ()
    widgets: tuple = ()
    selectors: tuple = ()
    texts: tuple = ()
    available: Callable[[Any], bool] = lambda widget: True
    available_during_busy: Callable[[Any, str], bool] = lambda widget, kind: False
    on_visibility: Callable[[bool], None] | None = None
    disconnect_ids: list[int] = field(default_factory=list)


def deactivate_group(group: PageGroup, canvas) -> None:
    for widget in group.widgets:
        if hasattr(widget, "stop_typing"):
            widget.stop_typing()
        widget.set_active(False)
        if hasattr(widget, "drag_active"):
            widget.drag_active = False
    for selector in group.selectors:
        selector.set_active(False)
        selector.set_visible(False)
        if canvas.widgetlock.isowner(selector):
            canvas.widgetlock.release(selector)
    if canvas.mouse_grabber in group.axes:
        canvas.release_mouse(canvas.mouse_grabber)
    for artist in (*group.axes, *group.texts):
        artist.set_visible(False)
    if group.on_visibility is not None:
        group.on_visibility(False)


class PageCoordinator:
    def __init__(self, canvas, groups: dict[str, PageGroup], *,
                 editing_transform: Callable[[], bool] = lambda: False,
                 on_refresh: Callable[[str], None] | None = None,
                 on_error: Callable[[str], None] | None = None):
        self.canvas = canvas
        self.groups = groups
        self.current = "process"
        self.busy: str | None = None
        self.editing_transform = editing_transform
        self.on_refresh = on_refresh
        self.on_error = on_error
        self.disposed = False
        self.refresh()

    def refresh(self) -> None:
        if self.disposed:
            return
        for name, group in self.groups.items():
            if name != self.current:
                deactivate_group(group, self.canvas)
                continue
            for artist in (*group.axes, *group.texts):
                artist.set_visible(True)
            if group.on_visibility is not None:
                group.on_visibility(True)
            for widget in group.widgets:
                widget.set_active(group.available(widget) if self.busy is None
                                  else group.available_during_busy(widget, self.busy))
            for selector in group.selectors:
                selector.set_active(self.busy is None)
        if self.on_refresh is not None:
            self.on_refresh(self.current)

    def switch(self, page: str) -> bool:
        if page not in self.groups:
            raise ValueError("unknown page")
        if self.disposed or self.busy is not None or self.editing_transform():
            if self.on_error is not None:
                self.on_error("Finish or cancel the current task/transform draft before switching pages")
            return False
        self.current = page
        self.refresh()
        self.canvas.draw_idle()
        return True

    def set_busy(self, kind: str | None) -> None:
        if kind not in (None, "analysis", "native_export", "analysis_export", "video_export"):
            raise ValueError("unknown busy task")
        self.busy = kind
        self.refresh()

    def dispose(self) -> None:
        if self.disposed:
            return
        self.disposed = True
        for group in self.groups.values():
            deactivate_group(group, self.canvas)
            for widget in (*group.widgets, *group.selectors):
                widget.disconnect_events()
            for cid in group.disconnect_ids:
                self.canvas.mpl_disconnect(cid)
