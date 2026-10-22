# 子 SEQ 另存为实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans 或经用户选择后的 superpowers:subagent-driven-development 逐项执行。使用复选框记录完成情况。

**Goal:** 用原生另存为替代 Clip name，支持选择输出位置，保留两次确认及成对文件保护。

**Architecture:** 在 SelectionDialogs 增加 SEQ 路径选择，Viewer 的待确认请求保存唯一目标路径。第二次点击重新预检同一路径，只有签名未变化才启动导出；变动则更新摘要等待再次确认。复用 extraction/export_bundle 的路径验证、快照、临时验证和成对发布。

**Tech Stack:** Python 3.13、Matplotlib、Tkinter、pytest、FLIR Science FileSDK。

---

## 执行约束

工作目录固定为 `D:\carbonfoam\infrared`，按用户要求不创建其他工作树。开始实现前检查干净状态，在当前目录建立 `codex/export-save-as` 分支；不自动合并或推送。不要改写 raw、square.seq、已有 CSV 或配置。

设计依据：`docs/superpowers/specs/2026-10-22-flir-seq-export-save-as-design.md`。设计已于本轮审核通过。

预检现有实现会创建输出目录及短暂写权限探针；设计中的“尚不写文件”按“不生成或覆盖 SEQ、配置及发布标记”执行，保留探针及清理。取消文件对话框发生在预检之前，不创建目录或探针。

## 任务 1：原生 SEQ 另存为接口

文件：`src/flir_seq_tool/selection_dialogs.py`、`tests/unit/test_selection_dialogs.py`。

- [ ] 在现有测试文件加入路径规范化和原生对话框测试。

```python
@pytest.mark.parametrize("value, expected", [
    ("", None), ("sample", Path("sample.seq")),
    ("sample.seq", Path("sample.seq")),
    ("sample.SEQ", Path("sample.SEQ")),
])
def test_normalize_seq_path(value, expected):
    from flir_seq_tool.selection_dialogs import normalize_seq_path
    assert normalize_seq_path(value) == expected

@pytest.mark.parametrize("value", ["a.csv", "a.", "a ", "CON.seq"])
def test_normalize_seq_path_rejects_invalid_names(value):
    from flir_seq_tool.selection_dialogs import normalize_seq_path
    with pytest.raises(ValueError):
        normalize_seq_path(value)

def test_choose_seq_export(fake_tk, tmp_path):
    from flir_seq_tool.selection_dialogs import SelectionDialogs
    roots, calls, behavior, _, _ = fake_tk
    behavior.value = str(tmp_path / "chosen.seq")
    assert SelectionDialogs().choose_seq_export(tmp_path, "suggested.seq") == Path(behavior.value)
    assert calls[0]["initialfile"] == "suggested.seq"
    assert calls[0]["initialdir"] == str(tmp_path)
    assert calls[0]["confirmoverwrite"] is False
    assert roots[0].destroyed
```

- [ ] 执行 `python -m pytest tests/unit/test_selection_dialogs.py -q`，确认新增接口缺失导致失败。
- [ ] 增加模块函数及 SelectionDialogs 方法；延迟导入命名校验函数，避免模块初始化引入 SDK。

```python
def normalize_seq_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.name or path.name.endswith((".", " ")):
        raise ValueError("Use a .seq filename")
    if path.suffix and path.suffix.lower() != ".seq":
        raise ValueError("Use a .seq filename")
    from .extraction import normalize_clip_name
    normalize_clip_name(path.name)
    return path if path.suffix else path.with_suffix(".seq")

# 添加到 SelectionDialogs 类中。
def choose_seq_export(self, initial_dir: Path, suggested_name: str) -> Path | None:
    value = self._run(
        "file", "asksaveasfilename", title="Export sub-SEQ",
        initialdir=str(initial_dir), initialfile=suggested_name,
        filetypes=[("FLIR SEQ", "*.seq")], confirmoverwrite=False,
    )
    return normalize_seq_path(value)
```

