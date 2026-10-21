# FLIR SEQ 工作流修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复编辑界面遮挡，支持配置文件选择/另存，导出原生子 SEQ 及配套配置并在重新打开时安全恢复视图。

**Architecture:** 保留 Matplotlib、现有 session 和 FileSDK 原生提取。抽取共享布局、文件对话框、文件写入保护、配套发布四个有限职责；配置仍采用 schema v2。先完成可独立验收的布局和配置交互，再接入两文件发布及启动恢复。

**Tech Stack:** Windows、Python 3.13.5、Matplotlib、NumPy/SciPy、FLIR Science File SDK 2026.1.2、pytest、tkinter 原生文件对话框。

---

## 执行约束与准备

设计依据：`D:\carbonfoam\infrared\docs\superpowers\specs\2026-10-21-flir-seq-workflow-fixes-design.md`，用户于 2026-10-21 审核通过。当前代码基线 `2b1a5d7`，设计提交 `a029ad7`，分支 `codex/fix-viewer-workflow`。

用户明确限定唯一工作目录，因此不另建 worktree。沿用当前修复分支，不在 main 直接开发，不移动 `v0.1.0`，不自动 push、合并、打标签或创建 GitHub Release。

本计划中的相对路径均以唯一工作目录 `D:\carbonfoam\infrared` 为根；执行命令前必须核实位置。以下为 PowerShell 命令，不使用 Anaconda Python：

```powershell
Set-Location -LiteralPath 'D:\carbonfoam\infrared'
$seqPython = 'C:\Users\Tracer\AppData\Local\Programs\Python\Python313\python.exe'
git rev-parse --show-toplevel
git status --short --branch
& $seqPython -c "import sys, fnv.file; print(sys.executable); print(sys.version)"
```

预期：仓库根目录正确，当前为修复分支；解释器与已验证环境一致。若有其他未提交修改，先识别并保留，不使用 reset/checkout 清除。

- 原始 `raw` 只读。危险路径、覆盖、异常、取消测试只能使用 `tmp_path` 模拟文件。
- 不修改插值、旋转中心、裁剪坐标或温度换算，不整段加载温度矩阵。
- 每个任务按“补失败测试 → 确认失败原因 → 最小实现 → 目标测试 → 回归 → 提交”推进；测试通过前不提交功能代码。
- 表中的一行测试情景为一个小循环，不把多个失败原因同时引入。不要一次写完所有实现再补测试。
- 仅暂存本任务文件；提交前执行 `git diff --check` 和 `git diff --cached --check`，检查暂存差异。
- 本文记录的是待执行步骤，不把代码片段、预期输出或旧的 298 项通过当成新的验证结果。

## 文件职责与依赖

| 文件（相对工作目录） | 本轮职责 |
| --- | --- |
| `src/flir_seq_tool/viewer_layout.py`（新增） | 公共区域坐标、编辑控件坐标、有限行数的信息排版、最小窗口提示 |
| `src/flir_seq_tool/viewer.py` | 使用布局；配置按钮协调；session 显式路径；冻结导出视图；启动恢复 |
| `src/flir_seq_tool/transform_editor.py` | 使用共享布局；标签位置；草稿状态；保持原选择框逻辑 |
| `src/flir_seq_tool/file_safety.py`（新增） | 复用目标快照、拒绝受保护路径、替换前快照比较 |
| `src/flir_seq_tool/selection.py` | 显式路径读写、一次备份、覆盖确认快照；保留旧入口 |
| `src/flir_seq_tool/selection_dialogs.py`（新增） | tkinter 主线程对话框及可注入接口；无 session/SDK 依赖 |
| `src/flir_seq_tool/export_bundle.py`（新增） | 配套路径、新子文件身份、JSON 准备、pending/恢复材料、两文件发布 |
| `src/flir_seq_tool/extraction.py` | 原生提取保持不变；导出请求可携带配置；发布阶段取消门；调用配套发布 |
| `src/flir_seq_tool/cli.py` | 区分未提供 `--frame` 和显式 `--frame 0` |
| `tests/unit/test_viewer_layout.py`、`test_file_safety.py`、`test_selection_dialogs.py`、`test_export_bundle.py`（新增） | 对应新模块的独立测试 |
| `tests/unit/test_viewer.py`、`test_transform_editor.py`、`test_selection.py`、`test_extraction.py`、`test_cli.py` | 现有行为回归及集成连接测试 |
| `tests/integration/conftest.py`（新增）及现有 `test_small_seq_extract.py`、`test_small_seq_viewer.py` | 全部 raw 元数据保护、最小真实子 SEQ 配套往返 |
| `README.md`、`CHANGELOG.md`、`docs/KNOWN_ISSUES.md`、`docs/MAINTENANCE.md` | 使用流程、已知限制、验证证据和真实发布状态 |

依赖顺序：任务 1–2 为布局；任务 3–5 为配置；任务 6–8 为配套导出；任务 9 为启动恢复；任务 10 为真实数据与交付。每个阶段可单独检查，但最后不能遗漏完整回归。

## 任务 1：复现遮挡并统一控件区域

文件：新增 `src/flir_seq_tool/viewer_layout.py`、`tests/unit/test_viewer_layout.py`；修改 `src/flir_seq_tool/viewer.py` 的 `build_viewer`、`set_normal_controls_visible`、`refresh_transform_status`；修改 `src/flir_seq_tool/transform_editor.py` 的 `build_transform_editor`。

- [x] 在现有 `tests/unit/test_viewer.py` 复用 `FakeTemperatureReader` 和 `make_frames`，先加入模式切换失败测试：

