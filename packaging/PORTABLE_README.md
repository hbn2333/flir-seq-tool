# FLIR SEQ Tool — Windows 便携预览版

此包对应 portable-preview-2026.10.28 预发布，Python 包版本仍为 0.1.0。已完成本机迁移诊断，尚未在独立干净电脑上验收；请先验证双击选文件、取消和拖入 SEQ。

## 使用

1. 完整解压整个文件夹到可写位置，不能只复制 EXE。
2. 双击 FLIR_SEQ_Tool.exe，选择自己的 .seq；也可将一个 SEQ 文件拖到 EXE 上。
3. Process 选择帧范围并旋转裁剪，Measure 添加 ROI、计算和导出。
4. 默认输出为 output 下的 clips、analysis、selections；另存为可指定其他位置。

包内含 Python、Tk、科学计算库和已收集的运行依赖，不需要 Git 或系统 Python。只读打开原始 SEQ。缓存、日志写入软件目录的 cache、logs，目录不可写时应换到自己有权限的位置。

## 发行状态

本包使用 FLIR 官方专有 SDK。SDK 的技术文档提供运行库分发指引，而同一安装包的通用 EULA 限制再分发；尚未取得消除此歧义的单独许可确认。项目源码公开不代表 SDK 获得开源授权，第三方原有许可继续适用，预发布不豁免许可义务。

FFmpeg 为独立 GPLv3 程序，来源、版本、校验和及随附许可见 licenses。对应源码和其他第三方发行材料仍待补齐，当前预发布不表示许可核查已经完成。

遇到错误请查看 logs/application.log 和 logs/startup-error.log。截图与诊断测试输出不是实验原始数据的替代品。
