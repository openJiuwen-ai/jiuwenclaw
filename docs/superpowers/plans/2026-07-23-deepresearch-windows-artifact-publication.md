# DeepResearch Windows Artifact Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make DeepResearch report files, asset directories, verification, and rollback work on native Windows while retaining the existing descriptor-secure POSIX implementation.

**Architecture:** Keep the existing POSIX functions as private backend helpers and add a Windows path backend selected only by `os.name == "nt"`. Windows publishes complete sibling temporary files/directories with no-replace rename semantics, and rollback quarantines the public name before verifying recorded identity and deleting it.

**Tech Stack:** Python 3.11-3.13, pathlib, tempfile, os/stat, pytest.

---

## File Map

- Modify `jiuwenclaw/agentserver/tools/deepresearch_tools.py`
  - dispatch publication and rollback by platform;
  - implement Windows no-replace file and flat-directory publication;
  - implement Windows identity verification and quarantine rollback.
- Modify `tests/unit/agentserver/test_deepresearch_stream_tool.py`
  - reproduce the missing POSIX APIs;
  - cover Windows publication, collision, verification, and rollback;
  - retain existing POSIX race-hardening coverage.

### Task 1: Reproduce Windows file-publication failure

**Files:**
- Test: `tests/unit/agentserver/test_deepresearch_stream_tool.py`

- [ ] **Step 1: Add failing backend-selection and file-publication tests**

Add tests that directly force the Windows dispatcher without changing the host
OS:

```python
def test_windows_publication_backend_does_not_require_posix_constants(
    tmp_path, monkeypatch
):
    target = tmp_path / "report.md"
    monkeypatch.setattr(dt, "_uses_windows_path_publication", lambda: True)

    def reject_link(*_args, **_kwargs):
        raise AssertionError("Windows publication must not call os.link")

    monkeypatch.setattr(dt.os, "link", reject_link)
    metadata = dt._atomic_create_bytes(target, b"complete")

    assert target.read_bytes() == b"complete"
    assert dt._same_identity(metadata, os.lstat(target))


def test_windows_file_publication_never_overwrites_existing_target(
    tmp_path, monkeypatch
):
    target = tmp_path / "report.md"
    target.write_bytes(b"protected")
    monkeypatch.setattr(dt, "_uses_windows_path_publication", lambda: True)

    with pytest.raises(FileExistsError):
        dt._atomic_create_bytes(target, b"replacement")

    assert target.read_bytes() == b"protected"
    assert not list(tmp_path.glob(".report.md.*"))
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run pytest \
  tests/unit/agentserver/test_deepresearch_stream_tool.py::test_windows_publication_backend_does_not_require_posix_constants \
  tests/unit/agentserver/test_deepresearch_stream_tool.py::test_windows_file_publication_never_overwrites_existing_target \
  -q
```

Expected: fail because `_uses_windows_path_publication` and the Windows file
backend do not exist.

### Task 2: Implement no-replace Windows file publication

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch_tools.py:554-569`
- Test: `tests/unit/agentserver/test_deepresearch_stream_tool.py`

- [ ] **Step 1: Split file publication into platform backends**

Preserve the current implementation as the POSIX backend and add:

```python
def _uses_windows_path_publication() -> bool:
    return os.name == "nt"


def _atomic_create_bytes_windows(path: Path, payload: bytes) -> os.stat_result:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            metadata = os.fstat(stream.fileno())
        os.rename(temporary_path, path)
        return metadata
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _atomic_create_bytes(path: Path, payload: bytes) -> os.stat_result:
    if _uses_windows_path_publication():
        return _atomic_create_bytes_windows(path, payload)
    return _atomic_create_bytes_posix(path, payload)
```

The POSIX helper must retain `os.link(..., follow_symlinks=False)`.

- [ ] **Step 2: Run the focused tests and verify GREEN**

Run the two tests from Task 1. Expected: `2 passed`.

- [ ] **Step 3: Run the existing no-overwrite tests**

Run:

```bash
uv run pytest \
  tests/unit/agentserver/test_deepresearch_stream_tool.py \
  -k "never_overwrites_preexisting_target or allocates_same_title_ordinal" \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Commit the first red-green cycle**

```bash
git add \
  jiuwenclaw/agentserver/tools/deepresearch_tools.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py
git commit -m "fix(deepresearch): publish files safely on Windows"
```

### Task 3: Reproduce Windows asset and rollback failures

**Files:**
- Test: `tests/unit/agentserver/test_deepresearch_stream_tool.py`

