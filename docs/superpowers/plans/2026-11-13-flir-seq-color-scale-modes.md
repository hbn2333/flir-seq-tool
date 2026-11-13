# FLIR SEQ 固定温标与逐帧自适应温标实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 默认通过后台完整扫描生成覆盖已确认、已旋转裁剪视图的固定温标，支持手动上下限和逐帧自适应，并使 selection、PNG、MP4 与实际界面一致。

**Architecture:** 数值极值、持久配置、运行状态、扫描 worker、调度控制器及 UI 分开。`ViewerSession` 持有温标状态和扫描控制器，UI 负责绑定控件及主线程 timer，窗口事务提交后才激活扫描。复用已有 reader、变换、文件身份、导出快照和资源回收流程。

**Tech Stack:** Windows x64、Python 3.13.5、官方 FLIR Science File SDK 2026.1.2、NumPy、Matplotlib/TkAgg、threading、现有 FFmpeg/PyInstaller 与 pytest。

---

## 依据、边界与执行顺序

日期为 2026-11-13，Asia/Shanghai。用户已批准 [设计文档](../specs/2026-11-13-flir-seq-color-scale-modes-design.md)。功能代码基线为 `c49858b`，设计提交为 `a582375`。当前分支为 `codex/color-scale-modes-design`，继续在此分支完成计划及后续实施，合并和推送按用户后续指令执行。

唯一工作目录是 `D:\carbonfoam\infrared`。下列文件表中的路径相对此目录，PowerShell 命令在此目录执行。沿用现有分支和单一工作目录，原始数据始终只读。使用项目内隔离环境，不运行全局 pip、修改系统环境或安装程序。每次临时任务目录使用独立名称，已有 bundle 和用户导出保留。

任务按 1→2→3→4→5→6→7→8→9→10 顺序集成。任务 3 可在任务 2 完成前编写纯 worker 测试；任务 7 与 8 在公共状态接口完成后可分别实现，但不得并行改动同一 `viewer.py`。每项完成后先检查规格符合性，再检查正确性与维护性。

本计划只记录待执行步骤，所有复选框初始为空。执行记录存入 `docs/superpowers/plans/2026-11-13-flir-seq-color-scale-modes-execution.md`，按实际命令输出填写结果与限制。

## 环境与验证命令

每个新 PowerShell 进程先执行以下设置。`Set-BuildEnvironment.ps1` 只配置当前进程和子进程，项目内缓存保持在 `.packaging`。

```powershell
Set-Location -LiteralPath D:\carbonfoam\infrared
. .\packaging\Set-BuildEnvironment.ps1
$env:PATH = (Join-Path $projectRoot 'dist/portable/slider-performance-20261105/FLIR_SEQ_Tool/tools') + ';' + $env:PATH
```

各任务给出目标测试命令。真实样本通过 `ScienceFileReader` 读取，输出使用 pytest 的项目内 `--basetemp`。Agg 单测直接调用 `poll()`，不依赖 GUI timer 自动运行。原生 UI 点验最终在新 EXE 进行。

## 文件职责与接口

| 文件 | 工作 |
| --- | --- |
| `src/flir_seq_tool/color_scale.py` | 保留旧百分位 helper，新增有限原始极值与显示范围函数 |
| 新 `src/flir_seq_tool/color_scale_models.py` | 自动扫描输入范围标识 `ColorScaleScope`，不引入 reader 或 UI 依赖 |
| `src/flir_seq_tool/selection.py` | `DisplaySettings` 增加模式、固定策略和完成范围标识，selection v5 迁移 |
| 新 `src/flir_seq_tool/color_scale_job.py` | 冻结扫描输入、独立 reader、取消、进度、只在完整成功时返回极值 |
| 新 `src/flir_seq_tool/color_scale_state.py` | 固定值、当前显示值、自动缓存、输入草稿、编辑冻结和选择配置快照 |
| 新 `src/flir_seq_tool/color_scale_controller.py` | 每会话一个 worker、请求代次、暂停恢复、重任务互斥和结果接收 |
| 新 `src/flir_seq_tool/color_scale_panel.py` | 共用温标控件、模式按钮、手动 Apply、Auto range、进度与取消 |
| `src/flir_seq_tool/viewer.py` | 会话接线、显示归一化、确认范围、变换编辑、保存、导出入口保护 |
| `src/flir_seq_tool/viewer_window.py` | 候选事务、提交后激活、回退恢复和最终 worker 回收 |
| `src/flir_seq_tool/viewer_layout.py` | 温标控件和文本区域布局，适配现有三个页面状态 |
| `src/flir_seq_tool/measurement_panel.py` | 按导出类型区分 PNG 与全量 CSV 的入口可用性 |
| `src/flir_seq_tool/export_bundle.py` | 子 SEQ 配套配置清除父自动范围标识，保留模式、手动值及来源链 |
| `src/flir_seq_tool/measurement_export.py`、`measurement_plots.py` | PNG 的实际温标快照及模式/临时状态标识 |
| `src/flir_seq_tool/video_export.py`、`video_renderer.py`、`video_controller.py` | 固定/自适应请求冻结、摘要、逐帧色条与取消保护 |
| `src/flir_seq_tool/portable_diagnostics.py` | 新自动扫描和自适应模式冻结诊断，沿用其他既有检查 |
| `README.md`、`CHANGELOG.md` | 使用步骤、全局范围含义、旧配置恢复及当前源码/EXE 状态 |

公共契约在任务实现时按下表建立，之后任务使用同名接口。

