# FLIR SEQ C2a Selection Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit, transactional save/load of the current candidate source-frame range and fixed display scale.

**Architecture:** A Matplotlib-independent `selection.py` owns the versioned data model, strict source identity checks, and atomic JSON persistence. `ViewerSession` translates between the timeline and selection document; `viewer.py` only wires buttons and performs UI mutation after a complete load succeeds.

**Tech Stack:** Python 3.13, dataclasses, pathlib, json, os.replace, Matplotlib, pytest, FLIR Science File SDK 2026.1.2.

---

### Task 1: Versioned selection document

**Files:**
- Create: `src/flir_seq_tool/selection.py`
- Create: `tests/unit/test_selection.py`

- [ ] **Step 1: Write failing model and round-trip tests**

```python
def test_selection_document_round_trip():
    document = sample_document()
    assert SelectionDocument.from_dict(document.to_dict()) == document

def test_selection_document_rejects_unknown_schema():
    payload = sample_document().to_dict()
    payload["schema_version"] = 2
    with pytest.raises(ValueError, match="schema_version"):
        SelectionDocument.from_dict(payload)
```

Also test missing keys, booleans where integers are required, reversed ranges, inconsistent `frame_count`, inconsistent timestamps/duration, non-finite color limits, and `vmin_c >= vmax_c`.

- [ ] **Step 2: Run the new tests and verify the missing module failure**

Run: `python -m pytest tests/unit/test_selection.py -q`

Expected: collection fails because `flir_seq_tool.selection` does not exist.

- [ ] **Step 3: Implement immutable typed records and strict parsing**

```python
SCHEMA_VERSION = 1

@dataclass(frozen=True)
class SourceIdentity:
    filename: str
    size_bytes: int
    modified_time_ns: int
    width: int
    height: int
    num_frames: int
    first_timestamp: datetime
    last_timestamp: datetime

@dataclass(frozen=True)
class FrameSelection:
    source_start_frame: int
    source_end_frame: int
    start_timestamp: datetime
    end_timestamp: datetime
    frame_count: int
    duration_s: float

@dataclass(frozen=True)
class DisplaySettings:
    colormap: str
    vmin_c: float
    vmax_c: float

@dataclass(frozen=True)
class SelectionDocument:
    schema_version: int
    source: SourceIdentity
    selection: FrameSelection
    display: DisplaySettings
```

Implement `to_dict()` and `from_dict()` with exact key sets, ISO 8601 timestamps, explicit numeric validation, `math.isfinite`, and derived-field consistency within `1e-6 s`.

- [ ] **Step 4: Run the model tests**

Run: `python -m pytest tests/unit/test_selection.py -q`

Expected: all model and validation tests pass.

- [ ] **Step 5: Commit the model**

```powershell
git add -- src/flir_seq_tool/selection.py tests/unit/test_selection.py
git commit -m "feat: add versioned SEQ selection model"
```

### Task 2: Source identity and candidate-range construction

**Files:**
- Modify: `src/flir_seq_tool/selection.py`
- Modify: `tests/unit/test_selection.py`
- Modify: `src/flir_seq_tool/timeline.py`
- Modify: `tests/unit/test_timeline.py`

- [ ] **Step 1: Write failing construction and restore tests**

```python
def test_build_document_maps_pending_local_range_to_source_frames(tmp_path):
    timeline = TimelineState(20)
    timeline.set_pending(5, 15)
    timeline.confirm_range()
    timeline.set_pending(2, 4)
    document = build_selection_document(
        source=sample_source(tmp_path, num_frames=20),
        source_start_frame=timeline.source_frame_at(timeline.pending_start),
        source_end_frame=timeline.source_frame_at(timeline.pending_end),
        start_timestamp=START + timedelta(seconds=7),
        end_timestamp=START + timedelta(seconds=9),
        display=DisplaySettings("inferno", 20.0, 40.0),
    )
    assert (document.selection.source_start_frame,
            document.selection.source_end_frame) == (7, 9)

def test_restore_source_range_renumbers_locally():
    timeline = TimelineState(20)
    timeline.restore_source_range(7, 9)
    assert timeline.source_start == 7
    assert timeline.source_end == 9
    assert timeline.current_local_frame == 0
    assert timeline.pending_range == (0, 2)
```

