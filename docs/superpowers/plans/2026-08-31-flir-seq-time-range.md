# FLIR SEQ 时间范围 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有 Matplotlib SEQ 查看器增加可重复缩小、可恢复完整序列的局部时间轴，并提供双端范围滑块、局部帧输入框、真实持续时间和原始帧追溯信息。

**Architecture:** 新建与 Matplotlib 解耦的 `TimelineState`，集中处理原始帧与局部帧映射、候选范围、确认和恢复。`ViewerSession` 负责把局部帧映射到 FileSDK 原始帧并缓存温度帧；`viewer.py` 只负责控件联动、状态文本和错误显示。所有源文件读取保持只读，区间持续时间由真实端点时间戳计算。

**Tech Stack:** Python 3.13、FLIR Science File SDK 2026.1.2、NumPy、Matplotlib `Slider`/`RangeSlider`/`TextBox`/`Button`、pytest。

---

## 文件结构

- Create: `src/flir_seq_tool/timeline.py`：纯时间轴状态、验证与坐标映射。
- Create: `tests/unit/test_timeline.py`：时间轴状态单元测试。
- Modify: `src/flir_seq_tool/viewer.py`：局部帧会话接口、范围控件和事件联动。
- Modify: `tests/unit/test_viewer.py`：局部帧读取、控件联动、错误处理和强引用测试。
- Modify: `tests/integration/test_small_seq_viewer.py`：真实小型 SEQ 的确认、重复缩小、持续时间、恢复和只读验收。
- Modify: `README.md`：时间范围操作说明与阶段状态。

### Task 1: 纯时间轴状态模型

**Files:**
- Create: `src/flir_seq_tool/timeline.py`
- Create: `tests/unit/test_timeline.py`

- [ ] **Step 1: 写初始状态、映射、确认和恢复的失败测试**

```python
import pytest

from flir_seq_tool.timeline import TimelineState


def test_timeline_starts_with_full_source_range():
    timeline = TimelineState(65)

    assert timeline.source_start == 0
    assert timeline.source_end == 64
    assert timeline.local_num_frames == 65
    assert timeline.current_local_frame == 0
    assert timeline.pending_range == (0, 64)


def test_timeline_maps_between_local_and_source_frames():
    timeline = TimelineState(65)
    timeline.set_pending(10, 50)
    assert timeline.confirm_range() is True

    assert timeline.source_frame_at(0) == 10
    assert timeline.source_frame_at(40) == 50
    assert timeline.local_frame_at(25) == 15


def test_timeline_can_narrow_repeatedly_and_reset_once():
    timeline = TimelineState(65)
    timeline.set_pending(10, 50)
    timeline.confirm_range()
    timeline.set_pending(5, 20)
    timeline.confirm_range()

    assert (timeline.source_start, timeline.source_end) == (15, 30)
    assert timeline.pending_range == (0, 15)
    assert timeline.current_local_frame == 0

    timeline.reset_full()

    assert (timeline.source_start, timeline.source_end) == (0, 64)
    assert timeline.pending_range == (0, 64)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python -m pytest tests\unit\test_timeline.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'flir_seq_tool.timeline'`。

- [ ] **Step 3: 实现最小时间轴模型**

