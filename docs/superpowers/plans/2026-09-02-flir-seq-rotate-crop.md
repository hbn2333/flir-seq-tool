# FLIR SEQ Rotate and Crop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有只读 FLIR SEQ Matplotlib 查看器中增加固定于整段序列的非破坏式旋转裁剪，并使变换可保存、加载、逐帧复现和供后续测温点与 ROI 使用。

**Architecture:** 用纯几何模块定义严格的变换配置、坐标映射、有效掩码和最大有效矩形；用独立逐帧处理器预计算 SciPy 双线性采样网格；用独立编辑状态和 Matplotlib 控件实现单窗口草稿式交互。`ViewerSession` 负责原始帧/修整帧缓存和原子会话状态，`selection.py` 负责 schema v1 到 v2 的兼容与持久化。

**Tech Stack:** Python 3.13.5 x64、NumPy、SciPy `ndimage.map_coordinates`、Matplotlib widgets/`RectangleSelector`、pytest、FLIR Science File SDK 2026.1.2。

---

## 实施前约束

- 唯一工作目录是 `D:\carbonfoam\infrared`。不得使用已经失效的 `D:\carbonfoam\红外`。
- 用户的唯一工作目录约束覆盖额外 Git worktree 的默认建议；所有实施都在当前 `main` 工作目录进行。
- `D:\carbonfoam\infrared\raw` 始终只读，不覆盖、不修改、不重命名。
- 开始实施时的设计基线提交为 `95bd4c0`；本计划提交将位于其后。
- 每个任务遵循红—绿—重构顺序，只暂存该任务列出的文件。
- 不清理或提交仓库中任何无关改动。

## 文件结构

### 新建

- `src/flir_seq_tool/transform.py`：不可变变换配置、角度规范化、扩展画布、正逆坐标映射、有效掩码、最大有效矩形、稳定指纹。
- `src/flir_seq_tool/frame_transformer.py`：预计算采样坐标，对单帧温度矩阵做双线性旋转和裁剪，提供完整画布预览与恒等快速路径。
- `src/flir_seq_tool/transform_editor.py`：变换草稿状态、角度/裁剪状态转换、Matplotlib 旋转裁剪控件和 `RectangleSelector` 同步。
- `tests/unit/test_transform.py`：几何、坐标、有效掩码和最大矩形测试。
- `tests/unit/test_frame_transformer.py`：双线性数值、恒等路径、缓存键和有限值测试。
- `tests/unit/test_transform_editor.py`：草稿、控件同步、非法框和 Apply/Cancel/Reset 测试。

### 修改

- `pyproject.toml`：正式声明 NumPy、Matplotlib 和 SciPy 运行依赖。
- `src/flir_seq_tool/frame_cache.py`：增加显式清空能力，供变换更改时失效修整帧缓存。
- `src/flir_seq_tool/selection.py`：schema v2、transform 字段、v1 内存迁移和严格校验。
- `src/flir_seq_tool/viewer.py`：会话变换状态、双缓存、普通/编辑模式切换、脏状态、加载二次确认和状态栏。
- `tests/unit/test_frame_cache.py`：缓存清空测试。
- `tests/unit/test_selection.py`：v2 往返、v1 兼容和非法 transform 测试。
- `tests/unit/test_viewer.py`：会话变换、单窗口模式、保存加载、导出不变和按需读取测试。
- `tests/integration/test_small_seq_viewer.py`：最小真实 SEQ 首/中/末帧旋转裁剪与只读验证。

## 公开类型契约

后续任务统一使用下列名称，不得另起同义 API：

```python
RotationSettings(angle_deg: float)
CropRect(x: int, y: int, width: int, height: int)
FrameTransform(source_width: int, source_height: int, rotation: RotationSettings, crop: CropRect)
FrameTransform.identity(source_width: int, source_height: int) -> FrameTransform
FrameTransform.with_angle(angle_deg: float) -> FrameTransform
FrameTransform.fingerprint -> str
FrameTransform.source_to_rotated(x: float, y: float) -> tuple[float, float]
FrameTransform.rotated_to_source(x: float, y: float) -> tuple[float, float]
FrameTransform.source_to_cropped(x: float, y: float) -> tuple[float, float]
FrameTransform.cropped_to_source(x: float, y: float) -> tuple[float, float]

FrameTransformer(transform: FrameTransform)
FrameTransformer.apply(values: np.ndarray) -> np.ndarray
FrameTransformer.preview(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]

TransformDraft.from_applied(transform: FrameTransform) -> TransformDraft
TransformDraft.set_angle(angle_deg: float) -> None
TransformDraft.set_crop(crop: CropRect) -> None
TransformDraft.reset() -> None
TransformDraft.build() -> FrameTransform
```

## Task 1: 严格变换值对象与角度契约

**Files:**
- Create: `src/flir_seq_tool/transform.py`
- Create: `tests/unit/test_transform.py`

- [ ] **Step 1: 写角度、裁剪值对象和恒等变换的失败测试**

```python
import pytest

from flir_seq_tool.transform import CropRect, FrameTransform, RotationSettings


def test_identity_transform_uses_full_source_frame():
    transform = FrameTransform.identity(640, 480)

    assert transform.rotation.angle_deg == 0.0
    assert transform.crop == CropRect(0, 0, 640, 480)
    assert transform.output_shape == (480, 640)


@pytest.mark.parametrize("angle", [15.1, 44.9, 74.9, 105.1, 180.0])
def test_rotation_rejects_angles_outside_cardinal_fine_ranges(angle):
    with pytest.raises(ValueError, match="angle"):
        RotationSettings(angle)


@pytest.mark.parametrize("angle", [0.05, -2.34, 89.95])
def test_rotation_rejects_more_than_one_decimal_place(angle):
    with pytest.raises(ValueError, match="0.1"):
        RotationSettings(angle)


def test_crop_rejects_boolean_and_nonpositive_dimensions():
    with pytest.raises(ValueError, match="integer"):
        CropRect(True, 0, 3, 2)
    with pytest.raises(ValueError, match="positive"):
        CropRect(0, 0, 0, 2)
```

- [ ] **Step 2: 运行测试并确认导入失败**

Run: `python -m pytest tests/unit/test_transform.py -q`

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'flir_seq_tool.transform'`。

- [ ] **Step 3: 实现值对象、标准化角度和基本属性**

在 `src/flir_seq_tool/transform.py` 中建立以下核心实现：

```python
from __future__ import annotations

from dataclasses import dataclass
import math


_ANGLE_TOLERANCE = 1e-9


def normalize_angle(angle_deg: float) -> float:
    if isinstance(angle_deg, bool) or not isinstance(angle_deg, (int, float)):
        raise ValueError("angle_deg must be numeric")
    value = float(angle_deg)
    if not math.isfinite(value):
        raise ValueError("angle_deg must be finite")
    normalized = (value + 180.0) % 360.0 - 180.0
    if math.isclose(normalized, 0.0, abs_tol=_ANGLE_TOLERANCE):
        return 0.0
    return normalized


def _validate_tenth_degree(angle_deg: float) -> None:
    if not math.isclose(angle_deg, round(angle_deg, 1), abs_tol=_ANGLE_TOLERANCE):
        raise ValueError("angle_deg must use 0.1 degree precision")
    distance = abs((angle_deg + 45.0) % 90.0 - 45.0)
    if distance > 15.0 + _ANGLE_TOLERANCE:
        raise ValueError("angle_deg must be within 15 degrees of a cardinal angle")


