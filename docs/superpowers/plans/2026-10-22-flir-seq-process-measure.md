# Process / Measure 双页面与矩形测温 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 SEQ 查看器中增加 Measure 页面，完成多矩形 ROI、当前帧统计/直方图/剖面、逐帧统计曲线、设置持久化及 CSV/PNG 输出。

**Architecture:** 复用当前 ViewerSession、TimelineState 和 FrameTransformer；新增纯数据模型、纯数值计算、页面控制器、磁盘结果存储和后台任务。Process 与 Measure 共用已确认范围、已应用变换及当前帧，通过显式页面生命周期隔离输入。selection v3 保存设置，曲线由独立只读线程逐帧计算到临时数据库。

**Tech Stack:** Python 3.13、NumPy、Matplotlib、已有 SciPy/FLIR Science FileSDK；SQLite、csv、threading、tempfile 使用标准库；pytest 验证。

---

## 0. 执行约束与顺序

依据：[设计文档](../specs/2026-10-22-flir-seq-process-measure-design.md)。功能代码基线 `0031a0f`；设计提交 `7473cf5`。当前仅编写计划，不代表以下任务已执行。

- 唯一目录 `D:\carbonfoam\infrared`，不得使用旧中文目录。用户对路径的明确要求优先于技能的独立 worktree 建议；在当前目录的功能分支工作，不另建 checkout。
- 当前设计分支为 `codex/measure-design`。执行前检查工作区；如使用新功能分支，可从此提交创建 `codex/process-measure`，不得重置或覆盖用户变更。
- 原始 `raw/*.seq` 始终只读。自动真实验证只读最小 SEQ 的少量帧，大文件仅 stat，性能由用户验收。
- 每个任务按“失败测试 → 确认失败原因 → 最小实现 → 目标回归 → 检查差异 → 本地提交”执行。代码块是接口与核心行为的具体实现约束，新增文件需包含下文列明的 imports、验证与所有分支。
- 每一任务先检查输入/输出接口是否与前序任务一致；不为赶进度删除保护性断言，不将 fixture 或权限错误当作预期业务失败。
- 不新增实验零点、热电偶同步、跨实验比较、毫米标定、拟合、自动跟踪或新 UI 框架。
- 默认尊重此前顺序执行偏好；用户另行选择子代理方式时才委派。计划不授权合并 main、push、标签或 Release。
- 所有命令在工作目录执行。下文的 `python` 必须是已安装 FileSDK 的 Python 3.13；必要时显式使用 `C:\Users\Tracer\AppData\Local\Programs\Python\Python313\python.exe`。
- pytest 使用 `-p no:cacheprovider`；若临时目录权限失败，按审批机制在沙箱外重跑，不改断言绕过失败。

### 检查点

| 阶段 | 任务 | 可以检查的结果 |
| --- | --- | --- |
| I：数据与设置 | 1–4 | 数值口径、配置兼容及范围/失效契约有测试证据 |
| II：可操作界面 | 5–7 | 顶部切页、ROI 编辑、当前帧分析可实际使用 |
| III：完整曲线 | 8–10 | 磁盘存储、后台扫描、取消和多 ROI 曲线闭环 |
| IV：交付 | 11–13 | 安全导出、真实小文件验证、人工验收说明 |

每阶段结束报告已提交内容、测试结果和未完成项。不要把阶段 II 误称为整项功能完成。

## 1. 文件职责与依赖

所有路径相对于唯一工作目录。未列出的原始数据、SDK 和既有输出文件不得改动。

| 文件 | 职责 |
| --- | --- |
| 新建 `src/flir_seq_tool/measurement_models.py` | Roi、MeasurementSettings、FrameStats、Histogram、Profile；合法性校验，不导入 selection 或 UI |
| 新建 `src/flir_seq_tool/measurement_stats.py` | 从二维温度数组计算当前帧结果 |
| 新建 `src/flir_seq_tool/measurement_state.py` | AnalysisKey、MeasureState、结果失效及保存签名 |
| 新建 `src/flir_seq_tool/viewer_pages.py` | 页面输入启停、任务互斥、导航；不做数值计算 |
| 新建 `src/flir_seq_tool/measurement_panel.py` | ROI 列表、单活跃 selector、卡片和 Measure 控件 |
| 新建 `src/flir_seq_tool/pixel_geometry.py` | 裁剪和ROI共同使用的像素边界取整；不依赖UI |
| 修改 `src/flir_seq_tool/transform_editor.py` | 仅复用共享边界取整，保留已有裁剪行为与鼠标最小跨度判断 |
| 新建 `src/flir_seq_tool/measurement_plots.py` | 分布、剖面、曲线和无控件报告 Figure 的绘制 |
| 新建 `src/flir_seq_tool/measurement_store.py` | SQLite 结果、完整行迭代、有界曲线提取和临时文件所有权 |
| 新建 `src/flir_seq_tool/measurement_job.py` | AnalysisRequest、AnalysisProgress、AnalysisOutcome、AnalysisJob、顺序扫描 |
| 新建 `src/flir_seq_tool/measurement_export.py` | 固定 CSV 模式、PNG 写出、单文件原子发布 |
| 修改 `src/flir_seq_tool/viewer.py` | 会话接线、确认范围、应用变换、两种保存入口和后台轮询 |
| 修改 `src/flir_seq_tool/viewer_layout.py` | 为顶部导航预留空间，定义 Measure 布局和 renderer 边界 |
| 修改 `src/flir_seq_tool/selection.py` | v3，兼容 v1/v2，测量设置完整校验 |
| 修改 `src/flir_seq_tool/export_bundle.py` | 子文件配套文档携带冻结的 measurement |
| 修改 `src/flir_seq_tool/selection_dialogs.py` | 分析 CSV/PNG 文件选择，复用主线程原生弹窗 |
| 新建 `tests/unit/conftest.py` | 小型合成 reader、会话及鼠标事件 fixture |
| 新建对应 `tests/unit/test_measurement_*.py`、`test_viewer_pages.py` | 数值、状态、后台、真实事件和安全测试 |
| 新建 `tests/integration/test_small_seq_measure.py` | 最小真实 SEQ 的测量与输出验证 |
| 修改既有 viewer/selection/export/layout/dialogs 测试 | 保留旧功能，增补 v3 和页面生命周期 |
| 修改 `.gitignore`、`README.md`、`CHANGELOG.md`、`docs/KNOWN_ISSUES.md` | 忽略 analysis 输出、操作说明、实际验证和人工验收 |

依赖方向：`measurement_models → measurement_stats/measurement_state`；selection 仅依赖 models；job 依赖 models、stats、store、现有 selection/reader/transformer；UI 依赖这些服务，服务不导入 UI。避免 selection 与 ViewerSession 循环导入。

## 任务 1：矩形模型与当前帧统计

**文件：** 新建 `measurement_models.py`、`measurement_stats.py`、`tests/unit/test_measurement_stats.py`。

- [ ] **1.1 写失败测试：** 测试文件 imports 为 `numpy as np`、`pytest`、`Roi` 和 `summarize`；加入以下用例。