| 接口 | 参数和结果 |
| --- | --- |
| `finite_extrema(values)` | 返回未扩展的 `(minimum, maximum)`，全无效返回 `None` |
| `limits_from_extrema(minimum, maximum)` | 返回 `ColorLimits`，仅相等时各扩展 0.5 °C |
| `frame_color_limits(values)` | 前两者组合，返回 `ColorLimits` 或 `None` |
| `ColorScaleScope.from_inputs(source, start, end, transform)` | 以已有 `SourceIdentity`、原始首尾帧和变换构造不可变标识 |
| `ColorScaleScope.to_dict()/from_dict(payload)` | 严格序列化、校验 digest、范围、变换指纹和算法版本 |
| `ColorScaleContext` | 不可变 `source_path/source_identity/source_start/source_end/transform`；`.scope` 是上述标识 |
| `ColorScaleRequest` | 不可变 `context/generation/session_token` |
| `ColorScaleOutcome` | `status/request/limits/completed/empty_frames/error`，仅 complete 可以持有 limits |
| `ColorScaleJob(request, reader_factory=ScienceFileReader)` | `start/cancel/is_alive/join`，`.progress` 和 `.outcome` 与现有任务模式一致 |
| `ColorScaleState(display=None)` | 默认 auto fixed，传入旧三参数 DisplaySettings 时按其 manual fixed 配置 |
| `state.settings` / `draft_bounds` / `draft_pending` | 已生效 DisplaySettings、未提交的两框字符串或 None、是否有未提交输入 |
| `state.fixed_ready` | manual 为 True；auto 仅匹配当前 scope 的完整值为 True |
| `state.set_scope(scope)` | 更新当前工作输入，按指纹使旧自动结果失效 |
| `state.observe_frame(values)` | 计算当前实际上下限并返回 `ColorLimits`，不覆写未应用草稿 |
| `state.select_mode(mode)` | 只接受 `fixed/per_frame`，保留固定值及草稿记忆 |
| `state.edit_bounds(min_text, max_text)` | 记录两个原始字符串，设置 `.draft_pending` |
| `state.discard_draft()` | 只清除未提交文本，保持已生效策略、显示快照和自动缓存 |
| `state.apply_draft()` / `state.apply_manual(minimum, maximum)` | 校验后一次提交，切 manual，清自动 scope 与输入草稿 |
| `state.restore_auto()` / `state.accept_auto(scope, limits)` | 恢复 auto 策略、复用匹配缓存；接收匹配的完整自动结果 |
| `state.settings_for_selection(scope)` | 返回保存用 DisplaySettings，数值为固定记忆，失配或 manual 的 auto_scope 为 null |
| `state.render_snapshot()` | 不可变 `EffectiveScale(limits, mode, status, no_finite)`；status 为 manual/auto_ready/temporary/per_frame/preview |
| `state.freeze_preview()` / `state.resume_preview()` | 变换草稿期间冻结实际范围，退出后按当前帧和原策略恢复 |
| `ColorScaleController(state, job_factory=ColorScaleJob)` | 会话持有，无 Matplotlib 对象；接收 context，管理 worker |
| `controller.set_context(context)` / `activate()` / `reconcile()` | 设置输入、显式激活并按当前策略调度 |
| `controller.attach(on_change)` / `detach()` | UI 绑定及解除刷新回调，解除不丢弃会话内退休 worker |
| `controller.suspend(reason)` / `resume(reason)` | 内部暂停原因采用集合，可嵌套；区别于用户取消 |
| `controller.set_heavy_busy(busy)` / `request_auto()` / `cancel()` | 重任务门控、用户重试/恢复自动、用户明确取消 |
| `controller.poll()` / `close(wait=False)` | 主线程接收结果，关闭时取消；最终 wait=True 等待退出 |
| `controller.status` / `error` / `progress_snapshot` | 调度状态、错误和 ProgressSnapshot，只读 |
| `controller.scanning` / `blocks_heavy_tasks` | scanning 表示正在计算；blocks_heavy_tasks 包含取消收尾中的 worker，直到回收所有权 |
| `ColorScalePanel(figure, state, controller, on_change)` | `.sync()`、`.set_editing(editing)`、`.dispose()`，控件暴露给事件测试 |
| `build_viewer` 新关键字参数 | `color_job_factory=ColorScaleJob`、`defer_color_scan=False`，保留已有参数 |
| `ui.color_scale_panel` / `ui.color_controller` / `ui.poll_color_scale()` | ViewerUI 追加温标控件、会话控制器引用和显式轮询入口 |

`ColorScaleState` 的配置和派生值分别持有。已有 `session.color_limits` 继续表示当前实际显示范围；保留兼容 setter，将显式赋值解释成手动固定。内部帧刷新通过 state 更新派生值，避免触发该 setter。保留 `DisplaySettings(colormap, vmin_c, vmax_c)` 三参数调用语义，新增字段默认 `fixed/manual/null`。

## Task 1：有限温度极值与扫描范围标识

**文件**

- 修改 `src/flir_seq_tool/color_scale.py`、`tests/unit/test_color_scale.py`。
- 新建 `src/flir_seq_tool/color_scale_models.py`、`tests/unit/test_color_scale_models.py`。

- [x] **1. 写失败测试。** 下列用例验证晚帧高温、非有限数和常温帧合并时的真实极值。scope 测试使用 `test_selection.sample_source()` 和 `FrameTransform.identity()`，逐个更改源 mtime、范围、角度和裁剪，要求标识改变；未知字段、bool 帧号、负帧、反向范围和未知算法版本须报错。

```python
def test_raw_extrema_merge_does_not_expand_individual_constant_frames():
    first = finite_extrema(np.full((2, 2), 20.0))
    last = finite_extrema(np.array([[40.0, np.nan], [np.inf, 40.0]]))
    assert first == (20.0, 20.0)
    assert last == (40.0, 40.0)
    assert limits_from_extrema(min(first[0], last[0]), max(first[1], last[1])) == ColorLimits(20.0, 40.0)
    assert frame_color_limits(np.full((2, 2), 25.0)) == ColorLimits(24.5, 25.5)
    assert frame_color_limits(np.array([[np.nan, np.inf, -np.inf]])) is None
```

- [x] **2. 执行并确认预期失败。** 完整 imports 写进测试文件，失败必须来自新接口尚未实现。

