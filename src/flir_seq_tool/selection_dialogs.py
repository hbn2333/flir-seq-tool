"""Lazy, main-thread native dialogs; never reads or writes selection content."""

from pathlib import Path
import threading


def normalize_video_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if (not path.name or path.name.endswith((".", " "))
            or (path.suffix and path.suffix.lower() != ".mp4")):
        raise ValueError("Use a .mp4 filename")
    from .extraction import normalize_clip_name
    # Reuse Windows base-name validation without imposing the SEQ suffix.
    normalize_clip_name(path.stem + ".seq")
    return path if path.suffix else path.with_suffix(".mp4")


def _speed_dialog(tk, parent):
    from tkinter import simpledialog

    class SpeedDialog(simpledialog.Dialog):
        def body(self, master):
            self.speed = tk.IntVar(master, value=1)
            tk.Label(master, text="Playback speed").pack(anchor="w", padx=12, pady=6)
            for value in (1, 2, 5, 10):
                tk.Radiobutton(master, text=f"{value}×", variable=self.speed,
                    value=value).pack(anchor="w", padx=12)

        def apply(self):
            self.result = self.speed.get()

    return SpeedDialog(parent, title="Export MP4").result


def normalize_selection_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.name.lower().endswith(".selection.json"):
        return path
    if path.suffix or path.name.endswith((".", " ")) or not path.name:
        raise ValueError("Use a .selection.json filename")
    return path.with_name(path.name + ".selection.json")


def normalize_analysis_path(value: str, kind: str) -> Path | None:
    if kind not in ("csv", "png"):
        raise ValueError("unknown analysis export kind")
    if not value:
        return None
    path = Path(value)
    if path.suffix.lower() == "." + kind:
        return path
    if path.suffix or path.name.endswith((".", " ")) or not path.name:
        raise ValueError(f"Use a .{kind} filename")
    return path.with_suffix("." + kind)


def normalize_seq_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.name or path.name.endswith((".", " ")):
        raise ValueError("Use a .seq filename")
    if path.suffix and path.suffix.lower() != ".seq":
        raise ValueError("Use a .seq filename")
    from .extraction import normalize_clip_name
    normalize_clip_name(path.name)
    return path if path.suffix else path.with_suffix(".seq")


class SelectionDialogs:
    def __init__(self, parent=None):
        self.parent = parent

    def _run(self, group: str, operation: str, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Dialogs must run on the GUI main thread")
        try:
            import tkinter as tk
            from tkinter import filedialog, messagebox
        except ImportError as exc:
            raise RuntimeError(f"Native dialogs unavailable: {exc}") from exc
        owned = None
        try:
            parent = self.parent
            if not isinstance(parent, tk.Misc):
                owned = tk.Tk()
                owned.withdraw()
                parent = owned
            if group == "speed":
                return _speed_dialog(tk, parent)
            module = filedialog if group == "file" else messagebox
            return getattr(module, operation)(parent=parent, **kwargs)
        except tk.TclError as exc:
            raise RuntimeError(f"Native dialog failed: {exc}") from exc
        finally:
            if owned is not None:
                try:
                    owned.destroy()
                except tk.TclError as exc:
                    raise RuntimeError(f"Native dialog cleanup failed: {exc}") from exc

    def choose_save(self, initial_dir: Path, suggested_name: str, *, title="Save selection") -> Path | None:
        value = self._run(
            "file", "asksaveasfilename", title=title,
            initialdir=str(initial_dir), initialfile=suggested_name,
            filetypes=[("Selection JSON", "*.selection.json")],
            confirmoverwrite=False,
        )
        return normalize_selection_path(value)

    def choose_seq_export(self, initial_dir: Path, suggested_name: str) -> Path | None:
        value = self._run(
            "file", "asksaveasfilename", title="Export sub-SEQ",
            initialdir=str(initial_dir), initialfile=suggested_name,
            filetypes=[("FLIR SEQ", "*.seq")], confirmoverwrite=False,
        )
        return normalize_seq_path(value)

    def choose_video_speed(self) -> int | None:
        return self._run("speed", "choose")

    def choose_video_export(self, initial_dir: Path, suggested_name: str) -> Path | None:
        value = self._run("file", "asksaveasfilename", title="Export MP4",
            initialdir=str(initial_dir), initialfile=suggested_name,
            filetypes=[("MP4 video", "*.mp4")], confirmoverwrite=False)
        return normalize_video_path(value)

    def choose_load(self, initial_dir: Path) -> Path | None:
        value = self._run(
            "file", "askopenfilename", title="Load selection",
            initialdir=str(initial_dir),
            filetypes=[("Selection JSON", "*.selection.json")],
        )
        return Path(value) if value else None

    def choose_seq_open(self, initial_dir: Path) -> Path | None:
        value = self._run("file", "askopenfilename", title="Open SEQ",
                          initialdir=str(initial_dir), filetypes=[("FLIR SEQ", "*.seq")])
        if not value:
            return None
        path = Path(value)
        if path.suffix.lower() != ".seq":
            raise ValueError("Use a .seq file")
        return path

    def choose_unsaved_action(self, message: str) -> str:
        answer = self._run("message", "askyesnocancel", title="Open another SEQ",
                           message=message, detail="Yes: Save and open    No: Discard and open    Cancel: Keep current SEQ")
        return "cancel" if answer is None else "save" if answer else "discard"

    def choose_analysis_export(self, kind: str, initial_dir: Path, suggested_name: str) -> Path | None:
        if kind not in ("csv", "png"):
            raise ValueError("unknown analysis export kind")
        value = self._run("file", "asksaveasfilename", title=f"Export {kind.upper()}",
                          initialdir=str(initial_dir), initialfile=suggested_name,
                          filetypes=[(kind.upper(), "*." + kind)], confirmoverwrite=False)
        return normalize_analysis_path(value, kind)

    def confirm(self, message: str) -> bool:
        return bool(self._run("message", "askyesno", title="Confirm selection", message=message))

    def show_details(self, message: str) -> None:
        self._run("message", "showinfo", title="Viewer details", message=message)