```python
def test_statistics_use_finite_pixels_and_population_std():
    roi = Roi("r1", "ROI 1", 0, 0, 2, 2, "#1f77b4", True)
    values = np.array([[1.0, 3.0], [np.nan, np.inf]])
    result = summarize(values, roi)
    assert (result.mean_c, result.min_c, result.max_c) == (2.0, 1.0, 3.0)
    assert (result.delta_c, result.std_c) == (2.0, 1.0)
    assert (result.valid_pixels, result.total_pixels) == (2, 4)

def test_all_invalid_is_missing_not_zero():
    roi = Roi("r1", "ROI 1", 0, 0, 1, 1, "#1f77b4", True)
    result = summarize(np.array([[np.nan]]), roi)
    assert result.mean_c is None
    assert result.delta_c is None
    assert result.valid_pixels == 0

def test_outside_roi_is_rejected_not_clipped():
    roi = Roi("r1", "ROI 1", 1, 0, 2, 1, "#1f77b4", True)
    with pytest.raises(ValueError, match="bounds"):
        summarize(np.ones((2, 2)), roi)
```

- [ ] **1.2 运行红灯：** `python -m pytest tests/unit/test_measurement_stats.py -q -p no:cacheprovider`。预期新增模块/接口缺失；确认不是 SDK 导入或 fixture 故障。
- [ ] **1.3 最小实现：** 定义 frozen dataclass `Roi(id, name, x, y, width, height, color, checked)` 和 `FrameStats(mean_c, min_c, max_c, delta_c, std_c, valid_pixels, total_pixels)`。Roi 构造时拒绝空 ID/空白名称、bool 伪装整数、负坐标、非正尺寸、非 bool checked；颜色限定 `#RRGGBB`。`validate_bounds(width,height)` 拒绝越界。在 stats 中实现：

```python
def roi_values(values, roi):
    array = np.asarray(values)
    if array.ndim != 2 or not np.issubdtype(array.dtype, np.number):
        raise ValueError("temperature must be a numeric 2-D array")
    if np.iscomplexobj(array):
        raise ValueError("temperature must be real")
    roi.validate_bounds(array.shape[1], array.shape[0])
    return np.asarray(array[roi.y:roi.y + roi.height,
                            roi.x:roi.x + roi.width], dtype=np.float64)

def summarize(values, roi):
    block = roi_values(values, roi)
    finite = block[np.isfinite(block)]
    count = int(finite.size)
    if not count:
        return FrameStats(None, None, None, None, None, 0, int(block.size))
    low, high = float(finite.min()), float(finite.max())
    return FrameStats(float(finite.mean()), low, high, high - low,
                      float(finite.std(ddof=0)), count, int(block.size))
```

imports：models 使用 dataclasses、re；stats 使用 NumPy 及本地 FrameStats。将类型/范围校验写入构造器，不依赖 GUI 防止非法调用。

- [ ] **1.4 扩展并验证：** 单像素 std/delta=0、负温度、常值、float32 高基值、小区域、二维以外数组、重叠 ROI、非法名称/颜色/坐标。运行同一命令，预期全部 PASS。
- [ ] **1.5 检查提交：** `git diff --check`；`git add src/flir_seq_tool/measurement_models.py src/flir_seq_tool/measurement_stats.py tests/unit/test_measurement_stats.py`；`git commit -m "feat: add validated ROI temperature statistics"`。

## 任务 2：直方图与平均剖面

**文件：** 修改 `measurement_models.py`、`measurement_stats.py`、`test_measurement_stats.py`。

- [ ] **2.1 写失败测试：** 在已有测试 imports 增加 `histogram`、`profile`。

```python
def test_profiles_average_only_finite_values_along_requested_axis():
    roi = Roi("r1", "ROI 1", 1, 0, 2, 2, "#1f77b4", True)
    values = np.array([[99, 1, np.nan], [99, 3, 5]], dtype=float)
    horizontal = profile(values, roi, "horizontal")
    vertical = profile(values, roi, "vertical")
    np.testing.assert_array_equal(horizontal.positions, [1, 2])
    np.testing.assert_allclose(horizontal.means_c, [2, 5])
    np.testing.assert_array_equal(horizontal.valid_counts, [2, 1])
    np.testing.assert_allclose(vertical.means_c, [1, 4])

def test_constant_histogram_has_32_bins_and_unit_probability():
    roi = Roi("r1", "ROI 1", 0, 0, 2, 2, "#1f77b4", True)
    result = histogram(np.full((2, 2), 20.0), roi)
    assert len(result.counts) == 32
    assert (result.edges[0], result.edges[-1]) == (19.5, 20.5)
    assert result.fractions.sum() == pytest.approx(1.0)
```

- [ ] **2.2 运行红灯：** `python -m pytest tests/unit/test_measurement_stats.py -q -p no:cacheprovider`，确认失败来自缺少剖面/分布行为。
- [ ] **2.3 实现：** `Histogram(edges, counts, fractions, valid_pixels)`、`Profile(direction, positions, means_c, valid_counts)` 为 frozen dataclass，数组结果不作为可修改全局缓存共享。具体数值路径：

```python
def histogram(values, roi):
    block = roi_values(values, roi)
    finite = block[np.isfinite(block)]
    if not finite.size:
        return Histogram(np.array([], float), np.array([], int),
                         np.array([], float), 0)
    low, high = float(finite.min()), float(finite.max())
    if low == high:
        low, high = low - 0.5, high + 0.5
    counts, edges = np.histogram(finite, bins=32, range=(low, high))
    return Histogram(edges, counts, counts / finite.size, int(finite.size))

def profile(values, roi, direction):
    if direction not in {"horizontal", "vertical"}:
        raise ValueError("unknown profile direction")
    block = roi_values(values, roi)
    axis = 0 if direction == "horizontal" else 1
    valid = np.isfinite(block)
    counts = valid.sum(axis=axis)
    sums = np.where(valid, block, 0.0).sum(axis=axis, dtype=np.float64)
    means = np.full(counts.shape, np.nan)
    np.divide(sums, counts, out=means, where=counts > 0)
    start = roi.x if axis == 0 else roi.y
    return Profile(direction, np.arange(start, start + len(means)), means, counts)
```

- [ ] **2.4 验证：** 追加空列/空行断点、全无效分布、箱最右端、单行/单列、坐标起点、纯裁剪/90°/±2.3° 变换后直接矩阵对照。`python -m pytest tests/unit/test_measurement_stats.py tests/unit/test_frame_transformer.py -q -p no:cacheprovider`，预期全通过，无 all-NaN RuntimeWarning。
- [ ] **2.5 提交：** 暂存这三个文件；`git commit -m "feat: add current-frame histogram and mean profiles"`。

## 任务 3：设置模型与 selection v3

**文件：** 修改 `measurement_models.py`、`selection.py`、`export_bundle.py`、`tests/unit/test_selection.py`、`tests/unit/test_export_bundle.py`；新建 `tests/unit/test_measurement_models.py`。

- [ ] **3.1 写失败测试：** 在 `test_selection.py` 中复用其已有 `sample_document`，import dataclasses.replace、Roi、MeasurementSettings。