```powershell
& .\.packaging\venv\Scripts\python.exe -B -m pytest tests/unit/test_color_scale.py tests/unit/test_color_scale_models.py -q --tb=short -p no:cacheprovider --basetemp .packaging/tmp/scale-task1-red
```

- [x] **3. 实现两个分离阶段和 scope。** 对全局汇总保留原始 extrema，完成后才做常值扩展。旧 `compute_color_limits()` 百分位 API 保留给原有明确调用者。核心计算使用以下实现，并在 `limits_from_extrema()` 校验有限数及顺序。

```python
def finite_extrema(values):
    array = np.asarray(values)
    finite = array[np.isfinite(array)]
    if not finite.size:
        return None
    return float(finite.min()), float(finite.max())


def limits_from_extrema(minimum, maximum):
    minimum, maximum = float(minimum), float(maximum)
    if not np.isfinite([minimum, maximum]).all() or minimum > maximum:
        raise ValueError("invalid temperature extrema")
    if minimum == maximum:
        return ColorLimits(minimum - 0.5, maximum + 0.5)
    return ColorLimits(minimum, maximum)


def frame_color_limits(values):
    extrema = finite_extrema(values)
    return None if extrema is None else limits_from_extrema(*extrema)
```

scope 使用 canonical JSON + SHA-256 生成源身份摘要，采用现有 `source.to_dict()` 和 `transform.fingerprint`。仅为类型标注使用的 SourceIdentity 放入 TYPE_CHECKING，避免 selection 反向导入时形成循环。

- [x] **4. 重跑任务 1 测试至通过，并验证所有未修改的百分位用例。** 同命令将 basetemp 改为 `scale-task1-green`，要求退出码 0。
- [x] **5. 提交本项四个文件。** `git diff --cached --check` 通过后提交 `feat: add finite temperature range primitives`。

## Task 2：DisplaySettings 与 selection v5

**文件**

- 修改 `src/flir_seq_tool/selection.py`。
- 修改 `tests/unit/test_selection.py`、`tests/unit/test_height_configuration.py`，新建 `tests/unit/test_color_scale_selection.py`。

- [x] **1. 写 v5 往返与真正旧 payload 的失败测试。** 使用 `sample_document()`，将新 display 字段从 v1～v4 payload 中实际移除。v1/v2 同时按旧格式删除根字段；v3 保持旧 measurement 格式。保留现有 migration fixture，不批量替换历史版本号。

```python
def test_v5_per_frame_preserves_remembered_fixed_values(tmp_path):
    document = sample_document(tmp_path / "source.seq")
    display = replace(document.display, scale_mode="per_frame", fixed_source="manual")
    document = replace(document, display=display)
    encoded = document.to_dict()
    assert encoded["schema_version"] == 5
    restored = SelectionDocument.from_dict(encoded)
    assert restored.display == display
    assert (restored.display.vmin_c, restored.display.vmax_c) == (22.5, 34.5)
    assert restored.display.auto_scope is None
```

另外覆盖 manual 携带非空 scope 拒绝、auto scope 严格解析、缺失/多余字段、v4 多层 provenance 与 mm 标定保留。保存和加载都不改写输入旧文件。

- [x] **2. 运行失败测试。**

```powershell
& .\.packaging\venv\Scripts\python.exe -B -m pytest tests/unit/test_color_scale_selection.py tests/unit/test_selection.py tests/unit/test_height_configuration.py -q --tb=short -p no:cacheprovider --basetemp .packaging/tmp/scale-task2-red
```

- [x] **3. 扩展 DisplaySettings，保留前三个位置参数。** 追加 `scale_mode='fixed'`、`fixed_source='manual'`、`auto_scope=None`，严格解析当前版，旧版显式补默认值。相应核心迁移条件采用以下形式，保留其已有 list/type 校验。

```python
legacy_display = schema_version < 5
display = DisplaySettings.from_dict(root["display"], legacy=legacy_display)
if schema_version >= 4:
    provenance = tuple(SourceLink.from_dict(link) for link in root["provenance"])
```

根 schema 校验显式接受 1～5，每版 keys 集合准确匹配。`SCHEMA_VERSION=5`。auto_scope 是否匹配当前实际恢复范围由状态/会话层判断，配置解析只负责结构、基本数值和策略合法性。

- [x] **4. 重跑上述测试及原生配套配置现有测试。** 添加 `tests/unit/test_export_bundle.py`，basetemp 使用 `scale-task2-green`。已知当前版 schema 断言改为 5，真正 v4 用例继续保持 4 输入。
- [x] **5. 提交本项文件。** 提交信息 `feat: persist color scale modes in selection v5`。

## Task 3：独立只读全段温标扫描

**文件**

- 新建 `src/flir_seq_tool/color_scale_job.py`、`tests/unit/test_color_scale_job.py`。
- 复用 `measurement_job.AnalysisProgress`、`validate_reader_identity()`、`FrameTransformer`。

- [x] **1. 建立纯读 fake reader 和冻结输入 fixture。** fake 构造函数只保存已有帧和路径，不写 source。测试临时 source 在 fixture 中只创建一次。分别记录 `temperature_frame_at()` 与 `timestamp_at()` 请求，身份核验的端点时间读取与实际扫描温度帧分开断言。
- [x] **2. 写完整扫描和失败/取消用例并运行 RED。** 后段 40 °C、裁剪外 100 °C、选区范围、单帧常值、混合全无效帧、全无效失败、读取异常、扫描中源 mtime 变化、取消前/中/最终发布前均须覆盖。以下为线程主入口的真实契约。

```python
outcome = run_color_scale(request, reader_factory=factory,
                          cancel_event=threading.Event(), progress=AnalysisProgress())
assert outcome.status == "complete"
assert outcome.request == request
assert outcome.limits == ColorLimits(20.0, 40.0)
assert reader.temperature_requests == [1, 2, 3]
assert reader.closed
```

```powershell
& .\.packaging\venv\Scripts\python.exe -B -m pytest tests/unit/test_color_scale_job.py -q --tb=short -p no:cacheprovider --basetemp .packaging/tmp/scale-task3-red
```