@dataclass(frozen=True)
class RotationSettings:
    angle_deg: float

    def __post_init__(self) -> None:
        value = normalize_angle(self.angle_deg)
        if not math.isclose(value, float(self.angle_deg), abs_tol=_ANGLE_TOLERANCE):
            raise ValueError("angle_deg must be normalized to [-180, 180)")
        _validate_tenth_degree(value)
        object.__setattr__(self, "angle_deg", value)


@dataclass(frozen=True)
class CropRect:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        for name in ("x", "y", "width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        if self.x < 0 or self.y < 0:
            raise ValueError("crop origin must be non-negative")
        if self.width < 1 or self.height < 1:
            raise ValueError("crop dimensions must be positive")


@dataclass(frozen=True)
class FrameTransform:
    source_width: int
    source_height: int
    rotation: RotationSettings
    crop: CropRect

    def __post_init__(self) -> None:
        for name in ("source_width", "source_height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    @classmethod
    def identity(cls, source_width: int, source_height: int) -> FrameTransform:
        return cls(
            source_width,
            source_height,
            RotationSettings(0.0),
            CropRect(0, 0, source_width, source_height),
        )

    @property
    def output_shape(self) -> tuple[int, int]:
        return self.crop.height, self.crop.width
```

- [ ] **Step 4: 运行 Task 1 测试并确认通过**

Run: `python -m pytest tests/unit/test_transform.py -q`

Expected: PASS。

- [ ] **Step 5: 提交值对象**

```powershell
git add src/flir_seq_tool/transform.py tests/unit/test_transform.py
git commit -m "feat: define SEQ frame transform values"
```

## Task 2: 扩展画布、坐标映射、有效掩码与最大矩形

**Files:**
- Modify: `src/flir_seq_tool/transform.py`
- Modify: `tests/unit/test_transform.py`

- [ ] **Step 1: 写映射、有效区和最大矩形的失败测试**

```python
import numpy as np

from flir_seq_tool.transform import largest_valid_rectangle


def test_quarter_turn_maps_source_and_swaps_canvas_shape():
    transform = FrameTransform.identity(3, 2).with_angle(90.0)

    assert transform.rotated_shape == (3, 2)
    assert transform.source_to_rotated(2.0, 0.0) == pytest.approx((0.0, 0.0))
    assert transform.rotated_to_source(0.0, 0.0) == pytest.approx((2.0, 0.0))


def test_arbitrary_angle_coordinate_round_trip():
    transform = FrameTransform.identity(640, 480).with_angle(-2.3)
    cropped = transform.source_to_cropped(321.25, 201.75)

    assert transform.cropped_to_source(*cropped) == pytest.approx(
        (321.25, 201.75), abs=1e-9
    )


def test_largest_valid_rectangle_uses_top_then_left_tie_break():
    mask = np.array(
        [
            [True, True, False, True, True],
            [True, True, False, True, True],
        ],
        dtype=bool,
    )

    assert largest_valid_rectangle(mask) == CropRect(0, 0, 2, 2)


def test_nonzero_angle_crop_contains_only_valid_pixels():
    transform = FrameTransform.identity(8, 6).with_angle(10.0)
    region = transform.valid_mask[
        transform.crop.y : transform.crop.y + transform.crop.height,
        transform.crop.x : transform.crop.x + transform.crop.width,
    ]

    assert region.all()
```

- [ ] **Step 2: 运行新增测试并确认缺少几何 API**

Run: `python -m pytest tests/unit/test_transform.py -q`

Expected: FAIL，错误指向 `with_angle`、坐标映射或 `largest_valid_rectangle` 尚未定义。

- [ ] **Step 3: 实现确定性旋转几何与正逆矩阵**

在 `transform.py` 中增加：

```python
import hashlib
import json

import numpy as np


def _snapped_trig(angle_deg: float) -> tuple[float, float]:
    radians = math.radians(angle_deg)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    if math.isclose(cosine, 0.0, abs_tol=1e-12):
        cosine = 0.0
    if math.isclose(sine, 0.0, abs_tol=1e-12):
        sine = 0.0
    return cosine, sine


def rotation_geometry(
    width: int, height: int, angle_deg: float
) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    cosine, sine = _snapped_trig(angle_deg)
    center_x = (width - 1) / 2.0
    center_y = (height - 1) / 2.0
    source_to_unshifted = np.array(
        [
            [cosine, sine, center_x - cosine * center_x - sine * center_y],
            [-sine, cosine, center_y + sine * center_x - cosine * center_y],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    corners = np.array(
        [[0.0, width - 1.0, 0.0, width - 1.0],
         [0.0, 0.0, height - 1.0, height - 1.0],
         [1.0, 1.0, 1.0, 1.0]],
        dtype=np.float64,
    )
    rotated = source_to_unshifted @ corners
    min_x = float(rotated[0].min())
    max_x = float(rotated[0].max())
    min_y = float(rotated[1].min())
    max_y = float(rotated[1].max())
    rotated_width = int(math.ceil(max_x - min_x - 1e-12)) + 1
    rotated_height = int(math.ceil(max_y - min_y - 1e-12)) + 1
    shift = np.array(
        [[1.0, 0.0, -min_x], [0.0, 1.0, -min_y], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    source_to_rotated = shift @ source_to_unshifted
    return source_to_rotated, np.linalg.inv(source_to_rotated), (
        rotated_height,
        rotated_width,
    )


def _map_point(matrix: np.ndarray, x: float, y: float) -> tuple[float, float]:
    result = matrix @ np.array([float(x), float(y), 1.0], dtype=np.float64)
    return float(result[0]), float(result[1])
```

为 `FrameTransform` 增加缓存属性 `source_to_rotated_matrix`、`rotated_to_source_matrix`、`rotated_shape`、`valid_mask` 和四个点映射方法。`cropped_to_source` 必须先加裁剪偏移再执行逆矩阵；`source_to_cropped` 必须先旋转再减裁剪偏移。

- [ ] **Step 4: 实现最大有效矩形与严格裁剪验证**

```python
def largest_valid_rectangle(mask: np.ndarray) -> CropRect:
    values = np.asarray(mask, dtype=bool)
    if values.ndim != 2 or values.size == 0 or not values.any():
        raise ValueError("valid mask must contain at least one valid pixel")
    heights = np.zeros(values.shape[1], dtype=np.int64)
    best_area = 0
    best: CropRect | None = None
    for row_index, row in enumerate(values):
        heights = np.where(row, heights + 1, 0)
        stack: list[tuple[int, int]] = []
        for column in range(values.shape[1] + 1):
            current = int(heights[column]) if column < values.shape[1] else 0
            start = column
            while stack and stack[-1][1] > current:
                left, height = stack.pop()
                start = left
                width = column - left
                candidate = CropRect(left, row_index - height + 1, width, height)
                area = width * height
                key = (area, -candidate.y, -candidate.x)
                best_key = (
                    -1 if best is None else best.width * best.height,
                    0 if best is None else -best.y,
                    0 if best is None else -best.x,
                )
                if key > best_key:
                    best_area = area
                    best = candidate
            if not stack or stack[-1][1] < current:
                stack.append((start, current))
    if best is None or best_area < 1:
        raise ValueError("valid mask has no rectangular region")
    return best
```

`FrameTransform.__post_init__` 必须根据旋转几何生成有效掩码，并拒绝越过旋转画布或包含无效像素的裁剪。`FrameTransform.with_angle()` 必须创建新 `RotationSettings`、计算有效掩码的最大矩形，再返回严格合法的新对象。`fingerprint` 使用规范 JSON 和 SHA-256：

```python
@property
def fingerprint(self) -> str:
    payload = {
        "source_width": self.source_width,
        "source_height": self.source_height,
        "angle_deg": self.rotation.angle_deg,
        "crop": [self.crop.x, self.crop.y, self.crop.width, self.crop.height],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
```

- [ ] **Step 5: 运行几何测试**

Run: `python -m pytest tests/unit/test_transform.py -q`

Expected: PASS，包括 90° 精确映射、往返映射和最大矩形并列规则。

- [ ] **Step 6: 提交几何实现**

```powershell
git add src/flir_seq_tool/transform.py tests/unit/test_transform.py
git commit -m "feat: add deterministic rotation geometry"
```

## Task 3: SciPy 双线性逐帧变换器

**Files:**
- Modify: `pyproject.toml`
- Create: `src/flir_seq_tool/frame_transformer.py`
- Create: `tests/unit/test_frame_transformer.py`

- [ ] **Step 1: 写恒等、90°、双线性和源非有限值测试**

```python
import numpy as np
import pytest

from flir_seq_tool.frame_transformer import FrameTransformer
from flir_seq_tool.transform import FrameTransform


def test_identity_fast_path_returns_original_array_object():
    values = np.arange(6, dtype=np.float32).reshape(2, 3)
    transformer = FrameTransformer(FrameTransform.identity(3, 2))

    assert transformer.apply(values) is values


def test_positive_quarter_turn_matches_numpy_counterclockwise_rotation():
    values = np.arange(6, dtype=np.float32).reshape(2, 3)
    transform = FrameTransform.identity(3, 2).with_angle(90.0)

    assert np.array_equal(FrameTransformer(transform).apply(values), np.rot90(values))


def test_bilinear_rotation_preserves_planar_temperature_field():
    yy, xx = np.indices((9, 11), dtype=np.float32)
    values = 2.0 * xx + 3.0 * yy + 5.0
    transform = FrameTransform.identity(11, 9).with_angle(10.0)

    output = FrameTransformer(transform).apply(values)

    source_x, source_y = transform.cropped_to_source(2.0, 2.0)
    assert output[2, 2] == pytest.approx(2.0 * source_x + 3.0 * source_y + 5.0)
    assert np.isfinite(output).all()


def test_source_nan_is_not_misclassified_as_rotation_blank():
    values = np.ones((4, 4), dtype=np.float32)
    values[1, 1] = np.nan
    transform = FrameTransform.identity(4, 4)

    output = FrameTransformer(transform).apply(values)

    assert np.isnan(output[1, 1])
```

- [ ] **Step 2: 运行测试并确认变换器尚不存在**

Run: `python -m pytest tests/unit/test_frame_transformer.py -q`

Expected: FAIL，错误包含 `ModuleNotFoundError`。

- [ ] **Step 3: 声明运行依赖并实现采样器**

在 `pyproject.toml` 的 `[project]` 中增加：

```toml
dependencies = [
  "matplotlib>=3.10",
  "numpy>=2.2",
  "scipy>=1.18",
]
```

在 `frame_transformer.py` 中实现：

```python
from __future__ import annotations

import numpy as np
from scipy.ndimage import map_coordinates

from .transform import FrameTransform


class FrameTransformer:
    def __init__(self, transform: FrameTransform) -> None:
        self.transform = transform
        self._identity = (
            transform.rotation.angle_deg == 0.0
            and transform.crop.x == 0
            and transform.crop.y == 0
            and transform.crop.width == transform.source_width
            and transform.crop.height == transform.source_height
        )
        self._cropped_coordinates = self._coordinates_for_crop()
        self._preview_coordinates: tuple[np.ndarray, np.ndarray] | None = None

    def _coordinates_for_crop(self) -> tuple[np.ndarray, np.ndarray]:
        crop = self.transform.crop
        yy, xx = np.indices((crop.height, crop.width), dtype=np.float64)
        return self._map_rotated_grid(xx + crop.x, yy + crop.y)

    def _map_rotated_grid(
        self, rotated_x: np.ndarray, rotated_y: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        matrix = self.transform.rotated_to_source_matrix
        source_x = matrix[0, 0] * rotated_x + matrix[0, 1] * rotated_y + matrix[0, 2]
        source_y = matrix[1, 0] * rotated_x + matrix[1, 1] * rotated_y + matrix[1, 2]
        return source_y, source_x

    def _sample(
        self, values: np.ndarray, coordinates: tuple[np.ndarray, np.ndarray]
    ) -> np.ndarray:
        array = np.asarray(values)
        expected = (self.transform.source_height, self.transform.source_width)
        if array.shape != expected:
            raise ValueError(f"frame shape {array.shape} does not match source {expected}")
        source_y, source_x = coordinates
        clipped = (
            np.clip(source_y, 0.0, self.transform.source_height - 1.0),
            np.clip(source_x, 0.0, self.transform.source_width - 1.0),
        )
        return map_coordinates(
            array,
            clipped,
            order=1,
            mode="nearest",
            prefilter=False,
        )

    def apply(self, values: np.ndarray) -> np.ndarray:
        array = np.asarray(values)
        if self._identity:
            expected = (self.transform.source_height, self.transform.source_width)
            if array.shape != expected:
                raise ValueError(f"frame shape {array.shape} does not match source {expected}")
            return array
        return self._sample(array, self._cropped_coordinates)

    def preview(self, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self._preview_coordinates is None:
            height, width = self.transform.rotated_shape
            yy, xx = np.indices((height, width), dtype=np.float64)
            self._preview_coordinates = self._map_rotated_grid(xx, yy)
        valid = self.transform.valid_mask.copy()
        rotated = self._sample(values, self._preview_coordinates)
        rotated[~valid] = np.nan
        return rotated, valid
```

- [ ] **Step 4: 运行双线性测试和几何回归**

Run: `python -m pytest tests/unit/test_frame_transformer.py tests/unit/test_transform.py -q`

Expected: PASS。

- [ ] **Step 5: 提交逐帧处理器和依赖**

```powershell
git add pyproject.toml src/flir_seq_tool/frame_transformer.py tests/unit/test_frame_transformer.py
git commit -m "feat: transform temperature frames on demand"
```

## Task 4: selection schema v2 与 v1 内存迁移

**Files:**
- Modify: `src/flir_seq_tool/selection.py:13-304`
- Modify: `tests/unit/test_selection.py`

- [ ] **Step 1: 把现有往返测试更新为 v2，并写 v1 兼容失败测试**

```python
from flir_seq_tool.transform import CropRect, FrameTransform, RotationSettings


def test_selection_document_round_trip_includes_transform(tmp_path: Path):
    document = sample_document(tmp_path / "sample.seq")
    payload = document.to_dict()

    assert payload["schema_version"] == 2
    assert payload["transform"]["rotation"] == {
        "angle_deg": 0.0,
        "center": "source_image_center",
        "canvas": "expand",
        "interpolation": "bilinear",
    }
    assert payload["transform"]["crop"] == {
        "x": 0,
        "y": 0,
        "width": 640,
        "height": 480,
    }
    assert SelectionDocument.from_dict(payload) == document


def test_schema_v1_loads_as_v2_identity_without_rewriting_file(tmp_path: Path):
    source_path = tmp_path / "sample.seq"
    store = SelectionStore(tmp_path / "selections")
    payload = sample_document(source_path).to_dict()
    payload["schema_version"] = 1
    payload.pop("transform")
    target = store.path_for(source_path)
    target.parent.mkdir()
    original = json.dumps(payload, indent=2) + "\n"
    target.write_text(original, encoding="utf-8")

    loaded = store.load(source_path)

    assert loaded.schema_version == 2
    assert loaded.transform == FrameTransform.identity(640, 480)
    assert target.read_text(encoding="utf-8") == original
```

再增加严格非法 v2 测试：

```python
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: p["transform"].update(extra=True), "keys"),
        (lambda p: p["transform"]["rotation"].update(angle_deg=44.9), "angle"),
        (
            lambda p: p["transform"]["rotation"].update(center="custom"),
            "center",
        ),
        (
            lambda p: p["transform"]["rotation"].update(interpolation="nearest"),
            "interpolation",
        ),
        (
            lambda p: (
                p["transform"]["rotation"].update(angle_deg=10.0),
                p["transform"].update(
                    crop={"x": 0, "y": 0, "width": 640, "height": 480}
                ),
            ),
            "crop",
        ),
    ],
)
def test_selection_v2_rejects_invalid_transform(tmp_path, mutation, message):
    payload = sample_document(tmp_path / "sample.seq").to_dict()
    mutation(payload)

    with pytest.raises(ValueError, match=message):
        SelectionDocument.from_dict(payload)
```

- [ ] **Step 2: 运行 selection 测试并确认 v1/v2 断言失败**

Run: `python -m pytest tests/unit/test_selection.py -q`

Expected: FAIL，现有实现仍返回 schema 1 且没有 `transform`。

- [ ] **Step 3: 实现 v2 transform 序列化和版本分派**

将 `SCHEMA_VERSION` 改为 `2`。为 `SelectionDocument` 增加 `transform: FrameTransform`。新增以下转换函数：

```python
def transform_to_dict(transform: FrameTransform) -> dict[str, Any]:
    return {
        "rotation": {
            "angle_deg": transform.rotation.angle_deg,
            "center": "source_image_center",
            "canvas": "expand",
            "interpolation": "bilinear",
        },
        "crop": {
            "x": transform.crop.x,
            "y": transform.crop.y,
            "width": transform.crop.width,
            "height": transform.crop.height,
        },
    }


def transform_from_dict(
    payload: dict[str, Any], source: SourceIdentity
) -> FrameTransform:
    _require_keys(payload, {"rotation", "crop"}, "transform")
    rotation = _require_mapping(payload["rotation"], "transform.rotation")
    crop = _require_mapping(payload["crop"], "transform.crop")
    _require_keys(
        rotation,
        {"angle_deg", "center", "canvas", "interpolation"},
        "transform.rotation",
    )
    _require_keys(crop, {"x", "y", "width", "height"}, "transform.crop")
    if _require_string(rotation["center"], "rotation.center") != "source_image_center":
        raise ValueError("rotation center must be source_image_center")
    if _require_string(rotation["canvas"], "rotation.canvas") != "expand":
        raise ValueError("rotation canvas must be expand")
    if _require_string(rotation["interpolation"], "rotation.interpolation") != "bilinear":
        raise ValueError("rotation interpolation must be bilinear")
    return FrameTransform(
        source.width,
        source.height,
        RotationSettings(_require_float(rotation["angle_deg"], "angle_deg")),
        CropRect(
            _require_int(crop["x"], "crop.x"),
            _require_int(crop["y"], "crop.y"),
            _require_int(crop["width"], "crop.width"),
            _require_int(crop["height"], "crop.height"),
        ),
    )
```

`SelectionDocument.from_dict` 必须先读取 `schema_version`，schema 1 严格要求旧四个根键并构造恒等变换；schema 2 严格要求五个根键并解析 transform；其他版本抛出 `unsupported schema_version`。内存文档统一使用 schema 2。

`build_selection_document` 增加可选参数 `transform: FrameTransform | None = None`，缺省时按 source 宽高创建恒等变换，并校验显式 transform 的源尺寸与 `SourceIdentity` 相同。

- [ ] **Step 4: 运行 selection 和原子保存回归**

Run: `python -m pytest tests/unit/test_selection.py -q`

Expected: PASS，包括原子替换、`.bak` 恢复和 v1 不改写。

- [ ] **Step 5: 提交 schema v2**

```powershell
git add src/flir_seq_tool/selection.py tests/unit/test_selection.py
git commit -m "feat: persist frame transforms in selection v2"
```

## Task 5: 纯编辑草稿状态

**Files:**
- Create: `src/flir_seq_tool/transform_editor.py`
- Create: `tests/unit/test_transform_editor.py`

- [ ] **Step 1: 写 Apply、Cancel、Reset 和角度重置裁剪的失败测试**

```python
import pytest

from flir_seq_tool.transform import CropRect, FrameTransform
from flir_seq_tool.transform_editor import TransformDraft


def test_draft_starts_from_applied_and_cancel_does_not_mutate_it():
    applied = FrameTransform.identity(8, 6).with_angle(10.0)
    draft = TransformDraft.from_applied(applied)
    draft.set_crop(CropRect(applied.crop.x, applied.crop.y, 2, 2))

    assert draft.cancel() == applied
    assert applied.crop != CropRect(applied.crop.x, applied.crop.y, 2, 2)


def test_angle_change_resets_crop_to_maximum_valid_area():
    draft = TransformDraft.from_applied(FrameTransform.identity(8, 6))
    draft.set_crop(CropRect(1, 1, 3, 2))

    draft.set_angle(10.0)

    expected = FrameTransform.identity(8, 6).with_angle(10.0)
    assert draft.crop == expected.crop
    assert draft.message == "Rotation changed — crop reset"


def test_invalid_crop_keeps_draft_visible_but_blocks_build():
    draft = TransformDraft.from_applied(FrameTransform.identity(8, 6).with_angle(10.0))
    draft.set_crop(CropRect(0, 0, 8, 6))

    assert draft.is_valid is False
    with pytest.raises(ValueError, match="crop"):
        draft.build()


def test_reset_returns_identity_without_mutating_applied():
    applied = FrameTransform.identity(8, 6).with_angle(10.0)
    draft = TransformDraft.from_applied(applied)

    draft.reset()

    assert draft.build() == FrameTransform.identity(8, 6)
    assert applied.rotation.angle_deg == 10.0
```

- [ ] **Step 2: 运行测试并确认编辑状态尚不存在**

Run: `python -m pytest tests/unit/test_transform_editor.py -q`

Expected: FAIL，错误包含 `cannot import name 'TransformDraft'`。

- [ ] **Step 3: 实现不修改已应用配置的草稿对象**

```python
from __future__ import annotations

from dataclasses import dataclass

from .transform import CropRect, FrameTransform, RotationSettings


@dataclass
class TransformDraft:
    applied: FrameTransform
    angle_deg: float
    crop: CropRect
    message: str = ""

    @classmethod
    def from_applied(cls, transform: FrameTransform) -> TransformDraft:
        return cls(transform, transform.rotation.angle_deg, transform.crop)

    @property
    def is_valid(self) -> bool:
        try:
            self.build()
        except ValueError:
            return False
        return True

    def set_angle(self, angle_deg: float) -> None:
        reset = FrameTransform.identity(
            self.applied.source_width, self.applied.source_height
        ).with_angle(angle_deg)
        self.angle_deg = reset.rotation.angle_deg
        self.crop = reset.crop
        self.message = "Rotation changed — crop reset"

    def set_crop(self, crop: CropRect) -> None:
        self.crop = crop
        self.message = "" if self.is_valid else "Crop includes invalid pixels"

    def reset(self) -> None:
        identity = FrameTransform.identity(
            self.applied.source_width, self.applied.source_height
        )
        self.angle_deg = identity.rotation.angle_deg
        self.crop = identity.crop
        self.message = "Draft reset"

    def build(self) -> FrameTransform:
        return FrameTransform(
            self.applied.source_width,
            self.applied.source_height,
            RotationSettings(self.angle_deg),
            self.crop,
        )

    def cancel(self) -> FrameTransform:
        return self.applied
```

- [ ] **Step 4: 运行编辑状态测试**

Run: `python -m pytest tests/unit/test_transform_editor.py -q`

Expected: PASS。

- [ ] **Step 5: 提交草稿状态**

```powershell
git add src/flir_seq_tool/transform_editor.py tests/unit/test_transform_editor.py
git commit -m "feat: add transactional transform draft state"
```

## Task 6: ViewerSession 逐帧变换、双缓存与脏状态

**Files:**
- Modify: `src/flir_seq_tool/frame_cache.py`
- Modify: `src/flir_seq_tool/viewer.py:38-168`
- Modify: `tests/unit/test_frame_cache.py`
- Modify: `tests/unit/test_viewer.py:27-100,285-359`

- [ ] **Step 1: 写缓存清空和会话变换的失败测试**

```python
from flir_seq_tool.transform import CropRect


def test_frame_cache_clear_discards_entries():
    cache = FrameCache(maxsize=2)
    cache.get(0, make_frame)

    cache.clear()

    assert len(cache) == 0


def test_session_applies_one_transform_on_demand_and_invalidates_derived_cache():
    reader = FakeTemperatureReader(make_frames(count=3))
    session = ViewerSession(reader, cache_size=3)
    first = session.frame_at(0)
    transform = session.applied_transform.with_angle(10.0)

    session.apply_transform(transform)
    transformed = session.frame_at(0)
    session.frame_at(0)

    assert first.temperature_c.shape == (2, 3)
    assert transformed.temperature_c.shape == transform.output_shape
    assert reader.requested_frames == [0]
    assert session.selection_dirty is True


def test_selection_document_contains_applied_transform(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=2), tmp_path / "sample.seq")
    session = ViewerSession(reader, selection_store=SelectionStore(tmp_path / "selections"))
    transform = session.applied_transform.with_angle(10.0)
    session.apply_transform(transform)

    assert session.selection_document().transform == transform
```

更新 `FakeTemperatureReader.__init__`，从第一帧矩阵设置 `width`、`height`，并给测试假 reader 一个稳定 `path` 时继续使用 `FileBackedFakeReader`。

- [ ] **Step 2: 运行缓存和会话测试并确认失败**

Run: `python -m pytest tests/unit/test_frame_cache.py tests/unit/test_viewer.py -q`

Expected: FAIL，缺少 `clear`、`applied_transform` 或 `apply_transform`。

- [ ] **Step 3: 增加缓存清空并把会话帧路径拆成原始与修整两层**

把 `FrameCache` 泛化为 `FrameCache[K]`，其中 `K` 是可哈希缓存键；原始缓存使用 `int`，修整缓存使用 `tuple[int, str]`。类型声明和清空方法使用：

```python
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar


K = TypeVar("K", bound=Hashable)


class FrameCache(Generic[K]):
    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be positive")
        self.maxsize = maxsize
        self._frames: OrderedDict[K, TemperatureFrame] = OrderedDict()

    def get(
        self,
        frame_key: K,
        loader: Callable[[K], TemperatureFrame],
    ) -> TemperatureFrame:
        if frame_key in self._frames:
            self._frames.move_to_end(frame_key)
            return self._frames[frame_key]
        frame = loader(frame_key)
        self._frames[frame_key] = frame
        if len(self._frames) > self.maxsize:
            self._frames.popitem(last=False)
        return frame

    def clear(self) -> None:
        self._frames.clear()

    def __len__(self) -> int:
        return len(self._frames)
```

`ViewerSession` 增加以下状态：

```python
self.source_cache: FrameCache[int] = FrameCache(maxsize=cache_size)
self.transformed_cache: FrameCache[tuple[int, str]] = FrameCache(maxsize=cache_size)
self.applied_transform = FrameTransform.identity(
    int(reader.width), int(reader.height)
)
self.frame_transformer = FrameTransformer(self.applied_transform)
self._preview_transformer_key: str | None = None
self._preview_transformer: FrameTransformer | None = None
self.selection_dirty = False
```

保留 `self.cache = self.source_cache` 作为现有测试和调用方的兼容别名。把帧读取改为：

```python
def raw_frame_at(self, frame_index: int) -> TemperatureFrame:
    if not 0 <= frame_index < self.reader.num_frames:
        raise IndexError(f"frame index out of range: {frame_index}")
    return self.source_cache.get(frame_index, self.reader.temperature_frame_at)

def _transform_frame(self, frame_index: int) -> TemperatureFrame:
    source = self.raw_frame_at(frame_index)
    return TemperatureFrame(
        frame_index=source.frame_index,
        timestamp=source.timestamp,
        temperature_c=self.frame_transformer.apply(source.temperature_c),
    )

def frame_at(self, frame_index: int) -> TemperatureFrame:
    key = (frame_index, self.applied_transform.fingerprint)
    frame = self.transformed_cache.get(
        key, lambda cache_key: self._transform_frame(cache_key[0])
    )
    if self.color_limits is None:
        self.color_limits = compute_color_limits(frame.temperature_c)
    return frame

def preview_frame(
    self, frame_index: int, transform: FrameTransform
) -> tuple[TemperatureFrame, np.ndarray]:
    source = self.raw_frame_at(frame_index)
    if self._preview_transformer_key != transform.fingerprint:
        self._preview_transformer_key = transform.fingerprint
        self._preview_transformer = FrameTransformer(transform)
    if self._preview_transformer is None:
        raise RuntimeError("preview transformer was not initialized")
    values, valid = self._preview_transformer.preview(source.temperature_c)
    return TemperatureFrame(source.frame_index, source.timestamp, values), valid

def apply_transform(self, transform: FrameTransform) -> None:
    if (transform.source_width, transform.source_height) != (
        int(self.reader.width), int(self.reader.height)
    ):
        raise ValueError("transform source dimensions do not match reader")
    self.applied_transform = transform
    self.frame_transformer = FrameTransformer(transform)
    self.transformed_cache.clear()
    self._preview_transformer_key = None
    self._preview_transformer = None
    self.selection_dirty = True
```

`selection_document()` 把 `applied_transform` 传给 `build_selection_document`。`save_selection()` 仅在成功保存后设置 `selection_dirty=False`。`load_selection()` 必须在所有字段验证后整体应用 transform，并在成功结束时设置 `selection_dirty=False`。

- [ ] **Step 4: 运行会话、selection 和缓存测试**

Run: `python -m pytest tests/unit/test_frame_cache.py tests/unit/test_selection.py tests/unit/test_viewer.py -q`

Expected: PASS；现有恒等变换查看器测试不改变像素结果。

- [ ] **Step 5: 提交会话数据流**

```powershell
git add src/flir_seq_tool/frame_cache.py src/flir_seq_tool/viewer.py tests/unit/test_frame_cache.py tests/unit/test_viewer.py
git commit -m "feat: apply cached transforms in viewer sessions"
```

## Task 7: Matplotlib 旋转裁剪编辑控件

**Files:**
- Modify: `src/flir_seq_tool/transform_editor.py`
- Modify: `tests/unit/test_transform_editor.py`

- [ ] **Step 1: 写控件创建、角度同步、裁剪同步和合法性测试**

测试使用 Agg 后端，创建 `Figure`、图像 `Axes` 和恒等变换；构建 `TransformEditorUI` 后断言：

```python
def test_editor_angle_controls_reset_crop_and_round_to_tenth():
    figure, axes = plt.subplots()
    previews: list[FrameTransform] = []
    editor = build_transform_editor(
        figure,
        axes,
        FrameTransform.identity(8, 6),
        preview_callback=previews.append,
        apply_callback=lambda transform: None,
        cancel_callback=lambda: None,
    )

    editor.angle_box.set_val("10.04")
    assert editor.angle_box.text == "0.0"
    assert editor.error_text.get_text()

    editor.angle_box.set_val("10.0")
    assert editor.draft.angle_deg == 10.0
    assert editor.angle_slider.val == pytest.approx(10.0)
    assert editor.draft.crop == FrameTransform.identity(8, 6).with_angle(10.0).crop
    plt.close(figure)


def test_invalid_crop_turns_selector_red_and_disables_apply():
    figure, axes = plt.subplots()
    editor = build_transform_editor(
        figure,
        axes,
        FrameTransform.identity(8, 6).with_angle(10.0),
        preview_callback=lambda transform: None,
        apply_callback=lambda transform: None,
        cancel_callback=lambda: None,
    )

    editor.set_crop(CropRect(0, 0, 8, 6))

    assert editor.draft.is_valid is False
    assert editor.apply_button.active is False
    assert editor.selector._selection_artist.get_edgecolor()[0] > 0.5
    plt.close(figure)
```

用参数化测试覆盖快捷角度，并分别验证最大区域、重置、取消和应用：

```python
@pytest.mark.parametrize(
    ("button_name", "expected"),
    [
        ("left_button", 100.0),
        ("right_button", -80.0),
        ("half_turn_button", -170.0),
        ("zero_button", 0.0),
    ],
)
def test_quick_rotation_buttons_keep_or_reset_tenth_degree_fine_angle(
    button_name, expected
):
    figure, axes = plt.subplots()
    editor = build_transform_editor(
        figure,
        axes,
        FrameTransform.identity(8, 6).with_angle(10.0),
        preview_callback=lambda transform: None,
        apply_callback=lambda transform: None,
        cancel_callback=lambda: None,
    )

    getattr(editor, button_name)._observers.process("clicked", None)

    assert editor.draft.angle_deg == expected
    plt.close(figure)


def test_maximum_reset_cancel_and_apply_callbacks_are_transactional():
    figure, axes = plt.subplots()
    applied: list[FrameTransform] = []
    cancelled: list[bool] = []
    initial = FrameTransform.identity(8, 6).with_angle(10.0)
    editor = build_transform_editor(
        figure,
        axes,
        initial,
        preview_callback=lambda transform: None,
        apply_callback=applied.append,
        cancel_callback=lambda: cancelled.append(True),
    )
    editor.set_crop(CropRect(initial.crop.x, initial.crop.y, 2, 2))
    editor.maximum_button._observers.process("clicked", None)
    assert editor.draft.crop == initial.crop
    editor.reset_button._observers.process("clicked", None)
    assert editor.draft.build() == FrameTransform.identity(8, 6)
    editor.cancel_button._observers.process("clicked", None)
    assert cancelled == [True]
    assert applied == []
    editor.apply_button._observers.process("clicked", None)
    assert applied == [FrameTransform.identity(8, 6)]
    plt.close(figure)
```

- [ ] **Step 2: 运行编辑器测试并确认 UI API 尚不存在**

Run: `python -m pytest tests/unit/test_transform_editor.py -q`

Expected: FAIL，缺少 `build_transform_editor` 或 `TransformEditorUI`。

- [ ] **Step 3: 实现编辑器 UI 容器和显隐控制**

在 `transform_editor.py` 增加 `TransformEditorUI` 数据类，字段固定为：

```python
@dataclass
class TransformEditorUI:
    figure: Figure
    image_axes: Axes
    draft: TransformDraft
    angle_slider: Slider
    angle_box: TextBox
    left_button: Button
    right_button: Button
    half_turn_button: Button
    zero_button: Button
    crop_boxes: dict[str, TextBox]
    maximum_button: Button
    reset_button: Button
    cancel_button: Button
    apply_button: Button
    selector: RectangleSelector
    status_text: Text
    error_text: Text
    control_axes: tuple[Axes, ...]
    updating: bool = False

    def set_visible(self, visible: bool) -> None:
        for control_axis in self.control_axes:
            control_axis.set_visible(visible)
        self.selector.set_active(visible)
        self.selector.set_visible(visible)
        self.status_text.set_visible(visible)
        self.error_text.set_visible(visible)
        self.figure.canvas.draw_idle()
```

`build_transform_editor` 必须创建 `RectangleSelector(image_axes, on_select, interactive=True, useblit=False, button=[1], minspanx=1, minspany=1, spancoords="pixels")`。`on_select` 把浮点 extents 按最近整数像素边界转换为 `CropRect`，然后调用统一的 `set_crop` 路径。四个数字框也调用相同路径，避免鼠标与文本产生不同校验规则。

- [ ] **Step 4: 实现角度和裁剪回调的单一同步入口**

实现以下方法，并在所有回调中使用 `updating` 防止递归：

```python
def set_angle(self, angle_deg: float) -> None:
    self.draft.set_angle(RotationSettings(angle_deg).angle_deg)
    self._sync_controls()
    self._publish_preview()

def set_crop(self, crop: CropRect) -> None:
    self.draft.set_crop(crop)
    self._sync_controls()
    self._publish_preview()

def _sync_controls(self) -> None:
    self.updating = True
    try:
        fine = (self.draft.angle_deg + 45.0) % 90.0 - 45.0
        self.angle_slider.set_val(fine)
        self.angle_box.set_val(f"{self.draft.angle_deg:.1f}")
        for name, value in zip(
            ("x", "y", "width", "height"),
            (self.draft.crop.x, self.draft.crop.y, self.draft.crop.width, self.draft.crop.height),
            strict=True,
        ):
            self.crop_boxes[name].set_val(str(value))
        crop = self.draft.crop
        self.selector.extents = (
            crop.x - 0.5,
            crop.x + crop.width - 0.5,
            crop.y - 0.5,
            crop.y + crop.height - 0.5,
        )
        self.apply_button.set_active(self.draft.is_valid)
        color = "limegreen" if self.draft.is_valid else "crimson"
        self.selector._selection_artist.set_edgecolor(color)
        self.status_text.set_text(self.draft.message)
    finally:
        self.updating = False
```

快捷按钮按总角度执行：左转 `+90°`、右转 `-90°`、180° `+180°`、归零 `0°`。微调滑块只替换当前最近整 90° 方向的余角。输入异常时恢复上一个合法值并显示 crimson 错误。

- [ ] **Step 5: 运行编辑器测试**

Run: `python -m pytest tests/unit/test_transform_editor.py -q`

Expected: PASS，无 GUI 窗口弹出。

- [ ] **Step 6: 提交编辑控件**

```powershell
git add src/flir_seq_tool/transform_editor.py tests/unit/test_transform_editor.py
git commit -m "feat: add rotation and crop editor controls"
```

## Task 8: 单窗口模式切换、预览、应用与加载确认

**Files:**
- Modify: `src/flir_seq_tool/viewer.py:169-610`
- Modify: `tests/unit/test_viewer.py`

- [ ] **Step 1: 写普通/编辑模式切换和预览测试**

```python
def test_edit_transform_switches_single_window_controls_and_keeps_frame_slider():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(count=3))))

    ui.edit_transform_button._observers.process("clicked", None)

    assert ui.transform_editor.control_axes[0].get_visible() is True
    assert ui.slider.ax.get_visible() is True
    assert ui.range_slider.ax.get_visible() is False
    assert ui.save_button.ax.get_visible() is False
    assert ui.transform_editor.draft.applied == ui.session.applied_transform
    plt.close(ui.figure)


def test_editor_frame_navigation_keeps_draft_and_uses_full_preview():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(count=3))))
    ui.edit_transform_button._observers.process("clicked", None)
    ui.transform_editor.set_angle(10.0)
    fingerprint = ui.transform_editor.draft.build().fingerprint

    ui.slider.set_val(2)

    assert ui.transform_editor.draft.build().fingerprint == fingerprint
    assert np.asarray(ui.image.get_array()).shape == ui.transform_editor.draft.build().rotated_shape
    plt.close(ui.figure)


