# FLIR SEQ 阶段 B 帧滑块查看器实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在阶段 A 只读检查器上增加工厂标定摄氏温度帧读取、固定温标、最多三帧 LRU 缓存和 Matplotlib 帧滑块查看器。

**Architecture:** `ScienceFileReader` 继续作为唯一 FileSDK 边界，返回拥有独立内存的 `TemperatureFrame`。`FrameCache` 和温标计算不依赖 GUI；`MatplotlibViewer` 只连接会话模型与 Matplotlib artists。CLI 的 `view` 子命令在 `plt.show()` 生命周期内保持 FileSDK 文件打开。

**Tech Stack:** Python 3.13、FLIR FileSDK 2026.1.2、NumPy 2.2、Matplotlib 3.10、pytest 9.1。

---

## 范围和已确认边界

- 当前 SEQ 支持 `TEMPERATURE_FACTORY`，不支持 `TEMPERATURE_USER`。
- FileSDK `final` 是长度 307,200 的 `array.array`，转换后为 `float32`，必须重塑为 `(480, 640)` 并复制。
- 文件内对象参数与记录值一致：发射率 0.95、反射温度 293.15 K、距离 1 m、大气温度 293.15 K、相对湿度 0.5、大气透过率 1.0。
- 阶段 B 只验证并显示这些参数，不声称可以在 FileSDK 内修改不可变的工厂参数。
- 阶段 B 不实现起止区间、标点、ROI 或 MP4。

## 文件结构

- Modify `src/flir_seq_tool/models.py`：温度帧和对象参数快照。
- Modify `src/flir_seq_tool/reader.py`：工厂摄氏温度帧读取与参数快照。
- Create `src/flir_seq_tool/frame_cache.py`：固定容量 LRU 缓存。
- Create `src/flir_seq_tool/color_scale.py`：有限像素百分位温标。
- Create `src/flir_seq_tool/viewer.py`：查看会话和 Matplotlib UI。
- Modify `src/flir_seq_tool/cli.py`：`view` 子命令。
- Modify `README.md`：阶段 B 使用说明。
- Add/modify tests under `tests/unit` and `tests/integration`.

### Task 1：扩展只读温度帧 API

**Files:**
- Modify: `src/flir_seq_tool/models.py`
- Modify: `src/flir_seq_tool/reader.py`
- Modify: `tests/unit/test_reader.py`
- Modify: `tests/integration/test_small_seq.py`

- [ ] **Step 1: 写失败测试**

假后端提供 `final=array('f', values)`、`unit`、`temp_type` 和对象参数。测试要求：

```python
frame = reader.temperature_frame_at(0)
assert frame.frame_index == 0
assert frame.timestamp == times[0]
assert frame.temperature_c.shape == (2, 3)
assert frame.temperature_c.dtype == np.float32
assert frame.temperature_c.flags.owndata is True
assert fake.unit == fnv.Unit.TEMPERATURE_FACTORY
assert fake.temp_type == fnv.TempType.CELSIUS
```

修改假后端 `final` 后，已返回的矩阵必须保持不变，以证明发生了复制。另测 FileSDK 返回像素数不等于 `width * height` 时抛出 `RuntimeError("unexpected pixel count")`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests\unit\test_reader.py -v`

Expected: FAIL，原因是 `ScienceFileReader` 没有 `temperature_frame_at`。

- [ ] **Step 3: 实现模型和读取方法**

`models.py` 新增：

```python
@dataclass(frozen=True)
class ObjectParameterSnapshot:
    emissivity: float
    reflected_temp_k: float
    distance_m: float
    atmosphere_temp_k: float
    relative_humidity: float
    atmospheric_transmission: float


@dataclass(frozen=True)
class TemperatureFrame:
    frame_index: int
    timestamp: datetime
    temperature_c: np.ndarray
```

`reader.py` 新增 `object_parameters` 属性和 `temperature_frame_at(frame_index)`。读取方法先验证索引，再设置：

```python
imager.unit = fnv.Unit.TEMPERATURE_FACTORY
imager.temp_type = fnv.TempType.CELSIUS
if not imager.get_frame(frame_index):
    raise RuntimeError(f"FileSDK could not read frame {frame_index}")
values = np.asarray(imager.final, dtype=np.float32)
if values.size != self.width * self.height:
    raise RuntimeError(
        f"unexpected pixel count for frame {frame_index}: "
        f"{values.size} != {self.width * self.height}"
    )