- [ ] **Step 1: Add failing asset-publication tests**

```python
def test_windows_asset_publication_avoids_directory_descriptors(
    tmp_path, monkeypatch
):
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "chart.png").write_bytes(b"chart")
    final = tmp_path / "report_charts"
    created = []
    monkeypatch.setattr(dt, "_uses_windows_path_publication", lambda: True)

    dt._publish_staged_asset_directory(staged, final, created)

    assert (final / "chart.png").read_bytes() == b"chart"
    assert len(created) == 1
    assert created[0].path == final
    assert created[0].directory_fd is None


def test_windows_asset_publication_preserves_existing_directory(
    tmp_path, monkeypatch
):
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "chart.png").write_bytes(b"replacement")
    final = tmp_path / "report_charts"
    final.mkdir()
    (final / "chart.png").write_bytes(b"protected")
    monkeypatch.setattr(dt, "_uses_windows_path_publication", lambda: True)

    with pytest.raises(FileExistsError):
        dt._publish_staged_asset_directory(staged, final, [])

    assert (final / "chart.png").read_bytes() == b"protected"
```

- [ ] **Step 2: Add failing verification and rollback tests**

```python
def test_windows_directory_verification_rejects_identity_change(
    tmp_path, monkeypatch
):
    public = tmp_path / "assets"
    public.mkdir()
    artifact = dt._CreatedArtifact(public, os.lstat(public))
    public.rename(tmp_path / "owned")
    public.mkdir()
    monkeypatch.setattr(dt, "_uses_windows_path_publication", lambda: True)

    with pytest.raises(RuntimeError, match="namespace changed"):
        dt._verify_created_directories([artifact])


def test_windows_rollback_removes_only_matching_owned_artifact(
    tmp_path, monkeypatch
):
    public = tmp_path / "report.md"
    public.write_bytes(b"owned")
    artifact = dt._CreatedArtifact(public, os.lstat(public))
    monkeypatch.setattr(dt, "_uses_windows_path_publication", lambda: True)

    dt._remove_created_artifacts([artifact])

    assert not public.exists()
    assert not list(tmp_path.glob(".report.md.quarantine-*"))
```

- [ ] **Step 3: Run the four new tests and verify RED**

Run the four exact node IDs with `uv run pytest ... -q`.

Expected: asset publication enters POSIX `O_DIRECTORY/dir_fd` code, and Windows
verification/rollback behavior is absent.

### Task 4: Implement Windows asset publication, verification, and rollback

**Files:**
- Modify: `jiuwenclaw/agentserver/tools/deepresearch_tools.py:632-906`
- Test: `tests/unit/agentserver/test_deepresearch_stream_tool.py`

- [ ] **Step 1: Add recursive removal after identity verification**

Import `shutil` and add a private helper:

```python
def _remove_quarantined_windows_path(
    path: Path, metadata: os.stat_result
) -> None:
    if stat.S_ISDIR(metadata.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()
```

This helper is called only after `lstat` identity matches.

- [ ] **Step 2: Add Windows quarantine rollback**

```python
def _quarantine_created_artifact_windows(artifact: _CreatedArtifact) -> None:
    quarantine = artifact.path.with_name(
        f".{artifact.path.name}.quarantine-{uuid.uuid4().hex}"
    )
    try:
        os.rename(artifact.path, quarantine)
    except FileNotFoundError:
        return

    quarantined_metadata = os.lstat(quarantine)
    if _same_identity(quarantined_metadata, artifact.metadata):
        _remove_quarantined_windows_path(quarantine, quarantined_metadata)
        return

    try:
        os.rename(quarantine, artifact.path)
    except OSError:
        logger.warning(
            "Report cleanup quarantined a replaced entry and left it intact. "
            "path=%s quarantine=%s",
            artifact.path,
            quarantine,
        )
```

Dispatch `_remove_created_artifacts` to this helper only when
`_uses_windows_path_publication()` is true. Leave the POSIX quarantine helpers
unchanged.

- [ ] **Step 3: Add Windows directory verification**

For Windows directory artifacts without a retained descriptor:

```python
namespace_metadata = os.lstat(artifact.path)
if (
    not stat.S_ISDIR(namespace_metadata.st_mode)
    or not _same_identity(namespace_metadata, artifact.metadata)
):
    raise RuntimeError(
        f"report asset directory namespace changed: {artifact.path}"
    )
```

POSIX artifacts with `directory_fd` continue using `fstat`.

- [ ] **Step 4: Add Windows sibling-directory publication**