```python
def test_edit_mode_hides_normal_export_text_and_marks_draft():
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames())))
    try:
        ui.export_text.set_text("Previous export summary")
        ui.progress_text.set_text("Previous export progress")
        ui.edit_transform_button._observers.process("clicked", None)
        assert not ui.export_text.get_visible()
        assert not ui.progress_text.get_visible()
        assert ui.status_text.get_visible()
        assert "draft" in ui.transform_status_text.get_text().lower()
        ui.transform_editor.cancel_button._observers.process("clicked", None)
        assert ui.export_text.get_visible()
        assert "applied" in ui.transform_status_text.get_text().lower()
    finally:
        plt.close(ui.figure)
```

- [x] 执行 `& $seqPython -m pytest tests/unit/test_viewer.py -k edit_mode_hides -q`，预期因普通文本未隐藏/仍显示 applied 而失败，不接受导入或测试夹具错误作为有效红灯。
- [x] 创建共享布局字典，普通模式位置先保持不变；编辑模式改为以下独立区域。两个构建函数均从该模块读取，不再分别定义重复坐标：

```python
# viewer_layout.py；figure 坐标，0..1
EDITOR_RECTS = {
    "angle_slider": (0.15, 0.425, 0.40, 0.025),
    "angle_box": (0.69, 0.410, 0.13, 0.040),
    "quick": tuple((x, 0.345, 0.13, 0.040)
                   for x in (0.15, 0.32, 0.49, 0.66)),
    "crop": tuple((x, 0.270, 0.13, 0.040)
                  for x in (0.15, 0.32, 0.49, 0.66)),
    "actions": tuple((x, 0.210, 0.15, 0.040)
                     for x in (0.15, 0.33, 0.51, 0.69)),
}
TEXT_POSITIONS = {
    "editor_status": (0.15, 0.190),
    "editor_error": (0.15, 0.163),
    "transform": (0.15, 0.125),
    "timeline": (0.15, 0.102),
    "message": (0.15, 0.052),
}

def label_above(box):
    box.label.set_position((0.0, 1.12))
    box.label.set_horizontalalignment("left")
    box.label.set_verticalalignment("bottom")
```

普通控件坐标从当前 `build_viewer` 原样迁移到同一模块常量；编辑框标签均调用 `label_above`，不增加脱离控件可见性管理的 figure 标签。普通 export/progress 文本随普通控件一起隐藏/恢复。`refresh_transform_status` 在编辑时根据 `transform_editor.draft` 显示 `Transform: draft`，退出后恢复原 `transform_status`。

- [x] 在现有 viewer 测试加入 3 个逻辑尺寸 × 3 个 DPI × 普通/编辑模式参数化检查；新模块测试验证坐标不越界。实际绘制后检查标签、数值与非所属控件的 bbox，允许文字与其所属输入框/按钮相交，不允许把所有 TextBox 标签排除。下面是角度冲突的具体断言：

```python
@pytest.mark.parametrize("size", [(10, 8), (12, 10), (16, 10)])
@pytest.mark.parametrize("dpi", [100, 150, 200])
def test_angle_value_does_not_overlap_total_box(size, dpi):
    ui = build_viewer(ViewerSession(FakeTemperatureReader(make_frames())))
    try:
        ui.figure.set_size_inches(*size)
        ui.figure.set_dpi(dpi)
        ui.edit_transform_button._observers.process("clicked", None)
        ui.transform_editor.set_angle(-3.2)
        ui.figure.canvas.draw()
        renderer = ui.figure.canvas.get_renderer()
        value = ui.transform_editor.angle_slider.valtext.get_window_extent(renderer)
        box = ui.transform_editor.angle_box.ax.get_window_extent(renderer)
        label = ui.transform_editor.angle_box.label.get_window_extent(renderer)
        assert not value.overlaps(box)
        assert not value.overlaps(label)
    finally:
        plt.close(ui.figure)
```

- [x] 执行 `& $seqPython -m pytest tests/unit/test_viewer_layout.py tests/unit/test_viewer.py tests/unit/test_transform_editor.py -q`。预期全部通过，包括原有真实 canvas 单击/拖拽回归。
- [x] 提交上述布局源码及测试，消息：`fix: separate transform controls from viewer status`。

## 任务 2：长信息、缩放和完整路径可访问性

文件：修改 `src/flir_seq_tool/viewer_layout.py`、`src/flir_seq_tool/viewer.py`、`src/flir_seq_tool/transform_editor.py`；测试 `tests/unit/test_viewer_layout.py`、`tests/unit/test_viewer.py`。

- [x] 写有限宽度、超长路径及窄窗口测试。布局模块提供 `fit_message(text, *, max_chars, max_lines) -> str`，保持换行、支持无空格路径；过长信息末尾明确标记详情入口。测试代码：

```python
from flir_seq_tool.viewer_layout import fit_message

def test_long_message_is_bounded_without_losing_detail_hint():
    original = "Save failed: D:/" + "nested/" * 60 + "a.selection.json"
    displayed = fit_message(original, max_chars=60, max_lines=2)
    assert len(displayed.splitlines()) <= 2
    assert all(len(line) <= 60 for line in displayed.splitlines())
    assert "F1" in displayed
```

- [x] 执行 `& $seqPython -m pytest tests/unit/test_viewer_layout.py -q`，先确认新增测试失败。
- [x] 实现纯文本边界函数；界面保留完整字符串，不用显示字符串反向覆盖原信息：

```python
import textwrap

def fit_message(text, *, max_chars, max_lines):
    if max_chars < 24 or max_lines < 1:
        raise ValueError("message area too small")
    lines = []
    for paragraph in text.splitlines() or [""]:
        lines.extend(textwrap.wrap(paragraph, width=max_chars,
                                   break_long_words=True,
                                   break_on_hyphens=False) or [""])
    if len(lines) <= max_lines:
        return "\n".join(lines)
    hint = "... [F1: details]"
    result = lines[:max_lines]
    result[-1] = result[-1][:max_chars - len(hint)] + hint
    return "\n".join(result)
```

