# FLIR SEQ 阶段 A 只读检查器实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建只读命令行检查器，输出 SEQ 的尺寸、帧数、首末时间和时间戳连续性，并用最小真实 SEQ 建立回归基线。

**Architecture:** `ScienceFileReader` 是对 `fnv.file.ImagerFile` 的最薄适配层；纯函数 `analyze_timestamps` 只处理时间列表；`inspect_seq` 组织读取与统计；CLI 只负责参数和序列化。任何模块均不向 `raw` 写入，真实文件测试在运行前后比较文件状态。

**Tech Stack:** Python 3.13、FLIR FileSDK 2026.1.2、标准库 `argparse/dataclasses/datetime/json/pathlib/statistics`、pytest 9.1。

---

## 文件结构

- 创建 `pyproject.toml`：包信息、CLI 入口和 pytest 配置。
- 创建 `src/flir_seq_tool/__init__.py`：版本与公共接口。
- 创建 `src/flir_seq_tool/models.py`：文件信息和时间戳摘要数据模型。
- 创建 `src/flir_seq_tool/timestamps.py`：纯时间戳分析逻辑。
- 创建 `src/flir_seq_tool/reader.py`：FileSDK 只读适配器。
- 创建 `src/flir_seq_tool/inspector.py`：检查流程编排。
- 创建 `src/flir_seq_tool/cli.py` 与 `src/flir_seq_tool/__main__.py`：命令行入口。
- 创建 `tests/unit/`：纯逻辑、适配器和 CLI 测试。
- 创建 `tests/integration/test_small_seq.py`：最小真实文件回归测试。

### Task 1：建立包骨架和测试入口

**Files:**
- Create: `pyproject.toml`
- Create: `src/flir_seq_tool/__init__.py`
- Create: `src/flir_seq_tool/__main__.py`
- Create: `tests/unit/test_package.py`

- [ ] **Step 1: 写失败的包导入测试**

```python
def test_package_exposes_version():
    import flir_seq_tool

    assert flir_seq_tool.__version__ == "0.1.0"
```

- [ ] **Step 2: 运行测试并确认因包不存在而失败**

Run: `python -m pytest tests/unit/test_package.py -v`

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'flir_seq_tool'`。

- [ ] **Step 3: 写最小包配置与入口**

`pyproject.toml`：

```toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "flir-seq-tool"
version = "0.1.0"
requires-python = ">=3.13"

[project.scripts]
flir-seq = "flir_seq_tool.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
markers = ["integration: requires the local FLIR FileSDK and real SEQ data"]
```

`src/flir_seq_tool/__init__.py`：

```python
__version__ = "0.1.0"
```

`src/flir_seq_tool/__main__.py`：

```python
from .cli import main

raise SystemExit(main())
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `python -m pytest tests/unit/test_package.py -v`

Expected: `1 passed`。

- [ ] **Step 5: 提交包骨架**

```powershell
git add infrared/pyproject.toml infrared/src/flir_seq_tool/__init__.py infrared/src/flir_seq_tool/__main__.py infrared/tests/unit/test_package.py
git commit -m "chore: scaffold FLIR SEQ tool"
```

### Task 2：实现时间戳连续性纯函数

**Files:**
- Create: `src/flir_seq_tool/models.py`
- Create: `src/flir_seq_tool/timestamps.py`
- Create: `tests/unit/test_timestamps.py`

- [ ] **Step 1: 写重复、倒退和异常间隔的失败测试**

```python
from datetime import datetime, timedelta

from flir_seq_tool.timestamps import analyze_timestamps


def test_analyze_timestamps_reports_continuity_anomalies():
    start = datetime(2026, 8, 28, 9, 0, 0)
    times = [
        start,
        start + timedelta(milliseconds=20),
        start + timedelta(milliseconds=20),
        start + timedelta(milliseconds=10),
        start + timedelta(milliseconds=30),
        start + timedelta(milliseconds=230),
    ]

    result = analyze_timestamps(times)

    assert result.frame_count == 6
    assert result.scanned_frame_count == 6
    assert result.continuity_checked is True
    assert result.duplicate_after_frames == (1,)
    assert result.backward_after_frames == (2,)
    assert result.gap_after_frames == (4,)
    assert result.median_interval_s == 0.02
```

另写空列表和单帧测试，要求空列表抛出 `ValueError("timestamps must not be empty")`，单帧的间隔统计为 `None` 且异常索引为空。

- [ ] **Step 2: 运行测试并确认缺少模块**

Run: `python -m pytest tests/unit/test_timestamps.py -v`