```python
from dataclasses import dataclass, field


@dataclass
class TimelineState:
    source_num_frames: int
    source_start: int = field(init=False, default=0)
    source_end: int = field(init=False)
    current_local_frame: int = field(init=False, default=0)
    pending_start: int = field(init=False, default=0)
    pending_end: int = field(init=False)

    def __post_init__(self) -> None:
        if self.source_num_frames < 1:
            raise ValueError("source_num_frames must be positive")
        self.source_end = self.source_num_frames - 1
        self.pending_end = self.source_end

    @property
    def local_num_frames(self) -> int:
        return self.source_end - self.source_start + 1

    @property
    def pending_range(self) -> tuple[int, int]:
        return self.pending_start, self.pending_end

    def _validate_local(self, local_frame: int) -> None:
        if not 0 <= local_frame < self.local_num_frames:
            raise IndexError(f"local frame out of range: {local_frame}")

    def source_frame_at(self, local_frame: int) -> int:
        self._validate_local(local_frame)
        return self.source_start + local_frame

    def local_frame_at(self, source_frame: int) -> int:
        if not self.source_start <= source_frame <= self.source_end:
            raise IndexError(f"source frame outside work range: {source_frame}")
        return source_frame - self.source_start

    def set_current(self, local_frame: int) -> None:
        self._validate_local(local_frame)
        self.current_local_frame = local_frame

    def set_pending(self, start: int, end: int) -> None:
        self._validate_local(start)
        self._validate_local(end)
        if start > end:
            raise ValueError("start frame must not exceed end frame")
        self.pending_start = start
        self.pending_end = end

    def set_pending_start(self, start: int) -> None:
        self.set_pending(start, self.pending_end)

    def set_pending_end(self, end: int) -> None:
        self.set_pending(self.pending_start, end)

    def confirm_range(self) -> bool:
        if self.pending_range == (0, self.local_num_frames - 1):
            return False
        old_source_start = self.source_start
        new_start = old_source_start + self.pending_start
        new_end = old_source_start + self.pending_end
        self.source_start = new_start
        self.source_end = new_end
        self.current_local_frame = 0
        self.pending_start = 0
        self.pending_end = self.local_num_frames - 1
        return True

    def reset_full(self) -> None:
        self.source_start = 0
        self.source_end = self.source_num_frames - 1
        self.current_local_frame = 0
        self.pending_start = 0
        self.pending_end = self.source_end
```

- [ ] **Step 4: 增加原子错误测试并确认 GREEN**

```python
@pytest.mark.parametrize("start,end", [(-1, 2), (0, 65), (5, 4)])
def test_invalid_pending_range_does_not_change_state(start, end):
    timeline = TimelineState(65)
    before = timeline.pending_range

    with pytest.raises((IndexError, ValueError)):
        timeline.set_pending(start, end)

    assert timeline.pending_range == before


def test_single_frame_range_is_valid():
    timeline = TimelineState(65)
    timeline.set_pending(12, 12)
    timeline.confirm_range()

    assert (timeline.source_start, timeline.source_end) == (12, 12)
    assert timeline.local_num_frames == 1
```

Run: `python -m pytest tests\unit\test_timeline.py -v`

Expected: all tests in `test_timeline.py` pass。

- [ ] **Step 5: 提交状态模型**

```powershell
git add src/flir_seq_tool/timeline.py tests/unit/test_timeline.py
git commit -m "feat: add SEQ timeline state"
```

### Task 2: ViewerSession 局部帧接口与真实持续时间

**Files:**
- Modify: `src/flir_seq_tool/viewer.py`
- Modify: `tests/unit/test_viewer.py`

- [ ] **Step 1: 写局部帧映射和持续时间的失败测试**

在 `FakeTemperatureReader` 增加：

```python
def timestamp_at(self, frame_index: int) -> datetime:
    return self.frames[frame_index].timestamp
```

增加测试：

```python
def test_viewer_session_reads_local_frame_from_confirmed_source_range():
    reader = FakeTemperatureReader(make_frames(count=2))
    session = ViewerSession(reader)
    session.timeline.set_pending(1, 1)
    session.timeline.confirm_range()

    frame = session.frame_at_local(0)

    assert frame.frame_index == 1
    assert reader.requested_frames == [1]


def test_viewer_session_uses_real_endpoint_timestamps_for_duration():
    reader = FakeTemperatureReader(make_frames(count=2))
    session = ViewerSession(reader)

    assert session.work_duration_s() == pytest.approx(0.02)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python -m pytest tests\unit\test_viewer.py -k "local_frame or endpoint_timestamps" -v`

Expected: FAIL，`ViewerSession` 没有 `timeline`、`frame_at_local` 或 `work_duration_s`。

- [ ] **Step 3: 实现局部帧读取和端点时间接口**

在 `viewer.py` 导入并扩展 `ViewerSession`：