- [ ] 用 fake_tk 再验证空返回、TclError 和非主线程：空返回为 None；异常为 RuntimeError；无残留窗口、输出文件或目录。
- [ ] 重跑该测试文件，通过后提交这两个文件：`git commit -m "feat: add native SEQ save-as dialog"`。

## 任务 2：待确认目标与回调

文件：`src/flir_seq_tool/viewer.py`、`tests/unit/test_viewer.py`。

- [ ] 扩展 FakeSelectionDialogs，保存单独的 seq_target，不借用 selection 的 save 字段。

```python
# FakeSelectionDialogs.__init__ 增加 self.seq_target = None。
def choose_seq_export(self, initial_dir, suggested_name):
    self.choices.append(("seq", initial_dir, suggested_name))
    return self.seq_target
```

- [ ] 修改现有两次确认测试，注入 dialogs 并设置 `dialogs.seq_target = tmp_path / "chosen" / "useful.seq"`。首次点击后断言无任务、目标为选定路径；第二次断言只有一次 seq 选择且任务启动；范围改变后断言 confirmation 为 None。保留任务开始/结束控件锁定断言。
- [ ] 运行 `python -m pytest tests/unit/test_viewer.py -q -k export`，记录红灯。
- [ ] 将 current_confirmation 改为接受 target，复用 session.export_request 后保留所选路径大小写。

```python
def current_confirmation(target: Path) -> ExportConfirmation:
    request = session.export_request(target.name, target.parent)
    request = replace(request, target_path=target)
    return preflight_export(
        request,
        work_source_range=(session.timeline.source_start, session.timeline.source_end),
    )
```

使用已有或新增 `from dataclasses import replace`。不得改变 ViewerSession.export_request 现有签名，保留非 UI 调用兼容。

- [ ] 替换 on_export 的预检 try 块；保留其后的 signature 比较和任务启动代码。

```python
try:
    if export_state.confirmation is None:
        source_start = session.timeline.source_frame_at(session.timeline.pending_start)
        source_end = session.timeline.source_frame_at(session.timeline.pending_end)
        suggested = f"{Path(session.reader.path).stem}-{source_start}-{source_end}.seq"
        target = dialogs.choose_seq_export(Path(clips_root), suggested)
        if target is None:
            return
        target = Path(target).absolute()
    else:
        target = export_state.confirmation.request.target_path
    confirmation = current_confirmation(target)
except (OSError, ValueError, RuntimeError) as exc:
    invalidate_export_confirmation()
    set_error(f"Export preflight failed: {exc}")
    return
```

- [ ] 增加测试：取消不启动任务且不创建目标父目录；对话框异常后控件可操作；默认建议名取原始帧范围；确认前修改 SEQ 或 sidecar，第二次点击不得启动，只更新摘要；第三次目标未变才启动；错误后重试重新选择。
- [ ] 重跑 export 测试和 `tests/unit/test_export_bundle.py`，通过后提交 Viewer 和测试。

## 任务 3：移除 Clip name 与布局回归

文件：`src/flir_seq_tool/viewer.py`、`src/flir_seq_tool/viewer_layout.py`、`tests/unit/test_viewer.py`，以及搜索命中的当前控件测试。

- [ ] 先加断言 `assert not hasattr(ui, "clip_name_box")`，绘制后检查所有 Axes 的文本均不包含 Clip name。运行该测试确认失败。
- [ ] 删除 ViewerUI 的 clip_name_box 字段、clip_name_axes 创建、TextBox 创建、模式轴列表项、所有 set_active 操作、控件元组项、on_clip_name_submit 回调和注册、返回对象构造参数。不得仅隐藏控件。
- [ ] 删除 NORMAL_RECTS 的 clip 键；导出行改为：

```python
"export": (0.47, 0.255, 0.20, 0.035),
"cancel": (0.69, 0.255, 0.19, 0.035),
```