Expected: FAIL，错误包含 `No module named 'flir_seq_tool.timestamps'`。

- [ ] **Step 3: 实现不可变摘要模型和分析函数**

`models.py` 定义：

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TimestampSummary:
    frame_count: int
    scanned_frame_count: int
    continuity_checked: bool
    first_time: datetime
    last_time: datetime
    duration_s: float
    median_interval_s: float | None
    estimated_fps: float | None
    duplicate_after_frames: tuple[int, ...]
    backward_after_frames: tuple[int, ...]
    gap_after_frames: tuple[int, ...]
```

`timestamps.py` 实现：正间隔的中位数用于基准；重复和倒退间隔不参与中位数；异常大间隔定义为严格大于 `3 * median_positive_interval`；索引 `i` 表示异常发生在帧 `i` 与 `i+1` 之间。

```python
from datetime import datetime
from statistics import median

from .models import TimestampSummary


def analyze_timestamps(times: list[datetime]) -> TimestampSummary:
    if not times:
        raise ValueError("timestamps must not be empty")
    intervals = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
    positive = [value for value in intervals if value > 0]
    median_interval = median(positive) if positive else None
    gaps = () if median_interval is None else tuple(
        i for i, value in enumerate(intervals) if value > 3 * median_interval
    )
    return TimestampSummary(
        frame_count=len(times),
        scanned_frame_count=len(times),
        continuity_checked=True,
        first_time=times[0],
        last_time=times[-1],
        duration_s=(times[-1] - times[0]).total_seconds(),
        median_interval_s=median_interval,
        estimated_fps=None if median_interval is None else 1.0 / median_interval,
        duplicate_after_frames=tuple(i for i, value in enumerate(intervals) if value == 0),
        backward_after_frames=tuple(i for i, value in enumerate(intervals) if value < 0),
        gap_after_frames=gaps,
    )
```

- [ ] **Step 4: 运行时间戳测试**

Run: `python -m pytest tests/unit/test_timestamps.py -v`

Expected: 所有测试通过。

- [ ] **Step 5: 提交时间戳分析**

```powershell
git add infrared/src/flir_seq_tool/models.py infrared/src/flir_seq_tool/timestamps.py infrared/tests/unit/test_timestamps.py
git commit -m "feat: analyze SEQ timestamp continuity"
```

### Task 3：实现 FileSDK 只读适配器

**Files:**
- Modify: `src/flir_seq_tool/models.py`
- Create: `src/flir_seq_tool/reader.py`
- Create: `tests/unit/test_reader.py`

- [ ] **Step 1: 用假 ImagerFile 写失败测试**

测试要求：

```python
from datetime import datetime
from pathlib import Path

from flir_seq_tool.reader import ScienceFileReader


def test_reader_reports_shape_count_and_timestamps(tmp_path: Path):
    path = tmp_path / "sample.seq"
    path.write_bytes(b"read-only-fixture")
    fake = FakeImagerFile(
        width=640,
        height=480,
        times=[datetime(2026, 8, 28, 9, 0, 0), datetime(2026, 8, 28, 9, 0, 1)],
    )

    with ScienceFileReader(path, imager_factory=lambda _: fake) as reader:
        assert reader.width == 640
        assert reader.height == 480
        assert reader.num_frames == 2
        assert list(reader.iter_timestamps()) == fake.times

    assert fake.close_called is True
```

另测不存在文件、非 `.seq` 后缀、越界帧以及 `get_frame` 返回 `False` 时的明确异常。

- [ ] **Step 2: 运行测试并确认缺少读取器**

Run: `python -m pytest tests/unit/test_reader.py -v`

Expected: FAIL，错误包含 `No module named 'flir_seq_tool.reader'`。

- [ ] **Step 3: 实现只读适配器**

实现完整适配器：

```python
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import fnv.file


