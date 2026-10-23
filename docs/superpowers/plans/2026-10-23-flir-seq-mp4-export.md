# MP4 展示视频导出实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Process 页安全导出包含已应用变换、固定温标、相对时间及倍率的 1080p MP4。

**Architecture:** 主线程负责原生对话框和状态协调；独立 spawn 工作进程独占 SDK reader、Agg renderer 和 FFmpeg。先扫描时间并等待用户确认，再流式编码、完整解码验证、复核身份和原子发布。时间映射、渲染、编码器和任务生命周期分开测试。

**Tech Stack:** Python 3.13、NumPy、Matplotlib Agg、现有 FileSDK / FrameTransformer、multiprocessing、FFmpeg libx264；不新增 Python 依赖，不依赖 ffprobe。

---

## 约束与执行方式

- 设计依据：`docs/superpowers/specs/2026-10-23-flir-seq-mp4-export-design.md`，用户于 2026-10-23 书面确认。
- 唯一工作目录 `D:\carbonfoam\infrared`。遵守用户目录约束，不创建外部 worktree；执行前检查工作区并创建 `codex/mp4-export` 分支，不直接在 main 实现功能。
- raw 内所有文件只读；真实测试只用 `Rec-000366-240_09_26_37_152.seq`。已有 clips、CSV、selection 不覆盖。测试输出使用 pytest 临时目录。
- 每项按测试失败 → 最小实现 → 测试通过 → 定向提交执行。下面代码块定义测试与关键算法；生命周期代码必须按列出的状态与故障矩阵逐项实现，不以仅通过 happy path 为完成。
- 不自动合并或推送。计划中的命令均从上述工作目录运行。

## 文件边界

执行状态：Task 1–7 已实现并完成定向验证；Task 8 的真实 SEQ、静态画面、文档已完成，最终完整回归和用户人工验收状态见同目录 `2026-10-23-flir-seq-mp4-export-execution.md`。下列原始步骤保留为实施检查依据，具体证据以执行记录为准。

| 文件 | 职责 |
| --- | --- |
| 新建 `src/flir_seq_tool/video_timeline.py` | 严格时间验证、整数时间网格、惰性源帧映射 |
| 新建 `src/flir_seq_tool/video_renderer.py` | 独立 Agg Figure，固定画布及像素布局 |
| 新建 `src/flir_seq_tool/video_encoder.py` | FFmpeg 发现、参数、流式编码、完整解码验证及进程回收 |
| 新建 `src/flir_seq_tool/video_export.py` | 冻结请求、spawn 工作任务、IPC、临时文件与发布 |
| 新建 `src/flir_seq_tool/video_controller.py` | GUI 无关的导出状态协调，方便注入假任务测试 |
| 修改 `src/flir_seq_tool/selection_dialogs.py` | 倍率单选与 MP4 Save As |
| 修改 `src/flir_seq_tool/viewer.py` | 绑定 Process 按钮、session 输入、timer 与关闭回调 |
| 修改 `src/flir_seq_tool/viewer_pages.py`、`viewer_layout.py` | MP4 忙状态、控件可用性、互不遮挡的按钮布局 |
| 修改 `src/flir_seq_tool/__main__.py` | spawn 安全入口 |
| 新建对应 `tests/unit/test_video_*.py`、`tests/integration/test_small_seq_video.py` | 算法、渲染、安全、故障、真实视频验证 |
| 修改 README、CHANGELOG；新建同名 `-execution.md` | 使用说明、验证证据与人工验收记录 |

## Task 1：时间网格与源帧映射

**Files:** 新建 `src/flir_seq_tool/video_timeline.py` 和 `tests/unit/test_video_timeline.py`。

- [ ] 写首个失败测试。约定 `VideoTimeline.from_timestamps(timestamps, speed)` 返回不可变对象，公开 `frame_count`、`duration_s`、`video_duration_s`、`iter_source_indices()`。