```python
def test_v3_roundtrip_preserves_measurement(tmp_path):
    roi = Roi("r1", "ROI 1", 1, 2, 3, 4, "#1f77b4", True)
    settings = MeasurementSettings(rois=(roi,), active_roi_id="r1")
    document = replace(sample_document(tmp_path / "source.seq"), measurement=settings)
    encoded = document.to_dict()
    assert encoded["schema_version"] == 3
    assert encoded["measurement"]["coordinate_space"] == "cropped_pixel"
    assert SelectionDocument.from_dict(encoded) == document

def test_v2_reads_as_empty_measurement(tmp_path):
    encoded = sample_document(tmp_path / "source.seq").to_dict()
    encoded["schema_version"] = 2
    encoded.pop("measurement", None)
    loaded = SelectionDocument.from_dict(encoded)
    assert loaded.measurement.rois == ()
    assert loaded.schema_version == 3
```

- [ ] **3.2 红灯命令：** `python -m pytest tests/unit/test_selection.py -k 'v3_roundtrip or v2_reads' -q -p no:cacheprovider`。
- [ ] **3.3 实现模型和版本分支：** `MeasurementSettings` 为 frozen dataclass，默认 `rois=()`、`active_roi_id=None`、`result_view="current"`、`frame_view="distribution"`、`profile_direction="horizontal"`、`curve_metric="mean"`。提供 `to_dict()`、`from_dict(payload)` 和 `validate_bounds(width,height)`；严格字段集合、唯一 ID/名称、合法枚举、存在的 active ID，不导入 Matplotlib。Roi 的颜色使用正则而不是 Matplotlib 色名解析。

版本键集合用以下明确规则替换原 v2 分支：

```python
root_keys = {"schema_version", "source", "selection", "display"}
expected = {
    1: root_keys,
    2: root_keys | {"transform"},
    3: root_keys | {"transform", "measurement"},
}
if schema_version not in expected:
    raise ValueError(f"unsupported schema_version: {schema_version}")
_require_keys(root, expected[schema_version], "root")
```

SelectionDocument 增加 `measurement: MeasurementSettings = field(default_factory=MeasurementSettings)`；SCHEMA_VERSION=3。from_dict 的 v1 仍生成 identity，v2/v3 都解析 transform；只有 v3 解析 measurement，其余使用空默认；构造后校验 ROI 在 transform.crop 的宽高中。build_selection_document 增加可选 measurement 参数，缺省为空。在 `build_clip_document` 调用 builder 时传 `measurement=view_document.measurement`。

- [ ] **3.4 验证：** 更新已有“新保存版本=2”的断言为3，同时保留真正 v1/v2 fixture，不能仅把所有旧测试替换成 v3。补 v1/v2 原文件字节不变、未知键/版本、bool 坐标、重复 ID/名称、非法 active ID、变换后越界、子文件几何不变。`python -m pytest tests/unit/test_measurement_models.py tests/unit/test_selection.py tests/unit/test_export_bundle.py -q -p no:cacheprovider`。
- [ ] **3.5 提交：** 暂存本任务列明文件；`git commit -m "feat: persist ROI setup in selection schema v3"`。

## 任务 4：会话状态、输入签名和两种保存范围

**文件：** 新建 `measurement_state.py`、`tests/unit/test_measurement_state.py`；修改 `viewer.py`、`tests/unit/test_viewer.py`。

- [ ] **4.1 写失败测试：** 放在现有 `test_viewer.py`，复用 `FakeTemperatureReader`、`make_frames`。

```python
def test_measure_scope_ignores_pending_range():
    session = ViewerSession(FakeTemperatureReader(make_frames(5)))
    session.timeline.set_pending(1, 3)
    assert session.measure_source_range() == (0, 4)
    session.timeline.confirm_range()
    assert session.measure_source_range() == (1, 3)

def test_measure_save_does_not_erase_unsaved_pending_range(tmp_path):
    session = ViewerSession(FileBackedFakeReader(make_frames(5), tmp_path / "s.seq"))
    session.timeline.set_pending(1, 3)
    assert session.selection_document(scope="measure").selection.frame_count == 5
    assert session.selection_document(scope="process").selection.frame_count == 3
```

在 state 测试中用 `dataclasses.replace` 比较同一 ROI 的 name/color/checked 改变不会改变 AnalysisKey，而 x 或 width 改变会改变。

- [ ] **4.2 红灯：** `python -m pytest tests/unit/test_viewer.py -k 'measure_scope or measure_save' tests/unit/test_measurement_state.py -q -p no:cacheprovider`。
- [ ] **4.3 实现：** `AnalysisKey` 是 frozen dataclass，字段 source_token（SourceIdentity.to_dict 的规范 JSON）、source_start、source_end、transform_fingerprint、roi_geometry（按 ID 排序的 `(id,x,y,width,height)` 元组）、algorithm_version（固定 `roi-stats-v1`）。不包含名字、颜色、勾选和温标。实现 `MeasureState(settings, result_key=None, result_store=None, status="empty")`，其 `invalidate()` 只标记 stale；`clear()` 清除标注和结果引用，并返回原 result_store 交给调用方 cleanup；`is_current(key)` 同时要求完整结果和相同 key。result_store 在任务8接入，使用 TYPE_CHECKING 类型引用避免提前循环导入；result_path 是由 store.path 派生的只读属性，不单独维护第二份路径状态。状态模型不执行文件IO，调用方必须处理 clear 返回的所有权，不能遗失临时目录引用。

在 ViewerSession 初始化 `self.measurement`；新增以下范围接口，并扩展 selection_document/save_selection 的 scope，缺省 process 兼容旧调用：

```python
def measure_source_range(self):
    return self.timeline.source_start, self.timeline.source_end

def selection_source_range(self, scope):
    if scope == "measure":
        return self.measure_source_range()
    if scope == "process":
        return (self.timeline.source_frame_at(self.timeline.pending_start),
                self.timeline.source_frame_at(self.timeline.pending_end))
    raise ValueError("unknown selection scope")
```

builder 接收 `self.measurement.settings`。脏状态使用两份已保存签名：`process_saved_signature` 针对候选范围/显示/变换；`measure_saved_signature` 针对当前确认范围/显示/变换/完整设置。Measure 保存后仅更新 measure 签名，只有两种范围相同才一并更新 process 签名；Process 保存仅在候选=确认时更新 measure 签名，否则保留 measure 脏状态。`has_unsaved_changes()` 为两者不匹配的并集。首次会话基准在初始化显示温标后建立，加载成功后同时建立；兼容现有 selection_dirty 的显式标记，不能让其屏蔽签名差异。

Confirm/Reset 实际改变确认范围时标记结果失效，未确认拖动不触发。相同变换 Apply 不清 ROI；不同变换在 UI 确认后才替换变换并清 ROI。加载先校验整个文档再原子替换测量设置，清结果、清当前帧分析缓存。

