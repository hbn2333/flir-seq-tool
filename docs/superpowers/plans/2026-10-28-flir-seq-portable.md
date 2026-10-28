# Windows 便携版实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生成项目内隔离构建、可迁移运行的 Windows x64 文件夹软件包。

**Architecture:** 新增轻量运行目录模块与 frozen launcher，原查看器复用；PyInstaller 收集指定依赖。项目内独立构建环境、明确环境变量与构建清单避免污染既有安装。

**Tech Stack:** Python 3.13、PyInstaller onedir、Tkinter、官方 FileSDK、FFmpeg。

执行结果与人工待验边界见 [执行记录](2026-10-28-flir-seq-portable-execution.md)。以下任务勾选表示本地自动验证工作完成，不表示公共分发获批或独立电脑验收完成。

## Task 1：可移植目录和依赖定位

- [x] 新建 `tests/unit/test_runtime_paths.py`：模拟 source/frozen/不同 cwd，验证绝对路径与受保护目录；先运行失败。
- [x] 新建 `src/flir_seq_tool/runtime_paths.py`：`application_root()` 在 frozen 下返回 `Path(sys.executable).resolve().parent`，源码下返回 `Path(__file__).resolve().parents[2]`；`resource_root()` 从 `_MEIPASS` 获取；`configure_portable_environment()` 创建包内缓存并设置进程级 TEMP/TMP/MPLCONFIGDIR。
- [x] viewer/file_safety/extraction 使用统一根；video_encoder 优先内置 FFmpeg，源码保留 PATH 回退。运行原文件安全、编码器和目录测试，通过后提交。

## Task 2：桌面启动器

- [x] 新建 `tests/unit/test_desktop.py`：无参数选择、取消、单路径拖入、CLI 参数透传；GUI 错误边界在启动器处理，原生文件框仍需人工验收。
- [x] 新建 `src/flir_seq_tool/desktop.py`，新建 `packaging/launcher.py`。launcher 先调用目录配置和 freeze_support，再导入桌面主函数；主函数延迟导入 GUI/SDK。
- [x] 非控制台异常记录包内日志并弹窗；保留 CLI。验证不因 worker 启动重复弹出选择框。

## Task 3：项目内隔离构建

- [x] 新建 `packaging/build.ps1`、`packaging/flir_seq_tool.spec`。显式 Python 3.13、`.packaging/venv`、`.packaging/cache`、`build` 与 `dist`，禁止全局 pip。
- [x] 从已安装环境只读获取 SDK wheel/库；下载依赖至项目内，收集 TkAgg、SciPy、fnv DLL、VC++ DLL 和内置 FFmpeg。生成组件版本和 SHA256。
- [x] 调用示例见 `docs/PORTABLE.md`。预期 `dist/portable/<构建号>/FLIR_SEQ_Tool/FLIR_SEQ_Tool.exe`；重复构建拒绝已有目标，失败不修改宿主环境。

## Task 4：实际便携验证

- [x] 项目内新目录复制包；用绝对源路径读取最小 SEQ。比较 Python 源码读数与 EXE 诊断结果；输出只写测试目录。
- [x] 冻结诊断覆盖变换/ROI、原生提取、MP4 生成与完整解码、spawn 与取消；子进程环境仅保留系统必要路径，核查加载 DLL 来源。
- [x] 运行相关 pytest，生成截图/日志；原始 SEQ 大小与 mtime 前后一致。没有独立干净机器验证时在记录中标明。

## Task 5：交付材料

- [x] 更新 README/CHANGELOG，增加中文便携版说明、发行前置条件和 Release 下载指引。
- [x] 生成本地 ZIP 和 SHA256；说明应下载附件而非 Source code.zip。检查包内不含 raw、个人实验产物或开发环境绝对路径依赖。
- [x] 定向提交源码/说明，构建产物保持忽略；保留分支交用户测试，不自动上传 Release。
