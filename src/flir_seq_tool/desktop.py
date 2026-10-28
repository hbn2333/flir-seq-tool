"""Small desktop entry point, keeping SDK and GUI imports out of startup routing."""

from pathlib import Path
import sys


def choose_seq():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    try:
        value = filedialog.askopenfilename(parent=root, title='Open FLIR SEQ',
            filetypes=[('FLIR SEQ', '*.seq')])
        return Path(value) if value else None
    finally:
        root.destroy()


def launch_desktop(args=None, *, choose=None, run=None):
    args = list(sys.argv[1:] if args is None else args)
    if not args:
        selected = (choose or choose_seq)()
        if selected is None:
            return 0
        args = ['view', str(selected)]
    elif len(args) == 1 and Path(args[0]).suffix.lower() == '.seq':
        args = ['view', args[0]]
    if run is None:
        from .cli import main
        run = main
    result = run(args)
    return 0 if result is None else result


def show_startup_error(message):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror('FLIR SEQ Tool', message, parent=root)
    finally:
        root.destroy()
