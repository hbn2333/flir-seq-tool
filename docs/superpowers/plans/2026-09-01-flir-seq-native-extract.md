# FLIR SEQ C2b Native Subsequence Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export the current candidate range as a validated, user-named SEQ-FFF file without blocking the Matplotlib UI or touching the raw source.

**Architecture:** A focused `extraction.py` owns name validation, preflight, FileSDK extraction, progress state, cancellation, output validation, and transactional replacement. The viewer owns only two-click confirmation, widget locking, timer polling, and user-visible status.

**Tech Stack:** Python 3.13, pathlib, threading, shutil, uuid, NumPy, Matplotlib, pytest, FLIR Science File SDK 2026.1.2.

---

### Task 1: Export request and path validation

**Files:**
- Create: `src/flir_seq_tool/extraction.py`
- Create: `tests/unit/test_extraction.py`

- [ ] **Step 1: Write failing request and naming tests**

```python
@pytest.mark.parametrize("name", ["", ".", "..", "a/b", r"a\\b", "bad:name", "CON", "com1.txt", "tail."])
def test_clip_name_rejects_unsafe_windows_names(name):
    with pytest.raises(ValueError):
        normalize_clip_name(name)

def test_build_export_request_adds_seq_under_fixed_root(tmp_path):
    request = build_export_request(
        source_path=tmp_path / "raw" / "source.seq",
        clips_root=tmp_path / "clips",
        clip_name="useful_run",
        source_start_frame=5,
        source_end_frame=9,
        start_timestamp=START,
        end_timestamp=END,
        source_num_frames=20,
    )
    assert request.target_path == tmp_path / "clips" / "useful_run.seq"
    assert request.frame_count == 5
```

- [ ] **Step 2: Run tests and verify the missing module failure**

Run: `python -m pytest tests/unit/test_extraction.py -q`

Expected: collection fails because `flir_seq_tool.extraction` does not exist.

- [ ] **Step 3: Implement immutable request and exact Windows-name checks**

```python
@dataclass(frozen=True)
class ExportRequest:
    source_path: Path
    target_path: Path
    source_start_frame: int
    source_end_frame: int
    start_timestamp: datetime
    end_timestamp: datetime
    source_num_frames: int
    source_size_bytes: int
    source_modified_time_ns: int

    @property
    def frame_count(self) -> int:
        return self.source_end_frame - self.source_start_frame + 1
```

Reject illegal characters, separators, reserved device stems, trailing dot/space, empty names, and ranges outside the source. Strip an optional user-supplied `.seq` once, then append exactly one `.seq`.

- [ ] **Step 4: Run request tests**

Run: `python -m pytest tests/unit/test_extraction.py -q`

Expected: all request and path tests pass.

- [ ] **Step 5: Commit request model**

```powershell
git add -- src/flir_seq_tool/extraction.py tests/unit/test_extraction.py
git commit -m "feat: validate native SEQ export requests"
```

### Task 2: Preflight and two-click confirmation state

**Files:**
- Modify: `src/flir_seq_tool/extraction.py`
- Modify: `tests/unit/test_extraction.py`

- [ ] **Step 1: Write failing preflight tests**

Test writable-directory creation, target outside `raw`, target unequal to source, `SEQ-FFF` availability, conservative space calculation, existing-target overwrite flag, and stable confirmation token invalidated by any name/range/work-range change.

```python
def test_required_space_uses_average_frame_size_and_margin():
    assert required_free_bytes(1_000, 100, 10) == 64 * 1024 * 1024 + 100

def test_confirmation_signature_changes_with_range():
    first = ExportConfirmation.from_request(sample_request(start=1, end=5))
    second = ExportConfirmation.from_request(sample_request(start=2, end=5))
    assert first.signature != second.signature
```

- [ ] **Step 2: Run tests and verify failures**

Run: `python -m pytest tests/unit/test_extraction.py -q`

Expected: failures identify missing preflight and confirmation functions.

