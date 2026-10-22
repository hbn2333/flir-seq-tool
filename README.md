# FLIR SEQ 本地处理工具

当前已实现 FLIR ResearchIR FFF `.seq` 的只读检查、摄氏温度逐帧浏览、工作时间范围选择、配置保存/加载、原生子 SEQ 导出，以及旋转裁剪预览与应用。本轮增加 Process / Measure 单窗口页面、多矩形 ROI、当前帧统计/分布/剖面、后台帧曲线及可追溯 CSV/PNG 导出；独立测温点和 MP4 导出尚未实现。

唯一工作目录为 `D:\carbonfoam\infrared`。原始文件放在 `raw`，始终只读；保存配置时默认打开 `selections`，可命名另存；子 SEQ 及同名视图配置默认配套导出到 `clips`。

版本与维护：[已知问题及验收记录](docs/KNOWN_ISSUES.md)、[版本变更记录](CHANGELOG.md)、[维护流程](docs/MAINTENANCE.md)。此前工作流修复已保存为 `0031a0f` 基线；本轮 Process / Measure 在 `codex/process-measure` 本地实现，未合并、推送或发布，Windows 原生界面及大文件仍待人工验收。

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

没有配套配置时，默认温标由首次显示帧的第 1 和第 99 百分位确定，并在浏览期间保持不变。若同目录存在同名 `.selection.json`，启动时核验后恢复其范围、变换和温标，再显示第一帧。也可显式给出固定温标：

```powershell
python -m flir_seq_tool view raw\Rec-000366-240_09_26_37_152.seq --vmin 20 --vmax 45
```

`--vmin` 与 `--vmax` 必须同时提供有限且递增的数值，单位均为摄氏度；显式参数优先于配套温标，改变已加载温标会标记为未保存。`--frame` 指当前打开 SEQ 的原始帧号：省略时显示恢复范围的首帧；范围内则跳到该帧；在完整源内但在恢复范围外则提示并显示范围首帧；超出源文件范围报错。

查看器的原始帧缓存和变换后帧缓存各默认最多保留 3 帧，其他帧按需读取；编辑时另有预览和坐标映射数据，但不会把整个 SEQ 载入内存。

## 选择工作时间范围

启动命令不变，默认进入 **Process**。顶部 **Measure** 切换到测量页，始终使用已 `Confirm range` 的工作范围和已 Apply 的变换；尚未确认的 Process 候选范围不会悄悄改变测量范围。Measure 提供同一条 `Current frame` 滑块和局部帧号输入框。

查看器保留 `Current frame` 浏览滑块，并提供 `Selected range` 双端滑块。拖动范围端点时，画面自动跳到对应帧；也可在 `Start frame` 和 `End frame` 输入局部整数帧号并按 Enter 精确定位。

点击 `Confirm range` 后，所选范围成为新的工作时间轴，两条滑块重新编号为 `0` 至 `N-1`。标题和状态区继续显示原始 SEQ 帧号、绝对时间、相对时间及基于真实端点时间戳的持续时间。可以在新区间内继续选择并确认，从而反复缩小范围；点击 `Reset full` 一次恢复完整序列。

该操作只改变查看状态，不裁切或改写原始 SEQ。相对时间以当前工作范围起点为零，并不是独立保存的实验加热零点。

## 保存与加载范围配置

`Save selection` 弹出保存文件框，保存当前候选时间范围、固定温标和已应用的旋转裁剪配置。可分别命名为方案 A/B；未填后缀时自动补 `.selection.json`。覆盖已有文件需要确认，确认后目标或 `.bak` 被外部改动则拒绝覆盖。保存使用临时文件校验和原子替换，保留单份 `.bak`。

新配置为 schema v3，增加 ROI 与显示设置，兼容读取旧 v1/v2（其 ROI 为空，不重写旧文件）。Process 的 `Save selection` 保存候选范围；Measure 的 `Save setup` 保存已确认范围，原生保存框标题注明该范围。两者都保存已应用变换及 ROI，但不保存 SQLite 统计结果。候选范围和已确认范围不同，保存其中之一不会把另一范围的修改标记为已保存。加载配置返回其工作范围、变换和 ROI；帧曲线需要重新 Calculate。

## ROI 与测量输出

在 Measure 点击 `Add ROI` 后在热图拖框，Esc 取消；每次只编辑一个活跃框。右栏支持名字、整数 x/y/width/height、删除及每页四行的 ROI 列表。列表行选择活跃 ROI；左侧勾选仅控制帧曲线和曲线导出的包含项，不改变当前帧卡片的活跃 ROI。完整长名称可用 F1 查看。

