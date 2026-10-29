# ROI 高度分析与窗口工作流实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留既有功能的前提下完成 ROI 高度标定、完整 T(z,t)/分区 CSV、蓝色 SEQ 确认与同窗口切源。

**Architecture:** 独立高度数学/磁盘数据/后台导出模块，复用标准统计结果与 ROI；独立高度面板接入现有 Measure。窗口控制器拥有当前 reader/session/UI，UI 拆卸与数据销毁分开。schema v4 保存高度及连续来源链。

**Tech Stack:** Python 3.13、NumPy、SQLite、Matplotlib/Tk、FLIR FileSDK，已有项目内隔离 Python。

依据：[已批准规格](../specs/2026-10-30-flir-seq-roi-height-analysis-design.md)。仅在 `D:\carbonfoam\infrared` 工作；现有专用分支已提供隔离，不另建工作区。独立文件任务可并行，公共模块由主代理集成，禁止互相覆盖。

## 验证命令与写入边界

每次 Python 调用先执行：

```powershell
. .\packaging\Set-BuildEnvironment.ps1
$env:PATH = (Join-Path $projectRoot 'dist\portable\release-preview-20261028\FLIR_SEQ_Tool\tools') + ';' + $env:PATH
& .\.packaging\venv\Scripts\python.exe -B -m pytest <测试路径> -q -rs -p no:cacheprovider --tb=short --disable-warnings
```

所有缓存、日志和测试输出在项目 `.packaging`。真实集成只读最小 raw SEQ，记录三份 raw 的大小与 mtime；不动现有 clips/square 产物。最终只交本地功能分支和测试说明，不自动推送、合并或替换已发布附件。

## 共享接口

以下接口作为任务间约定，调整须通知集成方并同步本文。

- `height_models.HeightSettings(roi_id, heated_edge='bottom', mm_per_pixel=None, reference_height_px=None, reference_height_mm=None, zones_enabled=False, cuts=(1/3,2/3), zone_names=('Low','Middle','High'), delta_pair=('low','high'))`：冻结设置，严格序列化、`calibrate(height_px,length_mm)`、`for_height(height_px)`、`validate_height(height_px)`。
- `height_models.row_profile(values, roi)`、`height_positions(height, settings)`、`zone_statistics(profile, settings)`：有限值行归约、规范 r 到 z 映射、像素加权三区统计及有符号温差。
- `MeasurementSettings` 新增 `height_settings=()`、`height_view='profile'`、`height_unit='px'`，允许 `result_view='height'`；`height_for(roi_id)` 给默认设置，`with_height(config)` 替换单 ROI 设置。旧设置正常往返。
- `height_store.HeightResultStore` 兼容 `ResultStore` 的完整标准统计与清理接口，新增行数据/时间状态表、`has_height`、逐帧与流式高度查询。存储规范 r，不在数据键里包含方向/标定/区间。
- `height_job.HeightAnalysisJob(request, reader_factory=ScienceFileReader, temp_root=None)` 使用现有 `AnalysisRequest`、progress/outcome/start/cancel/is_alive/join 契约；一次扫描生成标准统计和所有行数据，返回兼容 `AnalysisOutcome`。
- `height_export.build_height_snapshot(session)` 冻结全部来源/范围/变换/ROI/高度配置与有效数据表路径；`HeightExportJob(target,snapshot,kind,expected,raw_root)`，kind 为 `height` 或 `zones`，提供 progress/outcome/start/cancel/is_alive/join。分块写入、验证、目标保护和取消。
- `provenance.SourceLink(source,start_frame,frame_count)` 与 `origin_fields(links,source_frame)`：父文件身份与连续映射；selection/source Session 的 `provenance=()` 默认为未知。来源链最早记录层不称为已证实最初录像。
- MeasurePanel 提供高度模式入口、`on_export_height(kind)`、`can_export_height(kind)`，保留现有 on_calculate/on_cancel；当前帧与结果存储传入独立 HeightPanel。viewer 按模式选择高度工作任务和新的流式 CSV 任务。