def test_cancel_restores_applied_view_and_apply_marks_unsaved():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(count=2))))
    original = ui.session.applied_transform
    ui.edit_transform_button._observers.process("clicked", None)
    ui.transform_editor.set_angle(10.0)
    ui.transform_editor.cancel_button._observers.process("clicked", None)
    assert ui.session.applied_transform == original

    ui.edit_transform_button._observers.process("clicked", None)
    ui.transform_editor.set_angle(10.0)
    ui.transform_editor.apply_button._observers.process("clicked", None)
    assert ui.session.applied_transform.rotation.angle_deg == 10.0
    assert ui.session.selection_dirty is True
    assert "unsaved" in ui.transform_status_text.get_text()
    plt.close(ui.figure)
```

- [ ] **Step 2: 写未保存状态下 Load 二次确认和导出不变测试**

```python
def test_dirty_session_requires_second_load_click(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(reader, selection_store=SelectionStore(tmp_path / "selections"))
    ui = build_viewer(session)
    session.save_selection()
    session.apply_transform(session.applied_transform.with_angle(10.0))

    ui.load_button._observers.process("clicked", None)
    assert ui.load_button.label.get_text() == "Confirm load"
    assert session.applied_transform.rotation.angle_deg == 10.0

    ui.load_button._observers.process("clicked", None)
    assert session.applied_transform.rotation.angle_deg == 0.0
    assert session.selection_dirty is False
    plt.close(ui.figure)


def test_native_export_request_ignores_applied_transform(tmp_path):
    reader = FileBackedFakeReader(make_frames(count=3), tmp_path / "sample.seq")
    session = ViewerSession(reader)
    session.apply_transform(session.applied_transform.with_angle(10.0))

    request = session.export_request("raw_clip", tmp_path / "clips")

    assert not hasattr(request, "transform")
    assert request.source_start_frame == 0
    assert request.source_end_frame == 2
```

- [ ] **Step 3: 运行 viewer 测试并确认模式 API 尚未接入**

Run: `python -m pytest tests/unit/test_viewer.py -q`

Expected: FAIL，缺少 `edit_transform_button`、`transform_editor` 或加载确认状态。

- [ ] **Step 4: 在 ViewerUI 中加入编辑器和模式状态**

为 `ViewerUI` 增加：

```python
edit_transform_button: Button
transform_editor: TransformEditorUI
transform_status_text: Text
normal_control_axes: tuple[Axes, ...]
mode_state: ViewerModeState
```

实际可变的 `editing_transform` 和 `load_confirmation_signature` 放入新的非冻结 `ViewerModeState`，`ViewerUI` 保存 `mode_state: ViewerModeState`，避免修改冻结数据类字段：

```python
@dataclass
class ViewerModeState:
    editing_transform: bool = False
    load_confirmation_signature: str | None = None
```

新增统一图像更新函数，任何尺寸变化都更新 extent 和坐标轴：

```python
def set_image_array(values: np.ndarray) -> None:
    height, width = values.shape
    image.set_data(values)
    image.set_extent((-0.5, width - 0.5, height - 0.5, -0.5))
    axes.set_xlim(-0.5, width - 0.5)
    axes.set_ylim(height - 0.5, -0.5)
```

普通模式 `show_local` 调用 `session.frame_at_local`；编辑模式先把本地帧换算为源帧号，再调用 `session.preview_frame(source_frame, draft.build())`。预览使用复制后的 colormap 并调用 `set_bad("0.35")`，再用 `np.ma.masked_invalid` 显示灰色无效区，同时保持裁剪 selector 可见。

- [ ] **Step 5: 实现模式切换和事务式回调**

`on_edit_transform`：若导出任务正在运行则拒绝；复制 applied 为 draft；隐藏 `normal_control_axes`，但重新显示 current frame slider；显示 editor；刷新完整预览。

`on_apply_transform`：先 `draft.build()`，再对当前源帧执行一次 `preview_frame`；两步均成功后调用 `session.apply_transform`，退出编辑模式并显示裁剪结果。失败时保持编辑模式和原 applied。

`on_cancel_transform`：不调用 `session.apply_transform`，直接退出编辑模式并刷新原 applied 结果。

`on_reset_transform`：只调用 `draft.reset()` 和刷新完整预览。

普通状态栏使用：

```python
def transform_status(transform: FrameTransform, dirty: bool) -> str:
    crop = transform.crop
    state = "applied, unsaved" if dirty else "applied"
    return (
        f"Transform: {state} | angle {transform.rotation.angle_deg:.1f}° | "
        f"crop {crop.x},{crop.y},{crop.width}×{crop.height}"
    )
```

- [ ] **Step 6: 实现选择脏状态与 Load 二次确认**

为会话增加确定性 `state_signature()`，包含当前待保存源帧范围、温标、colormap 和变换指纹。时间范围滑块、文本框、确认、完整重置及变换 Apply 后设置 dirty 并清除 `load_confirmation_signature`。保存成功和加载成功后清除 dirty。

`on_load` 第一次遇到 dirty 时保存当前 signature、按钮改为 `Confirm load` 并显示将覆盖时间范围/温标/变换的摘要；第二次只有 signature 未变化时才加载。任何状态变化都使确认失效并恢复按钮文字。

更新现有 `test_save_and_load_buttons_restore_confirmed_range_and_display`：完整重置已使会话变脏，因此加载按钮必须点击两次；第一次断言按钮文字为 `Confirm load`，第二次再断言状态恢复。其他原有非脏加载测试仍只点击一次。

- [ ] **Step 7: 运行查看器、编辑器、selection 和导出回归**

Run: `python -m pytest tests/unit/test_viewer.py tests/unit/test_transform_editor.py tests/unit/test_selection.py tests/unit/test_extraction.py -q`

Expected: PASS；导出测试继续证明原生子 SEQ 不含 transform。

- [ ] **Step 8: 提交单窗口集成**

```powershell
git add src/flir_seq_tool/viewer.py tests/unit/test_viewer.py
git commit -m "feat: integrate single-window transform editing"
```

## Task 9: 最小真实 SEQ 只读旋转裁剪验证

**Files:**
- Modify: `tests/integration/test_small_seq_viewer.py`

- [ ] **Step 1: 写首/中/末帧真实变换与 selection 往返测试**

```python
from flir_seq_tool.transform import FrameTransform


@pytest.mark.integration
def test_small_seq_transform_round_trip_is_finite_fixed_shape_and_read_only(tmp_path):
    before = SMALL_SEQ.stat()

    with ScienceFileReader(SMALL_SEQ) as reader:
        session = ViewerSession(
            reader,
            cache_size=3,
            selection_store=SelectionStore(tmp_path / "selections"),
        )
        transform = session.applied_transform.with_angle(10.0)
        session.apply_transform(transform)
        expected_shape = transform.output_shape
        observed = []
        for source_index in (0, reader.num_frames // 2, reader.num_frames - 1):
            frame = session.frame_at(source_index)
            observed.append(frame.temperature_c.copy())
            assert frame.temperature_c.shape == expected_shape
            assert np.isfinite(frame.temperature_c).all()

        session.save_selection()
        session.apply_transform(FrameTransform.identity(reader.width, reader.height))
        loaded = session.load_selection()

        assert loaded.transform == transform
        assert session.applied_transform == transform
        assert np.allclose(session.frame_at(0).temperature_c, observed[0])
        assert len(session.source_cache) <= 3
        assert len(session.transformed_cache) <= 3

    after = SMALL_SEQ.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
```

- [ ] **Step 2: 运行真实集成测试**

Run: `python -m pytest tests/integration/test_small_seq_viewer.py -m integration -q`

Expected: PASS。若 FileSDK 或真实文件不可用，pytest 必须给出明确环境失败或既有跳过行为，不能写入 `raw`。

- [ ] **Step 3: 核对测试只使用临时 selection 目录**

Run: `rg -n -S "SelectionStore|write_|save_selection|clips|selections" tests/integration/test_small_seq_viewer.py`

Expected: 新增测试的 `SelectionStore` 根目录来自 `tmp_path`，没有写入 `raw`、生产 `selections` 或 `clips`。

同时更新现有 `test_small_seq_selection_round_trip_preserves_source`：`reset_button` 使会话变脏，随后对 `load_button` 连续点击两次，再执行原有范围和源文件不变断言。

- [ ] **Step 4: 提交真实文件验证**

```powershell
git add tests/integration/test_small_seq_viewer.py
git commit -m "test: verify read-only real SEQ transforms"
```

## Task 10: 全量回归、只读清单与最终审查

**Files:**
- Modify only if verification exposes a scoped defect in files already listed above.

- [ ] **Step 1: 记录三份原始 SEQ 的验证前清单**

Run:

```powershell
Get-ChildItem -LiteralPath raw -Filter *.seq | Sort-Object FullName | Select-Object FullName,Length,LastWriteTimeUtc
```

Expected: 列出三份原始 SEQ；保存终端输出用于测试后逐项比较，不对文件执行写操作。

- [ ] **Step 2: 运行全部单元测试**

Run: `python -m pytest tests/unit -q`

Expected: PASS，既有测试与新增测试全部通过。

- [ ] **Step 3: 运行全部集成测试**

Run: `python -m pytest tests/integration -m integration -q`

Expected: PASS；最小真实 SEQ 旋转裁剪、selection 往返和现有原生子 SEQ 提取验证均成功。

- [ ] **Step 4: 运行完整测试套件**

Run: `python -m pytest -q`

Expected: 基线 `118 passed` 加全部新增测试均通过，无失败。

- [ ] **Step 5: 重新记录原始 SEQ 清单并逐项比较**

Run:

```powershell
Get-ChildItem -LiteralPath raw -Filter *.seq | Sort-Object FullName | Select-Object FullName,Length,LastWriteTimeUtc
```

Expected: 三份文件的绝对路径、大小和修改时间与 Step 1 完全一致。

- [ ] **Step 6: 检查源码没有写入 raw 或失效路径**

Run: `rg -n -S "D:\\carbonfoam\\红外|raw.*write|write.*raw|os\.replace.*raw|rename.*raw" src tests`

Expected: 不出现失效目录；不出现会向 `raw` 写入、替换或重命名的新增路径。

- [ ] **Step 7: 检查变更范围和格式**

Run: `git diff --check 95bd4c0..HEAD`

Expected: 无空白错误。

Run: `git status --short`

Expected: 只显示用户原有的无关改动；本特性计划列出的文件均已提交。

- [ ] **Step 8: 请求代码审查**

使用 `superpowers:requesting-code-review` 审查设计覆盖、坐标数学、schema 迁移、UI 状态事务性、逐帧内存边界和原始 SEQ 只读保证。修复审查发现的问题后，重新运行受影响测试及 `python -m pytest -q`。

- [ ] **Step 9: 最终提交审查修复（仅在确有修复时）**

```powershell
git add pyproject.toml src/flir_seq_tool tests/unit tests/integration/test_small_seq_viewer.py
git commit -m "fix: address rotate crop review findings"
```

只有存在实际审查修复时才执行该提交；没有修复时保持工作树不新增提交。

## 手动大文件验收清单

自动实施完成后，由用户对另外两份大 SEQ 执行：

1. 打开文件并进入 `Edit transform`。
2. 使用快捷旋转和 `0.1°` 微调。
3. 选择最大有效区域，再拖动和数字微调裁剪框。
4. 切换首帧、中间帧、末帧，确认同一变换和同一输出尺寸。
5. 分别验证 `Cancel`、`Reset draft`、`Apply` 和重新编辑。
6. 保存 selection，关闭后重新打开并加载，确认结果一致。
7. 原生导出一个短子 SEQ，确认导出的仍是未经旋转裁剪的原始辐射帧。
8. 再次核对两份大 SEQ 的大小和修改时间没有变化。