- 卡片显示有限像素的均值、最小、最大、温差和总体标准差（`ddof=0`），标明有效/总像素；全无效显示 N/A，单像素温差与标准差为零。
- `Current frame` → `Distribution`：32 个自动温度箱，纵轴为有效像素比例，箱边界逐帧变化。`Profile` → `Horizontal` / `Vertical`：按列/按行平均，横轴为裁剪后像素坐标，不是毫米或物理距离。无有限值的位置留空断线。
- 任意非直角旋转会插值，所有测量基于已重采样的温度矩阵，不声称等同于原始探测像素测温。纯裁剪和直角旋转沿用精确重排。
- `Frame curves` → `Calculate` 显式扫描工作范围内所有 ROI（包括未勾选项），使用独立 reader 和临时 SQLite，不将全时序温度矩阵载入内存。上图选择 Mean/Min/Max，下图固定 ΔT。显示最多按 2000 个桶抽稀并保留极值，缺失不连线；CSV 始终导出所有原始统计行。
- 计算期间编辑、切页和原生子 SEQ 导出被锁定；`Cancel` 等当前 SDK 调用结束。失败/取消可保留同一输入的旧结果。范围或 ROI 几何改变使结果失效；变换 Apply 前确认清空 ROI。改名、勾选、颜色和曲线指标不重算。
- `Export CSV` / `Export PNG` 根据当前子视图冻结当前帧或曲线，再选择输出路径，默认目录为 `analysis`。CSV 使用 UTF-8 BOM、完整精度、真实时间戳、源身份、变换、ROI 和版本列；PNG 单独重绘无 GUI 的报告图。曲线全未勾选、无结果或失效时禁用输出。
- 输出先写临时文件、解析验证后原子替换，覆盖需确认且核验目标快照，拒绝 raw、源文件及其别名。取消不创建输出，失败不静默转存其他路径；F1 查看完整错误。stat 检查不承诺消除所有跨进程竞争。

回到 Process 使用 `Load selection` 选择刚才的 setup 文件（Measure 不另设 Load 按钮），**不是加载最新文件**。先验证所选配置及源文件身份，有未保存修改时再询问是否替换当前视图。取消打开、保存或覆盖均不改变当前状态。旧版 v1 配置仍可读取，按未旋转、未裁剪处理，不自动改写文件。

`Apply` 只应用变换，不写配置。常用流程是 `Edit transform → Apply → Save selection` 保存方案；若要导出时间切片，可直接 `Apply → Export sub-SEQ → 选择目录和文件名 → Confirm export`，**无须先 Save selection**。编辑中的草稿不可保存或导出，必须先应用或取消。

编辑模式与普通模式共用窗口，但各自显示自己的控件和提示。建议窗口至少为 1000×800（100 DPI；其他 DPI 按比例放大）。较小窗口会提示放大；长路径/错误会缩略，按 `F1` 查看完整详情，同时输出到终端。

### 人工试用顺序

在 PowerShell 中进入 `D:\carbonfoam\infrared`，使用装有官方 FileSDK 的 Python（不要使用缺少 FileSDK 的 Anaconda 环境）：

```powershell
python -m flir_seq_tool view raw\Rec-000366-240_09_26_37_152.seq --vmin 23 --vmax 45
```

1. Process 输入 Start=10、End=14，点 `Confirm range`；`Edit transform` 输入 Total=-2.3，点 `Apply`。这一步先于创建 ROI。
2. 切 Measure，用 `Add ROI` 在样品上画三个框。选择不同列表行，检查卡片、Distribution、Profile 的 Horizontal/Vertical；拖滑块或输入 Frame。
3. 点 `Frame curves`、`Calculate`，检查 Mean/Min/Max 与下图 ΔT；取消/恢复勾选，确认不删除 ROI。改一个 ROI 宽度，确认显示失效后再 Calculate。全不勾选时不能导出曲线。
4. Measure 点 `Save setup` 命名保存；回 Process 点 `Load selection` 明确选择该文件，再切 Measure，检查框与名称恢复、曲线为空待重算。另设一个未 Confirm 的候选范围，比较 Process `Save selection` 与 Measure `Save setup` 保存范围。
5. 分别在 Distribution、双向 Profile、Frame curves 下导出 CSV/PNG，打开输出检查列、完整行数和无 GUI 图。返回 Process 再 Apply 不同变换，确认清空 ROI 提示；拒绝时保留原 ROI。