```python
from datetime import datetime, timedelta
from flir_seq_tool.video_timeline import VideoTimeline

def test_irregular_times_hold_previous_and_include_last():
    zero = datetime(2026, 1, 1)
    times = tuple(zero + timedelta(microseconds=u) for u in (0, 40_000, 100_000))
    timeline = VideoTimeline.from_timestamps(times, speed=1)
    assert timeline.frame_count == 4
    assert list(timeline.iter_source_indices()) == [0, 0, 1, 2]
    assert timeline.duration_s == 0.1
    assert timeline.video_duration_s == 4 / 30
```

- [ ] 运行 `python -m pytest tests/unit/test_video_timeline.py -q`，确认因缺少模块/实现失败。
- [ ] 用整数微秒实现：`relative_us` 保存 tuple；datetime 差通过 days/seconds/microseconds 精确转换。多帧严格递增，拒绝空序列、非 datetime、混用时区感知、无效倍率（含 bool）。FPS 固定 30。

```python
# D 为最后一帧的整数相对微秒，speed 为 1、2、5、10。
denominator = speed * 1_000_000
frame_count = 30 if D == 0 else (D * 30 + denominator - 1) // denominator + 1
# 第 k 帧在整数域比较：relative_us[index] * 30 <= k * denominator。
# 单调推进 index；最后一个输出强制 index = len(relative_us) - 1。
# 单帧序列输出 30 次 index=0；不生成长度为 frame_count 的像素或索引列表。
```

- [ ] 补参数化测试：四倍率；0.1 秒整网格、0.101 秒非整网格；首末帧；单帧 30 帧；长间隔保持；重复/倒退/混合时区报错；索引惰性迭代。逐个运行新测试确认失败后实现。
- [ ] 重跑本文件，全部通过后提交：`git add src/flir_seq_tool/video_timeline.py tests/unit/test_video_timeline.py`，`git commit -m "feat: map source timestamps to MP4 frames"`。

## Task 2：固定尺寸视频渲染器

**Files:** 新建 `src/flir_seq_tool/video_renderer.py`、`tests/unit/test_video_renderer.py`；只调用现有 `FrameTransformer.apply`，不修改其插值行为。

- [ ] 写失败测试，输入矩阵是已变换温度，渲染器不再旋转裁剪。

```python
import numpy as np
from flir_seq_tool.selection import DisplaySettings
from flir_seq_tool.video_renderer import VideoRenderer

def test_rgb_canvas_and_fixed_limits():
    display = DisplaySettings(colormap="magma", vmin_c=20, vmax_c=40)
    with VideoRenderer(display, speed=2) as renderer:
        rgb = renderer.render(np.full((100, 200), 30.0), relative_s=12.34)
        assert rgb.shape == (1080, 1920, 3)
        assert rgb.dtype == np.uint8
        assert renderer.image.get_clim() == (20, 40)
        assert renderer.time_text.get_text() == "t=12.34s   2×"
```

- [ ] 运行 `python -m pytest tests/unit/test_video_renderer.py -q`，确认失败。
- [ ] 建立专属 `Figure(figsize=(19.2, 10.8), dpi=100)` 与 `FigureCanvasAgg`，不使用 pyplot/global backend。画布白色；热图可用像素区 `(100, 110, 1450, 860)`，色条 `(1650, 170, 40, 740)`，时间位于上部独立区域。由输入宽高计算等比例居中图框；`imshow(origin="upper", interpolation="nearest", aspect="equal")`；隐藏坐标轴，色条标注 `Temperature (°C)`。更新 image/text 后 draw，返回连续 RGB 副本；退出清理 Figure 引用。
- [ ] 新增横长、竖长、方形输入布局测试；draw 后实际 axes 像素宽高比等于矩阵宽高比；不同温度输入不得改变 clim。检查全 NaN 帧仍可渲染、色条及时间不与热图相交。复制前后输入数组相等。
- [ ] 重跑测试通过，提交上述两个文件，提交说明 `feat: render fixed thermal video frames`。