matrix = values.reshape(self.height, self.width).copy()
return TemperatureFrame(frame_index, imager.frame_info.time, matrix)
```

- [ ] **Step 4: 运行单元测试和真实首帧回归**

Run: `python -m pytest tests\unit\test_reader.py tests\integration\test_small_seq.py -v`

Expected: PASS；真实首帧 `min/mean/max` 近似 `22.0858/25.2810/42.1735` ℃。

- [ ] **Step 5: 提交**

```powershell
git add infrared/src/flir_seq_tool/models.py infrared/src/flir_seq_tool/reader.py infrared/tests/unit/test_reader.py infrared/tests/integration/test_small_seq.py
git commit -m "feat: read calibrated SEQ temperature frames"
```

### Task 2：实现固定温标和三帧 LRU 缓存

**Files:**
- Create: `src/flir_seq_tool/color_scale.py`
- Create: `src/flir_seq_tool/frame_cache.py`
- Create: `tests/unit/test_color_scale.py`
- Create: `tests/unit/test_frame_cache.py`

- [ ] **Step 1: 写温标失败测试**

```python
def test_compute_color_limits_ignores_nonfinite_values():
    values = np.array([[0.0, 10.0, np.nan], [20.0, 30.0, np.inf]])
    limits = compute_color_limits(values, lower_percentile=0, upper_percentile=100)
    assert limits == ColorLimits(vmin=0.0, vmax=30.0)


def test_compute_color_limits_expands_constant_frame():
    limits = compute_color_limits(np.full((2, 2), 25.0))
    assert limits == ColorLimits(vmin=24.5, vmax=25.5)
```

无有限像素时抛出 `ValueError("frame has no finite temperatures")`；百分位不满足 `0 <= lower < upper <= 100` 时抛出 `ValueError`。

- [ ] **Step 2: 写缓存失败测试**

```python
cache = FrameCache(maxsize=3)
assert cache.get(0, loader).frame_index == 0
assert cache.get(0, loader).frame_index == 0
assert calls == [0]
cache.get(1, loader)
cache.get(2, loader)
cache.get(3, loader)
cache.get(0, loader)
assert calls == [0, 1, 2, 3, 0]
```

另测命中旧帧会更新 LRU 顺序，以及 `maxsize < 1` 抛出 `ValueError("maxsize must be positive")`。

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests\unit\test_color_scale.py tests\unit\test_frame_cache.py -v`

Expected: FAIL，原因是两个模块不存在。

- [ ] **Step 4: 实现纯函数和缓存**

`ColorLimits` 是冻结 dataclass。`compute_color_limits` 使用 `np.isfinite` 和 `np.percentile`；若 `vmin == vmax`，固定扩展为 `value ± 0.5`。

`FrameCache` 使用 `OrderedDict[int, TemperatureFrame]`。命中时 `move_to_end`；插入后超过容量时执行 `popitem(last=False)`。

- [ ] **Step 5: 运行测试并提交**

Run: `python -m pytest tests\unit\test_color_scale.py tests\unit\test_frame_cache.py -v`

Expected: PASS。

```powershell
git add infrared/src/flir_seq_tool/color_scale.py infrared/src/flir_seq_tool/frame_cache.py infrared/tests/unit/test_color_scale.py infrared/tests/unit/test_frame_cache.py
git commit -m "feat: add fixed color scale and frame cache"
```

### Task 3：实现可测试的 Matplotlib 滑块查看器

**Files:**
- Create: `src/flir_seq_tool/viewer.py`
- Create: `tests/unit/test_viewer.py`

- [ ] **Step 1: 写会话和 UI 失败测试**

使用假读取器和 Matplotlib `Agg` backend：

```python
session = ViewerSession(fake_reader, cache_size=3)
first = session.frame_at(0)
second = session.frame_at(1)
assert first.frame_index == 0
assert second.frame_index == 1
assert session.color_limits.vmin == pytest.approx(1.05)
assert session.color_limits.vmax == pytest.approx(5.95)

ui = build_viewer(session, initial_frame=0)
ui.slider.set_val(1)
assert np.array_equal(ui.image.get_array(), second.temperature_c)
assert ui.image.get_clim() == pytest.approx((1.05, 5.95))
assert "Frame 1/" in ui.axes.get_title()
```