为 `ViewerUI` 增加 `message_details: dict[str, str]`。记录 error、export、progress、editor 状态原文；F1 将当前可见模式的完整信息写到终端（与 CLI 启动方式一致），并显示 `Full details printed to console`。成功/失败路径必须包含在原文。确认导出仍展示两个目标各自的覆盖标记，不能只依赖被截断路径。后续任务 4 接入原生详情窗口，可复用同一原文。

resize 回调使用画布英寸尺寸判断逻辑最小 10×8；低于此尺寸显示 `Enlarge window to at least 1000 x 800 logical pixels`。用 renderer 实测文本可用宽度迭代降低 `max_chars`，限制每块的最大行数；绘制回调设重入保护，不在每次 draw 递归触发 draw。

- [x] 新增长错误/导出路径/编辑草稿文字、单帧模式、连续 resize 的 bbox 测试，验证 F1 输出包含未截断全文（`capsys`）；低于最小尺寸只要求提示清楚，不宣称控件全部可用。
- [x] 执行 `& $seqPython -m pytest tests/unit/test_viewer_layout.py tests/unit/test_viewer.py tests/unit/test_transform_editor.py -q`，通过后提交：`fix: bound viewer messages and expose full details`。

检查点 A：布局测试通过可展示截图给用户；真实 Windows 窗口验收仍留到任务 10，不用 Agg 测试冒充人工验收。

## 任务 3：显式配置路径与写入保护

文件：新增 `src/flir_seq_tool/file_safety.py`、`tests/unit/test_file_safety.py`；修改 `selection.py`、`extraction.py`、`tests/unit/test_selection.py`、`tests/unit/test_extraction.py`。

- [ ] 在 `test_selection.py` 使用现有 `sample_document` 加入两套文件保存/加载失败测试：

```python
def test_explicit_selection_path_round_trip(tmp_path):
    from flir_seq_tool.file_safety import TargetSnapshot
    source = tmp_path / "sample.seq"
    source.write_bytes(b"source")
    doc = sample_document(source)
    store = SelectionStore(tmp_path / "defaults")
    target = tmp_path / "custom" / "experiment-a.selection.json"
    saved = store.save_to(
        target, doc, source_path=source,
        expected_target=TargetSnapshot.capture(target),
        expected_backup=TargetSnapshot.capture(target.with_name(target.name + ".bak")),
    )
    assert saved == target
    assert store.load_path(target, doc.source) == doc
    assert not store.path_for(source).exists()
```

- [ ] 执行 `& $seqPython -m pytest tests/unit/test_selection.py -k explicit_selection_path -q`，预期缺少新方法而失败。
- [ ] 将现有 `TargetSnapshot` 原样移入 `file_safety.py`，`extraction.py` 从新模块导入并保留模块层名称，保证旧导入兼容。新增以下公共保护函数：

```python
from pathlib import Path

def assert_snapshot(path, expected):
    if TargetSnapshot.capture(path) != expected:
        raise RuntimeError(f"target changed after confirmation: {path}")

def validate_write_path(path, *, source_path, raw_root):
    target = Path(path).resolve()
    source = Path(source_path).resolve()
    protected = Path(raw_root).resolve()
    if target == source or target.is_relative_to(protected):
        raise ValueError(f"protected output path: {path}")
    if target.exists() and source.exists() and target.samefile(source):
        raise ValueError(f"output aliases source: {path}")
    if target.exists() and not target.is_file():
        raise ValueError(f"output is not a regular file: {path}")
    return target
```

检查源路径、raw、目标父目录别名，以及 `.bak`；可用时测试 symlink/junction/hardlink，无法创建链接必须记录 skip 原因，不默认为已验证。保持现有导出目标必须直接位于 clips_root 的额外约束。

- [ ] 增加 `SelectionStore.load_path(target, expected_source=None)`；将旧 load_expected 的“先验证源，再构造变换”搬入新入口。旧 load/load_expected 按 `path_for` 转调。

```python
def load_path(self, target, expected_source=None):
    payload, _ = _validate_root_version_and_keys(_read_payload(Path(target)))
    configured_source = SourceIdentity.from_dict(
        _require_mapping(payload["source"], "source")
    )
    if expected_source is not None:
        validate_source_identity(expected_source, configured_source)
    return SelectionDocument.from_dict(payload)
```

- [ ] 增加 `SelectionStore.save_to(target, document, *, source_path, expected_target, expected_backup)`：保留现有写临时 JSON、flush/fsync、回读校验、单份 `.bak`、失败恢复流程；将 mkdir 改为已校验的 target.parent；在任何写入前和正式替换前核对两个快照及路径。目标变化不得覆盖。恢复前再次判断本事务目标仍空缺及备份仍为本次移入的文件，否则保留材料并报告完整路径。旧 `save(source_path, document)` 捕获当前两个快照后转调；不要求 source 必须存在，以兼容纯存储单元测试，但 GUI 的 source_identity 仍要求实际文件。

- [ ] 逐项加入失败测试：目标原不存在却被外部创建、目标被改写、备份被改写、源/raw/目录目标、final replace 失败、restore 失败、schema v1 只读兼容。目标外部变化断言其字节不变、无成功返回；保存失败不允许删除外部新文件。
- [ ] 执行 `& $seqPython -m pytest tests/unit/test_file_safety.py tests/unit/test_selection.py tests/unit/test_extraction.py -q`。全部通过后提交：`feat: add guarded explicit selection paths`。

## 任务 4：原生对话框适配器

文件：新增 `src/flir_seq_tool/selection_dialogs.py`、`tests/unit/test_selection_dialogs.py`。

- [ ] 先测试保存后缀与取消值，再测试假的 tkinter 调用及 finally 释放 root。对外对象 `SelectionDialogs` 提供 `choose_save(initial_dir, suggested_name)`、`choose_load(initial_dir)`、`confirm(message)`、`show_details(message)`；前两者返回 `Path | None`，confirm 返回 bool，详情无状态修改。