## Task 3：编码器发现、参数与完整解码验证

**Files:** 新建 `src/flir_seq_tool/video_encoder.py`、`tests/unit/test_video_encoder.py`。

- [ ] 首先测试参数数组，不启动真实 FFmpeg。

```python
from pathlib import Path
from flir_seq_tool.video_encoder import encode_args

def test_encoding_arguments_keep_path_as_one_argument():
    path = Path("folder with spaces/output.partial.mp4")
    args = encode_args("ffmpeg", path)
    assert args[-1] == str(path)
    assert args[args.index("-s:v") + 1] == "1920x1080"
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-pix_fmt", args.index("-i")) + 1] == "yuv420p"
```

- [ ] 运行 `python -m pytest tests/unit/test_video_encoder.py -q` 确认失败。实现明确参数，不经 shell：

```python
def encode_args(executable, target):
    return [str(executable), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s:v", "1920x1080",
            "-r", "30", "-i", "pipe:0", "-an", "-c:v", "libx264",
            "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(target)]
```

- [ ] 增加 `discover_encoder()`：`shutil.which("ffmpeg")`，有超时地调用 `-encoders`，解析编码器字段必须精确含 libx264。缺失/超时/失败抛清楚错误；路径冻结成绝对路径。Windows 使用 `CREATE_NO_WINDOW`，其他平台 flags=0。
- [ ] 定义 `VideoEncoder(executable, target, cancel_event)` 上下文管理器，公开 `write(rgb)`、`finish()`；单独线程持续排空 stderr，最多保存最后 64 KiB。写入在工作线程执行，任务监督线程可终止 FFmpeg 解除 pipe 阻塞。退出始终 close stdin、等待、必要时 terminate/kill、join stderr 线程，非零退出码带诊断尾部。取消抛本模块 `VideoCancelled`。
- [ ] 实现 `validate_video(executable, target, expected_frames, cancel_event)`：FFmpeg `-xerror -err_detect explode -i target -map 0:v:0 -an -sn -dn -vsync 0 -c:v rawvideo -pix_fmt rgb24 -f framehash pipe:1`。流式消费 framehash，核验 `#dimensions 0:` 为 1920×1080、`#tb 0:` 为 1/30、每条帧记录 size 为 6220800、记录数为 N；必须成功退出，不能只信容器声明。stderr 同样持续排空且限长，不载入整段解码像素。
- [ ] 补模拟故障测试：编码器缺失、名称误匹配、超时、BrokenPipe、失败退出、超大 stderr、损坏视频、错误尺寸、错误帧数、没有 header、验证取消。再用本机 FFmpeg 在临时目录编码两帧合成图，验证 framehash 格式和往返成功；不兼容时修正解析并记录实际输出。
- [ ] 通过后提交两个文件，说明 `feat: stream and validate H264 video encoding`。

## Task 4：冻结请求与文件安全

**Files:** 新建 `src/flir_seq_tool/video_export.py`、`tests/unit/test_video_export.py`。复用 `file_safety.py`，不改变原生 SEQ 配套导出协议。

- [ ] 定义并测试 `VideoExportRequest` 的 frozen dataclass 字段：`source_path: Path`、`source: SourceIdentity`、`start_frame: int`、`end_frame: int`、`transform_json: str`、`display: DisplaySettings`、`speed: int`、`target_path: Path`、`target_snapshot: TargetSnapshot`。transform 使用 `selection.transform_to_dict(transform)` 序列化，再通过 `selection.transform_from_dict(json.loads(transform_json), source)` 重建，不能传可变 NumPy 矩阵或 reader，不新增另一套格式。
- [ ] `build_video_request(session, target, speed)` 从 `session.measure_source_range()` 取已确认范围，从 `session.applied_transform` 取已 Apply 变换，从 `color_limits/colormap` 取固定显示配置，使用现有 source_identity；验证路径和后缀后只捕获一次目标快照。
- [ ] 先写下列安全测试并运行，确认缺少实现导致失败：受保护 raw 子路径、当前源路径/硬链接、非普通文件、大小写 .MP4、目标确认后新增/修改/删除、源 size/mtime 改变。所有失败都断言既有目标字节保持不变。

