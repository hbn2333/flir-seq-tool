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
| 6 控件、会话与窗口事务 | 完成 | 6A、6B1、6B2 均完成双审查；6B3 `44eed4d`、`9bdf6e3` 规格及质量审查通过；窗口/对话框 89 passed，修复后回退子集 12 passed，双重安装失败时恢复扫描的遗漏已关闭 |
| 7 PNG 与子 SEQ 配置 | 完成 | `690a213`；RED 13 failed、97 passed，GREEN 113 passed；独立规格复测 113 passed，质量审查通过；PNG 实际温标、模式提示与元数据核验通过 |
| 8 MP4 模式跟随 | 完成 | `f86a1c1`；RED 15 failed、21 passed，边界布局 RED 3 failed；GREEN 70 passed（含编码及时间轴）；独立规格 41 passed，质量审查通过 |
| 9 布局与滑块性能 | 完成 | `c7f9be0`、`273b1aa`；输入、统计、错误及坐标标签均先复现 RED；93 项定向回归，独立规格 74 + 18 passed，质量实鼠标/键盘验证通过；测试覆盖有重叠，不相加 |
| 10 真实 SEQ、冻结版与交付 | 集成通过，待完整回归与冻结验收 | `4e3af2f`；新增诊断 helper RED 3 failed，最小样本/Q1/高度/诊断 GREEN 10 passed，待独立审查、新 EXE 与交付 |

本记录随实际完成情况更新。实施中的任务和未执行的验收不计为通过。

## 实施接口补充

任务 4 增加 `discard_draft()`，用于切换文件时明确放弃未提交文本，保持已应用设置、当前快照和自动缓存。EffectiveScale 状态值固定为 manual、auto_ready、temporary、per_frame、preview，no_finite 独立记录。

任务 5 增加状态层 invalidate_auto()/reuse_cached_auto() 和 ColorScaleJob.started，用于源变化失效、保留输入草稿的缓存复用及启动后迅速结束的线程回收。审查发现的两处遗漏均已补 RED/GREEN 回归并关闭。

任务 6A 的长错误状态会截掉未提交输入提示，已通过实际绘制 RED 复现，将提示移到状态开头后复审关闭。面板坐标仍为组件阶段的临时位置，6B/9 接线与布局验收负责最终摆放。

6B 分为会话接口、查看器接线、窗口事务三段。6B1 使用准备后提交保证源失效时不半更新范围/变换；加载同范围待扫描配置造成的调度停滞已通过 5 个失败回归复现并修复，新增 controller.load_context()。

6B1 暂留两项旧 GUI 温标期望/加载刷新测试，由 6B2 接线解决；此阶段未宣称整套 UI 回归通过。

6B2 完成温标控件、异步 norm 更新、类型化导出可用性和 timer 生命周期。viewer 156 passed、workflow 17 passed、受影响布局/工作流 40 passed、最终 load/dirty/timer 子集 28 passed（覆盖有重叠，不相加）。独立规格复测 workflow 17 passed，质量审查通过。B1 暂留的两项 GUI 用例已修复。

## 提前执行的只读真实数据核验

当前 `raw/Q1.seq` 为 2,934 帧，1,813,195,915 字节，mtime_ns 为 1794559170000000000。全段 identity 变换扫描耗时 8.37 s，独立 reader 使用 NumPy 第二遍遍历耗时 8.24 s，二者精确得到 22.704004287719727～50.90658950805664 °C。首帧范围为 23.37359046936035～25.956514358520508 °C，末帧最高为 50.819454193115234 °C。

扫描 worker 存活期间，主 reader 读取首、中、末等五帧均正常。另对源帧 1～5 和中心 320×240 crop 进行扫描及独立参考核对，得到 23.538602828979492～25.249067306518555 °C，scope 与全段不同。验证前后 Q1 大小及 mtime_ns 一致，所有线程已 join。报告位于项目内忽略目录 `.packaging/q1-color-scale-validation-20261113.json`。

开始实施时的 raw 清单含七份 SEQ，这七份的大小与 UTC mtime ticks 均保持不变；当前清单另有 Q1.seq，因此对 Q1 单独记录验证前后身份。本项是 core 的真实数据核验，不能替代 Task 10 的集成测试、完整回归和冻结版验收。

## 集成验收与最终回归

`4e3af2f` 新增两项实际 SDK 温标集成测试，并扩展冻结诊断。定向运行新增温标、既有高度和诊断单测，结果为 10 passed，14 条既有依赖弃用警告，耗时 69.67 s。

最小真实样本使用 `raw/2stick_q1.seq` 的五帧和有效中心裁剪，核对自动范围、逐帧图像/色条、v5 自动与逐帧方案重载；实际提取子 SEQ，核对父 scope 清除、变换及来源链保留，并在子文件自身范围重算。高度回归显式等待温标完成后再 Calculate，仍验证完整高度/分区 CSV、配套配置和同窗切源。

当前 Q1 的 2,934 帧再次通过完整扫描及独立参考遍历，范围精确一致，为 22.704004287719727～50.90658950805664 °C，最高温出现在源帧 2,930。此次 worker 耗时 12.21 s，参考遍历 12.58 s；源帧 1～4 和中心裁剪的范围为 23.538602828979492～25.249067306518555 °C。报告保存在忽略目录 `.packaging/tmp/scale-task10-target/test_q1_complete_identity_and_0/q1-color-scale.json`。设计阶段抽查使用的是当时 Q1 路径对应的另一份文件身份，最终验收以此处当前文件身份为准，不混用两个记录。

完整测试、新 EXE 构建与冻结诊断在下述最终验收完成前仍不计为通过。