- [ ] **4.4 验证：** 两种保存+取消+覆盖拒绝的完整 IO 测试，加载失败/无变化 Apply/真实变化 Apply 的接受和拒绝，切帧不改变签名。`python -m pytest tests/unit/test_measurement_state.py tests/unit/test_viewer.py tests/unit/test_selection.py -q -p no:cacheprovider`。
- [ ] **4.5 提交：** `git commit -m "feat: track measurement scope and result invalidation"`，仅暂存本任务文件。**检查点 I：** 汇报数据/配置测试，不宣称界面可用。

## 任务 5：双页面导航与输入生命周期

**文件：** 新建 `viewer_pages.py`、`tests/unit/test_viewer_pages.py`；修改 `viewer.py`、`viewer_layout.py`、`tests/unit/test_viewer.py`、`tests/unit/test_viewer_layout.py`。

- [ ] **5.1 写失败测试：** 追加到 `test_viewer.py`，复用现有 `_drag_control` 模拟真实按钮事件。

```python
def test_measure_page_disables_process_range_input():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames(5))))
    try:
        _drag_control(ui, ui.measure_button, 0.5, 0.5)
        assert ui.pages.current == "measure"
        assert not ui.range_slider.active
        assert not ui.range_slider.ax.get_visible()
        _drag_control(ui, ui.process_button, 0.5, 0.5)
        assert ui.pages.current == "process"
        assert ui.range_slider.active
        assert ui.figure.canvas.mouse_grabber is None
    finally:
        plt.close(ui.figure)
```

- [ ] **5.2 红灯：** `python -m pytest tests/unit/test_viewer.py -k measure_page -q -p no:cacheprovider`。
- [ ] **5.3 实现页面管理：** `PageGroup` 管理 `axes/widgets/selectors/texts`、可用性谓词和 disconnect IDs；`PageCoordinator` 提供 `current`、`busy`、`switch(page)`、`set_busy(kind_or_none)`、`dispose()`。kind 只允许 analysis/native_export/analysis_export/None。switch 拒绝 busy 和 editing_transform，不自动 Apply。隐藏逻辑为：

```python
def deactivate_group(group, canvas):
    owned_axes = set(group.axes)
    for widget in group.widgets:
        if hasattr(widget, "stop_typing"):
            widget.stop_typing()
        widget.set_active(False)
        if hasattr(widget, "drag_active"):
            widget.drag_active = False
    for selector in group.selectors:
        selector.set_active(False)
        selector.set_visible(False)
    if canvas.mouse_grabber in owned_axes:
        canvas.release_mouse(canvas.mouse_grabber)
    for artist in (*group.axes, *group.texts):
        artist.set_visible(False)
```

显示时不要对所有控件直接 active=True，应重新结合当前页、单帧范围、草稿有效性和任务状态判定。顶部新增 process_button/measure_button，ViewerUI 暴露 pages 引用供测试；当前帧 image 可复用，但进入 Measure 要改变 Axes 位置，返回 Process 恢复。旧 Process 控件全部归组；当前 slider 采用页面位置映射且只有一个实例。

layout 中顶部预留 y=0.94 至0.99 导航/标题，内容上界0.92；现有 Process 图像与标题整体移到内容区。先提供空 Measure 提示，不添加假计算按钮。缩放提示依据 figure inches 与 DPI 的现有逻辑，保持 F1。

- [ ] **5.4 验证：** 循环切页20次，检查 callback 数量不增长、当前帧一致、隐藏按钮不生效、编辑模式无法切入、单帧返回后 slider 仍 inactive、旧 BUG-005 三项鼠标测试仍通过。运行 viewer/pages/layout 三个测试文件。
- [ ] **5.5 提交：** `git commit -m "feat: add isolated process and measure pages"`。

## 任务 6：多 ROI 编辑与列表

**文件：** 新建 `measurement_panel.py`、`pixel_geometry.py`、`tests/unit/test_measurement_panel.py`、`tests/unit/test_pixel_geometry.py`、`tests/unit/conftest.py`；修改 models/state/viewer/layout 的接线，以及 `transform_editor.py` 的共同取整调用。

- [ ] **6.1 建立测试 fixture 并写红灯：** 在 conftest 定义 `make_measure_ui` factory，使用只读合成 reader：5帧、每帧6×8、温度 `np.arange(48).reshape(6,8)+10*i`、20 ms 时间间隔；带一个 tmp_path 下的假源文件供身份验证。reader 实现 num_frames/width/height/path、temperature_frame_at/timestamp_at 和 context manager；记录 requested_frames。用注入 dialogs 的 confirm 布尔返回控制确认，不弹原生窗口。fixture 用 yield 后 plt.close 清理所有创建窗口。

在 panel 测试定义真实事件 helper：

```python
def send_mouse(ui, name, x, y):
    px, py = ui.axes.transData.transform((x, y))
    event = MouseEvent(name, ui.figure.canvas, px, py, button=1)
    ui.figure.canvas.callbacks.process(name, event)

def test_draw_roi_in_measure_coordinates(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    ui.measure_panel.begin_add()
    ui.figure.canvas.draw()
    send_mouse(ui, "button_press_event", 0.5, 0.5)
    send_mouse(ui, "motion_notify_event", 3.5, 2.5)
    send_mouse(ui, "button_release_event", 3.5, 2.5)
    roi = ui.session.measurement.settings.rois[0]
    assert (roi.x, roi.y, roi.width, roi.height) == (1, 1, 3, 2)
    assert ui.session.measurement.settings.active_roi_id == roi.id
```

imports 为 Matplotlib MouseEvent、pytest、numpy 和本地 models。红灯：`python -m pytest tests/unit/test_measurement_panel.py -q -p no:cacheprovider`。

- [ ] **6.2 实现面板契约：** `build_measure_panel(figure, image_axes, state, on_geometry_change, on_display_change, dialogs)` 返回 `MeasurePanel`；公开 `begin_add()`、`select(roi_id)`、`set_visible(bool)`、`set_busy(bool)`、`dispose()`。创建一个 RectangleSelector；所有非选中 ROI 使用普通 Rectangle artist。面板只通过 state 更改模型，不直接读 SEQ。

像素框转换复用 transform_editor 中已经测试的边界算法，提取到 `pixel_geometry.py`，供裁剪和ROI调用。保留原UI的显示像素最小拖动跨度判断；纯函数不依赖Matplotlib：

```python
def rect_from_extents(extents):
    if len(extents) != 4 or not all(math.isfinite(value) for value in extents):
        raise ValueError("rectangle extents must be finite")
    low_x, high_x = sorted(extents[:2])
    low_y, high_y = sorted(extents[2:])
    if high_x <= low_x or high_y <= low_y:
        raise ValueError("rectangle has zero span")
    tolerance = 1e-9
    left = math.ceil(low_x - tolerance)
    right = math.floor(high_x + 1.0 + tolerance)
    top = math.ceil(low_y - tolerance)
    bottom = math.floor(high_y + 1.0 + tolerance)
    return CropRect(left, top, right - left, bottom - top)
```