- [x] **3. 实现 Context/Request/Outcome 和 worker。** Context 校验原始范围、维度和 transform，request 校验正代次与非空会话 token。逐帧主体使用以下累计方式，前后调用已有身份核验，取消后返回 `limits=None`，异常 outcome 带当前原始帧号。

```python
minimum = maximum = None
empty_frames = 0
for completed, source_index in enumerate(range(context.source_start, context.source_end + 1), 1):
    if cancel_event.is_set():
        raise AnalysisCancelled()
    frame = reader.temperature_frame_at(source_index)
    values = transformer.apply(frame.temperature_c)
    extrema = finite_extrema(values)
    if extrema is None:
        empty_frames += 1
    else:
        low, high = extrema
        minimum = low if minimum is None else min(minimum, low)
        maximum = high if maximum is None else max(maximum, high)
    progress.update(completed, total, f"Source frame {source_index}")
    del values, frame
```

无有限值时失败；全部完成且再次通过身份/取消检查后调用 `limits_from_extrema()`。线程只执行读取、变换和累计，不访问 artist，不保存全部帧，也不创建 SQLite。

- [x] **4. 实现 ColorScaleJob 生命周期并验证通过。** 线程使用非 daemon、独立 SDK reader，复用现有 start/cancel/join 风格。测试取消后 reader 关闭、无 complete/partial limits 泄漏和内存中不积累帧引用。重跑任务 1～3 的目标测试，退出码 0。
- [x] **5. 提交新模块和测试。** 提交信息 `feat: scan confirmed temperature ranges in a read-only worker`。

## Task 4：配置、派生显示值与输入草稿分离

**文件**

- 新建 `src/flir_seq_tool/color_scale_state.py`、`tests/unit/test_color_scale_state.py`。

- [x] **1. 写状态转换失败测试。** 构造 `DisplaySettings('inferno', 20, 45, scale_mode='fixed', fixed_source='auto')`；首次帧 20～25 只作临时值，接收匹配完整结果 20～50 后 fixed ready。旧 scope 的结果返回 False。手动 Apply 后 scope 清空，确认范围变化继续使用手动值。

```python
def test_per_frame_browsing_preserves_manual_values_and_draft():
    state = ColorScaleState(DisplaySettings("inferno", 20.0, 45.0))
    state.edit_bounds("21", "46")
    state.select_mode("per_frame")
    assert state.observe_frame(np.array([[22.0, 50.0]])) == ColorLimits(22.0, 50.0)
    state.select_mode("fixed")
    assert state.draft_pending
    assert state.draft_bounds == ("21", "46")
    assert state.observe_frame(np.array([[22.0, 50.0]])) == ColorLimits(20.0, 45.0)
    state.apply_draft()
    assert state.observe_frame(np.array([[22.0, 50.0]])) == ColorLimits(21.0, 46.0)
```

补充以下参数化用例。扫描完成和切帧不能覆盖任一框的未提交字符串；无效输入不半提交；模式切回恢复来源策略；Auto range 清草稿并复用缓存；全无效首帧 0～1 占位，之后有效帧更新；常值扩展；编辑冻结期间换帧保持原实际范围；固定自动确认范围变化时 scope 失效；selection 快照不取当前自适应值。

- [x] **2. 运行失败测试。**

```powershell
& .\.packaging\venv\Scripts\python.exe -B -m pytest tests/unit/test_color_scale_state.py -q --tb=short -p no:cacheprovider --basetemp .packaging/tmp/scale-task4-red
```

- [x] **3. 按接口表实现 ColorScaleState。** 状态只持有一份已生效 DisplaySettings、一份实际 EffectiveScale、一个最近完成自动缓存和两框草稿。调度进度另由 controller 持有。保存的关键逻辑如下。

```python
def settings_for_selection(self, scope):
    display = self.settings
    valid_scope = display.auto_scope if display.fixed_source == "auto" and display.auto_scope == scope else None
    return replace(display, auto_scope=valid_scope)


def apply_draft(self):
    if self.draft_bounds is None:
        return
    minimum, maximum = map(float, self.draft_bounds)
    self.apply_manual(minimum, maximum)
```

`apply_manual()` 在写 state 前完成全部有限数及递增校验，再一次 replace 配置、清草稿并更新 EffectiveScale。`accept_auto()` 先检查 scope，再同时更新缓存和固定记忆，保留输入草稿。首个有效帧仅在没有固定记忆时初始化记忆，后续临时范围和 per_frame 仅改变 EffectiveScale。

- [x] **4. 重跑任务 4 与 selection 目标测试。** 比较浏览前后 `settings.to_dict()` 完全相同，结果接收时配置才改变；故意让回调抛错不应留下半提交配置。
- [x] **5. 提交状态模块及测试。** 提交信息 `feat: separate color scale settings from live bounds`。

## Task 5：会话持有的扫描调度控制器

**文件**

- 新建 `src/flir_seq_tool/color_scale_controller.py`、`tests/unit/test_color_scale_controller.py`。

- [x] **1. 建立 ControlledColorJob。** 复用 `test_viewer_window.ControlledJob` 的控制方式，明确保存 request、started、cancelled、alive、outcome。该 fake 不自动结束，测试显式设置 outcome/alive。使用实际 `ColorScaleOutcome`，避免以任意 dict 跳过输入校验。
- [x] **2. 写 RED 用例。** 连续 context 变化 A→B→C，A 退出前最多一个已启动任务，随后只启动 C；A 的迟到成功不能覆盖 C。用户 Cancel 后页签刷新/reconcile 不重启；Auto range 重试可以启动。内部 suspend 两个原因时，仅两个 resume 都完成后启动。

```python
controller.set_context(context_a)
controller.activate()
first = jobs[0]
controller.set_context(context_b)
controller.set_context(context_c)
assert first.cancelled
assert len(jobs) == 1
first.alive = False
first.outcome = ColorScaleOutcome("cancelled", first.request, None, 0, 0, None)
controller.poll()
assert len(jobs) == 2
assert jobs[1].request.context == context_c
```