```python
import pytest
from pathlib import Path
from flir_seq_tool.selection_dialogs import normalize_selection_path

@pytest.mark.parametrize("text, expected", [
    ("", None),
    ("experiment", Path("experiment.selection.json")),
    ("experiment.selection.json", Path("experiment.selection.json")),
])
def test_normalize_selection_path(text, expected):
    assert normalize_selection_path(text) == expected

def test_reject_seq_suffix():
    with pytest.raises(ValueError):
        normalize_selection_path("experiment.seq")
```

- [ ] 执行 `& $seqPython -m pytest tests/unit/test_selection_dialogs.py -q`，确认新能力缺失后实现：

```python
def normalize_selection_path(value):
    if not value:
        return None
    path = Path(value)
    if path.name.lower().endswith(".selection.json"):
        return path
    if path.suffix:
        raise ValueError("Use a .selection.json filename")
    return path.with_name(path.name + ".selection.json")
```

tkinter 采用惰性导入；优先使用 figure.manager.window 的 Tk 父窗口，非 Tk 后端临时 `Tk()` + `withdraw()`，只销毁自己创建的 root。对话框方法进入时核验主线程，异常转成含原因的 RuntimeError。

保存调用 `filedialog.asksaveasfilename(parent=parent, initialdir=str(initial_dir), initialfile=suggested_name, filetypes=[("Selection JSON", "*.selection.json")], confirmoverwrite=False)`。后缀规范化后由任务 5 捕获快照、执行一次明确覆盖确认，避免原生框确认的路径与补后缀后的目标不一致。打开用 `askopenfilename`；确认用 `messagebox.askyesno`；详情用 `messagebox.showinfo`。不在选择目录前主动 mkdir，取消必须无文件写入。

- [ ] 测试真实窗口函数均被替身替代：取消返回 None、后端失败不写文件、已有父窗口不销毁、临时 root 在成功/取消/异常均销毁、非主线程拒绝。测试不启动窗口、不依赖用户点击。
- [ ] 执行 `& $seqPython -m pytest tests/unit/test_selection_dialogs.py -q`，通过后提交：`feat: add native selection dialog adapter`。

## 任务 5：连接 Save / Load 与 session 状态

文件：修改 `viewer.py` 的 ViewerSession、ViewerModeState、build_viewer 与 on_save/on_load；修改 `tests/unit/test_viewer.py`。

- [ ] 先写取消测试，用明确的测试替身，不能让旧 GUI 测试弹真实框：

```python
class FakeSelectionDialogs:
    def __init__(self, save=None, load=None, answer=True):
        self.save = save
        self.load = load
        self.answer = answer
        self.confirmations = []
        self.details = []

    def choose_save(self, initial_dir, suggested_name):
        return self.save

    def choose_load(self, initial_dir):
        return self.load

    def confirm(self, message):
        self.confirmations.append(message)
        return self.answer

    def show_details(self, message):
        self.details.append(message)

def test_save_dialog_cancel_keeps_dirty_state_and_writes_nothing(tmp_path):
    reader = FileBackedFakeReader(make_frames(4), tmp_path / "source.seq")
    session = ViewerSession(reader, selection_store=SelectionStore(tmp_path / "configs"))
    ui = build_viewer(session, dialogs=FakeSelectionDialogs())
    try:
        session.selection_dirty = True
        before = session.state_signature()
        ui.save_button._observers.process("clicked", None)
        assert session.selection_dirty
        assert session.state_signature() == before
        assert not (tmp_path / "configs").exists()
    finally:
        plt.close(ui.figure)
```

- [ ] 执行 `& $seqPython -m pytest tests/unit/test_viewer.py -k save_dialog_cancel -q`，预期新注入参数/行为尚未存在。
- [ ] session 新增 `selection_path: Path | None = None`；`save_selection(target=None, *, expected_target=None, expected_backup=None)` 保留无参旧行为；显式路径要求两个快照，成功后才改变 selection_path、dirty、clean signature。`load_selection(target=None)` 用新 store 入口读取，再调用抽出的 `apply_selection_document(document, *, path)`；该函数在任何 session 赋值前完成源身份、colormap、FrameTransformer、ColorLimits 和新 TimelineState 的构造验证，最后整体赋值并清缓存。

`build_viewer` 增加 `dialogs=None`，默认 `SelectionDialogs`，测试显式注入。移除旧 load_confirmation_signature、Confirm load 二次点击流程及相关失效函数，替换其测试为单次显式确认行为，保留“切帧不变脏”的原断言。

构造适配器时传入 `figure.canvas.manager.window`（用 getattr 兼容 Agg）；不能在 figure 创建前访问 manager。任务 2 的 F1 回调在本任务改为调用 `dialogs.show_details`，终端全文输出保留用于后端异常诊断。

保存回调顺序固定为：选择 → None 返回 → 捕获规范化目标和备份快照 → 目标存在时确认 → 调用 session 保存 → 刷新状态/全文路径。加载顺序固定为：选择 → None 返回 → 读取并验证完整 document → 有未保存更改时确认 → 应用已验证 document → 更新控件/温标。确认后应用前再次核验源身份；读取无效文件不应弹丢弃改动确认。

- [ ] 给旧 GUI 保存/加载测试全部注入替身。新增两套配置选择、覆盖拒绝、确认期间外部创建目标、加载损坏文件、错误色图、脏状态拒绝、保存失败原状态保留、编辑模式禁止调用对话框、F1 详情窗口收到完整路径测试。
- [ ] 执行 `& $seqPython -m pytest tests/unit/test_selection.py tests/unit/test_selection_dialogs.py tests/unit/test_viewer.py -q`，通过后提交：`feat: connect selection file dialogs to viewer state`。

检查点 B：可单独验收“保存方案 A/B → 加载其中之一 → 取消不变”。此时原生导出仍未配套，不提前把 EXP-001 标为修复。