```python
# 发布时的强制顺序；使用请求原始快照，不能重新确认。
validated = validate_write_path(request.target_path,
    source_path=request.source_path, raw_root=DEFAULT_RAW_ROOT)
assert_snapshot(validated, request.target_snapshot)
# 再核验 source 的 size/mtime 和 SDK 身份；成功才允许 os.replace。
# 该检查不能提供针对恶意外部写入者的事务锁，保留现有快照保护边界。
```

- [ ] 临时文件通过同目录 `tempfile.mkstemp(suffix=".partial.mp4")` 独占创建并立即关闭 fd。只在编码确认后创建；取消预检不产生临时视频。发布前完整验证、关闭全部文件句柄、复核源和目标，然后 `os.replace`。finally 仅清理本任务临时文件；失败诊断包含无法清理的具体路径。
- [ ] 运行 `python -m pytest tests/unit/test_video_export.py -q`。通过后提交上述文件，说明 `feat: guard MP4 export snapshots and publication`。

## Task 5：后台进程、确认握手和取消

**Files:** 扩展 `video_export.py` 和其测试；修改 `src/flir_seq_tool/__main__.py`；新增 `tests/unit/test_video_job.py`。

- [ ] 写 spawn 回归测试：`import flir_seq_tool.__main__` 不执行 CLI；子进程能导入模块并退出。先运行失败，再改入口：

```python
from .cli import main

if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    raise SystemExit(main())
```

- [ ] 实现 `VideoJob(request)`：`start()`、`confirm()`、`cancel()`、`poll()`、`close()`。内部 `get_context("spawn")`；单个工作进程、取消 Event、确认 Event、有界 Queue。消息类型固定为 `progress`、`ready`、`result`，只传小型不可变数据。高频 progress 可丢弃，ready/result 不得静默丢失；非阻塞 poll 清空队列。记录进程退出码，若无 result 则报告异常退出。
- [ ] 状态链明确为 `scanning → awaiting_confirmation → encoding → validating → publishing → succeeded`；取消终态 `cancelled`，异常终态 `failed`。ready 携带 D、N、N/30、范围、倍率、温标、完整目标路径、覆盖标记。等待确认期间 Event 有界轮询，取消随时可达；只能确认一次。
- [ ] worker 自行打开 `ScienceFileReader`，扫描 start..end 时间戳，构造 Task 1 时间表；等待确认后建立 Task 2 renderer 和 Task 3 encoder。仅缓存上一次选中源帧的变换矩阵/RGB，重复源索引直接复用；逐帧写入；最后验证并按 Task 4 发布。每次读取、渲染、写入前检查取消。
- [ ] 生命周期监督：取消线程可以终止编码/解码子进程来解除阻塞；采用 Windows Job Object（kill-on-close）约束 FFmpeg 子进程的所有权，确保异常杀死 worker 不留下编码器。正常取消给 worker 清理机会，再在有界超时后 terminate/kill worker；发布状态禁止普通取消。关闭窗口同样执行回收，不在死进程上无限 join。Job Object 失败必须在编码前报错，不能假装有回收保证。
- [ ] 使用真实辅助进程测试“停止消费 stdin”“持续输出 stderr”“忽略优雅退出”；分别在扫描、等待确认、编码、验证时取消，10 秒内回收 worker 与 FFmpeg，旧目标不变。模拟 source reader 读取异常、渲染异常、无结果崩溃、清理失败。发布阶段 cancel 返回不可取消。
- [ ] 运行 `python -m pytest tests/unit/test_video_job.py tests/unit/test_video_export.py tests/unit/test_cli.py tests/unit/test_package.py -q`。通过后提交相关四个文件，说明 `feat: run cancellable MP4 jobs in isolated processes`。

