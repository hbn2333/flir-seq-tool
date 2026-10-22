from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import numpy as np
import pytest

from flir_seq_tool.models import TemperatureFrame
from flir_seq_tool.viewer import ViewerSession, build_viewer
from flir_seq_tool.selection import SelectionStore


class SyntheticReader:
    def __init__(self, path):
        self.path = path
        path.write_bytes(b"synthetic-source")
        self.width, self.height, self.num_frames = 8, 6, 5
        self.requested_frames = []

    def timestamp_at(self, index):
        return datetime(2026, 10, 22) + timedelta(milliseconds=20 * index)

    def temperature_frame_at(self, index):
        self.requested_frames.append(index)
        return TemperatureFrame(index, self.timestamp_at(index),
                                np.arange(48, dtype=float).reshape(6, 8) + 10 * index)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class SyntheticDialogs:
    def __init__(self):
        self.answer = True
        self.confirmations = []
        self.save = self.load = None
        self.analysis_paths = {"csv": None, "png": None}
        self.analysis_choices = []

    def confirm(self, message):
        self.confirmations.append(message)
        return self.answer

    def choose_save(self, *args, **kwargs):
        return self.save

    def choose_load(self, *args):
        return self.load

    def choose_analysis_export(self, kind, initial_dir, suggested_name):
        self.analysis_choices.append((kind, initial_dir, suggested_name))
        return self.analysis_paths[kind]

    def show_details(self, message):
        pass


@pytest.fixture
def make_measure_ui(tmp_path):
    figures = []
    viewers = []
    def make(**kwargs):
        reader = SyntheticReader(tmp_path / f"source-{len(figures)}.seq")
        session = ViewerSession(reader, selection_store=SelectionStore(tmp_path / "configs"))
        ui = build_viewer(session, dialogs=kwargs.pop("dialogs", SyntheticDialogs()), **kwargs)
        figures.append(ui.figure)
        viewers.append(ui)
        return ui
    yield make
    for ui in viewers:
        ui.shutdown_analysis(wait=True)
    for figure in figures:
        plt.close(figure)