另覆盖同 scope 缓存复用、脱离 UI 后的退休任务、重复 close、attach 替换旧回调、未 activate 的候选不启动、source token/generation/scope 各自失配、任务抛错/无 outcome、成功结果提交时源文件变化。

```powershell
& .\.packaging\venv\Scripts\python.exe -B -m pytest tests/unit/test_color_scale_controller.py -q --tb=short -p no:cacheprovider --basetemp .packaging/tmp/scale-task5-red
```

- [x] **3. 实现单 worker reconcile/poll。** `.set_context()` 设置最新目标并调用 `state.set_scope()`，使用户取消标记随真实输入变化失效。输入或模式使活动请求失效时推进 generation。`.suspend(reason)` 设置集合标记并取消当前任务，`.resume(reason)` 移除指定原因后 reconcile。`.cancel()` 单独记录用户取消的 scope。job 仍 alive 时，任何路径只请求 cancel，不新建 worker。

结果发布顺序固定为以下约束。

```python
matches = (
    not self.closed
    and self.context is not None
    and outcome.request.session_token == self.session_token
    and outcome.request.generation == self.generation
    and outcome.request.context.scope == self.context.scope
)
if outcome.status == "complete" and matches:
    self.state.accept_auto(outcome.request.context.scope, outcome.limits)
```

上述发布前还要核验当前文件大小和 mtime，匹配 complete 也必须通过当前策略、内部暂停和用户取消检查。`.poll()` 回收完已退出的 job 后再 reconcile 最新请求。状态通知由当前 attach 回调接收，detach 后不保留旧 UI 闭包。

- [x] **4. 验证停止与回收。** close(wait=False) 立刻使调度与发布失效并取消 worker；随后 close(wait=True) 仍能 join 同一退休 worker。重复调用安全。测试完全使用可控事件，避免睡眠猜测时序。
- [x] **5. 提交控制器与测试。** 提交信息 `feat: coordinate cancellable color scale scans per session`。

## Task 6：共用控件、会话接线与窗口事务

执行时分为顺序的 6A 共用面板及其事件测试、6B 会话与窗口接线。每段独立实施与双审查，全部完成后标记本项。

**文件**

- 新建 `src/flir_seq_tool/color_scale_panel.py`、`tests/unit/test_color_scale_panel.py`、`tests/unit/test_color_scale_workflow.py`。
- 修改 `src/flir_seq_tool/viewer.py`、`viewer_window.py`、`viewer_layout.py`、`selection_dialogs.py`、`measurement_panel.py`。
- 修改 `tests/unit/test_viewer.py`、`test_viewer_window.py`、`test_measurement_panel.py`、`test_selection_dialogs.py`、`tests/unit/conftest.py`。

- [x] **1. 写控件事件 RED。** 通过真实 Matplotlib 控件 observers 修改模式、两框文本、Apply、Auto range、Cancel。输入 change 只更新两框草稿，任一字段有草稿时 poll/refresh/失焦均保留两框。Per frame 禁用编辑，回 Fixed 恢复草稿。变换编辑期间模式和温标输入不可改，显示冻结范围。

```python
panel.min_box.set_val("21")
panel.max_box.set_val("46")
assert state.draft_pending
assert state.settings.vmin_c == 20
panel.apply_button._observers.process("clicked", None)
assert state.settings.fixed_source == "manual"
assert (state.settings.vmin_c, state.settings.vmax_c) == (21, 46)
```

panel 使用 `min_box/max_box/fixed_button/per_frame_button/apply_button/auto_button/cancel_button/status_text` 这些可测试属性。新输入框同步只在无草稿且未 capturekeystrokes 时改 text artist，避免已有 `sync_value()` 在输入中调用 set_val 而提交/改写草稿。

- [x] **2. 写工作流 RED。** 用受控 job factory 验证候选范围不启动扫描、Confirm/Reset/Apply 后新 scope、ROI 操作不改变 scope、per_frame 换帧不改变保存签名、温标不使已完成测量/高度结果 stale、用户 Cancel 与内部恢复区别。重任务的直接回调入口也要测试，不能只断言按钮颜色或 active。

```powershell
& .\.packaging\venv\Scripts\python.exe -B -m pytest tests/unit/test_color_scale_panel.py tests/unit/test_color_scale_workflow.py tests/unit/test_viewer_window.py -q --tb=short -p no:cacheprovider --basetemp .packaging/tmp/scale-task6-red
```

- [x] **3. 接线 session 与正常绘制。** `ViewerSession` 新增 `color_scale` 与惰性建立的 `color_controller`，在恢复配置后设置 Context。`frame_at()` 去掉首次帧百分位初始化，改为对实际变换矩阵 observe；保存和签名统一使用 `state.settings_for_selection()` 对应 Process/Measure scope 的快照；渲染使用实际 color_limits。显式 `--vmin/--vmax` 与三参数 DisplaySettings 按 manual 初始化。调用颜色归一化时只在 bounds 变化后更新，随后一次 draw_idle。

```python
limits = session.color_scale.observe_frame(next_frame.temperature_c)
if image.get_clim() != (limits.vmin, limits.vmax):
    image.set_clim(limits.vmin, limits.vmax)
scale_panel.sync()
```

控制器不调用 `pages.set_busy()`。UI timer 每约 100 ms poll，并注册到 UIResources。页面切换、show_local 和候选拖动只刷新显示。确认范围/应用变换/载入配置时更新 context 并 reconcile。编辑入口先 freeze_preview + suspend('transform')，退出时 resume_preview + resume('transform') 并 observe 当前帧。