## Task 1：高度数学与标定

**Files:** `src/flir_seq_tool/height_models.py`；`tests/unit/test_height_models.py`。

- [ ] 先写梯度、上下热端、标定与不等有效数的失败测试，运行确认功能尚不存在。

```python
values = np.array([[10., np.nan], [20., 40.], [50., 70.]])
# 上端受热，三行均温 10,30,60，行中心 .5,1.5,2.5。
# 前两行整体均温 = 70/3，不是 (10+30)/2。
```

- [ ] 实现纯设置及行归约：float64 sum/count、空行 NaN、标定参考检查、方向映射、区间按行中心归属、几何空区自动停用且保留分界。
- [ ] 验证 H=1/2/3、非法类型/比例/NaN、修改标定不改数据行，运行新增数学测试通过。
- [ ] 自查规范契合与数值边界，再交独立审查。

## Task 2：逐帧磁盘高度结果与导出

**Files:** `height_store.py`、`height_job.py`、`height_export.py`；对应 `test_height_store.py`、`test_height_job.py`、`test_height_export.py`。

- [ ] 以小型假 reader 写失败用例，验证每帧读取一次、两个 ROI 共享同一 t0，期望高度行数 `N*sum(roi.height)`，包含 n=0 行。

```python
assert outcome.status == 'complete'
assert outcome.store.has_height
assert len(list(outcome.store.iter_height_rows())) == frame_count * sum(r.height for r in rois)
```

- [ ] 实现兼容标准 `samples` 的 HeightResultStore，另加 frames/height_rows、独立高度完整标记及严格键/行数校验；读接口逐块提供 r、sum、count、mean、timestamp、relative_s、time_status。
- [ ] 实现 HeightAnalysisJob：独立只读 reader、每帧一次变换、标准统计和行数据一起写；开始/结束源身份核验，时间缺失失败、重复/倒退保留状态；取消/失败清理自身目录。
- [ ] 编写完整高度/三区宽表 CSV 的失败测试：两种方向排序、px/mm、来源、公式注入、空值、全部 ROI、A−B、roi_range、全部追溯字段，不从图形数据导出。
- [ ] 实现快照和 HeightExportJob：后台分块生成、进度、取消、同目录临时文件、流式验证、源与目标复核后 os.replace；取消保留旧目标。
- [ ] 运行新测试并验证兼容标准结果读取，独立审查后提交集成。

## Task 3：设置、schema v4 与来源链（主代理）

**Files:** `measurement_models.py`、`selection.py`、`export_bundle.py`、新 `provenance.py`；`test_measurement_models.py`、`test_selection.py`、`test_export_bundle.py`、新 `test_provenance.py`。

- [ ] 写失败用例：v4 全设置往返、v1/v2/v3 默认迁移、坏引用拒绝、标定比例一致、来源深度/索引复合与范围错误。

```python
assert decoded.measurement.height_for('r1').mm_per_pixel == .1
assert origin_fields(links, 2)['origin_frame'] == parent_start + ancestor_start + 2
assert origin_fields((), 2)['origin_status'] == 'unknown'
```

- [ ] MeasurementSettings 扩展高度字段和 helper，严格关联稳定 ROI ID；删除 ROI 时 UI 同步删设置。
- [ ] selection v4 保留旧版测量/变换迁移，增加 provenance；现有 source/target/备份保护保留。
- [ ] 子 SEQ 配置新增直接父映射及父已知链，保留高度设置，仍绑定子文件身份。
- [ ] 标准统计/PNG/CSV 接口不改变旧 delta_c 含义，检查原 schema 数字断言按新版本更新，同时保留真正旧版输入用例。
- [ ] 定向回归设置、配置、成对导出，通过后与任务2/4集成。

## Task 4：高度面板（独立 UI 任务）

**Files:** 新 `height_panel.py`，修改 `measurement_panel.py`，新 `test_height_panel.py`。

