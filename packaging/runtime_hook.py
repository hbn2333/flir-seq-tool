"""Runs before PyInstaller's automatic hooks create any temporary directories."""
from flir_seq_tool.runtime_paths import configure_portable_environment

configure_portable_environment()
