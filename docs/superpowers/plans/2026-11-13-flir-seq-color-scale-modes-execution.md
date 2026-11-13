# FLIR SEQ 温标模式执行记录

日期为 2026-11-13，Asia/Shanghai。依据 [已批准设计](../specs/2026-11-13-flir-seq-color-scale-modes-design.md) 和 [实施计划](2026-11-13-flir-seq-color-scale-modes.md)。用户选择由代理顺序执行，主代理逐项审查。

功能基线为 `c49858b`，设计提交为 `a582375`，计划提交为 `9f50373`。沿用 `codex/color-scale-modes-design` 分支和唯一工作目录 `D:\carbonfoam\infrared`。原始 SEQ 只读，Python 与缓存采用项目内 `.packaging` 环境。

## 基线

- 工作区在开始实施时干净。
- `test_color_scale.py` 和 `test_selection.py` 基线合计 67 passed，14 条既有依赖弃用警告。
- 已将全部 raw SEQ 的大小和 UTC mtime 保存至 `.packaging/color-scale-raw-before-20261113.json`，用于真实验证结束后的核对。

## 任务进度

| 任务 | 状态 | 提交及证据 |
| --- | --- | --- |
| 1 有限极值与范围标识 | 完成 | `aa19b56`；RED 缺少接口，GREEN 42 passed；独立规格、质量审查均通过，各复测 42 passed |
| 2 selection v5 | 完成 | `5e72b3f`；RED 33 failed、130 passed；GREEN 160 passed；独立规格复测 160 passed，质量审查通过 |
| 3 独立扫描 worker | 完成 | `9e9eb2b`；RED 缺少模块；GREEN 187 passed（42 新 worker 测试）；独立规格复测 42 passed，质量审查通过 |
| 4 温标状态与输入草稿 | 完成 | `62e4f7f`、`516b524`；RED 缺少模块及草稿丢弃方法；GREEN 127 passed；规格审查 127 passed，质量审查 146 passed |
| 5 扫描调度控制器 | 完成 | `fd776fa`、`1ef47a0`；RED 覆盖缺少模块、启动回收与返回缓存；GREEN 163 passed；规格与质量审查各复测 205 passed |
| 6 控件、会话与窗口事务 | 6B3 窗口事务实施中 | 6A、6B1、6B2 均完成双审查；6B2 `074ad1c`，viewer 156 passed，独立 workflow 17 passed |
| 7 PNG 与子 SEQ 配置 | 未开始 | — |
| 8 MP4 模式跟随 | 未开始 | — |
| 9 布局与滑块性能 | 未开始 | — |
| 10 真实 SEQ、冻结版与交付 | 未开始 | — |

本记录随实际完成情况更新。实施中的任务和未执行的验收不计为通过。

## 实施接口补充

任务 4 增加 `discard_draft()`，用于切换文件时明确放弃未提交文本，保持已应用设置、当前快照和自动缓存。EffectiveScale 状态值固定为 manual、auto_ready、temporary、per_frame、preview，no_finite 独立记录。

任务 5 增加状态层 invalidate_auto()/reuse_cached_auto() 和 ColorScaleJob.started，用于源变化失效、保留输入草稿的缓存复用及启动后迅速结束的线程回收。审查发现的两处遗漏均已补 RED/GREEN 回归并关闭。

任务 6A 的长错误状态会截掉未提交输入提示，已通过实际绘制 RED 复现，将提示移到状态开头后复审关闭。面板坐标仍为组件阶段的临时位置，6B/9 接线与布局验收负责最终摆放。

6B 分为会话接口、查看器接线、窗口事务三段。6B1 使用准备后提交保证源失效时不半更新范围/变换；加载同范围待扫描配置造成的调度停滞已通过 5 个失败回归复现并修复，新增 controller.load_context()。

6B1 暂留两项旧 GUI 温标期望/加载刷新测试，由 6B2 接线解决；此阶段未宣称整套 UI 回归通过。

6B2 完成温标控件、异步 norm 更新、类型化导出可用性和 timer 生命周期。viewer 156 passed、workflow 17 passed、受影响布局/工作流 40 passed、最终 load/dirty/timer 子集 28 passed（覆盖有重叠，不相加）。独立规格复测 workflow 17 passed，质量审查通过。B1 暂留的两项 GUI 用例已修复。