该文件 imports math 和现有 CropRect。边界上限由各自调用方校验：ROI使用裁剪后图像宽高，原编辑器继续使用原有旋转有效区校验。禁止零面积单击；画框期间 Esc 取消，数字输入验证失败保持原框。test_pixel_geometry覆盖半像素边界、微小浮点误差、逆向拖动、零跨度和非有限值；原crop测试必须保持通过。

列表每页4项，每项有颜色标记、选择按钮和显示复选框；ID 使用 uuid4().hex，名称按未用编号递增，颜色循环固定十色板并保留名称标识。名称 TextBox 提交后校验唯一；删除先 dialogs.confirm 再替换 tuple，选择相邻项。更新必须避免回调递归：

```python
def replace_roi(settings, updated):
    if not any(item.id == updated.id for item in settings.rois):
        raise ValueError("unknown ROI")
    rois = tuple(updated if item.id == updated.id else item for item in settings.rois)
    return replace(settings, rois=rois)
```

新增/删除/几何变化调用 on_geometry_change；改名/勾选只调用 on_display_change。每个操作在设置整体校验成功后一次替换。所有 numeric TextBox labels 放在框上方；无选中区域时编辑控件禁用。

- [ ] **6.3 几何与工具栏回归：** 创建/重叠/分页/改名/删除/输入越界/1像素框/空点击/Esc；一个活跃 selector；工具栏 `axes.get_navigate_mode()` 非空时停用 selector，解除后恢复。至少一个测试从真实 Add 按钮开始，不能只调用 begin_add。
- [ ] **6.4 绿灯：** `python -m pytest tests/unit/test_measurement_panel.py tests/unit/test_pixel_geometry.py tests/unit/test_transform_editor.py tests/unit/test_viewer.py -q -p no:cacheprovider`。
- [ ] **6.5 提交：** `git commit -m "feat: add multi-ROI editing in measure page"`。

## 任务 7：当前帧卡片、直方图与剖面

**文件：** 新建 `measurement_plots.py`、`tests/unit/test_measurement_plots.py`；修改 panel/viewer/layout 及对应测试。

- [ ] **7.1 写失败测试：** 使用任务6 fixture，直接建立 ROI 设置后选择其ID；当前帧分析接口由本任务引入。

```python
def test_current_analysis_reads_one_frame_and_has_correct_profile(make_measure_ui):
    ui = make_measure_ui()
    roi = Roi("r1", "ROI 1", 0, 0, 2, 2, "#1f77b4", True)
    ui.session.measurement.settings = MeasurementSettings(rois=(roi,), active_roi_id="r1")
    ui.pages.switch("measure")
    ui.measure_panel.refresh_frame(ui.session.frame_at_local(0))
    current = ui.measure_panel.current_analysis
    assert current.stats.mean_c == pytest.approx(4.5)
    np.testing.assert_allclose(current.horizontal.means_c, [4, 5])
    np.testing.assert_allclose(current.vertical.means_c, [0.5, 8.5])
    assert ui.session.reader.requested_frames.count(0) == 1
```

- [ ] **7.2 红灯：** 运行 panel/plots 新用例，预期缺少 current_analysis 或绘图接口。
- [ ] **7.3 实现：** models 增加 `CurrentAnalysis(source_frame, roi_id, stats, histogram, horizontal, vertical)`；panel.refresh_frame 接收已变换 TemperatureFrame，不再向 reader 取帧。viewer.show_local 在完成图像更新后把同一个 frame 传入当前可见 Measure；ROI 编辑仅复用该 frame。卡片可见显示 mean/min/max/delta/std、有效像素比例和重采样提示，None 显示 N/A。

绘图纯函数 `draw_distribution(ax,result)`、`draw_profile(ax,result)` 清当前 axes artists，不重建 canvas 或重复连接事件。核心坐标如下：

```python
def draw_distribution(ax, result):
    ax.clear()
    ax.set(xlabel="Temperature (°C)", ylabel="Valid pixel fraction")
    if not result.valid_pixels:
        ax.text(0.5, 0.5, "No finite pixels", transform=ax.transAxes, ha="center")
        return
    ax.bar(result.edges[:-1], result.fractions, width=np.diff(result.edges), align="edge")
    ax.set_title("Current-frame automatic bin range")

def draw_profile(ax, result):
    ax.clear()
    ax.plot(result.positions, result.means_c, marker="o" if len(result.positions) == 1 else None)
    label = "x (cropped pixel, rightward)" if result.direction == "horizontal" else "y (cropped pixel, downward)"
    ax.set(xlabel=label, ylabel="Mean temperature (°C)")
```

refresh 的拖动请求使用 figure.canvas.new_timer(interval=100) 合并最新框；鼠标释放立即调用，隐藏页面停止该 timer。子视图隐藏时同步停用控件。统计卡片不随子视图隐藏。

- [ ] **7.4 绿灯：** 空选区/全NaN、切帧后剖面、同帧重复刷新缓存、秒级快速拖动合并、释放最终状态、两种Profile方向、切页无额外scan。运行数值、panel、plots、viewer测试。
- [ ] **7.5 提交：** `git commit -m "feat: show current-frame ROI distributions and profiles"`。**检查点 II：** 给用户原CLI启动命令，明确全段 Calculate/导出尚未接通，不显示可点但无效果的按钮。

## 任务 8：磁盘结果表与有界曲线读取

**文件：** 新建 `measurement_store.py`、`tests/unit/test_measurement_store.py`。

- [ ] **8.1 失败测试：** imports sqlite3、pytest、ResultStore；写入按此任务定义的行字典。

```python
def test_store_preserves_complete_rows_and_missing_values(tmp_path):
    store = ResultStore.create(tmp_path)
    row = dict(roi_id="r1", local_frame=0, source_frame=10,
               timestamp="2026-08-28T09:26:37.152000", relative_s=0.0,
               mean_c=None, min_c=None, max_c=None, delta_c=None, std_c=None,
               valid_pixels=0, total_pixels=4)
    store.append_rows([row])
    store.complete(expected_rows=1, key_json="key")
    assert list(store.iter_rows())[0] == row
    location = store.path
    store.cleanup()
    assert not location.exists()
```

- [ ] **8.2 红灯：** `python -m pytest tests/unit/test_measurement_store.py -q -p no:cacheprovider`。
- [ ] **8.3 实现 SQLite 模式：** ResultStore.create(parent=None) 通过 TemporaryDirectory 创建独占子目录，仅管理其中 `results.sqlite3`；path 指向此文件。每个线程创建自己的连接，不跨线程传 sqlite connection。存储 schema：

```sql
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE samples (
    roi_id TEXT NOT NULL, local_frame INTEGER NOT NULL,
    source_frame INTEGER NOT NULL, timestamp TEXT NOT NULL, relative_s REAL NOT NULL,
    mean_c REAL, min_c REAL, max_c REAL, delta_c REAL, std_c REAL,
    valid_pixels INTEGER NOT NULL, total_pixels INTEGER NOT NULL,
    PRIMARY KEY (roi_id, local_frame)
);
```