## 任务 6：冻结导出视图与生成子文件配置

文件：新增 `src/flir_seq_tool/export_bundle.py`、`tests/unit/test_export_bundle.py`；修改 `extraction.py` 的 ExportRequest/build_export_request/preflight_export；修改 `viewer.py` 的 export_request、on_apply_transform；测试 `test_extraction.py`、`test_viewer.py`。

- [ ] 先在 `test_viewer.py` 用 file-backed reader 建立色标后创建 request，断言 request 保存不可变 document，后续改变 session 不改变 request；保留现有原生帧范围/文件尺寸不受 transform 影响的测试，但不再断言请求完全不携带 transform。
- [ ] 执行 `& $seqPython -m pytest tests/unit/test_viewer.py -k export_request -q`，确认新字段缺失导致失败。
- [ ] ExportRequest 增加默认兼容字段 `selection_document: SelectionDocument | None = None`、`sidecar_snapshot: TargetSnapshot | None = None`；旧底层调用 None 继续单 SEQ 导出，GUI 总是传入 `self.selection_document()`，包括 identity。请求构造及 worker 入口校验 document 的源身份/尺寸/范围/时间与 request 一致；拒绝一半字段缺失或内容不一致。

```python
# export_bundle.py；无 extraction 的运行时导入，避免循环依赖
from pathlib import Path

def sidecar_path(seq_path):
    return Path(seq_path).with_suffix(".selection.json")

def pending_path(seq_path):
    return Path(seq_path).with_suffix(".export-pending.json")
```

`preflight_export` 同时检查两个目标与 pending；摘要固定列出 SEQ/selection 及各自覆盖状态。signature 加入冻结 document 的排序 JSON 和 sidecar_snapshot。不要只添加角度，温标、裁剪也必须使确认失效。Apply 后主动 invalidate_export_confirmation；第二次点击仍重新比对完整 signature。

- [ ] 新增 `build_clip_document(partial_path, final_path, view_document, *, reader_factory)`：读取子 SEQ 尺寸、帧数、端点时间，用临时文件实际 size/mtime 和最终文件名创建 SourceIdentity，再用原 display/transform 与局部 `[0, N-1]` 创建 SelectionDocument。先比较维度、帧数、时间是否与冻结请求相符。该过程不得读取整段矩阵。

```python
# 函数内：output 是 reader_factory(partial_path) 的上下文对象；
# SourceIdentity/build_selection_document 从 selection 模块导入。
stat = Path(partial_path).stat()
source = SourceIdentity(
    filename=Path(final_path).name,
    size_bytes=stat.st_size,
    modified_time_ns=stat.st_mtime_ns,
    width=int(output.width), height=int(output.height),
    num_frames=int(output.num_frames),
    first_timestamp=output.timestamp_at(0),
    last_timestamp=output.timestamp_at(output.num_frames - 1),
)
document = build_selection_document(
    source=source, source_start_frame=0,
    source_end_frame=source.num_frames - 1,
    start_timestamp=source.first_timestamp, end_timestamp=source.last_timestamp,
    display=view_document.display, transform=view_document.transform,
)
```

- [ ] 以可注入 reader 测试单帧/五帧、identity/非零旋转、固定温标、最终文件名、mtime、局部帧和绝对时间；reader 的 temperature_frame_at 设为抛错以证明此函数不请求温度矩阵。新增确认之后仅 JSON 外部变化会使确认失效测试。
- [ ] 执行 `& $seqPython -m pytest tests/unit/test_export_bundle.py tests/unit/test_extraction.py tests/unit/test_viewer.py -q`，通过后提交：`feat: freeze view settings for paired SEQ export`。

## 任务 7：有恢复标记的两文件发布

文件：修改 `export_bundle.py`、`tests/unit/test_export_bundle.py`；必要的共享路径保护只修改 `file_safety.py`。

- [ ] 定义并测试 `publish_bundle(*, source_path, raw_root, seq_temp, selection_temp, seq_target, expected_seq, expected_selection, verify_pair) -> BundlePublishResult`；返回字段 `warnings: tuple[str, ...]`。失败抛出 `BundlePublishError`，包含 `stage`、`retained_paths: tuple[Path, ...]`，不得通过普通 finally 删除 retained_paths。

先加入具体回滚回归，全部输入是 tmp_path 中的普通字节文件，不使用真实 SDK：

```python
def test_failed_pair_verification_restores_old_outputs(tmp_path):
    import pytest
    from flir_seq_tool.file_safety import TargetSnapshot
    from flir_seq_tool.export_bundle import (
        BundlePublishError, pending_path, publish_bundle, sidecar_path,
    )
    source = tmp_path / "source.seq"
    source.write_bytes(b"source")
    target = tmp_path / "clip.seq"
    config = sidecar_path(target)
    target.write_bytes(b"old-seq")
    config.write_bytes(b"old-config")
    seq_temp = tmp_path / "unique.partial.seq"
    json_temp = tmp_path / "unique.partial.json"
    seq_temp.write_bytes(b"new-seq")
    json_temp.write_bytes(b"new-config")

    def reject_pair(seq, selection):
        assert seq.read_bytes() == b"new-seq"
        assert selection.read_bytes() == b"new-config"
        raise ValueError("injected pair verification failure")

    with pytest.raises(BundlePublishError):
        publish_bundle(
            source_path=source, raw_root=tmp_path / "raw",
            seq_temp=seq_temp, selection_temp=json_temp, seq_target=target,
            expected_seq=TargetSnapshot.capture(target),
            expected_selection=TargetSnapshot.capture(config),
            verify_pair=reject_pair,
        )
    assert target.read_bytes() == b"old-seq"
    assert config.read_bytes() == b"old-config"
    assert not pending_path(target).exists()
```

- [ ] 执行 `& $seqPython -m pytest tests/unit/test_export_bundle.py -k failed_pair_verification -q`，确认发布能力尚未实现。
- [ ] 分小步实现以下有限状态机，每个状态先加故障注入测试再实现，不建立通用事务框架：

