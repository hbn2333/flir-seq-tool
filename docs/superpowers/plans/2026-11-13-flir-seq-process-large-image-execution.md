# Process 大图布局执行记录

设计及用户布局选择始于 2026-11-13，最终回归与构建于 2026-11-14 收尾。依据 [设计](../specs/2026-11-13-flir-seq-process-large-image-design.md) 与 [计划](2026-11-13-flir-seq-process-large-image.md)。沿用 `codex/color-scale-modes-design`，唯一目录为 `D:\carbonfoam\infrared`，原始 SEQ 只读。

## 实现及实际尺寸

`04904d3` 改为左侧大图/色条、右侧普通或变换操作面板，帧及候选滑块仍在图下；显式创建 cax，按图像实际 aspect fit 定位，避免自动 colorbar 将图推到右侧。Measure 区域、单行标题及原状态位置保留。`5d179e2` 恢复 Process 两行时间信息，Absolute、Relative、Duration 可见。

先复现四个尺寸失败用例，再实现修复。真实 Q1 末帧的离屏检查结果：

| 窗口 | 图像显示尺寸 | 图像位置 x/y | 色条位置 x/y |
| --- | --- | --- | --- |
| 2560×1440 | 1113.6×835.2 | 384.0 / 316.8 | 1528.32 / 316.8 |
| 1000×800 | 585.0×438.75 | 75.0 / 188.625 | 672.0 / 188.625 |

原大窗口显示仅 480×360，宽高均增为 2.32 倍，面积增为 5.38 倍。保留 4:3 比例；横竖图和裁剪后图像均自适应分配区。真实 Q1 的 2560/1000 Process 及 1000 微角度编辑截图已实际查看，位于忽略目录 `.packaging/process-layout-preview/Q1-*.png`，报告为同目录 `Q1-layout.json`。布局变化前后数组一致，源文件大小和 mtime_ns 一致。

## 回归与复审

- 新布局及页面/DPI/数组/norm/色条测试最初 10 passed；补时间信息后独立规格审查运行 12 passed。
- 作者定向运行 20 项布局/页签/主题和 52 项渲染/拖动/长信息测试通过，部分覆盖重叠，不相加。
- 主代理并行运行 11 个互不重复 UI 测试文件，覆盖 390 个参数化用例：查看器/布局/主题/页签组为 165 passed、1 failed；温标工作流/新布局/编辑器组为 113 passed、2 failed；窗口/测量工作流/面板组为 109 passed。日志为 `.packaging/process-layout-ui-{1,2,3}.log`。
- 三处失败涉及侧栏换行：覆盖警告在单词间软换行，以及独立编辑器测试使用低于支持下限的 640×480 画布。`3f3fce3` 将组件测试改用 1000×800，保留覆盖断言的语义并规范化空白；生产编辑器错误允许两行，状态和错误的右边界与侧栏一致，使正常最小窗口下的 15° 提示可见。
- 三个原问题和三个新增 DPI 角度提示用例均先复现失败；修复后完整编辑器、新布局及所有外部目标变化用例共 62 passed。两次独立规格和质量审查均通过。

仅修改 UI 几何、标题/消息格式及相关测试；不改变温度、统计、帧范围、变换数学、ROI、配置 schema 或导出内容。本次没有重复与布局无关的数值套件；此前温标功能的完整回归记录仍保留，不能混作本轮全部源码再次串行运行的结果。

## 交付验证

额外长信息窗口缩放回归为 **6 passed、150 deselected**，覆盖普通/编辑及 100/150/200 DPI；日志为 `.packaging/process-layout-final-longmessages.log`。针对修改范围的检查均已完成，已发现的问题全部修复并复审关闭；没有把初轮三项失败记录改写为通过。

最终软件目录为 `dist/portable/process-layout-20261114/FLIR_SEQ_Tool`，构建源码提交 `a372c7a`，构建时工作区干净，先前软件目录全部保留。新 EXE 运行只读最小真实 SEQ 的 **14 项冻结诊断全部 passed**，包括 SDK、温标/色条、v5、变换/ROI、高度 CSV、同窗切源、原生提取、固定及逐帧 MP4 完整解码与取消。报告为 `.packaging/process-layout-frozen/diagnostics.json`。

再次核对 62 个构建源码文件和 2015 个包内文件 SHA256，与 manifest 一致；原七份 raw 清单及单独记录的 Q1 大小/mtime 均保持不变。核验记录为 `.packaging/process-layout-bundle-verification.json`。构建退出 0；日志保留一条 `scipy.special._cdflib` hidden import 警告，无构建错误，实际冻结诊断功能全部通过。

交付 EXE 为 `D:\carbonfoam\infrared\dist\portable\process-layout-20261114\FLIR_SEQ_Tool\FLIR_SEQ_Tool.exe`，保留整个文件夹使用。原生窗口人工试用和独立干净电脑验收留给用户；本轮不合并 main、不推送 GitHub、不更新 Release。最终验收文档提交不改变包内生产源码。
