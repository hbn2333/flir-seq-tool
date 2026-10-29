"""One native window with transactional reader/session replacement."""
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

from .file_safety import TargetSnapshot
from .selection_dialogs import SelectionDialogs


class UIResources:
    """A build boundary also catches callbacks registered before a build fails."""

    def __init__(self, figure):
        self.figure = figure
        self.baseline = self._callbacks()
        self.artists = set(figure.get_children())
        self.ids = None
        self.timers = []
        self.cleanups = []
        self.widgets = []
        self.errors = []
        self.disposed = False
        self.previous_resources = set(getattr(figure, "_viewer_resources", ()))
        if not hasattr(figure, "_viewer_resources"):
            figure._viewer_resources = []
        figure._viewer_resources.append(self)

    def _callbacks(self):
        return {cid for callbacks in self.figure.canvas.callbacks.callbacks.values() for cid in callbacks}

    def add_timer(self, timer):
        self.timers.append(timer)

    def add_cleanup(self, callback):
        self.cleanups.append(callback)

    def add_widgets(self, *widgets):
        self.widgets.extend(widgets)

    def seal(self):
        self.ids = self._callbacks() - self.baseline

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        for resource in tuple(self.figure._viewer_resources):
            if resource is not self and resource not in self.previous_resources:
                resource.dispose()
        self.forget()
        for timer in self.timers:
            timer.stop()
            timer.callbacks.clear()
        try:
            for widget in self.widgets:
                widget.eventson = False
                if hasattr(widget, "stop_typing") and getattr(widget, "capturekeystrokes", False):
                    widget.stop_typing()
                widget.set_active(False)
                widget.disconnect_events()
            for callback in reversed(self.cleanups):
                try:
                    callback()
                except Exception as exc:
                    # Reclaim every other owner even if one backend cleanup fails.
                    self.errors.append(str(exc))
        finally:
            canvas = self.figure.canvas
            for cid in self.ids if self.ids is not None else self._callbacks() - self.baseline:
                canvas.mpl_disconnect(cid)
            if canvas.mouse_grabber is not None:
                canvas.release_mouse(canvas.mouse_grabber)
            owner = getattr(canvas.widgetlock, "_owner", None)
            if owner is not None:
                canvas.widgetlock.release(owner)
            for artist in tuple(self.figure.get_children()):
                if artist not in self.artists:
                    artist.remove()

    def forget(self):
        if self in self.figure._viewer_resources:
            self.figure._viewer_resources.remove(self)


@dataclass(frozen=True)
class SaveOutcome:
    status: str
    signature: str | None = None
    path: Path | None = None
    error: str | None = None


def save_window_selection(session, dialogs) -> SaveOutcome:
    """Persist a frozen Process document; never infer success from dirty flags."""
    try:
        document = session.selection_document(scope="process")
        signature = session.state_signature(scope="process")
        target = dialogs.choose_save(session.selection_store.root,
                    session.selection_store.path_for(Path(session.reader.path)).name,
                    title="Save before opening: candidate range becomes next work range; results are not saved")
        if target is None:
            return SaveOutcome("cancelled")
        target = Path(target)
        expected = TargetSnapshot.capture(target)
        backup = TargetSnapshot.capture(target.with_name(target.name + ".bak"))
        if expected.exists and not dialogs.confirm(f"Overwrite selection?\n{target}"):
            return SaveOutcome("cancelled")
        saved = session.selection_store.save_to(target, document, source_path=Path(session.reader.path),
                    expected_target=expected, expected_backup=backup)
        # A modal dialog can dispatch UI events. Only this exact snapshot authorizes opening.
        if session.state_signature(scope="process") != signature:
            return SaveOutcome("failed", signature, saved, "Viewer settings changed while saving; open cancelled")
        session.selection_path = saved
        session.process_saved_signature = signature
        session.selection_dirty = False
        return SaveOutcome("saved", signature, saved)
    except Exception as exc:
        return SaveOutcome("failed", error=str(exc))


@dataclass
class _OwnedSession:
    session: Any
    stack: ExitStack
    local_frame: int
    notices: tuple[str, ...]

    def close(self):
        try:
            self.session.cleanup_measurement_results()
            self.session.source_cache.clear()
            self.session.transformed_cache.clear()
        finally:
            self.stack.close()