| 阶段 | 操作与校验 | 失败处理 |
| --- | --- | --- |
| validate | 两目标、两个 temp、marker、恢复文件均在目标同一目录，均不为 source/raw；解析路径并检查别名；核对两目标快照 | 无正式文件变化；不创建 marker |
| claim | 使用 `open(marker, "x", encoding="utf-8")` 独占建立 marker；记录 uuid、最终/临时/恢复路径及初始快照；flush/fsync | 已有 marker 时拒绝，不读取其内容执行移动/删除 |
| protect | 修改前刷新 journal 状态；各旧目标重新核对快照，再 `os.replace` 到唯一 uuid 恢复路径；记录移动后的快照 | 已移动部分逆序恢复；目标被外部占用则保留材料 |
| publish | 每次发布前确认目标仍不存在，记录将要执行动作；移入新 SEQ，再新 JSON；记录移入后的所有权快照 | 仅移走/清理本次发布且快照未变的文件，不覆盖新外部文件 |
| verify | 调用 verify_pair，读取正式 SEQ 身份与正式 JSON 全字段比较；记录 verified | 验证失败尝试回滚，不报告完成 |
| finish | 删除本事务未改变的恢复/临时文件；清除 marker | 恢复文件清理失败返回警告；marker 清除失败抛出异常并保留可说明状态 |
| rollback | 逆序处理已发布目标和已保护旧文件；只操作匹配记录的文件 | 完整恢复才清 marker；否则错误带 stage 与全部保留路径 |

journal 更新先写同目录独占临时文件、fsync 后替换；记录“即将操作”与“已完成”，保证中途崩溃至少留下待处理标记。不在启动时执行 journal 内记录的任意路径。事务只承诺已检查的外部改动不会被主动覆盖，不宣称跨进程两文件原子性或断电自动恢复；每次改名附近都重新核验，保留 Windows 的文件身份差异测试。

- [ ] 按表逐个注入失败：marker 创建/更新；第一/第二旧文件保护；第一/第二新文件发布；最终验证；恢复；清理。再参数化两个旧目标存在性的 4 种组合。覆盖原目标出现/变化、恢复文件变化、已发布新文件被外部改写、pending 已存在。每个情景明确断言最终 bytes、marker 存在性及恢复材料，而不是只断言抛异常。
- [ ] 增加正常成功测试，verify_pair 内断言 marker 此时仍存在；返回后两文件为新数据、marker 消失、无本事务遗留。验证成功后仅恢复材料删除失败，应保留新输出且返回警告，不错误回滚。
- [ ] 执行 `& $seqPython -m pytest tests/unit/test_export_bundle.py tests/unit/test_file_safety.py -q`，通过后提交：`feat: publish SEQ sidecars with recovery markers`。

## 任务 8：接入 worker、发布阶段取消与 UI 反馈

文件：修改 `extraction.py`、`export_bundle.py`、`viewer.py`；测试 `test_extraction.py`、`test_export_bundle.py`、`test_viewer.py`。

- [ ] 在现有 `ExportAbortHandle` 测试增加“取消先赢”和“发布先赢”，给出准确契约：

```python
def test_publish_gate_refuses_late_cancel():
    import threading
    from flir_seq_tool.extraction import ExportAbortHandle
    handle = ExportAbortHandle()
    event = threading.Event()
    assert handle.begin_publish(event) is True
    assert handle.request_cancel(event) is False
    assert not event.is_set()

def test_cancel_wins_before_publish():
    import threading
    from flir_seq_tool.extraction import ExportAbortHandle
    handle = ExportAbortHandle()
    event = threading.Event()
    assert handle.request_cancel(event) is True
    assert handle.begin_publish(event) is False
```

- [ ] 执行 `& $seqPython -m pytest tests/unit/test_extraction.py -k 'publish_gate or cancel_wins' -q`，确认新方法缺失。
- [ ] 在 handle 使用已有锁增加 `_publishing`；`request_cancel` 在同一锁中判断并设置 Event，SDK abort 在锁外调用；`begin_publish` 在同一锁中先读 Event，再切 publishing。ExportJob.cancel 返回是否接受，发布后不再 set Event。现有直接 run_export 的 Event 调用保留兼容，但进入发布后不依据晚到的 Event 将发布失败错报 cancelled。

```python
def begin_publish(self, cancel_event):
    with self._lock:
        if cancel_event.is_set():
            return False
        self._publishing = True
        return True
```

ExportProgressSnapshot/ExportProgress 增加 `cancel_allowed: bool = True`；update 支持该字段。准备完成、取得门后更新为 False 和 `Finalizing paired files; please wait`。UI poll 必须显示 snapshot.message，而不只是百分比；禁用取消按钮。Close 仍只 bounded join，不在 UI 线程等完整发布，不中断已进入发布的非 daemon worker。

- [ ] worker 保留现有 SDK 提取及 validator；有 document 时，在临时 SEQ 校验后生成临时 JSON（独占临时文件、flush/fsync、回读 SelectionDocument），校验 source 未变、两目标快照未变、取消未接受，再进入发布。`verify_pair` 用 reader 读取最终身份和 `SelectionStore.load_path` 校验并比较冻结 transform/display/局部范围。

临时文件集合由 worker 持有，移交事务后由事务负责；BundlePublishError.retained_paths 中的文件不得在 worker finally 中删除。无 document 时继续旧单文件路径并保留现有测试。ExportJob 新增可注入 reader_factory 供配套测试使用，默认 ScienceFileReader，传给准备和最终验证，不绕过现有 validate_extracted_seq。