```python
from datetime import datetime

from .timeline import TimelineState


class ViewerSession:
    def __init__(self, reader, cache_size=3, vmin=None, vmax=None):
        if (vmin is None) != (vmax is None):
            raise ValueError("vmin and vmax must be provided together")
        if vmin is not None and vmax is not None and vmin >= vmax:
            raise ValueError("vmin must be less than vmax")
        if reader.num_frames < 1:
            raise ValueError("SEQ contains no frames")
        self.reader = reader
        self.cache = FrameCache(maxsize=cache_size)
        self.timeline = TimelineState(reader.num_frames)
        self._work_time_key: tuple[int, int] | None = None
        self._work_time_value: tuple[datetime, datetime] | None = None
        self.color_limits = (
            None
            if vmin is None or vmax is None
            else ColorLimits(vmin=float(vmin), vmax=float(vmax))
        )

    def frame_at_local(self, local_frame: int) -> TemperatureFrame:
        self.timeline.set_current(local_frame)
        return self.frame_at(self.timeline.source_frame_at(local_frame))

    def work_timestamps(self) -> tuple[datetime, datetime]:
        key = (self.timeline.source_start, self.timeline.source_end)
        if self._work_time_key != key or self._work_time_value is None:
            self._work_time_key = key
            self._work_time_value = (
                self.reader.timestamp_at(key[0]),
                self.reader.timestamp_at(key[1]),
            )
        return self._work_time_value

    def work_duration_s(self) -> float:
        start, end = self.work_timestamps()
        return (end - start).total_seconds()
```

- [ ] **Step 4: 运行 ViewerSession 测试并确认 GREEN**

Run: `python -m pytest tests\unit\test_viewer.py -k "session" -v`

Expected: selected tests pass，现有固定温标和 LRU 缓存测试继续通过。

- [ ] **Step 5: 提交会话接口**

```powershell
git add src/flir_seq_tool/viewer.py tests/unit/test_viewer.py
git commit -m "feat: map viewer work ranges to source frames"
```

### Task 3: 双滑块、帧号输入框、确认与恢复

**Files:**
- Modify: `src/flir_seq_tool/viewer.py`
- Modify: `tests/unit/test_viewer.py`

- [ ] **Step 1: 写范围滑块自动预览的失败测试**

把 `make_frames` 改为按 `count` 动态生成数组和时间戳，避免只支持两帧：

```python
def make_frames(count: int = 2) -> list[TemperatureFrame]:
    start = datetime(2026, 8, 28, 9, 0, 0)
    return [
        TemperatureFrame(
            frame_index=index,
            timestamp=start + timedelta(milliseconds=20 * index),
            temperature_c=(
                np.arange(1, 7, dtype=np.float32).reshape(2, 3) + 10 * index
            ),
        )
        for index in range(count)
    ]
```

增加测试：

```python
def test_range_slider_updates_selection_and_previews_changed_endpoint():
    reader = FakeTemperatureReader(make_frames(count=10))
    ui = build_viewer(ViewerSession(reader))

    ui.range_slider.set_val((2, 7))

    assert ui.session.timeline.pending_range == (2, 7)
    assert ui.session.timeline.current_local_frame == 2
    assert ui.slider.val == pytest.approx(2)
    assert "Source frame 2/9" in ui.axes.get_title()
    plt.close(ui.figure)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `python -m pytest tests\unit\test_viewer.py::test_range_slider_updates_selection_and_previews_changed_endpoint -v`

Expected: FAIL，`ViewerUI` 没有 `range_slider`。

- [ ] **Step 3: 扩展 ViewerUI 和创建控件**

将 Matplotlib 控件导入改为：

```python
from matplotlib.text import Text
from matplotlib.widgets import Button, RangeSlider, Slider, TextBox
```

扩展 `ViewerUI`：

```python
@dataclass(frozen=True)
class ViewerUI:
    figure: Figure
    axes: Axes
    image: AxesImage
    slider: Slider
    range_slider: RangeSlider
    start_box: TextBox
    end_box: TextBox
    confirm_button: Button
    reset_button: Button
    status_text: Text
    error_text: Text
    session: ViewerSession
```

把 `build_viewer` 的首帧读取从原始坐标改为局部坐标：

```python
frame = session.frame_at_local(initial_frame)
```

在 `build_viewer` 中把底部留白增至 `0.34`，并创建：

```python
current_axes = figure.add_axes((0.15, 0.22, 0.65, 0.03))
range_axes = figure.add_axes((0.15, 0.17, 0.65, 0.03))
start_axes = figure.add_axes((0.15, 0.105, 0.12, 0.045))
end_axes = figure.add_axes((0.37, 0.105, 0.12, 0.045))
confirm_axes = figure.add_axes((0.57, 0.105, 0.13, 0.045))
reset_axes = figure.add_axes((0.72, 0.105, 0.16, 0.045))

