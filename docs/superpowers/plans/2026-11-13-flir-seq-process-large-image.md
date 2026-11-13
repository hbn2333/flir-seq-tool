# Process 大图布局实施计划

> 使用 superpowers:subagent-driven-development 顺序实施，规格审查后再做质量审查。

**目标：** 落实用户选定的左侧大图、右侧操作面板。

**结构：** `viewer_layout.py` 管理区域和图/色条定位；`viewer.py` 只接线页面及图像尺寸变化；`transform_editor.py` 调整侧栏标签；`viewer_theme.py` 同步装饰卡片。原测量与数据模块保持不变。

**技术：** 现有 Matplotlib/Tk、项目内 Python 环境，原始 SEQ 只读。

依据 [设计](../specs/2026-11-13-flir-seq-process-large-image-design.md)。

## 1. 布局与回归

- [x] 新增 `tests/unit/test_process_layout.py`，以 2560×1440、640×480 数据复现当前图像仅 480×360 且偏右的问题。新断言要求高度至少为窗口 50%、等比例、居于左侧分配区、色条紧邻图像。
- [x] 扩展窗口/DPI、Process/编辑/Measure 往返、横向/纵向/窄裁剪、微角度及直角旋转用例，确认温度数组和 norm 不变，标题/坐标/色条/滑块/侧栏边界分离。
- [x] 运行 RED，记录实际失败；保留原真实拖动和隐藏控件生命周期测试。

```powershell
. .\packaging\Set-BuildEnvironment.ps1
& .\.packaging\venv\Scripts\python.exe -B -m pytest tests/unit/test_process_layout.py -q --tb=short -p no:cacheprovider --basetemp .packaging/tmp/process-layout-red
```

- [x] 将 `NORMAL_RECTS`、`EDITOR_RECTS` 和 Process 状态位置改为大图/右侧栏/底部滑块。使用明确 Process 图区域、中心 anchor 和显式 cax，色条随实际图边界计算，不依赖旧自动 colorbar 捕获的布局。
- [x] 保留 Measure 常量。处理右侧输入与滑块标签、长摘要/F1、Process 两行帧时间标题，确保最小窗口可用且按钮字号不缩小。
- [x] 运行新增和既有相关 UI 回归，按互不重复的文件组并行验证，输出到独立 basetemp。规格及质量独立审查并关闭问题后提交。

涉及源码为 `src/flir_seq_tool/viewer_layout.py`、`viewer.py`、`viewer_theme.py`、`transform_editor.py`；相关测试为新文件及 `test_viewer.py`、`test_viewer_layout.py`、`test_viewer_theme.py`、`test_color_scale_workflow.py`、`test_viewer_window.py` 和必要的编辑器测试。只更新实际布局期望，不弱化测量/生命周期/源文件保护断言。

## 2. 真实数据预览与交付

- [x] 用真实最小 SEQ 和 Q1 的少量帧，只读生成修正后的 Process/编辑截图，主代理实际查看；验证 raw 大小/mtime 不变。
- [x] 在全新 `dist/portable/process-layout-20261114` 和对应 build 目录构建，禁止覆盖旧目录；执行 `prepare_bundle.py`。
- [x] 用新 EXE 的 `--diagnose` 验证包内 SDK、温标、分析及导出，核对 manifest 源码 SHA256。
- [x] 将实际尺寸、测试、截图和 EXE 路径记入执行记录，提交文档，交用户本地试用。