2026-10-22 用户反馈基础操作测试通过、当前操作未发现问题。上述清单保留作回归步骤；未逐项报告的边界测试和大文件性能不据此认定已全部验收。

## 旋转与裁剪

1. 点击 `Edit transform` 进入编辑模式，仍可切换帧检查效果。
2. 用 `+90°`、`-90°`、`180°` 快捷按钮调整方向，再用 `Fine angle` 滑块微调，范围为当前直角方向的 ±15°，精度为 0.1°。`Total` 输入框填写总角度并按 Enter。
3. 在图像中拖出裁剪框，拖动边框/手柄微调，也可在 `x`、`y`、`width`、`height` 输入框中输入整数。坐标属于旋转后的完整画布，从零开始。
4. 点击 `Apply` 应用；`Cancel` 放弃本次草稿。之后可再次点击 `Edit transform` 重新编辑。

改变旋转角度会将裁剪框重置为最大有效矩形；`Maximum` 重新选择该矩形。旋转产生的空白区不可纳入有效裁剪，越界时禁止应用。`Reset` 将草稿恢复为未旋转、未裁剪状态，仍需点击 `Apply` 才生效。

变换顺序为先旋转、后裁剪，同一个变换固定用于整个序列。纯裁剪及直角旋转使用精确像素重排，保留原始温度及无效值位置；非直角旋转使用双线性插值，所得温度是重采样结果。所有操作仅改变显示/分析视图，不改写原始 SEQ。

## 原生子 SEQ 导出

第一次点击 `Export sub-SEQ` 打开原生“另存为”，默认目录为 `clips`，建议文件名为源文件主名加原始帧范围。可选择其他目录和文件名；未填写扩展名时补充 `.seq`。取消对话框不会启动导出。

选择后显示范围、SEQ 与配套配置的完整路径及各自覆盖状态，第二次点击 `Confirm export` 才执行，不重复弹出对话框。长路径可按 F1 查看完整摘要。若确认前目标文件被外部修改，将更新摘要并要求再次确认；候选范围或导出配置改变后，需要重新选择目标。

例如在默认目录保存为 `trial_A.seq`，输出为：

```text
clips/trial_A.seq
clips/trial_A.selection.json
```

导出请求固定当时已应用的变换、温标与候选帧范围。后台先提取并校验临时 SEQ，再生成绑定子文件身份的配置；子文件局部帧从 0 开始，保留绝对时间。确认后任一正式目标发生变化都会拒绝覆盖。

准备和提取阶段支持取消；进入两文件发布阶段后取消按钮禁用，显示 `Finalizing paired files; please wait`。关闭窗口不会强行中断已开始的发布。

预检查和实际导出均会拒绝把源文件本身作为目标，或向受保护的 `raw` 目录写入。路径检查先于输出目录创建。

原生子 SEQ 只提取所选时间段内的原始帧，**不包含旋转裁剪后的像素结果**。使用本工具打开配套子 SEQ，会在首次绘图前自动恢复同目录同名配置；其他软件只打开 SEQ 时仍看到原方向、完整尺寸和原始温度像素。

移动时请一起移动这两个文件，保留文件名和 SEQ 修改时间。改名、修改 SEQ 或改变 mtime 可能使严格身份核验失败。缺少配置则显示原视图；无效或身份不匹配则警告，不扫描其他目录或套用“最近的”配置。

两文件不能作为一次跨文件原子替换：发布时创建 `.export-pending.json`，并保留唯一命名的旧文件恢复材料；全部校验成功后才清除标记。可处理的失败尝试回滚；遇到外部改动或恢复失败则保留材料并报告完整路径。发现 pending 时，自动及手动套用配置均被禁止，仍可查看原 SEQ。不要直接删除标记来消除警告；先保存错误详情并核查对应文件。程序不会按标记内容自动移动或删除文件，也不承诺断电后自动恢复。

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
python -m pytest -q -rs -p no:cacheprovider
```

本项目在 pytest 配置中禁用自动加载的 `napari` pytest 插件，因为该无关插件在 Windows 上先加载的原生 DLL 会与 FileSDK 创建 `ImagerFile` 时发生冲突。该设置不卸载或修改 napari。

集成测试只对最小真实 SEQ 读取温度并导出到测试临时目录；三份 raw 均在测试前后比较大小和纳秒 mtime。对话框自动测试使用替身，不代表已验证 Windows 原生弹窗体验；大文件交互性能由用户手动验收。