提供 `append_rows(rows)` 参数化 executemany；`complete(expected_rows,key_json)` 在事务中核对COUNT再写 complete=1；`iter_rows(roi_ids=None, chunk_size=1024)` 使用 `ORDER BY local_frame, roi_id` 和 fetchmany，不用fetchall，曲线单ROI读取用参数化WHERE限定；`close()` 关闭连接；`cleanup()` 先close再清理自己TemporaryDirectory；`open_readonly(path)` 仅在metadata complete=1时打开、无清理所有权。工作线程的store对象只在关闭写连接后转交所有权，UI通过独立只读连接使用；cleanup不能删除任意传入父目录。

曲线接口 `iter_plot_segments(roi_id, metric, start, end, bucket_count=2000)` 只允许 mean_c/min_c/max_c/delta_c/std_c 白名单。按帧区间等分桶，顺序读取完整行但只保留每桶端点和极值，输出每段的 `(x_array,y_array)`。全有效桶取首/末/最小/最大去重后按帧排序；若桶内存在缺失，不将其两侧有限点连线，该桶代表点分别为单点段。相邻桶仅当交界及桶内部均无缺失时连接。这样不会跨NaN制造连续曲线，段数与桶数有界；极值时间顺序不能倒置。空桶不连接，单帧显示单点；图注明抽稀。

- [ ] **8.4 验证：** >10000帧含尖峰和NaN序列，导出迭代行数完整、plot代表点上界不超过8×桶数、不跨NaN连接、严格源帧排序；重复主键/少行complete/磁盘错误不发布；readonly连接先关闭再cleanup的Windows行为。运行本文件，预期全部PASS。
- [ ] **8.5 提交：** `git commit -m "feat: store measurement results with bounded plot reads"`。

## 任务 9：只读后台逐帧统计任务

**文件：** 新建 `measurement_job.py`、`tests/unit/test_measurement_job.py`；修改 state 的结果发布接口。

- [ ] **9.1 失败测试：** 在本测试文件定义 FakeReader，context manager记录进入/退出线程ID；两帧温度分别为 `[[1,3],[5,7]]`、该矩阵+10；时间为START和START+20ms。源身份 fixture 使用tmp_path普通文件及对应2×2尺寸构造SourceIdentity；reader_factory参数返回此FakeReader，禁止真实SDK。测试入口 `run_analysis(request, reader_factory, cancel_event, progress, temp_root)`。

```python
def test_cancel_before_start_creates_no_result(analysis_request, fake_reader_factory, tmp_path):
    cancel = threading.Event()
    cancel.set()
    outcome = run_analysis(analysis_request, reader_factory=fake_reader_factory,
                           cancel_event=cancel, progress=AnalysisProgress(), temp_root=tmp_path)
    assert outcome.status == "cancelled"
    assert outcome.store is None
    assert not list(tmp_path.glob("flir-measure-*"))
```

analysis_request fixture 返回下述 AnalysisRequest；fake_reader_factory 自身记录温度读取次数供后续测试断言，不mock统计函数。

- [ ] **9.2 红灯：** `python -m pytest tests/unit/test_measurement_job.py -q -p no:cacheprovider`。
- [ ] **9.3 实现冻结请求与扫描：** `AnalysisRequest(source_path, source_identity, source_start, source_end, transform, rois, key)` 为 frozen dataclass；校验非空ROI、范围、几何，key与实际字段匹配。AnalysisProgress 使用 Lock 存 `(completed,total,message)` 并 snapshot；AnalysisOutcome(status,store,key,error) 只在status=complete时有store。AnalysisJob 封装非daemon线程、start/cancel/is_alive/join/outcome；cancel仅设置Event。

单线程扫描的实现核心如下，函数 `run_analysis` 外层捕获 `AnalysisCancelled` 并返回cancelled，其他Exception返回failed且记录当前source_frame；finally关闭失败/取消store，仅complete时交出所有权：

```python
transformer = FrameTransformer(request.transform)
first_time = None
with reader_factory(request.source_path) as reader:
    validate_reader_identity(reader, request.source_path, request.source_identity)
    for local_frame, source_frame in enumerate(range(request.source_start, request.source_end + 1)):
        if cancel_event.is_set():
            raise AnalysisCancelled()
        frame = reader.temperature_frame_at(source_frame)
        values = transformer.apply(frame.temperature_c)
        if first_time is None:
            first_time = frame.timestamp
        rows = []
        for roi in request.rois:
            stats = summarize(values, roi)
            rows.append(dict(roi_id=roi.id, local_frame=local_frame,
                             source_frame=source_frame, timestamp=frame.timestamp.isoformat(),
                             relative_s=(frame.timestamp - first_time).total_seconds(),
                             **asdict(stats)))
        store.append_rows(rows)
        progress.update(local_frame + 1, request.source_end - request.source_start + 1)
    validate_reader_identity(reader, request.source_path, request.source_identity)
if cancel_event.is_set():
    raise AnalysisCancelled()
store.complete(expected_rows=(request.source_end - request.source_start + 1) * len(request.rois),
               key_json=request.key.to_json())
store.close()
```

`validate_reader_identity(reader,path,expected)` 使用已打开reader的width/height/num_frames/首末timestamp与path.stat构造SourceIdentity，调用既有validate_source_identity；不重新打开UI reader。AnalysisKey.to_json规范json，禁止NaN。append_rows每帧commit可以先正确实现，再以有界256帧批次优化；不得囤积整段矩阵。

- [ ] **9.4 验证：** 完整2帧均温4/14、每帧温差6、每帧恰好一次temperature_frame_at、双ROI不增加帧读取；读帧失败/中途取消/发布前取消/源mtime变化/SQLite写入失败均无complete结果；线程归属和reader关闭顺序；旧有效store不得由新任务失败清除。
- [ ] **9.5 绿灯与提交：** 运行job/store/stats测试；`git commit -m "feat: calculate ROI time series in a read-only worker"`。

## 任务 10：Calculate、曲线与任务互斥接入

**文件：** 修改 viewer、panel、plots、pages、state；新建 `tests/unit/test_measurement_workflow.py`。

- [ ] **10.1 失败测试：** 使用make_measure_ui，注入 `job_factory` 返回事件门控制的AnalysisJob，不使用sleep猜完成时间。fixture只控制reader阻塞点，完整扫描用真stats/store。

```python
def test_analysis_locks_inputs_until_worker_releases_reader(make_measure_ui):
    ui = make_measure_ui()
    ui.pages.switch("measure")
    ui.measure_panel.begin_add()
    assert ui.pages.busy is None
    assert ui.measure_panel.calculate_button.active is False
```

该最小用例后补：创建一个合法ROI→真实点击Calculate→pages.busy为analysis→slider/导航/保存/导出/ROI编辑都inactive→释放reader事件并join→主线程poll_analysis→恢复各自可用性，结果complete；单帧slider仍inactive。避免仅检查不存在控件作为全部证据。

- [ ] **10.2 红灯：** `python -m pytest tests/unit/test_measurement_workflow.py -q -p no:cacheprovider`。
- [ ] **10.3 实现接线：** ViewerUI增加poll_analysis、analysis_job、measure_panel；Calculate从session.measure_source_range和applied_transform构造冻结request，保存old_result引用，设置pages.busy=analysis。timer只在主线程轮询；先确认job结束、结果完整且key仍匹配，再发布并清理被替换oldstore。失败/取消保留旧key匹配的结果，否则stale。最终恢复所有控件由统一可用性函数计算，不手动全开。