- [ ] 扩展 fake SDK/reader 测试：正常配套、单帧、准备阶段取消、SDK abort 异常、JSON 准备失败、发布前外部目标变化、不可取消期点击、journal 保留、worker 返回 None UI 恢复；故障返回消息包含失败阶段与恢复材料位置。确认成功必须晚于正式 pair 验证及 marker 清除。
- [ ] 执行 `& $seqPython -m pytest tests/unit/test_extraction.py tests/unit/test_export_bundle.py tests/unit/test_viewer.py -q`，通过后提交：`feat: integrate paired export and safe cancellation gate`。

## 任务 9：启动自动恢复与 CLI 优先级

文件：修改 `viewer.py` 的 launch_viewer/ViewerSession、`cli.py`；测试 `test_viewer.py`、`test_cli.py`。

- [ ] 先在 `test_cli.py` 加入显式零与未提供的区别：

```python
def test_cli_distinguishes_omitted_frame_from_explicit_zero():
    parser = cli.build_parser()
    assert parser.parse_args(["view", "clip.seq"]).frame is None
    assert parser.parse_args(["view", "clip.seq", "--frame", "0"]).frame == 0
```

在 `test_viewer.py` 建立 file-backed session 并把配置写到 reader.path.with_suffix('.selection.json')。新增 `ViewerSession.restore_startup(*, initial_frame=None, vmin=None, vmax=None) -> tuple[int, tuple[str, ...]]`：返回局部初始帧与启动提示。先测试无配置返回 `(0, ())`，有效配置恢复范围/变换，pending 存在时不调用 store.load_path。

- [ ] 执行 `& $seqPython -m pytest tests/unit/test_cli.py tests/unit/test_viewer.py -k 'startup or omitted_frame' -q`，确认新契约未实现。
- [ ] CLI 默认改为 None；launch_viewer 先创建无显式温标的 session，调用 restore_startup，再 build_viewer，最后显示启动消息，避免 show_local 清掉报警：

```python
def launch_viewer(path, initial_frame=None, vmin=None, vmax=None):
    with ScienceFileReader(path) as reader:
        session = ViewerSession(reader)
        local_frame, notices = session.restore_startup(
            initial_frame=initial_frame, vmin=vmin, vmax=vmax,
        )
        ui = build_viewer(session, initial_frame=local_frame)
        if notices:
            message = "\n".join(notices)
            ui.message_details["startup"] = message
            ui.error_text.set_text(fit_message(message, max_chars=100, max_lines=2))
            print(message)
        plt.show()
```

restore_startup 的顺序：先校验 CLI 参数（温标必须成对、有限、递增；frame 必须在整个源 SEQ 内）；检查同目录 pending；无 pending 时检查唯一同名配置并使用完整验证/原子应用路径；捕获配置的 OSError/ValueError/RuntimeError 变为警告但保留原视图；最后应用显式 CLI 温标和定位帧。不要把无效 CLI 错误吞成配置警告。

有效配套有范围时，未显式 frame 使用局部 0；显式源帧在范围内通过 timeline.local_frame_at 转换；范围外但仍在源文件内使用局部 0 并提示。CLI 温标覆盖与配套不同则 selection_dirty=True；数值恰好相同不制造假脏状态。不提供参数且无配置，保留原首帧自动温标行为。

在手动 load_selection 和 apply_selection_document 入口检查源旁 pending，堵住手动加载其他目录配置绕过未完成导出的路径。自动检测不放入 frame_at，切帧/Reset full 不触发重读。

- [ ] 按下表逐项测试，不只覆盖 happy path：

| 场景 | 预期 |
| --- | --- |
| 无 JSON、别处有更新的 JSON | 原始视图，不扫描、不加载 |
| 合法 v1/v2 | 先恢复再绘第一帧；v1 为 identity；显示实际路径 |
| 无效 JSON/身份/色图/变换 | 原状态启动并报警，不套用其他文件 |
| 任意内容 pending（包括空文件） | 阻止自动与手动加载，仍能看原 SEQ |
| CLI frame 省略/范围内/范围外/源外 | 首帧/指定局部帧/首帧并提示/抛 IndexError |
| CLI limits 成对覆盖/单个/NaN/倒序 | 规定覆盖/后三者拒绝 |
| 切帧、Reset full | 不再次调用 restore_startup 或读取配置 |

- [ ] 执行 `& $seqPython -m pytest tests/unit/test_cli.py tests/unit/test_viewer.py tests/unit/test_selection.py -q`，通过后提交：`feat: restore verified sidecars when opening SEQ files`。

## 任务 10：真实 SEQ、完整回归、文档及交付

文件：新增 `tests/integration/conftest.py`；修改两个最小真实数据测试文件、README.md、CHANGELOG.md、docs/KNOWN_ISSUES.md、docs/MAINTENANCE.md。

- [ ] 在 integration 下增加 autouse fixture，保护所有真实 raw SEQ，即使失败也执行比较；fixture 不读取大文件内容，只 stat：

```python
from pathlib import Path
import pytest

RAW = Path(r"D:\carbonfoam\infrared\raw")

def raw_metadata():
    result = {}
    for path in RAW.glob("*.seq"):
        stat = path.stat()
        result[path.name] = (stat.st_size, stat.st_mtime_ns)
    return result

@pytest.fixture(autouse=True)
def preserve_raw_sources():
    before = raw_metadata()
    try:
        yield
    finally:
        assert raw_metadata() == before, "raw SEQ metadata changed"
```

