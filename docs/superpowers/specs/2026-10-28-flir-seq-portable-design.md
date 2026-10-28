# Windows 文件夹便携版设计

用户已确认文件夹版、必要启动/路径补充工作，并允许在项目内新建目录管理。发行对象为 Windows 10/11 x64，解压后双击 EXE 选择 SEQ，无需 Python 或 Git。先交付本地测试包，尚不发布 GitHub Release。

## 文件与环境边界

全部源码、构建环境、下载、缓存、临时文件和产物位于 `D:\carbonfoam\infrared`。项目外仅允许读取、复制，不安装/卸载或修改原 Python、SDK、FFmpeg、注册表、系统环境变量。构建子进程使用进程级环境变量将 pip、PyInstaller、Matplotlib 和临时文件目录指向项目内。raw 及已有实验产物只读，不加入便携包。

## 运行结构

采用 PyInstaller onedir。EXE 旁为 output（clips、analysis、selections）、cache、logs、licenses 和依赖资源。启动时先配置便携目录，再处理 frozen multiprocessing，之后才导入 SDK/GUI。直接双击打开 SEQ 选择框，取消退出；保留 `view`/`inspect` 命令行入口，并接受拖入一个 SEQ 的路径参数。

资源目录与可写目录分离：Python/DLL/字体等来自 `_internal`；写入位置相对于 EXE，而非当前工作目录。不自动改写其他电脑的系统 PATH；内置 FFmpeg 优先按绝对路径定位。源码模式继续从项目根定位，不再依赖固定 D 盘路径。

临时缓存、SQLite、Matplotlib 字体缓存和日志归属当前包。目标目录不可写时弹出明确错误，不静默转写其他系统目录。源文件/别名保护、raw 保护、原子覆盖与取消策略保留。

## 依赖与分发

收集 Python、Tkinter、NumPy、SciPy、Matplotlib、官方 fnv 扩展与其 `_lib` DLL，核对 VC++ 依赖。FFmpeg 采用有明确来源和许可信息的构建；现有 Tecplot 安装目录只读，不直接将整套软件装入发行包。保留第三方许可与版本/哈希清单；SDK 许可不明时只形成内部测试包，并明确记录分发待确认项，不能宣称可公开任意再分发。

## 验证与交付

单测覆盖源/冻结目录定位、路径重定位、启动取消/错误、FFmpeg 优先级和启动入口顺序。实际构建后，在项目内另一个含中文及空格的目录复制并测试；清理子进程 PATH/PYTHONPATH，避免依赖宿主 Python/SDK/FFmpeg。最小真实 SEQ 验证 inspect、温度矩阵、变换、ROI、原生提取、MP4（spawn/取消/解码）；核对 raw 元数据。无法在没有宿主 DLL 的干净机器实测时明确标注，不把同机测试当作独立机器验收。

交付文件夹 ZIP、SHA256、中文说明、依赖清单和本地验证记录。GitHub Release 用于后续附加该 ZIP、校验值和更新说明，源码 ZIP 不等于可执行包。发布、打标签或上传仍需用户后续明确授权。