plots新增 `draw_frame_curves(temp_ax,delta_ax,store,settings,current_local,frame_count)`：对勾选ROI调用iter_plot_segments；temperature metric为settings.curve_metric+`_c`，下图delta_c；同一ROI颜色一致，段间不重复图例；单点段marker="o"。分别绘制当前帧竖线，图例来自当前名称，xlim对单帧扩展±0.5。未计算/失效/all unchecked各自显示明确提示且禁用曲线导出。

主线程关闭UI时先取消任务、停用新的输入与定时访问；worker结束后清理owned结果。在launch_viewer的finally中也执行取消+join+cleanup兜底，保证即使后端已发close_event也不访问已销毁的figure。不在GUI回调里长期阻塞join；终端退出兜底允许等待当前SDK调用结束。

- [ ] **10.4 验证：** 计算期间试图切页、改范围、导出、加载均拒绝；结果完成后改名/勾选不扫描、改ROI几何标stale；切帧竖线正确；native_export与analysis双向互斥；close时无悬挂回调/文件句柄；重复Calculate不泄漏临时目录。
- [ ] **10.5 提交：** `git commit -m "feat: connect measurement jobs and multi-ROI frame curves"`。**检查点 III：** 用户可测试完整曲线和取消，分析文件导出尚待下一阶段。

## 任务 11：CSV/PNG 输出与安全发布

**文件：** 新建 `measurement_export.py`、`tests/unit/test_measurement_export.py`；修改 plots、selection_dialogs及其测试。

- [ ] **11.1 先写安全发布失败测试：** imports pathlib.Path、pytest、TargetSnapshot、publish_analysis_file。

```python
def test_external_change_after_confirmation_is_not_overwritten(tmp_path):
    source = tmp_path / "source.seq"
    source.write_bytes(b"source")
    target = tmp_path / "data.csv"
    target.write_text("old", encoding="utf-8")
    expected = TargetSnapshot.capture(target)
    target.write_text("external update", encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed"):
        publish_analysis_file(target, source_path=source, raw_root=tmp_path / "raw",
                              expected=expected,
                              writer=lambda path: path.write_text("new", encoding="utf-8"),
                              validator=lambda path: None)
    assert target.read_text(encoding="utf-8") == "external update"
```

writer/validator替身仅用于验证发布机制；CSV和PNG测试必须真实写入并重新解析，不只assert mock called。

- [ ] **11.2 红灯：** `python -m pytest tests/unit/test_measurement_export.py -q -p no:cacheprovider`。
- [ ] **11.3 实现原子单文件输出：** publish_analysis_file 返回正式Path，校验target后才mkdir，snapshot核验在写入前与发布前都做。用mkstemp获取独占临时文件，关闭fd后writer写入；validator成功后再校验路径和snapshot，最后os.replace。finally仅清理由本调用创建的临时文件；不得因异常删除外部目标。沿用file_safety防raw/源/别名，后缀限定.csv/.png，由输出入口校验。不宣称stat检查消除了所有跨进程竞态。

冻结 `AnalysisExportSnapshot`（frozen dataclass）字段：source_identity、source_path、source_range、work_start_timestamp、transform_json、display（现有DisplaySettings，包含色图和clim）、settings、current_source_frame、current_local_frame、timestamp、current_values（copy，单帧）、current_analysis、result_store_path（可空）、algorithm_version=`roi-stats-v1`、format_version=`analysis-v1`。输出类型由 settings.result_view/frame_view/profile_direction 确定；当前帧导出的 relative_s 使用 timestamp 减 work_start_timestamp，而不是减完整源文件首帧时间。捕获后禁用编辑和重复导出；current/frame图由快照绘制，不从活动GUI重新取值。

CSV列分为固定公共列与各类型固定列：

```python
COMMON_COLUMNS = (
    "format_version", "algorithm_version", "source_identity_json", "transform_json",
    "source_start", "source_end", "roi_id", "roi_name", "roi_x", "roi_y",
    "roi_width", "roi_height", "source_frame", "local_frame", "timestamp", "relative_s",
)
STATS_COLUMNS = ("mean_c", "min_c", "max_c", "delta_c", "std_c", "valid_pixels", "total_pixels", "status")
HIST_COLUMNS = ("bin_left_c", "bin_right_c", "bin_count", "bin_fraction")
PROFILE_COLUMNS = ("direction", "position_pixel", "position_mean_c", "position_valid_pixels")
```

curves列为COMMON+STATS；distribution再加HIST；profile再加PROFILE。全部无效分布输出1行status=no_finite_pixels、空箱字段，不能只输出表头而丢失缺失信息。曲线按local_frame、ROI显示顺序输出全部原始统计：流式读store，仅在当前一帧的至多R行中按ROI显示顺序排序，禁止对完整结果执行list/sorted。不能从plot抽稀数组导出。SourceIdentity/transform用json.dumps(allow_nan=False)；数值None为空，不对正常数字截断到2位。

用户名称的公式防护只处理文本：

```python
def spreadsheet_text(value):
    text = str(value)
    probe = text.lstrip()
    if text.startswith(("\t", "\r", "\n")) or probe.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text
```

csv.DictWriter使用encoding="utf-8-sig"、newline=""，固定fieldnames。校验器重新以DictReader流式数行，核对表头与预期数，失败不发布。

plots新增 `build_report_figure(snapshot)`，无UI控件，左/上画快照温度场、固定clim与ROI，另一侧画当前直方图/剖面或双曲线；标题带源文件、帧范围、ROI及单位。使用Figure/FigureCanvasAgg在主线程生成PNG，写后用Matplotlib/Pillow已有解码能力验证PNG格式和宽高非零；close自建figure，不影响viewer。PNG注明抽稀只影响曲线显示。

- [ ] **11.4 绿灯：** 文件选择取消、非法后缀、覆盖拒绝、raw路径/源别名、临时写失败、解析失败、发布前外部修改、Unicode/逗号/引号/公式文本、全NaN、单帧/大结果CSV流式写。PNG真实解码并检查不包含GUI按钮。运行export/plots/dialogs/file_safety测试。
- [ ] **11.5 提交：** `git commit -m "feat: export traceable measurement CSV and PNG safely"`。

## 任务 12：保存与导出按钮、布局和回归收口

**文件：** 修改 viewer/panel/dialogs/layout、`.gitignore`、对应unit测试。

- [ ] **12.1 写失败测试：** 扩展FakeSelectionDialogs提供 `choose_analysis_export(kind, initial_dir, suggested_name)`，返回预先指定Path或None；错误后端通过已有_run注入异常。添加Measure保存真实IO测试，确保pending不同于work时Save setup仍保存work，加载回到Process可恢复ROI。