- [ ] **Step 3: Implement preflight and summary data**

Use `shutil.disk_usage(clips_root).free`, `os.access`, and `fnv.file.ImagerFileWriter.get_writable_formats()`. Required bytes are estimate plus `max(ceil(estimate * 0.1), 64 MiB)`. Return an `ExportConfirmation` containing the immutable request, overwrite flag, human-readable summary, and a tuple signature of name plus source range and current work range.

- [ ] **Step 4: Run preflight tests**

Run: `python -m pytest tests/unit/test_extraction.py -q`

Expected: all preflight and confirmation tests pass.

- [ ] **Step 5: Commit preflight**

```powershell
git add -- src/flir_seq_tool/extraction.py tests/unit/test_extraction.py
git commit -m "feat: preflight native SEQ exports"
```

### Task 3: FileSDK extraction worker and cleanup

**Files:**
- Modify: `src/flir_seq_tool/extraction.py`
- Modify: `tests/unit/test_extraction.py`

- [ ] **Step 1: Write fake-SDK failure, progress, and cancellation tests**

```python
def test_worker_configures_filesdk_for_inclusive_seq_fff_range(tmp_path):
    fake = FakeImagerFile(extract_result=True)
    result = run_export(sample_request(tmp_path, start=3, end=7), imager_factory=lambda _: fake,
                        writable_formats=lambda: ["SEQ-FFF"], validator=fake_validator)
    assert fake.options.start_frame == 3
    assert fake.options.end_frame == 7
    assert fake.options.decimation == 1
    assert fake.options.preset == -1
    assert fake.options.preferredFormat == "SEQ-FFF"
    assert result.status is ExportStatus.SUCCEEDED
```

Also test callback progress, callback returning `True` after cancellation, `extract()` returning false, SDK exception, validator exception, and removal of the unique `.partial.seq` on every non-success path. Pre-create the formal target and assert its bytes remain unchanged on failures.

- [ ] **Step 2: Run worker tests and verify failures**

Run: `python -m pytest tests/unit/test_extraction.py -q`

Expected: failures identify missing worker and result state.

- [ ] **Step 3: Implement worker state and SDK call**

```python
class ExportStatus(Enum):
    READY = "ready"
    RUNNING = "running"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUCCEEDED = "succeeded"

@dataclass
class ExportProgress:
    status: ExportStatus = ExportStatus.READY
    current: int = 0
    total: int = 0
    message: str = ""
```

Guard progress with a `threading.Lock`, cancellation with `threading.Event`, and create the partial path as `<stem>.<uuid>.partial.seq` beside the target. The worker opens and closes its own `ImagerFile`, calls `extract`, validates the partial output, rechecks source stat, then calls `os.replace(partial, target)`. A `finally` block removes an existing partial unless the formal replacement succeeded.

- [ ] **Step 4: Run worker tests**

Run: `python -m pytest tests/unit/test_extraction.py -q`

Expected: all worker, cleanup, progress, and cancellation tests pass.

- [ ] **Step 5: Commit worker**

```powershell
git add -- src/flir_seq_tool/extraction.py tests/unit/test_extraction.py
git commit -m "feat: extract SEQ ranges with FileSDK"
```

### Task 4: Real output validation

**Files:**
- Modify: `src/flir_seq_tool/extraction.py`
- Modify: `tests/unit/test_extraction.py`
- Create: `tests/integration/test_small_seq_extract.py`

- [ ] **Step 1: Write failing validator tests with fake readers**

Assert rejection of unreadable output, wrong dimensions, wrong frame count, wrong endpoint timestamp, differing first temperature matrix, and changed source size/mtime.

- [ ] **Step 2: Run validator tests and verify failures**

Run: `python -m pytest tests/unit/test_extraction.py -q`

Expected: failures report that `validate_extracted_seq` is not defined.

- [ ] **Step 3: Implement validator using independent read handles**

