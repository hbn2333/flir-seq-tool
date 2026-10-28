# Windows 便携版预览 · 2026-10-28

首次提供 Windows x64 文件夹程序包：下载、完整解压后即可运行，无需 Git 或系统 Python。日期标签标记便携发行预览，Python 包版本仍为 0.1.0。

## 下载与使用

1. 在本页 Assets 下载 `FLIR_SEQ_Tool-portable-preview-win64.zip`，旁边的 `.zip.sha256` 用于核对文件完整性。
2. 完整解压到有写权限的位置，双击 `FLIR_SEQ_Tool.exe` 选择自己的 SEQ，或将一个 SEQ 拖到 EXE 上。不能只复制 EXE。
3. Process 选择帧范围、旋转裁剪；Measure 添加矩形 ROI、查看统计和计算曲线。
4. 默认输出在软件目录的 `output`，缓存和日志在 `cache`、`logs`。升级时另行解压，保留旧版结果。

`Source code.zip` / `.tar.gz` 是 GitHub 自动生成的源码压缩包，不是可直接运行的软件包。安装与完整用法见仓库 README。

## 本版包含

- 逐帧摄氏温度读取、时间范围选择、旋转裁剪和配置保存/加载。
- 多矩形 ROI、当前帧统计/分布/剖面、帧曲线及 CSV/PNG 导出。
- 带同名视图配置的原生子 SEQ，以及固定温标、相对时间的 MP4 展示导出。
- 浅色界面、包内运行依赖、独立输出/缓存目录及源数据保护。不包含实验 SEQ。

原生子 SEQ 保留完整原始像素，旋转裁剪由配套 selection JSON 恢复；MP4 会呈现已应用的旋转裁剪。

## 验证与已知边界

目标为 Windows 10/11 x64。已完成本机不同目录、中文/空格路径及清除宿主依赖 PATH 的迁移诊断：SDK 读取、变换/ROI、五帧 SEQ 提取、MP4 工作进程/完整解码及取消。原始 SEQ 大小和修改时间不变。

完整回归 755 passed、2 skipped，另有后续目录/路由/诊断保护测试通过。尚未在独立干净电脑上测试，双击选文件/取消、拖入文件和大文件体验仍需人工验收。此版本标记为 Pre-release。

## 第三方依赖

包含官方 FLIR Science File SDK 2026.1.2 运行依赖、Python 3.13.5、Tk、NumPy、SciPy、Matplotlib、VC++ 运行 DLL 及 Gyan FFmpeg 9.0.2（含 libx264）。随包文件与 SHA256 见 `build-manifest.json`，已收集声明见 `licenses`。

FLIR SDK 为专有第三方依赖，项目源码公开不代表 SDK 获得开源授权。SDK 技术文档提供分发指引，但安装包 EULA 的再分发限制存在适用歧义，尚未取得明确确认；FFmpeg 对应源码和其他发行材料也仍待补齐。本次公开预发布不表示许可核查已完成，预发布同样受原有许可约束。
