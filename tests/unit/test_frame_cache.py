from datetime import datetime

import numpy as np
import pytest

from flir_seq_tool.frame_cache import FrameCache
from flir_seq_tool.models import TemperatureFrame


def make_frame(frame_index: int) -> TemperatureFrame:
    return TemperatureFrame(
        frame_index=frame_index,
        timestamp=datetime(2026, 8, 28, 9, 0, frame_index),
        temperature_c=np.full((1, 1), frame_index, dtype=np.float32),
    )


def test_frame_cache_reuses_hits_and_evicts_least_recently_used():
    calls: list[int] = []

    def loader(frame_index: int) -> TemperatureFrame:
        calls.append(frame_index)
        return make_frame(frame_index)

    cache = FrameCache(maxsize=3)
    assert cache.get(0, loader).frame_index == 0
    assert cache.get(0, loader).frame_index == 0
    assert calls == [0]
    cache.get(1, loader)
    cache.get(2, loader)
    cache.get(3, loader)
    cache.get(0, loader)

    assert calls == [0, 1, 2, 3, 0]
    assert len(cache) == 3


def test_frame_cache_hit_updates_lru_order():
    calls: list[int] = []

    def loader(frame_index: int) -> TemperatureFrame:
        calls.append(frame_index)
        return make_frame(frame_index)

    cache = FrameCache(maxsize=2)
    cache.get(0, loader)
    cache.get(1, loader)
    cache.get(0, loader)
    cache.get(2, loader)
    cache.get(1, loader)

    assert calls == [0, 1, 2, 1]


def test_frame_cache_rejects_nonpositive_size():
    with pytest.raises(ValueError, match="maxsize must be positive"):
        FrameCache(maxsize=0)


def test_frame_cache_accepts_hashable_keys_and_clear_removes_all_entries():
    calls: list[tuple[int, str]] = []

    def loader(key: tuple[int, str]) -> TemperatureFrame:
        calls.append(key)
        return make_frame(key[0])

    cache = FrameCache[tuple[int, str]](maxsize=2)
    key = (0, "transform-a")
    cache.get(key, loader)
    cache.get(key, loader)

    cache.clear()

    assert len(cache) == 0
    cache.get(key, loader)
    assert calls == [key, key]