```python
def _publish_staged_asset_directory_windows(
    staged_directory: Path,
    final_directory: Path,
    created_paths: list[_CreatedArtifact],
) -> None:
    staging_directory = Path(tempfile.mkdtemp(
        prefix=f".{final_directory.name}.",
        dir=final_directory.parent,
    ))
    published = False
    try:
        for staged_path in sorted(staged_directory.iterdir()):
            metadata = os.lstat(staged_path)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(
                    f"staged report asset is not a regular file: "
                    f"{staged_path.name}"
                )
            _atomic_create_bytes_windows(
                staging_directory / staged_path.name,
                staged_path.read_bytes(),
            )
        os.rename(staging_directory, final_directory)
        published = True
        created_paths.append(_CreatedArtifact(
            path=final_directory,
            metadata=os.lstat(final_directory),
        ))
    finally:
        if not published:
            shutil.rmtree(staging_directory, ignore_errors=True)
```

Rename the current implementation to
`_publish_staged_asset_directory_posix` and make the original helper a
dispatcher.

- [ ] **Step 5: Run the four Task 3 tests and verify GREEN**

Expected: `4 passed`.

- [ ] **Step 6: Run existing publication race tests**

Run:

```bash
uv run pytest tests/unit/agentserver/test_deepresearch_stream_tool.py \
  -k "write_report_markdown or install_styled_bundle" -q
```

Expected: all selected POSIX tests pass.

- [ ] **Step 7: Commit the second red-green cycle**

```bash
git add \
  jiuwenclaw/agentserver/tools/deepresearch_tools.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py
git commit -m "fix(deepresearch): publish asset directories on Windows"
```

### Task 5: Verify full Windows-dispatched report flows

**Files:**
- Test: `tests/unit/agentserver/test_deepresearch_stream_tool.py`
- Modify only if required: `jiuwenclaw/agentserver/tools/deepresearch_tools.py`

- [ ] **Step 1: Add end-to-end dispatcher tests**

```python
def test_write_report_markdown_uses_windows_publication_backend(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dt, "_uses_windows_path_publication", lambda: True)

    report_path = _write_report_in(
        tmp_path, final_result=_report_result_with_assets()
    )

    assert Path(report_path).read_text(encoding="utf-8")
    assert (tmp_path / "研究报告-v1_infer" / "inference_7.html").exists()
    assert (tmp_path / "研究报告-v1_charts" / "chart-1.png").exists()


def test_install_styled_bundle_uses_windows_publication_backend(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dt, "_uses_windows_path_publication", lambda: True)
    html_path = tmp_path / "研究报告-v1.html"

    dt._install_styled_bundle(_styled_bundle(tmp_path), html_path)

    assert html_path.exists()
    assert (
        tmp_path / "研究报告-v1_html_infer" / "inference_7.html"
    ).exists()
    assert (
        tmp_path / "研究报告-v1_html_charts" / "chart-1.png"
    ).exists()
```

- [ ] **Step 2: Verify RED or immediate coverage**

If these tests fail, the failure must identify a remaining POSIX-only call and
the implementation is corrected at that call site. If they pass immediately,
record that they cover the already implemented dispatcher and continue.

- [ ] **Step 3: Run focused and full regression suites**

```bash
uv run pytest tests/unit/agentserver/test_deepresearch_stream_tool.py -q
uv run pytest \
  tests/unit/agentserver/test_deepresearch_artifact_naming.py \
  tests/unit/agentserver/test_deepresearch_document_rewrite.py \
  tests/unit/agentserver/test_deepresearch_rewrite_tools.py \
  tests/unit/agentserver/test_deepresearch_stream_router.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py \
  tests/unit/agentserver/test_deepresearch_task_manager_tls.py \
  tests/unit/agentserver/test_markdown_rewrite_map.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 4: Run static verification**

```bash
python -m compileall -q \
  jiuwenclaw/agentserver/tools/deepresearch_tools.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py
git diff --check
git status --short
```

Expected: exit code 0; only planned files are modified.

- [ ] **Step 5: Commit final flow coverage**

```bash
git add \
  jiuwenclaw/agentserver/tools/deepresearch_tools.py \
  tests/unit/agentserver/test_deepresearch_stream_tool.py
git commit -m "test(deepresearch): cover Windows report publication"
```

## Completion Boundary

This plan completes the code-level Windows P1 and macOS-hosted regression
coverage. It does not claim native Windows validation, resolve PR 3649 merge
conflicts, merge/rebase `enterprise_dev`, or push the branch.