slider = Slider(current_axes, "Current frame", 0, slider_max,
                valinit=initial_frame, valstep=1, valfmt="%0.0f")
range_slider = RangeSlider(range_axes, "Selected range", 0, slider_max,
                           valinit=(0, session.timeline.local_num_frames - 1),
                           valstep=1, valfmt="%0.0f")
start_box = TextBox(start_axes, "Start frame", initial="0")
end_box = TextBox(
    end_axes,
    "End frame",
    initial=str(session.timeline.local_num_frames - 1),
)
confirm_button = Button(confirm_axes, "Confirm range")
reset_button = Button(reset_axes, "Reset full")
status_text = figure.text(0.15, 0.065, "", fontsize=9)
error_text = figure.text(0.15, 0.035, "", fontsize=9, color="crimson")
```

- [ ] **Step 4: 实现控件联动和范围重新编号**

在 `build_viewer` 内使用 `updating = False` 防止程序化同步递归，并实现以下局部函数：

```python
def set_error(message: str = "") -> None:
    error_text.set_text(message)
    figure.canvas.draw_idle()


def set_widget_domain(widget, local_max: int) -> None:
    visual_max = max(1, local_max)
    widget.valmin = 0
    widget.valmax = visual_max
    widget.ax.set_xlim(0, visual_max)
    widget.set_active(local_max > 0)
    widget.ax.set_visible(local_max > 0)


def refresh_status(frame: TemperatureFrame) -> None:
    start_time, end_time = session.work_timestamps()
    relative_s = (frame.timestamp - start_time).total_seconds()
    status_text.set_text(
        f"Local frame {session.timeline.current_local_frame}/"
        f"{session.timeline.local_num_frames - 1} | "
        f"Source frame {frame.frame_index}/{session.reader.num_frames - 1} | "
        f"Work source [{session.timeline.source_start}, "
        f"{session.timeline.source_end}] | "
        f"Absolute {frame.timestamp.isoformat()} | "
        f"Relative {relative_s:.3f} s | Duration "
        f"{(end_time - start_time).total_seconds():.3f} s"
    )


def show_local(local_frame: int, sync_slider: bool = True) -> None:
    nonlocal updating
    frame = session.frame_at_local(local_frame)
    image.set_data(frame.temperature_c)
    _set_frame_title(
        axes,
        frame,
        session.reader.num_frames,
        local_frame=local_frame,
        local_num_frames=session.timeline.local_num_frames,
    )
    refresh_status(frame)
    if sync_slider:
        updating = True
        try:
            slider.set_val(local_frame)
        finally:
            updating = False
    set_error()
    figure.canvas.draw_idle()


def sync_all_controls() -> None:
    nonlocal updating
    local_max = session.timeline.local_num_frames - 1
    updating = True
    try:
        set_widget_domain(slider, local_max)
        set_widget_domain(range_slider, local_max)
        slider.set_val(session.timeline.current_local_frame)
        range_slider.set_val(session.timeline.pending_range)
        start_box.set_val(str(session.timeline.pending_start))
        end_box.set_val(str(session.timeline.pending_end))
    finally:
        updating = False


def on_current(value: float) -> None:
    if not updating:
        show_local(int(round(value)), sync_slider=False)


def on_range(values: tuple[float, float]) -> None:
    if updating:
        return
    old_start, old_end = session.timeline.pending_range
    new_start, new_end = (int(round(values[0])), int(round(values[1])))
    try:
        session.timeline.set_pending(new_start, new_end)
    except (IndexError, ValueError) as exc:
        set_error(str(exc))
        sync_all_controls()
        return
    target = new_start if new_start != old_start else new_end
    sync_all_controls()
    show_local(target)


def submit_endpoint(text: str, endpoint: str) -> None:
    try:
        value = int(text.strip())
        if endpoint == "start":
            session.timeline.set_pending_start(value)
        else:
            session.timeline.set_pending_end(value)
    except (IndexError, ValueError) as exc:
        set_error(str(exc))
        sync_all_controls()
        return
    sync_all_controls()
    show_local(value)