- [x] **4. 在所有重任务入口和可用性上接入扫描门控。** Calculate、原生 SEQ、MP4、全量 CSV、height/zones CSV 进入时检查 `blocks_heavy_tasks`，给等待或取消提示。当前帧 PNG 独立允许，`can_export_analysis(kind)` 与 MeasurePanel 分别询问 CSV/PNG，可保持 kind 默认值兼容现有调用。任务真的启动后 set_heavy_busy(True)，结束/失败/取消的 finally 中恢复 False。手动 Apply/切 Per frame 后等待退休扫描 worker 退出再允许重任务。
- [x] **5. 实现窗口两阶段激活与清理，并补 RED/GREEN。** `build_viewer()` 增加受控 job 注入和 `defer_color_scan=False` 参数。ViewerWindow 的 _prepare 只恢复并读首帧，_install 使用 defer=True，首次 draw 成功且 current/ui 事务提交后才 activate。打开模态框前 suspend('open')，finally 对仍存活旧会话 resume('open')；取消、坏源和安装失败都可恢复。未 Apply 的 Min/Max 用明确的应用/放弃/取消选择处理，通过新 `choose_color_draft_action()` 对话框；Apply 失败则停留旧文件，放弃只影响草稿，保存仍仅保存已生效配置。

UI-only dispose 调用 detach，保留会话控制器对退休 job 的所有权。最终 `_OwnedSession.close()` 和 standalone 关闭路径先 `color_controller.close(wait=True)`，再释放现有 reader。close(wait=False) 的最终等待在已有 launch finally 继续完成。失败构建只有已注册 timer/widget 被 UIResources 回收，未激活候选不会遗留新线程。

```python
self.current, self.candidate, self.ui = self.candidate, None, candidate_ui
self.ui.color_controller.activate()
old.close()
```

上例用于成功切源提交处；首次 start 和回退重建也按相同提交原则激活。状态和草稿由 session 持有，因此 rollback 不丢草稿或用户取消状态。

- [x] **6. 修正测试生命周期并验证。** 普通旧 UI 用例显式设置 manual 或注入受控 job，不以真实 SDK 扫描 fake 路径。真正测试默认行为的专用用例维持 auto。`FileBackedFakeReader` 和 `SyntheticReader` 构造会写文件，不能作为独立扫描 reader factory；任务 3 使用纯读 fake。`tests/unit/conftest.py::make_measure_ui` 等 teardown 改为 `ui.dispose(wait=True)` 后 `plt.close()`。仅将默认行为断言按新设计更新，保留既有温标稳定和导出安全断言。

完整运行新增 scale UI/workflow、viewer、window、pages、measurement_panel、dialogs 套件。通过后提交 `feat: integrate fixed and adaptive color scale controls`。

## Task 7：PNG 实际温标与原生子 SEQ 配置

**文件**

- 修改 `src/flir_seq_tool/viewer.py`、`measurement_export.py`、`measurement_plots.py`、`export_bundle.py`。
- 修改 `tests/unit/test_measurement_export.py`、`test_measurement_plots.py`、`test_export_bundle.py`，扩展 `test_color_scale_workflow.py`。

- [ ] **1. 写 PNG 和 sidecar 失败测试。** 复用 `test_measurement_export.snapshot`。设置记忆固定范围 20～45，但当前 per_frame 为 22～50，要求 PNG 使用 22～50。冻结后切帧、手动修改或扫描完成都不改快照。临时模式、逐帧模式和全无效帧提示入图。复用 export_bundle 的 `view_document/MetadataReader/paired_request`。

```python
snapshot = replace(snapshot, display=replace(snapshot.display, scale_mode="per_frame"),
                   effective_limits=ColorLimits(22.0, 50.0), scale_status="per_frame")
figure = build_report_figure(snapshot)
assert figure.axes[0].images[0].get_clim() == (22.0, 50.0)
```

- [ ] **2. 运行 RED。**

```powershell
& .\.packaging\venv\Scripts\python.exe -B -m pytest tests/unit/test_measurement_export.py tests/unit/test_measurement_plots.py tests/unit/test_export_bundle.py tests/unit/test_color_scale_workflow.py -q --tb=short -p no:cacheprovider --basetemp .packaging/tmp/scale-task7-red
```

- [ ] **3. 扩展 AnalysisExportSnapshot 并冻结实际范围。** 在末尾追加 `effective_limits: ColorLimits | None = None`、`scale_status: str = 'fixed'` 和 `no_finite: bool = False`。兼容旧构造，未传 effective 时从 display 固定值取得。实际 UI 传 `render_snapshot()` 的 bounds/status，而 display 仍保存完整策略及固定记忆。PNG renderer 使用 effective_limits，并给出 Per-frame/Temporary/No finite temperatures 状态。配置和 PNG 元数据保留策略与本张实际范围，CSV 的温度统计列保持原数学定义。
- [ ] **4. 子 SEQ 配套配置清除父 auto_scope。** 完整保留 mode、fixed_source、固定数值、测量和来源链，使用以下变更；子文件重开后在自身范围重新计算。

```python
child_display = replace(view_document.display, auto_scope=None)
```

Process 保存候选范围时则用 `state.settings_for_selection(candidate_scope)`，只有范围/源/变换完全匹配才保留 auto_scope。覆盖既有确认签名冻结与外部目标变更用例。

- [ ] **5. 回归并提交。** PNG 用 PIL 解码，检查图内温标和标题不遮挡。配套 SEQ+JSON 保留已有原子/失败恢复测试。提交 `feat: preserve effective color scales in image and clip exports`。

## Task 8：MP4 跟随温标模式

**文件**

- 修改 `src/flir_seq_tool/video_export.py`、`video_renderer.py`、`video_controller.py`。
- 修改 `tests/unit/test_video_renderer.py`、`test_video_export.py`、`test_video_controller.py`、`test_video_job.py`。

- [ ] **1. 写渲染与请求 RED。** 复用现有 `video_session/controller/FakeJob`。验证 auto 未 ready 禁止固定导出；manual 不受未匹配自动缓存影响；per_frame 导出冻结 mode 和记忆值，但实际由每个渲染帧决定。重复帧使用相同范围，常值扩展，全无效初帧使用 0～1 并提示，后续全无效沿用最近有效显示范围。

