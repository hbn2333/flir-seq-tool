# FLIR SEQ 本地处理工具

当前已实现 FLIR ResearchIR FFF `.seq` 的只读检查、摄氏温度逐帧浏览、工作时间范围选择、范围配置保存/加载、原生子 SEQ 导出，以及旋转裁剪预览与应用。测温点、矩形 ROI 分析、温度曲线/轴向分布导出和 MP4 导出尚未实现。

唯一工作目录为 `D:\carbonfoam\infrared`。原始文件放在 `raw`，始终只读；范围配置默认保存在 `selections`，原生子 SEQ 默认导出到 `clips`。

版本与维护：[已知问题及验收记录](docs/KNOWN_ISSUES.md)、[版本变更记录](CHANGELOG.md)、[维护流程](docs/MAINTENANCE.md)。当前初版仍有已记录的布局和工作流限制，不代表已完成全部人工验收。

## 环境

- Windows x64。
- Python 3.13。
- FLIR Science File SDK 2026.1.2。
- 与 Python 3.13 匹配的官方 FileSDK `cp313` wheel。

安装本地命令：

```powershell
python -m pip install -e . --no-build-isolation
```

## 只读保证

- 工具仅通过 `fnv.file.ImagerFile` 打开 SEQ。
- 工具不修改、不覆盖、不重命名原始文件，也不写回元数据。
- 集成测试会比较读取前后的文件大小和纳秒级修改时间。
- 大文件默认只读首末时间；只有显式选择 `full` 才逐帧扫描时间戳。

## 基本检查

快速读取文件尺寸、帧数和首末时间：

```powershell
python -m flir_seq_tool inspect raw\Rec-000366-240_09_26_37_152.seq
```

输出 JSON：

```powershell
python -m flir_seq_tool inspect raw\Rec-000366-240_09_26_37_152.seq --json
```

## 完整时间戳检查

逐帧读取时间戳并检查重复、倒退和异常大间隔：

```powershell
python -m flir_seq_tool inspect raw\Rec-000366-240_09_26_37_152.seq --timestamp-mode full --json
```

异常索引 `i` 表示异常发生在帧 `i` 与 `i+1` 之间。异常大间隔定义为严格大于正时间间隔中位数的 3 倍。

`endpoints` 模式的 `continuity_checked` 为 `false`，且不估计帧率；`full` 模式才报告完整连续性和由时间间隔中位数计算的估计帧率。

## 交互浏览温度帧

打开最小 SEQ，并用窗口下方的整数滑块切换帧：

```powershell
python -m flir_seq_tool view raw\Rec-000366-240_09_26_37_152.seq
```

从指定帧打开：

```powershell
python -m flir_seq_tool view raw\Rec-000366-240_09_26_37_152.seq --frame 20
```

默认温标由首次显示帧的第 1 和第 99 百分位确定，并在浏览期间保持不变，便于比较不同帧。也可显式给出固定温标：

```powershell
python -m flir_seq_tool view raw\Rec-000366-240_09_26_37_152.seq --vmin 20 --vmax 45
```

`--vmin` 与 `--vmax` 必须同时提供，单位均为摄氏度。查看器的原始帧缓存和变换后帧缓存各默认最多保留 3 帧，其他帧按需读取；编辑时另有预览和坐标映射数据，但不会把整个 SEQ 载入内存。

## 选择工作时间范围

查看器保留 `Current frame` 浏览滑块，并提供 `Selected range` 双端滑块。拖动范围端点时，画面自动跳到对应帧；也可在 `Start frame` 和 `End frame` 输入局部整数帧号并按 Enter 精确定位。

点击 `Confirm range` 后，所选范围成为新的工作时间轴，两条滑块重新编号为 `0` 至 `N-1`。标题和状态区继续显示原始 SEQ 帧号、绝对时间、相对时间及基于真实端点时间戳的持续时间。可以在新区间内继续选择并确认，从而反复缩小范围；点击 `Reset full` 一次恢复完整序列。

该操作只改变查看状态，不裁切或改写原始 SEQ。相对时间以当前工作范围起点为零，并不是独立保存的实验加热零点。

## 保存与加载范围配置

`Save selection` 保存当前候选时间范围、固定温标和已应用的旋转裁剪配置；`Load selection` 加载前严格核验源文件身份。保存采用临时文件与原子替换，并保留一份 `.bak`。尚未点击 `Apply` 的变换草稿不会成为已保存的变换。

## 旋转与裁剪

1. 点击 `Edit transform` 进入编辑模式，仍可切换帧检查效果。
2. 用 `+90°`、`-90°`、`180°` 快捷按钮调整方向，再用 `Fine angle` 滑块微调，范围为当前直角方向的 ±15°，精度为 0.1°。`Total` 输入框填写总角度并按 Enter。
3. 在图像中拖出裁剪框，拖动边框/手柄微调，也可在 `x`、`y`、`width`、`height` 输入框中输入整数。坐标属于旋转后的完整画布，从零开始。
4. 点击 `Apply` 应用；`Cancel` 放弃本次草稿。之后可再次点击 `Edit transform` 重新编辑。

改变旋转角度会将裁剪框重置为最大有效矩形；`Maximum` 重新选择该矩形。旋转产生的空白区不可纳入有效裁剪，越界时禁止应用。`Reset` 将草稿恢复为未旋转、未裁剪状态，仍需点击 `Apply` 才生效。

变换顺序为先旋转、后裁剪，同一个变换固定用于整个序列。纯裁剪及直角旋转使用精确像素重排，保留原始温度及无效值位置；非直角旋转使用双线性插值，所得温度是重采样结果。所有操作仅改变显示/分析视图，不改写原始 SEQ。

## 原生子 SEQ 导出

填写自定义名称后，第一次点击 `Export sub-SEQ` 显示范围、目标和覆盖提示，第二次确认才执行。导出在后台逐帧提取，支持进度显示和取消；临时文件验证通过后才替换目标。如果确认后目标文件发生变化，拒绝覆盖。

预检查和实际导出均会拒绝把源文件本身作为目标，或向受保护的 `raw` 目录写入。路径检查先于输出目录创建。

原生子 SEQ 只提取所选时间段内的原始帧，**不包含旋转裁剪后的像素结果**。旋转裁剪配方单独保存在 selection 配置中。

## 已知最小文件基线

`Rec-000366-240_09_26_37_152.seq` 的当前回归基线：

- 尺寸：$640\times480$。
- 帧数：65。
- 首帧时间：`2026-08-28T09:26:37.152000`。
- 末帧时间：`2026-08-28T09:26:38.932000`。
- 时间间隔中位数：0.021 s。
- 估计帧率：约 47.619 fps。
- 未发现重复、倒退或超过 3 倍中位数的间隔。

## 测试

```powershell
python -m pytest -v
```

本项目在 pytest 配置中禁用自动加载的 `napari` pytest 插件，因为该无关插件在 Windows 上先加载的原生 DLL 会与 FileSDK 创建 `ImagerFile` 时发生冲突。该设置不卸载或修改 napari。