- [ ] **Step 2: Run the focused tests and verify failures**

Run: `python -m pytest tests/unit/test_selection.py tests/unit/test_timeline.py -q`

Expected: failures report missing `build_selection_document` and `restore_source_range`.

- [ ] **Step 3: Implement construction and atomic timeline restore**

```python
def restore_source_range(self, start: int, end: int) -> None:
    if not 0 <= start <= end < self.source_num_frames:
        raise ValueError("invalid source frame range")
    self.source_start = start
    self.source_end = end
    self.current_local_frame = 0
    self.pending_start = 0
    self.pending_end = self.local_num_frames - 1
```

`build_selection_document()` derives frame count and duration rather than accepting caller-supplied derived values.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/unit/test_selection.py tests/unit/test_timeline.py -q`

Expected: all focused tests pass.

- [ ] **Step 5: Commit source mapping**

```powershell
git add -- src/flir_seq_tool/selection.py src/flir_seq_tool/timeline.py tests/unit/test_selection.py tests/unit/test_timeline.py
git commit -m "feat: map saved selections to source frames"
```

### Task 3: Transactional configuration store

**Files:**
- Modify: `src/flir_seq_tool/selection.py`
- Modify: `tests/unit/test_selection.py`

- [ ] **Step 1: Write failing path, backup, and failure-recovery tests**

```python
def test_store_uses_one_fixed_file_per_source(tmp_path):
    store = SelectionStore(tmp_path / "selections")
    assert store.path_for(Path("sample.seq")) == (
        tmp_path / "selections" / "sample.selection.json"
    )

def test_save_replaces_config_and_keeps_one_backup(tmp_path):
    store = SelectionStore(tmp_path / "selections")
    first, second = sample_document(), changed_document()
    store.save(Path("sample.seq"), first)
    store.save(Path("sample.seq"), second)
    assert store.load_raw(Path("sample.seq")) == second
    assert store.load_backup(Path("sample.seq")) == first