- [ ] 将测试中所有 clip_name_box.set_val 替换为 fake dialog 的 seq_target 设置；锁定断言改为 export_button、save_button、range_slider 等仍存在的控件。编辑 transform 时导出不可用的测试应断言未调用 seq 对话框。
- [ ] 执行 `rg -n 'clip_name_box|clip_name_axes|on_clip_name_submit' src tests`，应无匹配。执行 `python -m pytest tests/unit/test_viewer.py tests/unit/test_viewer_layout.py -q`，通过后提交。
- [ ] 以 Agg 渲染 Process / Measure / transform 模式，常规窗口和高 DPI 各一份，检查导出行和摘要无遮挡；保留隐藏控件不能抓取鼠标的测试。

## 任务 4：成对安全与最小真实 SEQ 验证

文件：`tests/unit/test_viewer.py`、`tests/unit/test_extraction.py`、`tests/integration/test_small_seq_extract.py`。

- [ ] 为任意目标目录与大写 .SEQ 增加回调测试：导出请求路径必须与选择结果相同；sidecar_path(request.target_path) 必须在同一目录。
- [ ] 使用 tmp_path 构造源文件、受保护 raw 目录、既存目标与 sidecar，分别测试拒绝源文件覆盖、raw 写入、路径别名、未恢复 pending。复用现有安全测试参数化，不修改真实实验文件。
- [ ] 确认快照签名含目标路径身份；若不同父目录同名文件会相同，先增加失败测试，再将 preflight_export.signature 首项由 `request.target_path.name` 改为 `str(request.target_path.absolute())`。
- [ ] 最小真实测试复用现有 `test_real_paired_export_reopens_with_matching_transformed_temperatures`，输出父目录改为 `tmp_path / "chosen-output" / "trial"`，验证五帧、时间戳、配套配置、重开温度一致；不覆盖 square.seq。
- [ ] 执行相关测试：

```powershell
python -m pytest tests/unit/test_selection_dialogs.py tests/unit/test_viewer.py tests/unit/test_extraction.py tests/unit/test_export_bundle.py tests/integration/test_small_seq_extract.py -q -rs -p no:cacheprovider
```

- [ ] 测试前后分别用 `Get-ChildItem raw -Filter *.seq | Select-Object Name,Length,LastWriteTimeUtc` 记录原始数据元信息，必须一致。若失败，先定位，不继续提交。
- [ ] 执行 `python -m pytest -q -rs -p no:cacheprovider --tb=short` 和 `git diff --check`。记录实际通过/跳过数量，不沿用历史测试数字。

## 任务 5：文档、审核与交付

文件：`README.md`、`CHANGELOG.md`。

- [ ] README 两处 Clip name 流程改为：

```text
Edit transform → Apply → Export sub-SEQ → 选择目录和文件名 → Confirm export。
默认目录为 clips，可另选目录。同名 .seq 和 .selection.json 一起保存，
无需先 Save selection。取消另存为不启动导出。选择目标后先核对两个完整路径
和各自覆盖状态；确认前文件若被外部修改，须核对更新后的摘要并再次确认。
```

- [ ] CHANGELOG 的 Unreleased 增加“子 SEQ 导出支持原生另存为，移除 Clip name，保留成对发布与覆盖保护”。不要改写历史设计文档中的旧界面描述。
- [ ] 自审回调状态转换、取消、覆盖快照和模式切换，确认用户原有数据无变动；根据 requesting-code-review 技能安排实现审核。
- [ ] 分别暂存本次源码、测试、文档，提交剩余改动；报告分支、提交、测试结果和人工操作步骤。未经用户要求不合并 main、不推送 GitHub。

## 计划自查

覆盖接口、两阶段确认、取消/异常、外部目标变更、路径安全、扩展名、控件清理、布局、真实最小数据和完整回归。现有底层保留目录创建及写权限探针，不在本次重写成对发布机制；大文件由用户手动验证。