另测初始帧越界、显式 `vmin >= vmax` 和单帧文件滑块范围。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests\unit\test_viewer.py -v`

Expected: FAIL，原因是 `flir_seq_tool.viewer` 不存在。

- [ ] **Step 3: 实现会话模型**

`ViewerSession` 保存读取器、`FrameCache` 和可选显式温标。首次 `frame_at` 用该帧第 1/99 百分位确定固定温标；之后不改变。若同时只传 `vmin` 或只传 `vmax`，抛出 `ValueError("vmin and vmax must be provided together")`。

- [ ] **Step 4: 实现 Matplotlib UI**

`build_viewer` 创建：

- 主 axes 调用 `imshow(frame.temperature_c, cmap="inferno", vmin=limits.vmin, vmax=limits.vmax)`。
- 右侧 Celsius colorbar。
- 底部 `Slider(valmin=0, valmax=num_frames-1, valstep=1)`。
- 回调将滑块值四舍五入为整数，读取缓存帧，更新 image data 和 `Frame i/(N-1) | ISO timestamp` 标题，只调用 `figure.canvas.draw_idle()`。

返回冻结 dataclass `ViewerUI(figure, axes, image, slider, session)`，便于测试和后续加入选区控件。

单帧文件使用内部范围 `0..1` 创建 Slider，随后 `set_active(False)` 并隐藏 slider axes；回调始终把目标帧裁剪到 `0..num_frames-1`，因此不会请求不存在的帧。

- [ ] **Step 5: 运行测试并提交**

Run: `python -m pytest tests\unit\test_viewer.py -v`

Expected: PASS，并显式关闭测试 figure。

```powershell
git add infrared/src/flir_seq_tool/viewer.py infrared/tests/unit/test_viewer.py
git commit -m "feat: add Matplotlib SEQ frame viewer"
```

### Task 4：增加 view CLI、真实文件验证和文档

**Files:**
- Modify: `src/flir_seq_tool/cli.py`
- Modify: `tests/unit/test_cli.py`
- Create: `tests/integration/test_small_seq_viewer.py`
- Modify: `README.md`

- [ ] **Step 1: 写 view CLI 失败测试**

```python
def test_cli_dispatches_view_command(monkeypatch, tmp_path):
    path = tmp_path / "sample.seq"
    path.write_bytes(b"fixture")
    calls = []
    monkeypatch.setattr(cli, "launch_viewer", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert cli.main(["view", str(path), "--frame", "2", "--vmin", "20", "--vmax", "40"]) == 0
    assert calls == [((path,), {"initial_frame": 2, "vmin": 20.0, "vmax": 40.0})]
```

CLI 参数固定为：`flir-seq view INPUT.seq [--frame N] [--vmin C --vmax C]`。

- [ ] **Step 2: 运行 CLI 测试确认失败**

Run: `python -m pytest tests\unit\test_cli.py -v`

Expected: FAIL，原因是 parser 不认识 `view`。

- [ ] **Step 3: 实现 CLI 分派和 launch_viewer**

`launch_viewer` 使用：

```python
with ScienceFileReader(path) as reader:
    session = ViewerSession(reader, vmin=vmin, vmax=vmax)
    build_viewer(session, initial_frame=initial_frame)
    plt.show()
```

CLI 捕获预期文件、帧号和温标错误并返回 2。

- [ ] **Step 4: 写真实文件 headless 集成测试**

以 `Agg` backend 打开最小 SEQ，构建第 0 帧 UI，再把滑块移到第 64 帧。断言二维数组尺寸始终为 `(480, 640)`、温标不变、缓存长度不超过 3，且原始文件大小和修改时间不变。

- [ ] **Step 5: 更新 README 并完整验证**

README 增加查看命令、滑块说明、固定温标行为和已知限制。

Run: `python -m pytest -v`

Expected: 0 failures。

Run: `python -m flir_seq_tool view raw\Rec-000366-240_09_26_37_152.seq --frame 0`

Expected: 出现 QtAgg Matplotlib 窗口；滑块可访问 0–64 帧，关闭窗口后进程正常退出。此人工 GUI 验收需要用户允许打开窗口时执行。

- [ ] **Step 6: 提交**

```powershell
git add infrared/src/flir_seq_tool/cli.py infrared/src/flir_seq_tool/viewer.py infrared/tests/unit/test_cli.py infrared/tests/integration/test_small_seq_viewer.py infrared/README.md
git commit -m "feat: expose interactive SEQ viewer"
```

## 阶段 B 完成判据

- 真实温度帧为独立 `float32 (480, 640)` 数组。
- 最小文件首帧温度回归值与已验证基线一致。
- 查看器整个会话使用固定温标。
- 缓存最多保留 3 帧，往返访问命中缓存。
- 滑块按需读取单帧，不加载完整 SEQ。
- 全部自动测试通过，三份原始 SEQ 的大小和修改时间未变化。
- README 不宣称时间段、标点、ROI 或 MP4 已完成。
