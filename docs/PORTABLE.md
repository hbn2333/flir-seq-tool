# Windows 便携版构建与验收

当前状态：按维护者 2026-10-28 的要求发布 [Windows 便携预览版](https://github.com/hbn2333/flir-seq-tool/releases/tag/portable-preview-2026.10.28)。本机迁移诊断已通过，独立干净电脑和原生文件选择框仍待人工验收。SDK 再分发许可歧义及 FFmpeg 等发行材料尚未核清，公开预发布不表示这些事项已经解决，也不豁免许可义务。

构建环境、临时文件和软件包都在项目内；既有 Python、SDK、Tecplot 安装只读，不修改系统 PATH 或注册表。

## 普通使用者

从 Release 下载便携 ZIP 后，完整解压到可写目录，双击 `FLIR_SEQ_Tool.exe` 选择 SEQ。取消选择即退出；也支持将一个 SEQ 拖到 EXE。无需 Git 或系统 Python，不能单独复制 EXE。使用第三方 SDK 仍需遵守其原有许可。

```text
FLIR_SEQ_Tool/
  FLIR_SEQ_Tool.exe
  _internal/             Python、Tk、科学计算库与 SDK
  tools/ffmpeg.exe
  licenses/
  build-manifest.json
  使用说明.txt
  output/                运行时生成，保存 clips、analysis、selections
  cache/                 运行时生成，保存临时文件与字体缓存
  logs/                  运行时生成
```

每个版本使用单独文件夹。升级时另行解压，保留旧版的 output；按需用另存为或复制结果。软件目录不可写时会提示错误，不自动向系统目录转存。

## 开发者构建

要求 Windows x64、CPython 3.13 x64（包含 Tk）、Git 和可用网络。自行取得有使用许可的 `FileSDK-2026.1.2-cp313-cp313-win_amd64.whl`，复制到项目 `.packaging/vendor/`。构建脚本不安装系统 SDK。

在项目根的 PowerShell 执行，Python 路径替换为自己的安装位置：

```powershell
.\packaging\build.ps1 -Python "C:\path\to\Python313\python.exe"
```

脚本只在 `.packaging/venv` 安装锁定依赖，缓存/下载也在 `.packaging`，PyInstaller 中间文件在 `build/portable/<构建号>`，软件包在 `dist/portable/<构建号>/FLIR_SEQ_Tool`。同目录生成便携版 ZIP 和 `.zip.sha256`，逐文件核对清单并完整检查 ZIP。可传 `-BuildId preview-001` 指定构建号；已存在的目标会直接拒绝，避免删除旧版结果。

FFmpeg 使用 Gyan 9.0.2 essentials，下载 SHA256 为 `60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba`。`packaging/requirements-build.lock` 固定构建环境版本；清单另记源码提交、dirty 状态、源码文件及成品 SHA256。VC++ DLL 从已有系统运行库只读复制，其发行条件仍在核查；目标系统仍需 Windows 10/11 自带组件。

## 冻结程序诊断

将整个软件包复制到项目内不同目录，保留一份未运行的干净构建。诊断只能传入最小测试 SEQ，并将报告和输出放入项目内新的验收目录：

```powershell
Start-Process -FilePath ".\dist\portable\<构建号>\FLIR_SEQ_Tool\FLIR_SEQ_Tool.exe" `
  -ArgumentList '--diagnose "C:\data\small.seq" --report "C:\data\acceptance\report.json"' `
  -Wait -WindowStyle Hidden
```

此处 `C:\data` 仅为参数示例。本项目实际自动验收全部使用项目内路径；使用者也应指定有写权限、与原始数据分开的测试目录。

诊断覆盖包内临时目录和 SQLite、Tk、SDK 温度读取、变换/ROI、两页离屏渲染、五帧原生 SEQ、MP4 工作进程/完整解码及编码取消。源码与 EXE 温度统计需比较；还应检查加载 DLL 的绝对路径和 raw 文件大小/mtime。离屏渲染不能代替原生窗口试用；同机清除 PATH 后的迁移测试也不能替代独立干净机器验证。

## 第三方许可待核实事项

SDK 安装包 EULA 限制再分发，而 SDK 附带技术文档又提供运行库分发指引；不能据此自行认定全部运行库已获得公共再分发许可。需取得 FLIR 对适用许可的明确说明，或改为要求使用者自行提供 SDK。

FFmpeg 9.0.2 构建为包含 libx264 的 GPLv3 程序。随包许可文本不等于完成全部义务，仍需整理准确对应的源码、依赖和构建材料。还需核对 Python、Tk、VC++ 等第三方声明与分发条件。

本次使用日期标签 `portable-preview-2026.10.28` 标记便携发行预览，Python 包版本保留 0.1.0。预发布与正式版同样受上述许可条件约束；本次公开不构成第三方授权确认。Release 附件为便携版 ZIP、SHA256 及更新说明；二进制不提交到 Git 源码目录，自动生成的源码压缩包不包含可执行软件包。

依据：[GitHub Release 说明](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)、[FFmpeg 文档](https://ffmpeg.org/ffmpeg.html)、[PyInstaller 使用文档](https://pyinstaller.org/en/stable/usage.html)。SDK 许可依据为本地 2026.1.2 安装包 EULA 及其 `python/doc/_sources/redistribution.rst.txt`，保留原许可文件，不在仓库公开复制其全文。