def on_confirm(_event) -> None:
    if session.timeline.confirm_range():
        sync_all_controls()
        show_local(0)


def on_reset(_event) -> None:
    session.timeline.reset_full()
    sync_all_controls()
    show_local(0)
```

连接回调：

```python
slider.on_changed(on_current)
range_slider.on_changed(on_range)
start_box.on_submit(lambda text: submit_endpoint(text, "start"))
end_box.on_submit(lambda text: submit_endpoint(text, "end"))
confirm_button.on_clicked(on_confirm)
reset_button.on_clicked(on_reset)
```

创建控件后执行一次同步，并返回全部强引用：

```python
sync_all_controls()
show_local(initial_frame)
return ViewerUI(
    figure=figure,
    axes=axes,
    image=image,
    slider=slider,
    range_slider=range_slider,
    start_box=start_box,
    end_box=end_box,
    confirm_button=confirm_button,
    reset_button=reset_button,
    status_text=status_text,
    error_text=error_text,
    session=session,
)
```

更新标题函数，使局部与原始帧同时可见：

```python
def _set_frame_title(
    axes: Axes,
    frame: TemperatureFrame,
    source_num_frames: int,
    local_frame: int,
    local_num_frames: int,
) -> None:
    axes.set_title(
        f"Local frame {local_frame}/{local_num_frames - 1} | "
        f"Source frame {frame.frame_index}/{source_num_frames - 1} | "
        f"{frame.timestamp.isoformat()}"
    )
```

将既有标题断言从 `Frame 1/1` 更新为同时检查 `Local frame 1/1` 和
`Source frame 1/1`；温标断言 `(1.05, 5.95)` 保持不变。

- [ ] **Step 5: 写确认、重复缩小、回退和输入错误测试**

```python
def test_confirm_range_renumbers_both_sliders_and_shows_local_zero():
    reader = FakeTemperatureReader(make_frames(count=10))
    ui = build_viewer(ViewerSession(reader))
    ui.range_slider.set_val((2, 7))

    ui.confirm_button._observers.process("clicked", None)

    assert (ui.session.timeline.source_start, ui.session.timeline.source_end) == (2, 7)
    assert ui.session.timeline.current_local_frame == 0
    assert ui.slider.val == pytest.approx(0)
    assert ui.range_slider.val == pytest.approx((0, 5))
    assert "Source frame 2/9" in ui.axes.get_title()
    assert "Duration 0.100 s" in ui.status_text.get_text()
    plt.close(ui.figure)


def test_confirm_can_narrow_again_and_reset_returns_to_full_sequence():
    reader = FakeTemperatureReader(make_frames(count=10))
    ui = build_viewer(ViewerSession(reader))
    ui.range_slider.set_val((2, 7))
    ui.confirm_button._observers.process("clicked", None)
    ui.range_slider.set_val((1, 3))
    ui.confirm_button._observers.process("clicked", None)

    assert (ui.session.timeline.source_start, ui.session.timeline.source_end) == (3, 5)

    ui.reset_button._observers.process("clicked", None)

    assert (ui.session.timeline.source_start, ui.session.timeline.source_end) == (0, 9)
    assert ui.range_slider.val == pytest.approx((0, 9))
    plt.close(ui.figure)


def test_invalid_frame_text_restores_valid_value_and_shows_error():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(count=10))))

    ui.start_box.set_val("not-an-integer")

    assert ui.session.timeline.pending_range == (0, 9)
    assert ui.start_box.text == "0"
    assert ui.error_text.get_text()
    plt.close(ui.figure)