```python
def test_renderer_adapts_image_and_colorbar_each_frame():
    display = DisplaySettings("inferno", 20, 45, scale_mode="per_frame")
    with VideoRenderer(display, speed=1) as renderer:
        renderer.render(np.array([[22., 30.], [24., 28.]]), 0)
        assert renderer.image.get_clim() == (22, 30)
        renderer.render(np.array([[23., 50.], [25., 40.]]), 1)
        assert renderer.image.get_clim() == (23, 50)
        assert (renderer.colorbar.norm.vmin, renderer.colorbar.norm.vmax) == (23, 50)
```

- [ ] **2. 运行失败测试。**

```powershell
& .\.packaging\venv\Scripts\python.exe -B -m pytest tests/unit/test_video_renderer.py tests/unit/test_video_export.py tests/unit/test_video_controller.py tests/unit/test_video_job.py -q --tb=short -p no:cacheprovider --basetemp .packaging/tmp/scale-task8-red
```

- [ ] **3. build_video_request 冻结完整策略并检查 fixed auto 就绪。** 使用已确认范围和 applied transform；扫描覆盖全部源帧，沿用已完成结果，不根据 timeline 抽帧重算全局极值。保留 source/target snapshots 和发布检查。摘要根据 display mode/source 输出 Fixed automatic / Fixed manual / Per-frame adaptive，逐帧模式不展示记忆值为实际统一温标。
- [ ] **4. renderer 共用有限范围函数。** fixed 沿用现有冻结值；per_frame 使用 `frame_color_limits(values)`，维护本次视频的 last_valid_limits，初始为空。更新 bounds 时调用 image.set_clim，colorbar 与同一 image norm 绑定。存在状态变化或真正范围改变时才更新对应 artist。适配模式标识和无效提示后保持图像/色条边界分离。

```python
if self.display.scale_mode == "per_frame":
    current = frame_color_limits(values)
    if current is not None:
        self.last_valid_limits = current
    limits = self.last_valid_limits or ColorLimits(0.0, 1.0)
    if self.image.get_clim() != (limits.vmin, limits.vmax):
        self.image.set_clim(limits.vmin, limits.vmax)
```

`VideoRenderer.__init__` 保存 self.display、初始化 last_valid_limits 和状态文本。render 在 fixed 模式也检测无有效温度提示。现有 worker 对重复源帧复用 RGB 的逻辑保留。

- [ ] **5. 验证保护与提交。** 跑上述全部 MP4 单测及 `test_video_encoder.py/test_video_timeline.py`，确认取消、最终解码、原子替换、目标和源变化保护通过。提交 `feat: export videos with adaptive temperature scales`。

## Task 9：布局与交互性能回归

**文件**

- 修改 `tests/unit/test_viewer_layout.py`、`test_viewer.py`、`test_color_scale_panel.py`、`test_color_scale_workflow.py`。
- 按实际布局检查结果小幅调整 `viewer_layout.py`、`color_scale_panel.py`。

- [ ] **1. 扩展现有最小窗口及 DPI 布局测试。** Process、变换编辑、Measure current/curves/height 均画出新控件，1000×800 及既有较大窗口，DPI 100/150/200，检查文字 bounds 与相邻控件/图像/滑块不重叠。输入值 22.586210250854492、50.18052673339844 和取消/失败长消息纳入，输入可水平定位，显示格式不截断生效精度。
- [ ] **2. 运行布局 RED。**

```powershell
& .\.packaging\venv\Scripts\python.exe -B -m pytest tests/unit/test_viewer_layout.py tests/unit/test_color_scale_panel.py -q --tb=short -p no:cacheprovider --basetemp .packaging/tmp/scale-task9-red
```

- [ ] **3. 完成所需区域调整。** 在 viewer_layout 定义所有温标区域常量，panel 只消费常量。保留现有主题的字体层级和输入保护。移动控件时统一更新图片/滑块区域，隐藏或冻结的控件 active 与可见状态一致。所有 widgets/timer/回调注册到已有 UIResources，避免创建新的资源框架。
- [ ] **4. 锁定滑块优化和零重复扫描。** 保留 `test_slider_updates_do_not_force_full_canvas_draw` 与 `test_frame_curves_navigation_reuses_plotted_data_and_moves_both_cursors`。新测试将 draw_idle 替换成空函数，真实拖动 Frame/Range 要求无强制 canvas.draw，连续拖动 20 次保持扫描 job 数量不变。模拟慢扫描阻塞 worker 事件时，主线程滑块回调仍能执行并显示最后帧。当前帧数字框及新温标框均不通过 set_val 连续同步整窗绘制。
- [ ] **5. 回归并提交。** 运行 viewer/measurement/theme/layout/scale UI 定向用例，保留按帧曲线只移动标记线的性能行为，提交 `fix: fit color scale controls without slider redraw regressions`。

## Task 10：真实 SEQ、冻结版、文档与交付

**文件**

- 新建 `tests/integration/test_small_seq_color_scale.py`。
- 修改 `src/flir_seq_tool/portable_diagnostics.py`、`tests/unit/test_portable_diagnostics.py`。
- 修改 `README.md`、`CHANGELOG.md`、设计状态，创建本计划开头指定的执行记录。

- [ ] **1. 写小样本集成 RED。** 动态选择当前 raw 中最小 SEQ，只读验证 3～5 帧，选择实际可用中心 crop。新模块的 `SMALL_SEQ` 使用动态选择，缺失时明确 skip 原因。使用独立参考遍历汇总 transformed 有限值，与完成 job 范围一致；per_frame 切前后帧，检查 image/colorbar；保存和重载 v5；原生小子 SEQ 自动 scope 失效并重算。输出全部在 tmp_path，fixture 使用现有 raw 前后元数据核验。
- [ ] **2. 执行 Q1 全范围验证。** 在新模块单独用例选择 `D:\carbonfoam\infrared\raw\Q1.seq`，identity transform、全部帧，独立 reader 第二次逐帧累计 NumPy min/max。比较结果精确一致，确认完成帧数与 reader.num_frames 一致，最后一帧高温得到覆盖。之后选较小确认范围与裁剪矩形，核对新 scope 和新范围。记录耗时、初末温标、全局范围和 raw 大小/mtime。缺少 Q1 时报告该项未验证，不能把最小样本结果代作通过。