- [ ] 写实际控件事件失败测试：高度入口、分区复选框、热端、mm 输入、两条边界、A/B、未标定 mm 比较禁用；隐藏状态不得响应输入。
- [ ] HeightPanel 管理高度设置控件、当前剖面与分区图；复用 ROI 列表/滑块，选项变化走显示/dirty 更新，不把派生设置变成 SDK 重算。
- [ ] 高度模式调整下方区域布局；区域名称/几何仍可编辑；新增 ROI 默认设置、删除/改高后的设置同步。
- [ ] 完整/分区 CSV 按钮不依赖图形勾选，旧 CSV/PNG 模式保持；图例含 ROI+分区，缺失为断点，当前帧曲线读取当前矩阵。
- [ ] 大结果分区派生/抽稀通过后台或分块调度，主线程仅消费有界绘图数据；停止/隐藏/关闭释放 timer/selector/回调。
- [ ] 运行新增面板及既有测量面板/布局测试，交审查并向任务5提供接口说明。

## Task 5：窗口切源、确认色与高度任务接线（独立 viewer 任务）

**Files:** `viewer.py`、`viewer_layout.py`、`selection_dialogs.py`、新 `viewer_window.py`，必要 `viewer_pages.py`；对应窗口/工作流测试。

- [ ] 先写 Export 确认态的颜色/失效恢复失败测试；进入确认时 `role='primary'; refresh_style()`，恢复初态时恢复默认角色，保留全部安全预检。
- [ ] 写窗口事务测试：当前文件取消、同文件/别名、坏源、首帧失败、UI安装失败回退、成功 reader 释放及 A→B→A 回调不累积。
- [ ] 把 build_viewer 改为可接受现有 Figure；统一 UI-only dispose，单独最终 shutdown，窗口控制器集中持有当前和候选 reader/session/UI。
- [ ] 新增顶部 Open SEQ，现有忙碌/草稿/ROI绘制时禁用；SelectionDialogs 增加 SEQ 打开及三选一未保存处理；窗口级保存返回明确成功/取消/失败，不依赖全局 dirty 被清空。
- [ ] ViewerSession 保存/加载/签名接入高度字段和 provenance；首个 CLI 的显式参数不带入后来文件。启动、直接 build_viewer 单测接口保持兼容。
- [ ] 高度 Calculate 选 HeightAnalysisJob；高度 CSV 选 HeightExportJob 并用计时器更新进度和取消；完成后验证基础数据键与能力后发布，其他页面行为保留。
- [ ] 运行新增窗口/导出/任务集成与既有 viewer 相关测试，交规范与质量审查。

## Task 6：统一审查、真实只读验证与文档

**Files:** 新 `tests/integration/test_small_seq_height.py`，README、CHANGELOG、执行记录。

- [ ] 集成后运行所有新增与相关旧测试，修复回归；独立规范审查后进行质量审查，所有可操作问题逐项关闭。
- [ ] 真实最小 SEQ 创建小 ROI 验证像素/毫米、两个热端、完整与分区 CSV、一次原生小片段来源映射、文件切换；开始/结束核对所有 raw 元数据。
- [ ] 完整 pytest 输出保存 `.packaging/height-full-tests.log`；运行有意义的异常/取消/文件别名测试，不因低风险文档重复扩大验证。
- [ ] 实际 TkAgg 或原生自动化验证高度面板、确认蓝色、Open SEQ，记录无法自动完成的人工项；打包诊断覆盖新模块，构建产物仅留项目内。
- [ ] README 写明操作、CSV 长表/宽表、坐标/时间/两种温差；执行记录给出实际通过数字和限制，更新任务勾选并提交功能分支。

## 交付标准

不能以“核心函数通过”代替界面和导出的完成。三项入口可用，完整 CSV 逐行可追溯，旧统计/旧配置/原生导出不回归，窗口切换可释放资源。源码、文档和测试提交在功能分支；不自动发布新 Release。