```

Monkeypatch the final `os.replace` to fail and assert the prior document remains at the formal path or fixed backup path, and no `.tmp` remains.

- [ ] **Step 2: Run store tests and verify failures**

Run: `python -m pytest tests/unit/test_selection.py -q`

Expected: failures report missing `SelectionStore`.

- [ ] **Step 3: Implement same-directory temporary write and replace**

```python
class SelectionStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path_for(self, source_path: Path) -> Path:
        return self.root / f"{source_path.stem}.selection.json"

    def save(self, source_path: Path, document: SelectionDocument) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path_for(source_path)
        backup = target.with_name(target.name + ".bak")
        temp_path: Path | None = None
        moved_old = False
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".tmp",
                prefix=target.name + ".", dir=target.parent, delete=False,
            ) as stream:
                json.dump(document.to_dict(), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
                temp_path = Path(stream.name)
            validated = SelectionDocument.from_dict(
                json.loads(temp_path.read_text(encoding="utf-8"))
            )
            if validated != document:
                raise ValueError("selection verification mismatch")
            if target.exists():
                os.replace(target, backup)
                moved_old = True
            try:
                os.replace(temp_path, target)
                temp_path = None
            except Exception:
                if moved_old and not target.exists() and backup.exists():
                    os.replace(backup, target)
                raise
            return target
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
```

Add `load_raw()` and `load_expected()` using the same strict parser. No partial JSON is ever written directly to the formal path.

- [ ] **Step 4: Run store tests**

Run: `python -m pytest tests/unit/test_selection.py -q`

Expected: model, transaction, backup, and injected-failure tests pass.

- [ ] **Step 5: Commit persistence**

```powershell
git add -- src/flir_seq_tool/selection.py tests/unit/test_selection.py
git commit -m "feat: persist SEQ selections transactionally"
```

### Task 4: Viewer session save/load operations

**Files:**
- Modify: `src/flir_seq_tool/viewer.py`
- Modify: `tests/unit/test_viewer.py`

- [ ] **Step 1: Extend the fake reader and write failing session tests**

```python
def test_session_builds_document_from_pending_range(tmp_path):
    session = ViewerSession(make_reader(tmp_path, count=10))
    session.timeline.set_pending(2, 7)
    document = session.selection_document()
    assert document.selection.source_start_frame == 2
    assert document.selection.source_end_frame == 7

def test_session_load_validates_before_mutating(tmp_path):
    session = ViewerSession(make_reader(tmp_path, count=10))
    before = snapshot_session(session)
    with pytest.raises(ValueError, match="size_bytes"):
        session.apply_selection(mismatched_document())
    assert snapshot_session(session) == before
```

Also test successful restore after prior narrowing and restoration of colormap/color limits.

- [ ] **Step 2: Run session tests and verify failures**

Run: `python -m pytest tests/unit/test_viewer.py -q`

Expected: failures report missing selection session methods.

- [ ] **Step 3: Implement session operations**

Add `colormap` and `selection_store` constructor parameters. Implement `source_identity()`, `selection_document()`, `save_selection()`, and `load_selection()`. `load_selection()` must parse and validate the full document against a freshly built source identity before calling `timeline.restore_source_range()` or changing display settings.

- [ ] **Step 4: Run session tests**

Run: `python -m pytest tests/unit/test_viewer.py -q`

Expected: all session tests pass.

- [ ] **Step 5: Commit session integration**

```powershell
git add -- src/flir_seq_tool/viewer.py tests/unit/test_viewer.py
git commit -m "feat: add viewer selection save and load operations"
```

### Task 5: Save/load controls and real-file regression

**Files:**
- Modify: `src/flir_seq_tool/viewer.py`
- Modify: `tests/unit/test_viewer.py`
- Modify: `tests/integration/test_small_seq_viewer.py`

- [ ] **Step 1: Write failing UI tests**

```python
def test_save_button_persists_current_candidate_range(tmp_path):
    ui = build_viewer(make_session(tmp_path, count=10))
    ui.range_slider.set_val((2, 7))
    click(ui.save_button)
    assert ui.session.selection_store.load_raw(ui.session.reader.path).selection.source_start_frame == 2

def test_load_button_restores_confirmed_range_and_scale(tmp_path):
    ui = build_viewer(make_session(tmp_path, count=10))
    save_known_document(ui.session.selection_store)
    click(ui.load_button)
    assert ui.session.timeline.pending_range == (0, 5)
    assert ui.image.get_clim() == pytest.approx((20.0, 40.0))
```

Also assert load failure keeps the image array, title, timeline, colormap and clim unchanged while displaying an in-window error.

- [ ] **Step 2: Run UI tests and verify failures**

Run: `python -m pytest tests/unit/test_viewer.py -q`

Expected: failures report missing save/load buttons.

- [ ] **Step 3: Add buttons and atomic UI refresh**

Extend `ViewerUI` with `save_button` and `load_button`, lay out controls without overlapping existing widgets, and connect callbacks. On successful load call `image.set_cmap()`, `image.set_clim()`, `sync_all_controls()`, and `show_local(0)`. On failure call only `set_error()`.

- [ ] **Step 4: Add and run real SEQ round-trip test**

Use a temporary `SelectionStore`, save source frames 10–20 from the smallest SEQ, reset, reload, and assert local range `0–10`, source range `10–20`, fixed scale equality, and unchanged source size/mtime.

Run: `python -m pytest tests/unit/test_selection.py tests/unit/test_timeline.py tests/unit/test_viewer.py tests/integration/test_small_seq_viewer.py -q`

Expected: all C2a tests pass.

- [ ] **Step 5: Run the full suite and commit C2a**

Run: `python -m pytest -q`

Expected: all tests pass; the only permitted warning is the pre-existing pytest cache permission warning.

```powershell
git add -- src/flir_seq_tool/viewer.py tests/unit/test_viewer.py tests/integration/test_small_seq_viewer.py
git commit -m "feat: add selection save and load controls"
```