## Task 6：原生倍率设置和另存为

**Files:** 修改 `src/flir_seq_tool/selection_dialogs.py`、`tests/unit/test_selection_dialogs.py`。

- [ ] 先添加后缀失败测试：

```python
from pathlib import Path
from flir_seq_tool.selection_dialogs import normalize_video_path

def test_mp4_path_normalization():
    assert normalize_video_path("") is None
    assert normalize_video_path("demo") == Path("demo.mp4")
    assert normalize_video_path("demo.MP4") == Path("demo.MP4")
```

- [ ] 运行定向测试确认失败后实现归一化：拒绝其他后缀、尾点/空格、Windows 保留文件名；保留完整用户路径，不把完整路径当作 clip name。
- [ ] 增加 `choose_video_speed() -> int | None` 和 `choose_video_export(initial_dir, suggested_name) -> Path | None`。倍率 Tk modal 单选 1/2/5/10，默认 1；确定返回数值，取消/Escape/关闭返回 None。复用 `_run` 的主线程检查与 owned root 生命周期，通过小范围提取共享 parent 上下文避免复制清理逻辑；不修改已有对话框行为。
- [ ] MP4 Save As `confirmoverwrite=False`，由 ready 后统一确认覆盖。模拟 root 已有/自建、TclError、取消、非主线程调用，断言自建 root 清理；测试已有 SEQ/JSON/CSV/PNG 对话框仍通过。
- [ ] 运行 `python -m pytest tests/unit/test_selection_dialogs.py -q`，通过后提交两个文件，说明 `feat: add MP4 speed and save-as dialogs`。

## Task 7：Process 接入与 UI 生命周期

**Files:** 新建 `src/flir_seq_tool/video_controller.py`、`tests/unit/test_video_controller.py`；修改 viewer、viewer_pages、viewer_layout 及对应测试。

- [ ] 建立可注入假 dialogs/job 的 `VideoController`：构造参数为 `session, dialogs, output_dir, job_factory, on_busy, on_message`，公开 `start(), poll(), cancel(), close()`。start 按倍率 → 另存为 → 冻结请求 → 启动预检排序；poll 收到 ready 时在主线程调用 `dialogs.confirm(summary)`，同意调用 job.confirm，不同意调用 job.cancel。终态清 busy；关闭后不得再调用 GUI 回调。先用假对象断言完整事件序列及每个取消出口，再实现。
- [ ] 摘要包含完整目标路径、覆盖状态、源确认范围、源时长、预计视频时长、倍率、1920×1080/30fps、温标；单帧说明 1 秒静止；通用说明“展示视频可能跳过/重复源帧，不替代温度数据”。错误显示 BoundedMessage，完整详情可 F1。
- [ ] Process 底部同一行调整为下面三列，不压缩状态文本区：

```python
NORMAL_RECTS["export"] = (0.28, 0.255, 0.20, 0.035)
NORMAL_RECTS["video"] = (0.50, 0.255, 0.18, 0.035)
NORMAL_RECTS["cancel"] = (0.70, 0.255, 0.18, 0.035)
```

- [ ] 新增 Export MP4 Button，加入 normal_control_axes/process_widgets/返回 Viewer 控件对象。`PageCoordinator.set_busy` 接受 `video_export`；busy 时只有 Cancel export 可用（publishing 除外）。所有入口有逻辑 guard，不能只依赖按钮灰色。预检前清除旧原生 SEQ confirmation，避免两个导出摘要互相覆盖。
- [ ] viewer timer 每 100 ms 调用 controller.poll；原生 poll_export 在 video_export busy 时不得清空状态或激活控件。Cancel export 按当前任务分流，close_event 回收 controller。编辑变换、Measure 页均隐藏且停用 MP4 控件；恢复页时无鼠标捕获残留。
- [ ] 测试 pending 不等于 confirmed 时只导出 confirmed；变换草稿期间拒绝；所有 busy 阶段不可切页/改范围/启动分析或其他导出；错误与取消后控件恢复；窗口关闭停止 timer 并回收。测试宽/窄窗口按钮及状态 bbox 不相交。
- [ ] 运行 `python -m pytest tests/unit/test_video_controller.py tests/unit/test_viewer_pages.py tests/unit/test_viewer_layout.py tests/unit/test_viewer.py -q -p no:cacheprovider`。全部通过后定向提交，说明 `feat: integrate MP4 export into Process workflow`。

