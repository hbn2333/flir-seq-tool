from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar

from .models import TemperatureFrame


K = TypeVar("K", bound=Hashable)


class FrameCache(Generic[K]):
    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be positive")
        self.maxsize = maxsize
        self._frames: OrderedDict[K, TemperatureFrame] = OrderedDict()

    def get(
        self,
        key: K,
        loader: Callable[[K], TemperatureFrame],
    ) -> TemperatureFrame:
        if key in self._frames:
            self._frames.move_to_end(key)
            return self._frames[key]

        frame = loader(key)
        self._frames[key] = frame
        if len(self._frames) > self.maxsize:
            self._frames.popitem(last=False)
        return frame

    def clear(self) -> None:
        self._frames.clear()

    def __len__(self) -> int:
        return len(self._frames)
