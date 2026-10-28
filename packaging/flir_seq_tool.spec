# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_dynamic_libs, copy_metadata
import fnv

project = Path(SPECPATH).parent
sdk_dir = Path(fnv.__file__).parent
sdk_search_handle = os.add_dll_directory(str(sdk_dir / '_lib'))
binaries = collect_dynamic_libs('fnv')
# App-local VC++ runtime: read/copy existing files, never install a redist.
for name in ('msvcp140.dll', 'concrt140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):
    path = Path(os.environ['SystemRoot']) / 'System32' / name
    if path.is_file():
        binaries.append((str(path), '.'))

a = Analysis([str(project / 'packaging' / 'launcher.py')], pathex=[str(project / 'src')],
    binaries=binaries, datas=copy_metadata('FileSDK'),
    hiddenimports=['fnv._fnv', 'fnv.file', 'fnv.reduce', 'matplotlib.backends.backend_tkagg',
                   'flir_seq_tool.portable_diagnostics'],
    hookspath=[], hooksconfig={'matplotlib': {'backends': ['TkAgg', 'Agg']}},
    runtime_hooks=[str(project / 'packaging' / 'runtime_hook.py')],
    excludes=['pytest', 'IPython', 'notebook', 'PyQt5', 'PyQt6', 'PySide6'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='FLIR_SEQ_Tool',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='FLIR_SEQ_Tool')