class ViewerWindow:
    def __init__(self, *, reader_factory=None, builder=None, dialogs=None, session_factory=None):
        from .viewer import ViewerSession, build_viewer
        from .reader import ScienceFileReader
        self.reader_factory = reader_factory or ScienceFileReader
        self.builder = builder or build_viewer
        self.session_factory = session_factory or ViewerSession
        self.dialogs = dialogs
        self.figure = None
        self.ui = None
        self.current = None
        self.candidate = None
        self.opening = False
        self.closed = False

    @property
    def session(self):
        return None if self.current is None else self.current.session

    def _prepare(self, path, **startup):
        stack = ExitStack()
        session = None
        try:
            reader = stack.enter_context(self.reader_factory(Path(path)))
            session = self.session_factory(reader)
            local, notices = session.restore_startup(**startup)
            session.frame_at_local(local)
            return _OwnedSession(session, stack, local, notices)
        except BaseException:
            if session is not None:
                session.cleanup_measurement_results()
            stack.close()
            raise

    def _install(self, owned, *, local_frame=None, page="process"):
        boundary = UIResources(self.figure)
        ui = None
        try:
            ui = self.builder(owned.session,
                initial_frame=owned.local_frame if local_frame is None else local_frame,
                figure=self.figure, dialogs=self.dialogs, on_open_seq=self.open_source,
                can_open_seq=lambda: not self.opening and not self.closed,
                on_window_close=lambda: self.close(wait=False))
            if page != "process":
                ui.pages.switch(page)
            if owned.notices:
                message = "\n".join(owned.notices)
                ui.message_details["startup"] = message
                ui.error_text.set_text(message)
            self.figure.canvas.draw()
            boundary.forget()
            return ui
        except BaseException:
            if ui is not None:
                ui.dispose(preserve_session=True)
            boundary.dispose()
            raise

    def start(self, path, *, initial_frame=None, vmin=None, vmax=None):
        if self.current is not None:
            raise RuntimeError("Viewer window is already open")
        self.candidate = self._prepare(path, initial_frame=initial_frame, vmin=vmin, vmax=vmax)
        try:
            self.figure = plt.figure(figsize=(12, 10))
            if self.dialogs is None:
                self.dialogs = SelectionDialogs(getattr(self.figure.canvas.manager, "window", None))
            ui = self._install(self.candidate)
        except BaseException:
            self.candidate.close()
            self.candidate = None
            if self.figure is not None:
                plt.close(self.figure)
            raise
        self.current, self.candidate, self.ui = self.candidate, None, ui
        return ui

    def can_open(self):
        return (not self.closed and not self.opening and self.ui is not None
                and not self.ui.pages.disposed and self.ui.pages.busy is None
                and not self.ui.mode_state.editing_transform and not self.ui.measure_panel.adding)

    def _report(self, text):
        if self.ui is not None:
            self.ui.error_text.set_text(text)
            self.figure.canvas.draw_idle()

    def open_source(self, path=None):
        if not self.can_open():
            return False
        self.opening = True
        self.ui.pages.refresh()
        old = self.current
        try:
            if path is None:
                path = self.dialogs.choose_seq_open(Path(old.session.reader.path).parent)
            if path is None:
                return False
            path = Path(path)
            current_path = Path(old.session.reader.path)
            if path.resolve() == current_path.resolve() or (path.exists() and path.samefile(current_path)):
                return False
            if old.session.has_unsaved_changes():
                action = self.dialogs.choose_unsaved_action(
                    "Current SEQ has unsaved changes. Save and open another file?\n"
                    "Save uses the Process candidate range as the next work range, plus the applied transform, "
                    "all ROI/height and display settings. The confirmed range is not saved separately. "
                    "Calculated results are not saved.")
                if action == "save":
                    saved = save_window_selection(old.session, self.dialogs)
                    if saved.status != "saved" or saved.signature != old.session.state_signature(scope="process"):
                        if saved.error:
                            self._report(f"Save before open failed: {saved.error}")
                        return False
                elif action != "discard":
                    return False
            self.candidate = self._prepare(path)
            old_page = self.ui.pages.current
            old_frame = old.session.timeline.current_local_frame
            self.ui.dispose(preserve_session=True)
            try:
                candidate_ui = self._install(self.candidate)
            except Exception as exc:
                self.candidate.close()
                self.candidate = None
                self.ui = self._install(old, local_frame=old_frame, page=old_page)
                self._report(f"Open SEQ failed; restored previous view: {exc}")
                return False
            self.current, self.candidate, self.ui = self.candidate, None, candidate_ui
            old.close()
            return True
        except Exception as exc:
            if self.candidate is not None:
                self.candidate.close()
                self.candidate = None
            self._report(f"Open SEQ failed: {exc}")
            return False
        finally:
            self.opening = False
            if self.ui is not None:
                self.ui.pages.refresh()

    def close(self, *, wait=True):
        self.closed = True
        if self.ui is not None:
            self.ui.dispose(wait=wait)
        # GUI close requests cancel promptly; launch's finally completes resource release.
        if not wait:
            return
        if self.candidate is not None:
            self.candidate.close()
            self.candidate = None
        if self.current is not None:
            self.current.close()
            self.current = None
