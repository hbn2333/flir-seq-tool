"""Read-only resources and app-owned writable paths for source and frozen runs."""

import os
from pathlib import Path
import sys


def is_frozen():
    return bool(getattr(sys, 'frozen', False))


def application_root():
    return (Path(sys.executable).resolve().parent if is_frozen()
            else Path(__file__).resolve().parents[2])


def workspace_root():
    root = application_root()
    return root / 'output' if is_frozen() else root


def resource_root():
    return Path(getattr(sys, '_MEIPASS', application_root())).resolve()


def bundled_ffmpeg():
    for root in dict.fromkeys((application_root(), resource_root())):
        candidate = root / 'tools' / 'ffmpeg.exe'
        if candidate.is_file():
            return candidate
    return None


def configure_portable_environment():
    if not is_frozen():
        return
    root = application_root()
    directories = ('cache/tmp', 'cache/matplotlib', 'logs',
                   'output/clips', 'output/analysis', 'output/selections')
    for relative in directories:
        (root / relative).mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = str(root / 'cache/tmp')
    os.environ['TMP'] = os.environ['TEMP']
    os.environ['MPLCONFIGDIR'] = str(root / 'cache/matplotlib')
    os.environ['XDG_CACHE_HOME'] = str(root / 'cache')
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.dont_write_bytecode = True
    # tempfile caches its first directory; changing TEMP alone does not move it.
    import tempfile
    tempfile.tempdir = str(root / 'cache/tmp')