- [ ] 使用唯一最小真实源建立 session，读取首帧初始化温标，设置 pending `[10,14]`，应用 `FrameTransform.identity(width,height).with_angle(-2.3)` 的有效默认裁剪，创建带 document 的导出请求。通过 preflight 后在 tmp_path 下运行 ExportJob（不写正式 clips）。等待线程时使用有界 join 并断言结束；失败时 result.message 必须出现在断言中。
- [ ] 重新打开导出的五帧 SEQ 并调用 restore_startup；断言局部 `[0,4]`、真实首末时间不变、源尺寸不变、应用变换/色图/温标一致。比较重新打开的第 0/2/4 帧变换结果与原 session 第 10/12/14 帧，使用 `np.testing.assert_allclose(reopened_frame.temperature_c, original_frame.temperature_c, rtol=1e-6, atol=1e-4, equal_nan=True)`，且缓存 maxsize 不变。确认最终配置绑定子文件而非源文件、pending 不存在。
- [ ] 执行 `& $seqPython -m pytest tests/integration/test_small_seq_extract.py tests/integration/test_small_seq_viewer.py -q`。预期真实 SDK 用例全部通过，fixture 在通过/失败均检查三份 raw 元数据；若 SDK 不可用或 source 缺失，记录阻塞，不用模拟结果替代真实通过。
- [ ] 执行完整回归，并在整个 pytest 进程之外额外比较 raw 列表、大小与时间，覆盖单元测试阶段。以下 PowerShell 中 Ticks 为 Windows 100 ns 时间单位，integration fixture 另以 st_mtime_ns 比较。保存实际用例数、耗时、skip 原因及前后元数据；首次失败先诊断，不通过删测试/放宽温度误差消除失败。

```powershell
function Get-SeqRawMetadata {
    Get-ChildItem -LiteralPath 'D:\carbonfoam\infrared\raw' -Filter '*.seq' -File |
        Sort-Object Name |
        Select-Object Name, Length, @{Name='MtimeTicks';Expression={$_.LastWriteTimeUtc.Ticks}} |
        ConvertTo-Json -Compress
}
$seqBefore = Get-SeqRawMetadata
try {
    & $seqPython -m pytest -q
    $seqTestExit = $LASTEXITCODE
} finally {
    $seqAfter = Get-SeqRawMetadata
    if ($seqBefore -ne $seqAfter) { throw 'raw SEQ metadata changed' }
    Write-Output $seqAfter
}
if ($seqTestExit -ne 0) { throw "pytest failed with exit code $seqTestExit" }
```

预期 pytest 退出码 0、前后元数据相同。仅在全套完成后报告验证结论。
- [ ] 更新文档：0.1.0 已提交并标记在 2b1a5d7，不称 GitHub Release 已创建；新增 Unreleased 修复项。KNOWN_ISSUES 区分“代码/自动测试通过”与“等待人工验收”，记录真实命令及结果。README 给出 Apply → Save selection（保存方案）、Export sub-SEQ（直接输出两文件，无须先 Save）与自动恢复流程；注明其他软件只看原像素、缺少 sidecar 显示原视图、改名/mtime可能破坏身份、pending 禁止套用、F1 完整详情。MAINTENANCE 说明独立仓库/修复分支状态，不保留仍等待首次 git init 的过期操作。
- [ ] 执行文档差异检查及完整代码复核。重点复核路径保护、两个目标快照、回滚外部文件保护、取消临界区、读取失败原子性、首次绘制恢复、逐帧内存。不因测试全绿而跳过复核；复核修复须重新跑受影响测试和完整回归。
- [ ] 仅提交对应测试与文档：`test: verify paired SEQ workflow and document maintenance`。

### 人工验收交接（不自动代用户勾选）

- [ ] Windows 实际后端打开最小 SEQ，在常用屏幕缩放/窗口大小检查普通与编辑模式；F1 能看到完整路径与错误。
- [ ] 旋转 0.1° 微调、拖动裁剪、输入精确框、Apply/Cancel/Reset/Maximum、切帧仍正常。
- [ ] Save 分别命名两套配置，Load 明确选择；取消保存/打开/覆盖不改变当前状态。
- [ ] 导出五帧配套并关闭，重新打开子 SEQ 自动恢复视图；单独打开原 SEQ不受影响。
- [ ] JSON 缺失时原视图；配置不匹配或 pending 时有明确警告且不误加载。
- [ ] 用户自行检查大文件性能；未做即记录未验证，不自动长时读取大 SEQ。

完成后给用户：各任务提交、最新完整测试结果、原始文件只读验证、待人工确认项。由用户决定合并/推送/版本号；本计划不授权自动发布。

## 覆盖自查

| 设计要求 | 计划任务 |
| --- | --- |
| 单窗口模式切换、遮挡、草稿状态 | 1 |
| DPI、长信息、最小窗口、完整路径 | 1–2、10 |
| Save/Load 选择、取消、覆盖、兼容 schema、脏状态 | 3–5 |
| raw/源文件/别名保护 | 3、6–8、10 |
| 冻结变换与温标、子帧重编号、新源身份 | 6 |
| 两文件快照、pending、失败回滚、保留材料 | 7–8 |
| 后台进度、取消边界 | 8 |
| 自动恢复、手动 pending 阻断、CLI 优先级 | 9 |
| 最小真实源、三份 raw 前后核对、有界缓存 | 10 |
| Git 分项提交、修正文档、用户验收后发布 | 全部任务及任务 10 |

执行状态：任务 1–2 已实现并验证，任务 3–10 未完成。任务 1 提交 aab11a6；Total 输入框根据 renderer 测试从计划坐标下移，避免 1000×800 下标签接触当前帧滑块。基线 298 passed（83.85 s，pytest 缓存权限警告 1 条），raw 三文件大小/mtime 不变；后续关闭 cacheprovider，任务 1 目标回归 120 passed（112.68 s），无警告。任务 2 目标回归 131 passed（145.73 s），无警告；用 BoundedMessage 在绘制时按 renderer 宽度缩略，完整信息独立保存，F1 输出全文；长度拟合按测量比例收敛，避免逐字重复测量。最小真实 SEQ 的 1000×800 普通/编辑模式渲染图已检查，保存于忽略目录 picture/workflow-layout-20261021；三份 raw 的大小和纳秒 mtime 均未改变。Windows 原生窗口人工验收尚未完成。尚未完成的新增函数/字段仍是实施契约，不表示当前代码已具备。