```python
reference_min = reference_max = None
with ScienceFileReader(source) as reader:
    for index in range(reader.num_frames):
        values = reader.temperature_frame_at(index).temperature_c
        finite = values[np.isfinite(values)]
        if finite.size:
            lo, hi = float(finite.min()), float(finite.max())
            reference_min = lo if reference_min is None else min(reference_min, lo)
            reference_max = hi if reference_max is None else max(reference_max, hi)
assert outcome.limits == limits_from_extrema(reference_min, reference_max)
```

测试等待 worker 用明确 deadline，超时先 cancel/join，测试结束无存活线程；不得把等待中的任务标为完成。

- [ ] **3. 跑新增、相关与完整套件。** 先局部测试定位故障，局部通过后完整运行一次并保存日志。依赖弃用警告、旧固定路径缺失造成的 skip 单独记录。

```powershell
& .\.packaging\venv\Scripts\python.exe -B -m pytest tests/integration/test_small_seq_color_scale.py -q -rs --tb=short -p no:cacheprovider --basetemp .packaging/tmp/scale-real
if ($LASTEXITCODE -ne 0) { throw 'Real sequence validation failed' }
& .\.packaging\venv\Scripts\python.exe -B -m pytest -q -rs --tb=short -p no:cacheprovider --basetemp .packaging/tmp/scale-full *> .packaging/color-scale-full-tests.log
if ($LASTEXITCODE -ne 0) { Get-Content .packaging/color-scale-full-tests.log -Tail 80; throw 'Regression suite failed' }
```

- [ ] **4. 扩展冻结诊断并构建独立本地 bundle。** 现有 diagnose 手动 20～40 用例继续保留。追加小范围完整扫描、per_frame bounds 和 v5 保存检查；用明确 join/poll，避免 Agg timer 未运行；所有 worker 在 finally 中回收。构建前确保目标目录不存在，复用现有环境，保留旧 bundle。

```powershell
$scaleDist = Join-Path $projectRoot 'dist/portable/color-scale-20261113'
$scaleBuild = Join-Path $projectRoot 'build/color-scale-20261113'
if ((Test-Path -LiteralPath $scaleDist) -or (Test-Path -LiteralPath $scaleBuild)) { throw 'Use a fresh color-scale build directory' }
& .\.packaging\venv\Scripts\python.exe -B -m PyInstaller --noconfirm --distpath $scaleDist --workpath $scaleBuild .\packaging\flir_seq_tool.spec *> .packaging/color-scale-build.log
if ($LASTEXITCODE -ne 0) { throw 'Portable build failed' }
& .\.packaging\venv\Scripts\python.exe -B .\packaging\prepare_bundle.py --app (Join-Path $scaleDist 'FLIR_SEQ_Tool')
if ($LASTEXITCODE -ne 0) { throw 'Bundle preparation failed' }
```

用新 EXE `--diagnose` 读取最小样本并将报告写入新项目内目录，Start-Process 使用 Hidden。校验 report.status=passed 和 source_files hashes，与当前实现一致。真实原生窗口检查范围切换、编辑草稿保护、模式切换、颜色条、取消后重试、PNG/MP4 结果及窗口切源；按实际完成情况记录。人工点击未完成项留给用户验收，不以离屏测试替代。

- [ ] **5. 更新用户文档并完成审查。** README 说明两个模式、完整扫描耗时/进度、手动 Apply/Auto range、颜色跨帧比较、已确认范围/裁剪统计、旧 selection 手动恢复与 MP4跟随模式。删除“默认首帧第 1/99 百分位”的现行操作说明，保留旧行为只在变更记录解释。执行记录列出真实值、通过/跳过数、bundle 路径、原生点验范围、风险与限制。独立规格审查后再做质量审查，所有可操作问题关闭再交付。
- [ ] **6. 提交验收资料并交用户测试。** 使用明确文件列表暂存，`git diff --cached --check`，确认原始数据、SDK、视频、bundle 和缓存均未进入提交。提交 `test: validate color scale modes with real sequences` 和 `docs: explain fixed and adaptive temperature scales`，按实际文件范围拆分。交付本地 EXE、使用步骤及测试结果；用户之后授权时再合并/推送或更新 Release。

## 规格覆盖自查与完成标准

| 设计要求 | 对应任务 |
| --- | --- |
| 当前默认问题、全帧有限 min/max、常值/全无效边界 | 1、3、4、10 |
| 已确认范围、applied crop、候选/ROI 不影响扫描 | 3、5、6、10 |
| 两模式、手动 Apply/Auto range、固定值记忆 | 2、4、6 |
| 两框草稿保护、编辑冻结、内部恢复与用户取消 | 4、5、6 |
| 单 worker、代次、源身份、逐帧有界内存、异步 UI | 3、5、6 |
| 旧配置/CLI 与 v5、来源链和高度标定保留 | 2、6、7 |
| 保存候选范围与子 SEQ auto_scope 失效 | 4、6、7、10 |
| PNG 冻结实际范围、MP4 跟随模式及固定完整扫描 | 7、8、10 |
| 控件布局、拖帧无额外重绘、曲线复用 | 6、9 |
| 最小真实 SEQ、Q1 全段、只读核验、EXE与说明 | 10 |

交付时要求功能通过正常 UI 入口可操作，colorbar 与当前图像归一化一致，固定自动结果有完整范围依据，逐帧浏览不覆写固定设置。执行步骤和实际证据按复选框及执行记录跟踪，计划本身不作为测试已通过的证据。