Open source and partial outputs through separate `ScienceFileReader` instances. Compare dimensions and frame count exactly, timestamps exactly, and first matrices with `numpy.testing.assert_allclose(rtol=1e-6, atol=1e-4, equal_nan=True)`. Re-stat the source immediately before formal replacement.

- [ ] **Step 4: Add a real short extraction integration test**

Extract source frames 10–14 from `raw/Rec-000366-240_09_26_37_152.seq` to pytest's temporary directory. Assert five frames, $640\times480$, endpoint timestamps matching source frames 10 and 14, first temperature matrix equality, no partial files, and unchanged raw size/mtime.

Run: `python -m pytest tests/integration/test_small_seq_extract.py -q`

Expected: the real five-frame SEQ-FFF extraction passes.

- [ ] **Step 5: Commit validation**

```powershell
git add -- src/flir_seq_tool/extraction.py tests/unit/test_extraction.py tests/integration/test_small_seq_extract.py
git commit -m "test: validate native SEQ extraction"
```

### Task 5: Responsive viewer export controls

**Files:**
- Modify: `src/flir_seq_tool/viewer.py`
- Modify: `tests/unit/test_viewer.py`

- [ ] **Step 1: Write failing UI state tests**

Test that the first click produces a summary only, the second starts exactly one background task, editing name/range invalidates confirmation, controls lock while running, cancel sets the event, polling updates progress, and success/failure/cancel restores controls and reports the outcome.

```python
def test_first_export_click_only_arms_confirmation(tmp_path):
    ui = build_viewer(make_session(tmp_path, count=10), export_runner=recording_runner)
    ui.clip_name_box.set_val("useful")
    click(ui.export_button)
    assert ui.export_button.label.get_text() == "Confirm export"
    assert recording_runner.calls == []
```

- [ ] **Step 2: Run UI tests and verify failures**

Run: `python -m pytest tests/unit/test_viewer.py -q`

Expected: failures report missing export widgets and state controller.

- [ ] **Step 3: Implement export controller and widgets**

Extend `ViewerUI` with `clip_name_box`, `export_button`, `cancel_button`, `export_text`, and `progress_text`. Keep a strong reference to the thread, timer, cancellation event, and progress state. Only the timer callback mutates widgets. Disable range/name/save/load controls during work; leave Cancel active. Window close requests cancellation and calls `options.abort()` through the worker controller when available.

- [ ] **Step 4: Run viewer and extraction unit tests**

Run: `python -m pytest tests/unit/test_viewer.py tests/unit/test_extraction.py -q`

Expected: all UI and extraction tests pass without live GUI interaction.

- [ ] **Step 5: Commit viewer export UI**

```powershell
git add -- src/flir_seq_tool/viewer.py tests/unit/test_viewer.py
git commit -m "feat: add responsive sub-SEQ export controls"
```

### Task 6: Full verification and manual smoke test

**Files:**
- Verify only; no source file is changed in this task.

- [ ] **Step 1: Run the complete automated suite**

Run: `python -m pytest -q`

Expected: all tests pass; only the pre-existing pytest cache permission warning is acceptable.

- [ ] **Step 2: Verify raw identity and repository scope**

Record sizes and `mtime_ns` for all three files under `raw`, run the smallest-file extraction test, then compare the same values. Run `git status --short` and confirm no files outside `infrared` were staged or modified by this work.

- [ ] **Step 3: Launch the viewer for user smoke testing**

Run:

```powershell
python -m flir_seq_tool view raw\Rec-000366-240_09_26_37_152.seq
```

Expected: save/load controls work; a custom clip name shows a two-click summary; short extraction displays progress and creates a reopenable SEQ under `clips`.

- [ ] **Step 4: Record the verification result**

Record the exact pytest pass count, the three unchanged raw-file `(size_bytes, mtime_ns)` pairs, and the manually created clip path in the completion report. Do not commit generated selections, clips, caches, or raw data.