```

- [ ] **Step 6: 运行查看器测试并修正到 GREEN**

Run: `python -m pytest tests\unit\test_viewer.py -v`

Expected: all viewer tests pass；单帧工作区间隐藏两条滑块，输入框和恢复按钮仍可用。

- [ ] **Step 7: 提交交互查看器**

```powershell
git add src/flir_seq_tool/viewer.py tests/unit/test_viewer.py
git commit -m "feat: add interactive SEQ time-range controls"
```

### Task 4: 真实 SEQ 验收与文档

**Files:**
- Modify: `tests/integration/test_small_seq_viewer.py`
- Modify: `README.md`

- [ ] **Step 1: 写真实 SEQ 时间范围失败测试**

先把既有真实滑块测试的标题断言更新为：

```python
assert "Local frame 64/64" in ui.axes.get_title()
assert "Source frame 64/64" in ui.axes.get_title()
```

再增加：

```python
@pytest.mark.integration
def test_small_seq_time_range_uses_source_frames_and_real_timestamps():
    before = SMALL_SEQ.stat()

    with ScienceFileReader(SMALL_SEQ) as reader:
        session = ViewerSession(reader, cache_size=3)
        ui = build_viewer(session, initial_frame=0)
        ui.range_slider.set_val((10, 50))
        ui.confirm_button._observers.process("clicked", None)

        assert (session.timeline.source_start, session.timeline.source_end) == (10, 50)
        assert session.timeline.current_local_frame == 0
        assert "Source frame 10/64" in ui.axes.get_title()
        start = reader.timestamp_at(10)
        end = reader.timestamp_at(50)
        assert session.work_duration_s() == pytest.approx(
            (end - start).total_seconds()
        )

        ui.range_slider.set_val((5, 20))
        ui.confirm_button._observers.process("clicked", None)
        assert (session.timeline.source_start, session.timeline.source_end) == (15, 30)

        ui.reset_button._observers.process("clicked", None)
        assert (session.timeline.source_start, session.timeline.source_end) == (0, 64)
        assert len(session.cache) <= 3
        plt.close(ui.figure)

    after = SMALL_SEQ.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
```

- [ ] **Step 2: 运行集成测试并确认行为**

Run: `python -m pytest tests\integration\test_small_seq_viewer.py -v`

Expected: tests pass；如新测试暴露控件同步问题，只修复对应根因后重新运行。

- [ ] **Step 3: 更新 README**

把阶段说明更新为已完成 C1，并加入：

```markdown
## 选择工作时间范围

查看器保留 `Current frame` 浏览滑块，并提供 `Selected range` 双端滑块。
拖动范围端点时画面自动跳到对应帧，也可在 `Start frame` 和
`End frame` 输入局部整数帧号。

点击 `Confirm range` 后，所选范围成为新的工作时间轴，两条滑块重新编号为
`0` 至 `N-1`。标题和状态区继续显示原始 SEQ 帧号及真实持续时间。
可以重复缩小范围；点击 `Reset full` 一次恢复完整序列。

该操作只改变内存中的查看状态，不裁切或改写原始 SEQ。范围配置保存、旋转裁剪、
测温点、矩形 ROI 和 MP4 导出尚未实现。
```

- [ ] **Step 4: 运行完整验证**

Run: `python -m pytest -v`

Expected: all tests pass，无 FileSDK 崩溃；允许既有 `.pytest_cache` 权限警告，但不得有测试失败。

Run: `git diff --check`

Expected: no whitespace errors in Stage C1 files。

- [ ] **Step 5: 手动 Qt 验收**

Run:

```powershell
python -m flir_seq_tool view raw\Rec-000366-240_09_26_37_152.seq
```

验证：

- 拖动当前帧滑块只改变画面。
- 拖动范围端点会预览该端点。
- 输入局部帧号可精确定位。
- 确认 `10–50` 后滑块变为 `0–40`，画面显示原始第 10 帧。
- 再确认局部 `5–20` 后滑块变为 `0–15`，画面显示原始第 15 帧。
- 状态区显示真实秒数。
- `Reset full` 恢复 `0–64`。

- [ ] **Step 6: 提交验收与文档**

```powershell
git add tests/integration/test_small_seq_viewer.py README.md
git commit -m "test: verify SEQ time-range workflow"
```

## 最终完成检查

- [ ] `TimelineState` 与 Matplotlib 解耦，所有范围变化先验证后写入。
- [ ] 局部帧只用于界面，FileSDK 始终接收原始帧号。
- [ ] 确认后两条滑块重新编号，支持重复缩小和一次完整恢复。
- [ ] 区间长度来自真实端点时间戳，单位为秒。
- [ ] 非整数、越界和反向范围不会破坏有效状态。
- [ ] 真实 SEQ 大小和纳秒级修改时间未变化。
- [ ] 完整测试与手动 Qt 验收通过。