## Task 8：真实 SEQ、完整回归与交付

**Files:** 新建 `tests/integration/test_small_seq_video.py`；修改 README.md、CHANGELOG.md；新建 `docs/superpowers/plans/2026-10-23-flir-seq-mp4-export-execution.md`。

- [ ] 集成测试使用最小 SEQ 的源帧 10..14，参数化 speed=1 和 5，另加单帧与非零角度/裁剪场景。用与 UI 相同的 VideoJob 流程等待 ready、confirm、结果；带测试超时并 finally 回收。
- [ ] 每次测试前后在 finally 比较原始源 `(st_size, st_mtime_ns)`；输出仅 tmp_path。成功后调用真实完整解码验证；N 使用源时间戳独立计算，不能只引用生产函数的结果作为唯一期望。
- [ ] 抽取最后解码帧，与同一源末帧的独立渲染作有损误差比较；允许 H264 小误差，不能精确像素相等。可先用合成明显不同色块帧验证末帧身份，再对真实帧检查非空、色条固定、布局比例。检查所有源帧进入 renderer 前的矩阵来自 `FrameTransformer.apply` 且固定 clim 不随帧变。
- [ ] 保存横长、竖长、实际裁剪的代表性渲染图到测试临时目录，用图像查看工具人工检查色条、时间、边距。视频用播放器做最终人工验收，不以静态截图替代视频验收。
- [ ] 运行 `python -m pytest tests/integration/test_small_seq_video.py -q -rs -p no:cacheprovider`。SDK/FFmpeg 缺失可以显式 skip，但执行记录不能把 skip 当真实验证通过；本机已发现 FFmpeg，预期应运行成功。
- [ ] 更新 README 的安装要求和 MP4 使用步骤；说明 FFmpeg/libx264、30fps 时间映射、单帧规则、展示用途、另存为和取消。删除“MP4 不支持”的旧描述，不声称导出科学温度视频。CHANGELOG 增加未发布条目，不虚构版本/tag/release。
- [ ] 记录全部 raw 的文件名、大小和 mtime；执行 `python -m pytest -q -rs -p no:cacheprovider --tb=short`；再比对 raw 元数据。基线历史为 692 passed / 2 skipped，不将它当作本次结果；记录本次真实数量和耗时。
- [ ] 运行 `git diff --check`，审查定向 diff，不添加生成视频、raw、SDK、缓存或用户实验输出。提交测试与文档，说明 `test: verify MP4 export and document workflow`。
- [ ] 交付用户测试命令 `python -m flir_seq_tool view raw\Rec-000366-240_09_26_37_152.seq`；请用户检查 1×/5×、取消/关闭、覆盖既有目标、旋转裁剪画面、色条及时间文字。人工确认后才讨论合并/推送。

## 计划自查与完成标准

- 时间、画面、交互、安全、资源、依赖、回归分别由 Task 1、2、6–7、4、3–5、3、8 覆盖。
- `video_encoder.py` 和 `video_controller.py` 是对设计中 video_export 职责的进一步拆分，不改变已批准功能范围。
- Windows spawn 入口和后台 FFmpeg 进程树回收是本次必须交付内容，不能留作后续优化。
- O(N) 时间戳允许，完整温度/RGB 序列不允许。测试可收集短索引列表，生产流必须惰性。
- 每个任务都应在 execution 文档记录失败测试、通过测试、提交哈希和偏差；未运行/跳过/人工待验收须分别标明。
- 当前只是计划，未实现 MP4、未运行新增测试、未合并或推送功能。