class ScienceFileReader:
    def __init__(
        self,
        path: Path,
        imager_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.path = Path(path).resolve(strict=True)
        if self.path.suffix.lower() != ".seq":
            raise ValueError(f"expected a .seq file: {self.path}")
        self._imager_factory = imager_factory or fnv.file.ImagerFile
        self._file: Any | None = None

    def __enter__(self) -> "ScienceFileReader":
        self._file = self._imager_factory(str(self.path))
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def _require_open(self) -> Any:
        if self._file is None:
            raise RuntimeError("SEQ reader is not open")
        return self._file

    @property
    def width(self) -> int:
        return int(self._require_open().width)

    @property
    def height(self) -> int:
        return int(self._require_open().height)

    @property
    def num_frames(self) -> int:
        return int(self._require_open().num_frames)

    def timestamp_at(self, frame_index: int) -> datetime:
        if not 0 <= frame_index < self.num_frames:
            raise IndexError(f"frame index out of range: {frame_index}")
        imager = self._require_open()
        if not imager.get_frame(frame_index):
            raise RuntimeError(f"FileSDK could not read frame {frame_index}")
        timestamp = imager.frame_info.time
        if not isinstance(timestamp, datetime):
            raise RuntimeError(f"frame {frame_index} has no valid timestamp")
        return timestamp

    def iter_timestamps(self) -> Iterator[datetime]:
        for frame_index in range(self.num_frames):
            yield self.timestamp_at(frame_index)
```

实现规则：

- 使用 `Path.resolve(strict=True)` 验证文件。
- 后缀必须为 `.seq`，比较时忽略大小写。
- 默认工厂是 `fnv.file.ImagerFile`。
- FileSDK 帧号固定为 `0..num_frames-1`；不调用需要 preset 参数的 `first_frame_number()` 或 `last_frame_number()`。
- `timestamp_at` 检查边界，调用 `get_frame(frame_index)` 并读取 `frame_info.time`。
- `close()` 在退出上下文时无条件调用。

- [ ] **Step 4: 运行读取器测试**

Run: `python -m pytest tests/unit/test_reader.py -v`

Expected: 所有测试通过。

- [ ] **Step 5: 提交只读适配器**

```powershell
git add infrared/src/flir_seq_tool/models.py infrared/src/flir_seq_tool/reader.py infrared/tests/unit/test_reader.py
git commit -m "feat: add read-only FileSDK adapter"
```

### Task 4：实现检查流程与 CLI

**Files:**
- Modify: `src/flir_seq_tool/models.py`
- Create: `src/flir_seq_tool/inspector.py`
- Create: `src/flir_seq_tool/cli.py`
- Create: `tests/unit/test_inspector.py`
- Create: `tests/unit/test_cli.py`

- [ ] **Step 1: 写 endpoints/full 两种模式与 JSON 输出的失败测试**

检查器测试使用注入的假读取器：`endpoints` 只访问帧 0 和最后一帧；`full` 访问全部帧并调用 `analyze_timestamps`。CLI 测试调用 `main(["inspect", "sample.seq", "--timestamp-mode", "full", "--json"])`，断言退出码为 0 且 JSON 包含：

```json
{
  "width": 640,
  "height": 480,
  "num_frames": 65,
  "timestamp_mode": "full"
}
```

- [ ] **Step 2: 运行测试并确认缺少检查流程**

Run: `python -m pytest tests/unit/test_inspector.py tests/unit/test_cli.py -v`

Expected: FAIL，错误指向缺少 `inspector` 或 `cli`。

- [ ] **Step 3: 实现检查结果、序列化与命令行**

命令：

```text
python -m flir_seq_tool inspect INPUT.seq [--timestamp-mode endpoints|full] [--json]
```

默认 `--timestamp-mode endpoints`，避免在用户只需要基本信息时扫描大文件。`endpoints` 仅报告首末时间和总时长，`median_interval_s`、`estimated_fps` 为 `null`，`continuity_checked=false`；`full` 才逐帧建立完整时间戳摘要。JSON 中的时间统一用 ISO 8601，浮点秒保留原始数值，不格式化为字符串。

`SeqInspection` 模型固定为：

```python
@dataclass(frozen=True)
class SeqInspection:
    path: Path
    size_bytes: int
    modified_time_ns: int
    width: int
    height: int
    num_frames: int
    timestamp_mode: str
    timestamps: TimestampSummary
```

检查流程固定为：

```python
def inspect_seq(path: Path, timestamp_mode: str = "endpoints") -> SeqInspection:
    if timestamp_mode not in {"endpoints", "full"}:
        raise ValueError(f"unknown timestamp mode: {timestamp_mode}")
    resolved = Path(path).resolve(strict=True)
    file_stat = resolved.stat()
    with ScienceFileReader(resolved) as reader:
        if reader.num_frames < 1:
            raise RuntimeError("SEQ contains no frames")
        if timestamp_mode == "full":
            times = list(reader.iter_timestamps())
            summary = analyze_timestamps(times)
        else:
            first_time = reader.timestamp_at(0)
            last_time = first_time if reader.num_frames == 1 else reader.timestamp_at(reader.num_frames - 1)
            summary = TimestampSummary(
                frame_count=reader.num_frames,
                scanned_frame_count=1 if reader.num_frames == 1 else 2,
                continuity_checked=False,
                first_time=first_time,
                last_time=last_time,
                duration_s=(last_time - first_time).total_seconds(),
                median_interval_s=None,
                estimated_fps=None,
                duplicate_after_frames=(),
                backward_after_frames=(),
                gap_after_frames=(),
            )
        return SeqInspection(
            path=resolved,
            size_bytes=file_stat.st_size,
            modified_time_ns=file_stat.st_mtime_ns,
            width=reader.width,
            height=reader.height,
            num_frames=reader.num_frames,
            timestamp_mode=timestamp_mode,
            timestamps=summary,
        )
```

CLI 捕获可预期的 `FileNotFoundError`、`ValueError`、`RuntimeError`，将简洁错误写入 stderr 并返回 2；未知异常不吞掉。

- [ ] **Step 4: 运行检查器与 CLI 单元测试**

Run: `python -m pytest tests/unit/test_inspector.py tests/unit/test_cli.py -v`

Expected: 所有测试通过。

- [ ] **Step 5: 提交检查流程**

```powershell
git add infrared/src/flir_seq_tool/models.py infrared/src/flir_seq_tool/inspector.py infrared/src/flir_seq_tool/cli.py infrared/tests/unit/test_inspector.py infrared/tests/unit/test_cli.py
git commit -m "feat: add SEQ inspection command"
```

### Task 5：建立真实最小 SEQ 回归基线并验收

**Files:**
- Create: `tests/integration/test_small_seq.py`
- Create: `README.md`

- [ ] **Step 1: 写真实文件集成测试**

```python
from datetime import datetime
from pathlib import Path

import pytest

from flir_seq_tool.inspector import inspect_seq


SMALL_SEQ = Path("raw/Rec-000366-240_09_26_37_152.seq")


@pytest.mark.integration
def test_small_seq_metadata_and_timestamps_are_stable():
    before = SMALL_SEQ.stat()
    result = inspect_seq(SMALL_SEQ, timestamp_mode="full")
    after = SMALL_SEQ.stat()

    assert (result.width, result.height) == (640, 480)
    assert result.num_frames == 65
    assert result.timestamps.first_time == datetime(2026, 8, 28, 9, 26, 37, 152000)
    assert result.timestamps.last_time == datetime(2026, 8, 28, 9, 26, 38, 932000)
    assert result.timestamps.duplicate_after_frames == ()
    assert result.timestamps.backward_after_frames == ()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
```

- [ ] **Step 2: 运行集成测试并确认它能发现尚未满足的行为**

Run: `python -m pytest tests/integration/test_small_seq.py -v`

Expected: 如果 Task 1–4 有遗漏则 FAIL；修正只允许针对失败暴露的最小行为，且先保留失败证据。

- [ ] **Step 3: 补充最小使用说明**

`README.md` 记录：环境要求、只读保证、基本检查命令、完整时间戳扫描命令、JSON 示例和 `endpoints`/`full` 的性能差异。不得宣称尚未实现的滑块或 MP4 功能已经可用。

- [ ] **Step 4: 执行完整验证**

Run: `python -m pytest -v`

Expected: 单元测试与真实文件集成测试全部通过，0 failures。

Run: `python -m flir_seq_tool inspect raw\Rec-000366-240_09_26_37_152.seq --timestamp-mode full --json`

Expected: 退出码 0；JSON 报告 `640x480`、65 帧、首时间 `2026-08-28T09:26:37.152000`、末时间 `2026-08-28T09:26:38.932000`，无重复或倒退时间戳。

Run: `git diff -- raw`

Expected: 无输出，证明实现没有修改原始目录中的已跟踪内容；同时再次比较三个原始文件的大小和修改时间清单。

- [ ] **Step 5: 提交阶段 A**

```powershell
git add infrared/tests/integration/test_small_seq.py infrared/README.md
git commit -m "test: verify smallest FLIR SEQ inspection"
```

## 阶段 A 完成判据

- 所有新增测试经过可解释的 RED 到 GREEN 过程。
- `python -m pytest -v` 为 0 failures。
- 最小 SEQ 的尺寸、65 帧、首末时间与已知基线一致。
- 完整扫描未发现重复或倒退时间戳，异常大间隔按定义报告。
- 三个原始 SEQ 的大小和修改时间保持不变。
- CLI 默认只读端点，用户明确选择 `full` 才扫描全部帧。
- 尚未实现的交互查看和 MP4 导出在 README 中明确标记为后续阶段。
