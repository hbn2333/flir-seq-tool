# FLIR SEQ Tool — Windows 便携预览版

具体代码提交、源码校验和与依赖版本见本目录的 build-manifest.json，Python 包版本仍为 0.1.0。本地构建与 GitHub Release 附件可能不同；请先用自己的 SEQ 验证双击选文件、取消和拖入操作。独立干净电脑的运行环境仍需另行验收。

## 使用

1. 完整解压整个文件夹到可写位置，不能只复制 EXE。
2. 双击 FLIR_SEQ_Tool.exe，选择自己的 .seq；也可将一个 SEQ 文件拖到 EXE 上。
3. Process 左侧浏览大图，右侧选择帧范围并点击 Confirm range；旋转裁剪时右侧切换为编辑控件，调整后点击 Apply。帧和范围滑块位于图下。
4. 默认自动扫描已确认范围的全部帧，取得统一温标。扫描完成后可进行 Calculate、MP4 等重任务，期间可以浏览帧。
5. Measure 添加 ROI，查看当前帧、帧曲线或高度分析，再保存方案、导出数据。顶部 Open SEQ 可在原窗口切换文件。
6. 默认输出为 output 下的 clips、analysis、selections；另存为可指定其他位置。

包内含 Python、Tk、科学计算库和已收集的运行依赖，不需要 Git 或系统 Python。只读打开原始 SEQ。缓存、日志写入软件目录的 cache、logs，目录不可写时应换到自己有权限的位置。

## 温标与保存

- Fixed / Auto range：在已应用旋转裁剪后的图像上，扫描全部已确认帧的有效温度 min/max，跨帧使用同一范围。候选范围拖动不触发重算。
- 手动固定：在 Fixed 下编辑 Min/Max，再点击温标旁的 Apply。Enter 或失焦不会应用。Auto range 放弃输入草稿并恢复自动策略。
- Per frame：根据当前帧更新图像和色条，颜色随帧重新归一化；切回 Fixed 可恢复记忆的固定策略和值。
- 扫描未完成时的 temporary 是临时范围；温标旁的 Cancel 可取消扫描，之后用 Auto range 重试。此时可导出带临时标识的 PNG，固定自动 MP4 则需等完整结果就绪。
- Save selection / Save setup 保存已应用设置，不保存曲线计算结果。旧配置按手动固定温标加载，需要全段自动时点击 Auto range。
- 原生子 SEQ 保留原始像素；旋转裁剪和温标模式在同名 .selection.json 中。请一起移动两个文件。重开自动固定的子 SEQ 会按子文件自身范围重新扫描。

## 发行状态

本包使用 FLIR 官方专有 SDK。SDK 的技术文档提供运行库分发指引，而同一安装包的通用 EULA 限制再分发；尚未取得消除此歧义的单独许可确认。项目源码公开不代表 SDK 获得开源授权，第三方原有许可继续适用，预发布不豁免许可义务。

FFmpeg 为独立 GPLv3 程序，来源、版本、校验和及随附许可见 licenses。对应源码和其他第三方发行材料仍待补齐，当前预发布不表示许可核查已经完成。

遇到错误请查看 logs/application.log 和 logs/startup-error.log。截图与诊断测试输出不是实验原始数据的替代品。
