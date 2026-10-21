from __future__ import annotations

import numpy as np
from scipy.ndimage import map_coordinates

from .transform import CropRect, FrameTransform


class FrameTransformer:
    def __init__(self, transform: FrameTransform) -> None:
        self._transform = transform
        self._identity = (
            transform.rotation.angle_deg == 0.0
            and transform.crop
            == CropRect(0, 0, transform.source_width, transform.source_height)
        )
        angle = transform.rotation.angle_deg
        self._quarter_turns = int(angle / 90) if angle % 90 == 0 else None
        self._crop_coordinates = (
            None if self._quarter_turns is not None
            else self._coordinates_for_crop(transform.crop)
        )
        self._preview_coordinates: tuple[np.ndarray, np.ndarray] | None = None

    @property
    def transform(self) -> FrameTransform:
        return self._transform

    def _coordinates_for_crop(
        self,
        crop: CropRect,
    ) -> tuple[np.ndarray, np.ndarray]:
        rotated_y, rotated_x = np.indices(
            (crop.height, crop.width),
            dtype=np.float64,
        )
        rotated_x += crop.x
        rotated_y += crop.y
        source_y, source_x = self._map_rotated_grid(rotated_x, rotated_y)
        return (
            np.clip(source_y, 0.0, self.transform.source_height - 1),
            np.clip(source_x, 0.0, self.transform.source_width - 1),
        )

    def _map_rotated_grid(
        self,
        rotated_x: np.ndarray,
        rotated_y: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        matrix = self.transform.rotated_to_source_matrix
        source_x = (
            matrix[0, 0] * rotated_x
            + matrix[0, 1] * rotated_y
            + matrix[0, 2]
        )
        source_y = (
            matrix[1, 0] * rotated_x
            + matrix[1, 1] * rotated_y
            + matrix[1, 2]
        )
        return source_y, source_x

    def _validated_array(self, values: np.ndarray) -> np.ndarray:
        array = np.asarray(values)
        source_shape = (
            self.transform.source_height,
            self.transform.source_width,
        )
        if array.shape != source_shape:
            raise ValueError(
                f"frame shape {array.shape} does not match source shape {source_shape}"
            )
        return array

    def _sample(
        self,
        array: np.ndarray,
        coordinates: tuple[np.ndarray, np.ndarray],
    ) -> np.ndarray:
        if not np.issubdtype(array.dtype, np.inexact):
            array = array.astype(np.float64)
        return map_coordinates(
            array,
            coordinates,
            order=1,
            mode="nearest",
            prefilter=False,
        )

    def apply(self, values: np.ndarray) -> np.ndarray:
        array = self._validated_array(values)
        if self._identity:
            return array
        if self._quarter_turns is not None:
            rotated = np.rot90(array, self._quarter_turns)
            crop = self.transform.crop
            return rotated[
                crop.y:crop.y + crop.height, crop.x:crop.x + crop.width
            ].copy()
        assert self._crop_coordinates is not None
        return self._sample(array, self._crop_coordinates)

    def preview(self, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        array = self._validated_array(values)
        if self._quarter_turns is not None:
            return (
                np.rot90(array, self._quarter_turns).copy(),
                self.transform.valid_mask.copy(),
            )
        if self._preview_coordinates is None:
            rotated_height, rotated_width = self.transform.rotated_shape
            self._preview_coordinates = self._coordinates_for_crop(
                CropRect(0, 0, rotated_width, rotated_height)
            )

        rotated = self._sample(array, self._preview_coordinates)
        valid_mask = self.transform.valid_mask.copy()
        if not np.issubdtype(rotated.dtype, np.inexact):
            rotated = rotated.astype(np.float64)
        rotated[~valid_mask] = np.nan
        return rotated, valid_mask