```python
def test_save_setup_uses_confirmed_scope(make_measure_ui, tmp_path):
    ui = make_measure_ui()
    ui.session.timeline.set_pending(1, 3)
    document = ui.session.selection_document(scope="measure")
    assert document.selection.source_start_frame == 0
    assert document.selection.source_end_frame == 4
    assert ui.session.selection_document(scope="process").selection.source_start_frame == 1
```

该模型测试以外，必须用真实按钮事件触发原生适配层替身，检查输出文件、target/.bak快照、取消和脏状态。

- [ ] **12.2 红灯：** `python -m pytest tests/unit/test_measurement_workflow.py tests/unit/test_selection_dialogs.py -q -p no:cacheprovider`。
- [ ] **12.3 接入按钮：** panel的Save setup走session.save_selection(scope="measure")，Process旧入口默认process。Export CSV/PNG先冻结snapshot，再通过dialogs选路径、捕获TargetSnapshot、询问覆盖，然后发布；无有效结果时按钮禁用且回调内也检查。后端异常进入统一F1详情，不静默另存默认路径。`.gitignore`追加 `analysis/`，不创建或清空任何既有输出目录。

页面布局约束：固定公共导航与状态区，Measure上部热图/右栏、共享帧控件、下部结果、底部输出按钮分区，所有TextBox标签在其自身区域内。用renderer真实文字bbox测试，而不仅测Axes矩形。加3种DPI×最小/放大两种尺寸×两页与所有子视图。长ROI名称只缩略显示，不改变保存值；4行列表翻页保留选中/勾选状态。

- [ ] **12.4 回归：** 运行全部unit测试，补所有页面切换实际鼠标/键盘事件、工具栏互斥、窗口resize和F1；现有导出确认/取消门、原selection保护、BUG-005及帧滑块测试必须仍通过。
- [ ] **12.5 提交：** `git commit -m "feat: complete measure setup and export workflows"`。

## 任务 13：最小真实 SEQ、文档与交付验收

**文件：** 新建 `tests/integration/test_small_seq_measure.py`；修改README/CHANGELOG/KNOWN_ISSUES，不更改原始SEQ。

- [ ] **13.1 先添加真实集成断言：** 复用integration/conftest.py的raw前后stat保护及禁止原生弹窗fixture。仅打开 `raw/Rec-000366-240_09_26_37_152.seq`，确认源帧10–14、应用-2.3°有效默认裁剪，在裁剪后图像内建立2个不越界ROI。

测试需要直接数值对照而非只验证文件存在：

```python
values = session.frame_at_local(0).temperature_c
block = values[roi.y:roi.y + roi.height, roi.x:roi.x + roi.width]
finite = block[np.isfinite(block)].astype(np.float64)
actual = summarize(values, roi)
assert actual.mean_c == pytest.approx(float(finite.mean()), rel=1e-6, abs=1e-4)
assert actual.delta_c == pytest.approx(float(finite.max() - finite.min()), rel=1e-6, abs=1e-4)
```

所有变量在测试中通过任务1/4接口构造；同时运行AnalysisJob并对源帧10/12/14的完整store行与即时计算比较。保存v3重开ROI一致，导出CSV总行数=5×2、真实时间对应、PNG可解码；Process导出5帧子SEQ后加载配套配置，ROI坐标相同、结果为空待重算。输出仅tmp_path。

- [ ] **13.2 红灯/绿灯：** 先运行新增integration测试查真实连接缺口，再最小修正；不得把SDK不匹配误归为逻辑失败。`python -m pytest tests/integration/test_small_seq_measure.py tests/integration/test_small_seq_extract.py tests/integration/test_small_seq_viewer.py -q -p no:cacheprovider`。
- [ ] **13.3 完整回归并记录元数据：**

```powershell
function Get-SeqRawMetadata {
    Get-ChildItem -LiteralPath 'D:\carbonfoam\infrared\raw' -Filter '*.seq' -File |
        Sort-Object Name |
        Select-Object Name,Length,@{Name='MtimeTicks';Expression={$_.LastWriteTimeUtc.Ticks}} |
        ConvertTo-Json -Compress
}
$seqBefore = Get-SeqRawMetadata
try {
    python -m pytest -q -rs -p no:cacheprovider
    $seqTestExit = $LASTEXITCODE
}
finally {
    $seqAfter = Get-SeqRawMetadata
    if ($seqBefore -ne $seqAfter) { throw 'Raw SEQ metadata changed' }
    Write-Output $seqAfter
}
exit $seqTestExit
```

预期无失败。既有符号链接权限skip必须报告原因；不预先填写新测试总数。原始文件参考大小为42873727、1518245059、7625825755 bytes，仍以前后实际快照比较为准。

- [ ] **13.4 文档与人工验收：** README说明Process/Measure启动不变、ROI/两个分析视图、Calculate锁定和取消、两种保存范围、v3兼容和导出。KNOWN_ISSUES把自动验证与待用户窗口验收分开；注明插值温度、ddof=0、当前帧直方图自动箱、像素距离和曲线抽稀。CHANGELOG记Unreleased，不擅自升级包版本、打标签或创建Release。
- [ ] **13.5 本地提交：** `git diff --check`，暂存新增集成测试和三份文档；`git commit -m "test: verify measure workflow on a read-only real SEQ"`。
- [ ] **13.6 交付检查点 IV：** 提供启动命令，按设计第10.4节引导用户检查三个ROI、卡片/直方图/双向剖面、Calculate、显示勾选、修改后失效、保存加载、CSV/PNG、回Process变换确认。实际大文件由用户验证；未经人工确认不标其通过。不要自动合并或push。

## 覆盖自查与执行记录规则

| 设计内容 | 计划任务 |
| --- | --- |
| 单窗口导航、已确认范围、已应用变换 | 4、5、12 |
| 多ROI、单活跃selector、分页、编辑/勾选分离 | 1、6 |
| 五种统计、NaN/Inf、总体标准差 | 1、2、7 |
| 直方图、水平/竖直剖面、单帧和缺失 | 2、7、11 |
| 手动Calculate、所有ROI扫描、两张曲线 | 8、9、10 |
| 状态失效、旧结果保护、源身份变化 | 4、9、10 |
| 有界矩阵/结果内存、完整CSV与抽稀图 | 8、9、11 |
| v1/v2/v3、两种保存、子SEQ配套ROI | 3、4、12、13 |
| 原生弹窗、安全单文件输出、公式注入 | 11、12 |
| 输入锁、关闭/取消、真实事件、DPI | 5、6、10、12 |
| 最小真实SEQ只读、用户大文件验收 | 13 |

本计划只完成文字级覆盖和接口一致性检查；代码块尚未作为项目实现运行。执行时每步记录红灯的具体失败、绿灯命令与结果、提交ID；不能用计划示例数字代替实际测试证据。

## 执行方式交接

计划完成后等待用户选择：

1. 子代理分任务实施并逐任务审查，使用 `superpowers:subagent-driven-development`。
2. 当前会话顺序实施、按上述四阶段检查点汇报，使用 `superpowers:executing-plans`；这与用户此前的执行偏好一致。

未选择或未明确要求开始实现前，只交付计划，不修改功能代码。
