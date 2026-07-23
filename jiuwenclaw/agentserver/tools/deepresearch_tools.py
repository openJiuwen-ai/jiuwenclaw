# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import importlib.util
import io
import json
import logging
import os
import stat
import sys
import tempfile
import threading
import uuid
import zipfile
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.config import get_config
from jiuwenclaw.agentserver.tools._deepresearch_tls import (
    DEEPRESEARCH_TLS_ENV_LOCK as _REPORT_STYLE_LLM_INIT_LOCK,
    scoped_deepresearch_tls_env,
)

logger = logging.getLogger(__name__)
_DEEPRESEARCH_DEPENDENCY = "openjiuwen_deepsearch"
_REPORT_PUBLICATION_ATTEMPTS = 8


@dataclass
class _KeyedLock:
    lock: threading.Lock
    references: int = 0


@dataclass
class _CreatedArtifact:
    path: Path
    metadata: os.stat_result
    parent_fd: int | None = None
    entry_name: str | None = None
    directory_fd: int | None = None


_REPORT_OUTPUT_LOCKS: dict[Path, _KeyedLock] = {}
_REPORT_OUTPUT_LOCKS_GUARD = threading.Lock()

# 使用 contextvars
_deepresearch_route_ctx: contextvars.ContextVar[dict[str, object] | None] = contextvars.ContextVar(
    "jiuwenclaw_deepresearch_route", default=None
)



def push_deepresearch_route(request_id: str, channel_id: str, session_id: str) -> contextvars.Token:
    """设置 DeepResearch 路由上下文。
    
    Args:
        request_id: 请求 ID
        channel_id: 渠道 ID
        session_id: 会话 ID
        
    Returns:
        contextvars.Token，用于恢复上下文
    """
    return _deepresearch_route_ctx.set({
        "request_id": request_id,
        "channel_id": channel_id,
        "session_id": session_id,
    })


def reset_deepresearch_route(token: contextvars.Token) -> None:
    """恢复 DeepResearch 路由上下文。
    
    Args:
        token: contextvars.Token
    """
    _deepresearch_route_ctx.reset(token)


def _get_route() -> dict[str, object]:
    """获取当前路由上下文。
    
    Returns:
        包含 request_id、channel_id、session_id 的字典
    """
    route = _deepresearch_route_ctx.get()
    return route if route is not None else {"request_id": "", "channel_id": "", "session_id": ""}


def _outline_title_cache(route: dict[str, object]) -> dict[str, dict[str, str]]:
    cache = route.get("_deepresearch_outline_titles")
    if isinstance(cache, dict):
        return cache
    cache = {}
    route["_deepresearch_outline_titles"] = cache
    return cache


def _normalize_citation_artifacts(value: object) -> dict[str, str]:
    """Keep only non-empty hidden citation paths allowed in provenance/events."""
    if not isinstance(value, dict):
        return {}
    artifacts: dict[str, str] = {}
    for key in ("raw_report_path", "citations_preview_path"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            artifacts[key] = item.strip()
    return artifacts


def _build_related_artifact_bundle(
    value: object, markdown_index: int
) -> dict | None:
    """Build the hidden companion contract for one visible Markdown file."""
    artifacts = _normalize_citation_artifacts(value)
    related_artifacts = []
    raw_report_path = artifacts.get("raw_report_path")
    if raw_report_path:
        related_artifacts.append({
            "type": "raw_report",
            "path": raw_report_path,
            "contentType": "text/markdown",
            "relatedToPathIndex": markdown_index,
        })
    preview_path = artifacts.get("citations_preview_path")
    if preview_path:
        related_artifacts.append({
            "type": "citations_preview",
            "path": preview_path,
            "contentType": "application/json",
            "schemaVersion": "1.1",
            "relatedToPathIndex": markdown_index,
        })
    if not related_artifacts:
        return None
    return {"schemaVersion": "1.0", "relatedArtifacts": related_artifacts}


def _write_report_markdown(
    final_result: dict,
    file_name: str,
    conversation_id: str,
    citation_artifacts: object = None,
) -> str:
    """Build and write the completed report bundle into the request output directory."""
    from jiuwenclaw.agentserver.tools.deepresearch_plugin.artifact_naming import (
        allocate_initial_paths,
    )
    from jiuwenclaw.agentserver.tools.deepresearch_plugin.report_bundle import (
        build_report_bundle,
        serialize_final_result_snapshot,
    )
    from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
        get_effective_request_output_dir,
    )

    output_dir = get_effective_request_output_dir()
    if not output_dir:
        raise RuntimeError("current request output_dir is unavailable")

    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    with _report_output_lock(output_path):
        allocation_reservations: list[_CreatedArtifact] = []
        try:
            for _attempt in range(_REPORT_PUBLICATION_ATTEMPTS):
                paths = allocate_initial_paths(output_path, file_name)
                created_paths: list[_CreatedArtifact] = []
                try:
                    with tempfile.TemporaryDirectory(
                        prefix=".deepresearch-report-", dir=output_path
                    ) as staging_name:
                        staging_dir = Path(staging_name)
                        staging_base = staging_dir / paths.markdown_path.stem
                        bundle = build_report_bundle(final_result, staging_base)
                        markdown_bytes = bundle.markdown_text.encode("utf-8")
                        snapshot_bytes = serialize_final_result_snapshot(
                            bundle.final_result_snapshot
                        )
                        provenance = {
                            "schema_version": 2,
                            "document_id": f"doc_{uuid.uuid4().hex}",
                            "revision_id": f"rev_{uuid.uuid4().hex}",
                            "parent_revision_id": None,
                            "conversation_id": conversation_id,
                            "markdown_path": str(paths.markdown_path),
                            "content_sha256": hashlib.sha256(
                                markdown_bytes
                            ).hexdigest(),
                            "final_result_path": paths.final_result_path.name,
                            "final_result_sha256": hashlib.sha256(
                                snapshot_bytes
                            ).hexdigest(),
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "operation": {"action": "deepresearch_generate"},
                            "version_number": paths.version.version_number,
                            "version_base_stem": paths.version.base_stem,
                            "citations": bundle.citations,
                            "inference_manifest": bundle.inference_manifest,
                            "chart_manifest": bundle.chart_manifest,
                            "rewrite_history": [],
                        }
                        normalized_artifacts = _normalize_citation_artifacts(
                            citation_artifacts
                        )
                        if normalized_artifacts:
                            provenance["citation_artifacts"] = (
                                normalized_artifacts
                            )
                        provenance_bytes = json.dumps(
                            provenance, ensure_ascii=False, indent=2
                        ).encode("utf-8")

                        report_base = paths.markdown_path.with_suffix("")
                        for staged_directory, final_directory in (
                            (
                                bundle.infer_dir,
                                report_base.with_name(
                                    f"{report_base.name}_infer"
                                ),
                            ),
                            (
                                bundle.chart_dir,
                                report_base.with_name(
                                    f"{report_base.name}_charts"
                                ),
                            ),
                        ):
                            if staged_directory:
                                _publish_staged_asset_directory(
                                    Path(staged_directory),
                                    final_directory,
                                    created_paths,
                                )

                        _verify_created_directories(created_paths)
                        created_paths.append(_CreatedArtifact(
                            path=paths.final_result_path,
                            metadata=_atomic_create_bytes(
                                paths.final_result_path, snapshot_bytes
                            ),
                        ))
                        created_paths.append(_CreatedArtifact(
                            path=paths.provenance_path,
                            metadata=_atomic_create_bytes(
                                paths.provenance_path, provenance_bytes
                            ),
                        ))
                        created_paths.append(_CreatedArtifact(
                            path=paths.markdown_path,
                            metadata=_atomic_create_bytes(
                                paths.markdown_path, markdown_bytes
                            ),
                        ))
                        _verify_created_directories(created_paths)
                except FileExistsError:
                    _remove_created_artifacts(created_paths)
                    allocation_reservations.append(
                        _create_allocation_reservation(paths.markdown_path)
                    )
                    continue
                except BaseException:
                    _remove_created_artifacts(created_paths)
                    raise
                _close_created_artifact_handles(created_paths)
                return str(paths.markdown_path)
        finally:
            _remove_created_artifacts(allocation_reservations)

    raise FileExistsError("report publication attempts exhausted")


def _build_styled_export_llm_config() -> dict:
    """Build llm_config dict for the SDK's report_style_llm_context().

    Resolves LLM credentials via the same bridge-env logic
    (``_build_bridge_env``) used by the skill subprocess, ensuring
    identical API KEY resolution (DeepSearch-专属 → 项目全局 fallback,
    provider-to-type mapping, SSL defaults).
    The SDK's LLMConfig only accepts ``"openai"`` or ``"siliconflow"``
    as model_type; most providers are OpenAI-compatible and default to
    ``"openai"``.
    """
    bridge_env = _build_bridge_env(os.environ)
    api_key = bridge_env.get("LLM_API_KEY", "")
    model_name = bridge_env.get("LLM_MODEL_NAME", "")
    base_url = bridge_env.get("LLM_BASE_URL", "")
    model_type = bridge_env.get("LLM_MODEL_TYPE", "openai").lower()

    # LLMConfig.model_type only allows "openai" or "siliconflow";
    # map everything else to "openai" (OpenAI-compatible).
    if model_type not in ("openai", "siliconflow"):
        model_type = "openai"

    return {
        "general": {
            "model_name": model_name,
            "model_type": model_type,
            "base_url": base_url,
            "api_key": bytearray(api_key, encoding="utf-8"),
            "extension": {
                "extra_body": {
                    "thinking": {"type": "disabled"},
                },
            },
            "verify_ssl": False,
        },
    }


@asynccontextmanager
async def _scoped_report_style_llm_context(context_factory, llm_config):
    """Scope the SDK's env-based TLS setting to runtime LLM initialization."""
    async with AsyncExitStack() as stack:
        async with scoped_deepresearch_tls_env(
            lambda: {
                "LLM_SSL_VERIFY": _build_bridge_env(os.environ)["LLM_SSL_VERIFY"]
            }
        ):
            llm = await stack.enter_async_context(context_factory(llm_config))

        yield llm


def _validate_zip_member(member_name: str) -> str:
    """Normalize and validate a ZIP member path to prevent traversal attacks."""
    from pathlib import PurePosixPath, PureWindowsPath

    normalized_name = member_name.replace("\\", "/")
    posix_path = PurePosixPath(normalized_name)
    windows_path = PureWindowsPath(member_name)
    if posix_path.is_absolute() or windows_path.is_absolute() or ".." in posix_path.parts:
        raise ValueError(f"unsafe ZIP member: {member_name}")
    return normalized_name


def _extract_styled_bundle(convert_content: str, destination: Path) -> Path:
    """Decode base64 ZIP payload from SDK and extract to *destination*."""
    try:
        archive_bytes = base64.b64decode(convert_content, validate=True)
    except ValueError as exc:
        raise ValueError("invalid styled report base64 payload") from exc

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        normalized_names = {
            _validate_zip_member(member.filename)
            for member in archive.infolist()
        }
        if "report_bundle/report.html" not in normalized_names:
            raise ValueError("styled report bundle is missing report_bundle/report.html")
        archive.extractall(destination)

    return destination / "report_bundle"


def _install_styled_bundle(bundle_root: Path, html_path: Path) -> None:
    """Install styled assets without mutating the provenance-bearing bundle."""
    report_base = html_path.with_suffix("")
    infer_dir = report_base.with_name(f"{report_base.name}_html_infer")
    chart_dir = report_base.with_name(f"{report_base.name}_html_charts")

    html = (bundle_root / "report.html").read_text(encoding="utf-8")
    html = html.replace('href="infer/', f'href="{infer_dir.name}/')
    html = html.replace("href='infer/", f"href='{infer_dir.name}/")
    html = html.replace('src="charts/', f'src="{chart_dir.name}/')
    html = html.replace("src='charts/", f"src='{chart_dir.name}/")

    html_path.parent.mkdir(parents=True, exist_ok=True)
    created_paths: list[_CreatedArtifact] = []
    try:
        for staged_directory, final_directory in (
            (bundle_root / "infer", infer_dir),
            (bundle_root / "charts", chart_dir),
        ):
            if staged_directory.is_dir() and any(staged_directory.iterdir()):
                _publish_staged_asset_directory(
                    staged_directory, final_directory, created_paths
                )
        _verify_created_directories(created_paths)
        created_paths.append(_CreatedArtifact(
            path=html_path,
            metadata=_atomic_create_bytes(
                html_path, html.replace("\r\n", "\n").encode("utf-8")
            ),
        ))
        _verify_created_directories(created_paths)
    except BaseException:
        _remove_created_artifacts(created_paths)
        raise
    _close_created_artifact_handles(created_paths)


async def _write_report_artifacts_stream(
    final_result: dict,
    file_name: str,
    conversation_id: str,
    citation_artifacts: object = None,
) -> dict[str, str]:
    """Build and write the completed report bundle as MD + styled HTML.

    Follows the same pattern as ``DeepResearchTaskManager._write_report_artifacts``:
    Markdown is always written; HTML is a best-effort conversion that
    logs a warning on failure but never blocks the primary MD delivery.

    The primary HTML path directly calls the SDK's
    ``report_style_llm_context`` + ``stylize_report`` to produce an
    LLM-styled report bundle, then extracts and installs it locally.
    If that fails (e.g. SDK unavailable, LLM error), the function falls
    back to the lightweight offline converter (``convert_md_to_html``).

    Returns:
        dict mapping format key (``"md"``, ``"html"``) to the
        absolute file path of the generated artifact.  ``"md"`` is always
        present; ``"html"`` is included only when conversion succeeds.
    """
    # Reuse the rewrite-aware Markdown writer so the hidden final-result and
    # provenance sidecars are created before any visible artifact is delivered.
    report_path_md = Path(
        await asyncio.to_thread(
            _write_report_markdown,
            final_result,
            file_name,
            conversation_id,
            citation_artifacts,
        )
    )
    artifacts: dict[str, str] = {"md": str(report_path_md)}

    # --- Styled HTML (primary, best-effort via SDK direct call) ---
    report_path_html = report_path_md.with_suffix(".html")
    try:
        from openjiuwen_deepsearch.algorithm.report_style.service import (
            stylize_report,
        )
        from openjiuwen_deepsearch.framework.openjiuwen.llm.report_style_runtime import (
            report_style_llm_context,
        )

        llm_config = _build_styled_export_llm_config()
        async with _scoped_report_style_llm_context(
            report_style_llm_context, llm_config
        ) as llm:
            result = await stylize_report(final_result, llm)

        # Extract the base64-encoded ZIP bundle and install to target path.
        with tempfile.TemporaryDirectory(prefix="jiuwenclaw_report_") as temporary_dir:
            bundle_root = _extract_styled_bundle(
                result.convert_content, Path(temporary_dir)
            )
            _install_styled_bundle(bundle_root, report_path_html)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(
            "SDK styled HTML export failed, falling back to offline conversion. error=%s",
            exc,
        )
        # --- Offline HTML fallback ---
        try:
            from jiuwenclaw.agentserver.tools.deepresearch_plugin.convert_html_offline import (
                convert_md_to_html,
            )

            with tempfile.TemporaryDirectory(
                prefix=".deepresearch-html-", dir=report_path_html.parent
            ) as staging_name:
                staging_path = Path(staging_name) / report_path_html.name
                convert_md_to_html(str(report_path_md), str(staging_path))
                _atomic_create_bytes(report_path_html, staging_path.read_bytes())
        except Exception as fallback_exc:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Offline HTML conversion also failed. output=%s error=%s",
                report_path_html,
                fallback_exc,
            )
        else:
            artifacts["html"] = str(report_path_html)
    else:
        artifacts["html"] = str(report_path_html)

    return artifacts


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Atomically replace one file without exposing a partially written artifact."""
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


@contextmanager
def _report_output_lock(output_dir: Path):
    """Serialize one output directory and retire the keyed lock when unused."""
    with _REPORT_OUTPUT_LOCKS_GUARD:
        keyed_lock = _REPORT_OUTPUT_LOCKS.get(output_dir)
        if keyed_lock is None:
            keyed_lock = _KeyedLock(lock=threading.Lock())
            _REPORT_OUTPUT_LOCKS[output_dir] = keyed_lock
        keyed_lock.references += 1
    keyed_lock.lock.acquire()
    try:
        yield
    finally:
        keyed_lock.lock.release()
        with _REPORT_OUTPUT_LOCKS_GUARD:
            keyed_lock.references -= 1
            if (
                keyed_lock.references == 0
                and _REPORT_OUTPUT_LOCKS.get(output_dir) is keyed_lock
            ):
                del _REPORT_OUTPUT_LOCKS[output_dir]


def _atomic_create_bytes(path: Path, payload: bytes) -> os.stat_result:
    """Publish a complete immutable file without replacing an existing target."""
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            metadata = os.fstat(stream.fileno())
        os.link(temp_name, path, follow_symlinks=False)
        return metadata
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _atomic_create_bytes_at(
    directory_fd: int, name: str, payload: bytes
) -> os.stat_result:
    """Publish one immutable child relative to a retained directory handle."""
    temporary_name = f".{name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            metadata = os.fstat(stream.fileno())
        os.link(
            temporary_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        return metadata
    finally:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def _same_identity(
    left: os.stat_result, right: os.stat_result
) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _as_created_artifact(
    value: _CreatedArtifact | tuple[Path, os.stat_result],
) -> _CreatedArtifact:
    if isinstance(value, _CreatedArtifact):
        return value
    path, metadata = value
    return _CreatedArtifact(path=path, metadata=metadata)


def _close_created_artifact_handles(
    created_paths: list[_CreatedArtifact | tuple[Path, os.stat_result]],
) -> None:
    for value in created_paths:
        artifact = _as_created_artifact(value)
        if artifact.directory_fd is not None:
            try:
                os.close(artifact.directory_fd)
            except OSError:
                pass
            artifact.directory_fd = None


def _verify_created_directories(
    created_paths: list[_CreatedArtifact | tuple[Path, os.stat_result]],
) -> None:
    """Ensure every public directory name still denotes its retained handle."""
    for value in created_paths:
        artifact = _as_created_artifact(value)
        if artifact.directory_fd is None:
            continue
        handle_metadata = os.fstat(artifact.directory_fd)
        try:
            namespace_metadata = os.stat(
                artifact.path, follow_symlinks=False
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"report asset directory namespace changed: {artifact.path}"
            ) from exc
        if not _same_identity(handle_metadata, namespace_metadata):
            raise RuntimeError(
                f"report asset directory namespace changed: {artifact.path}"
            )


def _make_quarantine_directory(parent_fd: int, entry_name: str) -> tuple[str, int]:
    for _attempt in range(8):
        quarantine_name = (
            f".{entry_name}.quarantine-{uuid.uuid4().hex}"
        )
        try:
            os.mkdir(quarantine_name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        try:
            quarantine_fd = os.open(
                quarantine_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
        except BaseException:
            try:
                os.rmdir(quarantine_name, dir_fd=parent_fd)
            except OSError:
                pass
            raise
        return quarantine_name, quarantine_fd
    raise FileExistsError("unable to allocate report cleanup quarantine")


def _restore_quarantined_entry(
    quarantine_fd: int,
    parent_fd: int,
    entry_name: str,
) -> bool:
    """Restore a non-directory entry without replacing a concurrent writer."""
    try:
        os.link(
            "entry",
            entry_name,
            src_dir_fd=quarantine_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError:
        return False
    os.unlink("entry", dir_fd=quarantine_fd)
    return True


def _restore_quarantined_directory(
    quarantine_fd: int,
    parent_fd: int,
    entry_name: str,
    metadata: os.stat_result,
) -> bool:
    """Recreate a quarantined directory only after claiming the public name."""
    try:
        os.mkdir(
            entry_name,
            mode=stat.S_IMODE(metadata.st_mode),
            dir_fd=parent_fd,
        )
    except FileExistsError:
        return False

    source_fd = -1
    destination_fd = -1
    try:
        source_fd = os.open(
            "entry",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=quarantine_fd,
        )
        destination_fd = os.open(
            entry_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        os.fchmod(destination_fd, stat.S_IMODE(metadata.st_mode))
        public_metadata = os.stat(
            entry_name, dir_fd=parent_fd, follow_symlinks=False
        )
        if not _same_identity(os.fstat(destination_fd), public_metadata):
            return False
        for child_name in os.listdir(source_fd):
            os.rename(
                child_name,
                child_name,
                src_dir_fd=source_fd,
                dst_dir_fd=destination_fd,
            )
        os.rmdir("entry", dir_fd=quarantine_fd)
        return True
    except OSError:
        return False
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        if source_fd >= 0:
            os.close(source_fd)


def _quarantine_created_artifact(artifact: _CreatedArtifact) -> None:
    parent_fd = artifact.parent_fd
    close_parent_fd = False
    entry_name = artifact.entry_name or artifact.path.name
    if parent_fd is None:
        parent_fd = os.open(
            artifact.path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        close_parent_fd = True

    quarantine_name = ""
    quarantine_fd = -1
    try:
        quarantine_name, quarantine_fd = _make_quarantine_directory(
            parent_fd, entry_name
        )
        try:
            os.rename(
                entry_name,
                "entry",
                src_dir_fd=parent_fd,
                dst_dir_fd=quarantine_fd,
            )
        except FileNotFoundError:
            return

        quarantined_metadata = os.stat(
            "entry", dir_fd=quarantine_fd, follow_symlinks=False
        )
        if _same_identity(quarantined_metadata, artifact.metadata):
            if stat.S_ISDIR(quarantined_metadata.st_mode):
                os.rmdir("entry", dir_fd=quarantine_fd)
            else:
                os.unlink("entry", dir_fd=quarantine_fd)
            return

        if stat.S_ISDIR(quarantined_metadata.st_mode):
            restored = _restore_quarantined_directory(
                quarantine_fd,
                parent_fd,
                entry_name,
                quarantined_metadata,
            )
        else:
            restored = _restore_quarantined_entry(
                quarantine_fd, parent_fd, entry_name
            )
        if restored:
            return
        logger.warning(
            "Report cleanup quarantined a replaced entry and left it intact. "
            "path=%s quarantine=%s",
            artifact.path,
            artifact.path.parent / quarantine_name / "entry",
        )
    finally:
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        if quarantine_name:
            try:
                os.rmdir(quarantine_name, dir_fd=parent_fd)
            except OSError:
                pass
        if close_parent_fd:
            os.close(parent_fd)


def _remove_created_artifacts(
    created_paths: list[_CreatedArtifact | tuple[Path, os.stat_result]],
) -> None:
    """Quarantine names atomically, then delete only matching owned entries."""
    for value in reversed(created_paths):
        artifact = _as_created_artifact(value)
        try:
            _quarantine_created_artifact(artifact)
        except OSError as exc:
            logger.warning(
                "Unable to clean current report publication path. "
                "path=%s error=%s",
                artifact.path,
                exc,
            )
        finally:
            if artifact.directory_fd is not None:
                try:
                    os.close(artifact.directory_fd)
                except OSError:
                    pass
                artifact.directory_fd = None


def _publish_staged_asset_directory(
    staged_directory: Path,
    final_directory: Path,
    created_paths: list[_CreatedArtifact],
) -> None:
    """Publish a flat asset directory entirely through a retained dir handle."""
    parent_fd = os.open(
        final_directory.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    directory_fd = -1
    try:
        os.mkdir(final_directory.name, dir_fd=parent_fd)
        namespace_metadata = os.stat(
            final_directory.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        directory_artifact = _CreatedArtifact(
            path=final_directory,
            metadata=namespace_metadata,
        )
        created_paths.append(directory_artifact)
        directory_fd = os.open(
            final_directory.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        handle_metadata = os.fstat(directory_fd)
        if not _same_identity(namespace_metadata, handle_metadata):
            raise RuntimeError(
                f"report asset directory namespace changed: {final_directory}"
            )
        directory_artifact.directory_fd = directory_fd
        directory_fd = -1
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)
        os.close(parent_fd)

    retained_directory_fd = directory_artifact.directory_fd
    if retained_directory_fd is None:
        raise RuntimeError(
            f"report asset directory handle unavailable: {final_directory}"
        )
    for staged_path in sorted(staged_directory.iterdir()):
        metadata = os.lstat(staged_path)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(
                f"staged report asset is not a regular file: {staged_path.name}"
            )
        child_metadata = _atomic_create_bytes_at(
            retained_directory_fd,
            staged_path.name,
            staged_path.read_bytes(),
        )
        created_paths.append(_CreatedArtifact(
            path=final_directory / staged_path.name,
            metadata=child_metadata,
            parent_fd=retained_directory_fd,
            entry_name=staged_path.name,
        ))


def _create_allocation_reservation(
    markdown_path: Path,
) -> _CreatedArtifact:
    """Make a hidden marker that causes the allocator to skip one rejected base."""
    descriptor, reservation_name = tempfile.mkstemp(
        prefix=f".{markdown_path.name}.", dir=markdown_path.parent
    )
    try:
        metadata = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    return _CreatedArtifact(path=Path(reservation_name), metadata=metadata)


def _deepresearch_dependency_available() -> bool:
    """Return whether DeepResearch optional runtime is importable."""
    return importlib.util.find_spec(_DEEPRESEARCH_DEPENDENCY) is not None


def _get_task_manager_cls():
    """Import DeepResearch implementation lazily so agent startup can skip it."""
    from jiuwenclaw.agentserver.tools.deepresearch_task_manager import (  # pylint: disable=import-outside-toplevel
        DeepResearchTaskManager,
    )
    return DeepResearchTaskManager


@tool(
    name="deepresearch_create_task",
    description=(
        "创建深度研究任务，生成独立的长文研究报告。"
        "适用场景：独立的深度研究报告生成、全面市场调研、行业分析报告、政策解读报告。"
        "任务将在后台异步运行且耗时长，完成后通过 WebSocket 推送结果。"
        "返回任务 ID，可用于后续查询状态、取消任务或获取结果，但由于任务执行时间较长，创建后无需立刻查询。"
        "⚠不适用场景：PPT制作辅助研究或准备内容素材、单点数据查询、快速搜索"
    ),
)
async def deepresearch_create_task(
    query: str,
    file_name: str,
) -> str:
    """创建 DeepResearch 任务.

    Args:
        query: 研究查询
        file_name: 报告文件名，不带后缀

    Returns:
        任务 ID
    """
    manager = await _get_task_manager_cls().get_instance()
    route = _get_route()
    task_id = await manager.create_task(
        query=query,
        file_name=file_name,
        session_id=route.get("session_id", ""),
        channel_id=route.get("channel_id", ""),
        request_id=route.get("request_id", ""),
    )

    # 任务已在 create_task() 返回前写入 _tasks，无需等待
    return f"已创建 DeepResearch 任务，任务 ID: {task_id}"


@tool(
    name="deepresearch_get_status",
    description=(
        "查询 DeepResearch 任务的状态。"
        "DeepResearch任务是一个长时间的任务，建议不要频繁查询，以避免对系统造成过大压力。"
        "返回任务的详细信息，包括状态、创建时间、开始时间、完成时间等。"
    ),
)
async def deepresearch_get_status(task_id: str) -> str:
    """获取任务状态.

    Args:
        task_id: 任务 ID

    Returns:
        任务状态信息（JSON 格式字符串）
    """
    manager = await _get_task_manager_cls().get_instance()
    route = _get_route()
    task_info = await manager.get_task_status(
        task_id,
        caller_session_id=route.get("session_id", ""),
        caller_channel_id=route.get("channel_id", ""),
    )

    if task_info is None:
        return f"未找到任务 ID: {task_id} 或无权访问"

    return json.dumps(task_info, ensure_ascii=False, indent=2)


@tool(
    name="deepresearch_list_tasks",
    description=(
        "列出所有 DeepResearch 任务。"
        "支持按状态过滤（running/completed/cancelled/error）。"
        "返回任务列表，按创建时间倒序排列。"
    ),
)
async def deepresearch_list_tasks(status: str = "") -> str:
    """列出当前会话的任务.

    Args:
        status: 可选的状态过滤器

    Returns:
        任务列表（JSON 格式字符串）
    """
    manager = await _get_task_manager_cls().get_instance()
    route = _get_route()
    status_filter = status if status else None
    tasks = await manager.list_tasks(
        status_filter=status_filter,
        caller_session_id=route.get("session_id", ""),
        caller_channel_id=route.get("channel_id", ""),
    )

    if not tasks:
        return "暂无 DeepResearch 任务"

    return json.dumps(tasks, ensure_ascii=False, indent=2)


@tool(
    name="deepresearch_cancel_task",
    description=(
        "取消正在运行的 DeepResearch 任务。"
        "取消后任务状态将变为 cancelled，已生成的内容将被保留。"
        "用于取消不需要的独立研究报告任务。"
    ),
)
async def deepresearch_cancel_task(task_id: str) -> str:
    """取消任务.

    Args:
        task_id: 任务 ID

    Returns:
        操作结果
    """
    manager = await _get_task_manager_cls().get_instance()
    route = _get_route()
    success = await manager.cancel_task(
        task_id,
        caller_session_id=route.get("session_id", ""),
        caller_channel_id=route.get("channel_id", ""),
    )

    if success:
        return f"已取消任务 ID: {task_id}"
    return f"取消任务失败，任务不存在、已完成或无权访问: {task_id}"


@tool(
    name="deepresearch_get_result",
    description=(
        "获取已完成任务的详细结果。"
        "如果任务未完成，返回提示信息。"
    ),
)
async def deepresearch_get_result(task_id: str) -> str:
    """获取任务结果.

    Args:
        task_id: 任务 ID

    Returns:
        任务结果
    """
    manager = await _get_task_manager_cls().get_instance()
    route = _get_route()
    result = await manager.get_task_result(
        task_id,
        caller_session_id=route.get("session_id", ""),
        caller_channel_id=route.get("channel_id", ""),
    )

    if result is None:
        task_info = await manager.get_task_status(
            task_id,
            caller_session_id=route.get("session_id", ""),
            caller_channel_id=route.get("channel_id", ""),
        )
        if task_info:
            return f"任务 {task_id} 尚未完成，当前状态: {task_info['status']}"
        else:
            return f"未找到任务 ID: {task_id} 或无权访问"

    return result


@tool(
    name="deepresearch_run_task",
    description=(
        "旧版兼容入口。deepsearch-research skill 不应调用本工具,必须使用 deepresearch_stream。"
        "执行深度研究任务，并阻塞等待生成独立的长文研究报告。"
        "适用场景：独立的深度研究报告生成、全面市场调研、行业分析报告、政策解读报告。"
        "与异步版本的区别：不提交到后台任务池，直接在当前协程执行，阻塞等待完成后返回结果。"
        "会进行多源检索、分析和报告导出，因此执行时间较长（通常约 20-30 分钟），会阻塞当前 Agent 会话直至完成。"
        "⚠不适用场景：PPT制作辅助研究、PPT准备内容素材、单点数据查询、快速搜索"
    ),
)
async def deepresearch_run_task(
    query: str,
    file_name: str,
) -> str:
    """阻塞执行 DeepResearch 任务并返回结果.

    与 deepresearch_create_task 的区别：
    - 不创建后台任务，直接在当前协程执行
    - 不返回任务 ID，直接返回报告保存路径
    - 阻塞等待完成，适合需要即时获取结果的场景
    - 不受任务池资源限制

    Args:
        query: 研究查询
        file_name: 报告文件名，不带后缀

    Returns:
        报告保存路径信息字符串
    """
    manager = await _get_task_manager_cls().get_instance()
    route = _get_route()
    result = await manager.run_task_direct(
        query=query,
        file_name=file_name,
        session_id=route.get("session_id", ""),
        channel_id=route.get("channel_id", ""),
        request_id=route.get("request_id", ""),
    )
    return result


# ---- 凭据桥接 ----
# 把 RelayClaw sidecar env(project-global 名)桥接成 run_deepsearch.py strict 读的 DeepSearch
# 专属名(LLM_API_KEY/LLM_MODEL_NAME/WEB_SEARCH_API_KEY)。"有值才设",让 skill/.env 兜底。
_PROVIDER_TO_TYPE = {
    "openai": "openai", "openrouter": "openai",
    "zhipu": "zhipu", "glm": "zhipu",
    "qwen": "qwen", "dashscope": "qwen",
    "deepseek": "deepseek",
    "modelarts": "qwen",  # 华为云 ModelArts,spike 确认
}
def _map_provider_to_type(provider: str) -> str:
    return _PROVIDER_TO_TYPE.get(provider.strip().lower(), provider.strip().lower())


def _build_bridge_env(os_env: dict[str, str]) -> dict[str, str]:
    """Reuse deepresearch_run_task config resolution for the stream child."""
    env = dict(os_env)  # 继承全部 sidecar env(API_KEY/MODEL_NAME/BOCHA_API_KEY/default_headers/...)
    for key in ("WEB_SEARCH_ENGINE_NAME", "WEB_SEARCH_API_KEY", "WEB_SEARCH_URL"):
        env.pop(key, None)

    resolved = _get_task_manager_cls()._load_config(os_env)
    api_key = resolved["LLM_API_KEY"]
    model = resolved["LLM_MODEL_NAME"]
    base_url = resolved["LLM_BASE_URL"]
    model_type = _map_provider_to_type(resolved["LLM_MODEL_TYPE"])
    if api_key:
        env["LLM_API_KEY"] = api_key
    if model:
        env["LLM_MODEL_NAME"] = model
    if base_url:
        env["LLM_BASE_URL"] = base_url
    if model_type:
        env["LLM_MODEL_TYPE"] = model_type

    engine = resolved["WEB_SEARCH_ENGINE_NAME"]
    skey = resolved["WEB_SEARCH_API_KEY"]
    surl = resolved["WEB_SEARCH_URL"]
    search_is_complete = bool(engine and skey and (engine != "petal" or surl))
    if search_is_complete:
        env["WEB_SEARCH_API_KEY"] = skey
        env["WEB_SEARCH_ENGINE_NAME"] = engine
        if surl:
            env["WEB_SEARCH_URL"] = surl

    # LLM_SSL_VERIFY:JiuwenClaw Python 中的 openjiuwen 0.1.10+ factory 从 env 读
    # verify_ssl(os.getenv("LLM_SSL_VERIFY","true")),sidecar 不设此项 → 子进程默认
    # true → 需 ssl_cert → "ssl_cert is required when verify_ssl is True" 构建失败。
    # in-process manager 因 sidecar 的 openjiuwen 版本不同不受影响;子进程必须显式 false
    # (匹配 manager 的 verify_ssl=False 实际效果)。sidecar 若显式设了则尊重。
    env["LLM_SSL_VERIFY"] = os_env.get("LLM_SSL_VERIFY", "false")
    env["TOOL_SSL_VERIFY"] = os_env.get("TOOL_SSL_VERIFY", "false")

    return env


def _build_deepresearch_child_env(
    os_env: dict[str, str], *, interactive_ask: bool
) -> dict[str, str]:
    """Build the stream child env with an explicit per-request HITL switch."""
    env = _build_bridge_env(os_env)
    env["DEEPSEARCH_HITL"] = "true" if interactive_ask else "false"
    env["PYTHONUNBUFFERED"] = "1"
    return env


async def _iter_ndjson_lines(stream, read_size: int = 64 * 1024):
    """Read newline-delimited subprocess output without StreamReader's line limit."""
    if stream is None:
        return

    read = getattr(stream, "read", None)
    if not callable(read):
        async for line in stream:
            yield line
        return

    pending = bytearray()
    while True:
        chunk = await read(read_size)
        if not chunk:
            break
        pending.extend(chunk)
        while True:
            newline = pending.find(b"\n")
            if newline < 0:
                break
            yield bytes(pending[:newline])
            del pending[:newline + 1]

    if pending:
        yield bytes(pending)


@tool(
    name="deepresearch_stream",
    description=(
        "deepsearch-research skill 的首选且唯一入口。流式执行 DeepResearch 深度研究,"
        "进度经 chat 通道(chat.reasoning/task.start/task.complete/"
        "processing_status)实时推送到前端。执行到人机交互节点时返回 interrupted outcome,"
        "由 agent 调 ask_user_question 处理后,再以 action=resume 调本工具恢复。"
        "不返回中间 chunk,只返回 outcome,避免污染 agent context。"
        "⚠不适用场景:PPT制作辅助研究、单点数据查询、快速搜索"
    ),
)
async def deepresearch_stream(
    action: str,
    query: str = "",
    conversation_id: str = "",
    feedback: str = "",
    node: str = "",
    file_name: str = "",
) -> str:
    """流式执行 DeepResearch,进度走 chat 通道,中断/完成返短 outcome.

    Args:
        action: "start"(首轮)或 "resume"(中断恢复)
        query: action=start 时的研究主题
        conversation_id: action=resume 必填;start 时空则脚本自生成并在 outcome 回传
        feedback: action=resume 时的 per-node 反馈 JSON(见 SKILL.md 映射表)
        node: action=resume 时的中断节点 id
        file_name: 报告文件名(不带后缀)

    Returns:
        JSON 串:
          {"status":"interrupted","conversation_id":"...","node_id":"...","marker":{...},"prompt":"..."}
            marker 结构化透传(agent 按 (1) §Stage3 读 marker.content(OutlineContent→preview 卡)
            /marker.questions/marker.prompt 建 free_input/preview 卡);prompt 扁平字符串 fallback。
          {"status":"completed","conversation_id":"...","report_delivered":true,"report_chars":123}
            正常 chat 路由下报告已通过 chat.file 作为 Markdown 文件交付,不进入 tool outcome。
          {"status":"error","error":"..."}
    """
    from jiuwenclaw.agentserver.gateway_push.transport import (  # pylint: disable=import-outside-toplevel
        WebSocketGatewayPushTransport,
    )
    from jiuwenclaw.agentserver.deep_agent.ask_user_question_registry import (  # pylint: disable=import-outside-toplevel
        get_ask_request_context,
    )
    from jiuwenclaw.agentserver.tools.deepresearch_stream_router import (  # pylint: disable=import-outside-toplevel
        RouterState,
        advance_stage,
        build_interrupt_prompt,
        collected_questions,
        is_outline_status_placeholder,
        route_chunk,
    )

    route = _get_route()
    interactive_ask, _, _, _ = get_ask_request_context()
    # Force HITL on: deepsearch-research SKILL.md requires feedback_handler
    # interruption for research direction clarification.  The upstream
    # ContextVar defaults to False when the frontend omits interactiveAsk,
    # which causes DEEPSEARCH_HITL="false" and the SDK skips the
    # feedback_handler node entirely.
    if not interactive_ask:
        interactive_ask = True
    outline_title_cache = _outline_title_cache(route)
    python_bin = _resolve_jiuwenclaw_python()
    script = _resolve_run_script()
    if not script:
        return '{"status":"error","error":"run_deepsearch.py not found"}'

    import tempfile  # pylint: disable=import-outside-toplevel
    progress_file = os.path.join(
        tempfile.gettempdir(), f"dr_progress_{os.getpid()}_{conversation_id or 'new'}.jsonl"
    )

    if action == "start":
        argv = [python_bin, script, "run", "--query", query, "--progress-file", progress_file]
        if conversation_id:
            argv += ["--conversation-id", conversation_id]
    elif action == "resume":
        if not conversation_id or not node:
            return '{"status":"error","error":"resume requires conversation_id and node"}'
        argv = [python_bin, script, "resume", "--conversation-id", conversation_id,
                "--feedback", feedback or "", "--node", node,
                "--interrupt-feedback", "", "--progress-file", progress_file]
    else:
        return f'{{"status":"error","error":"unknown action: {action}"}}'

    push = WebSocketGatewayPushTransport()
    cached_titles = outline_title_cache.get(conversation_id, {}) if action == "resume" else {}
    state = RouterState(section_titles=dict(cached_titles))
    outcome_cid = conversation_id

    async def _send(payload: dict) -> bool:
        if not route.get("session_id") or not route.get("channel_id"):
            return False
        msg = {
            "request_id": route.get("request_id", ""),
            "channel_id": route["channel_id"],
            "session_id": route["session_id"],
            "payload": payload,
            "is_complete": False,
        }
        try:
            await push.send_push(msg)
            logger.info(
                "[deepresearch_stream] send_push event_type=%s agent=%s current_task=%s",
                payload.get("event_type", ""),
                payload.get("agent", ""),
                payload.get("current_task", ""),
            )
            return True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("[deepresearch_stream] send_push failed: %s", exc)
            return False

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_build_deepresearch_child_env(
                os.environ, interactive_ask=interactive_ask
            ),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return f'{{"status":"error","error":"spawn failed: {exc}"}}'

    stderr_tail = bytearray()

    async def _drain_stderr() -> None:
        stream = proc.stderr
        if stream is None:
            return
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                return
            stderr_tail.extend(chunk)
            if len(stderr_tail) > 20000:
                del stderr_tail[:-20000]

    stderr_task = asyncio.create_task(_drain_stderr())
    outcome = {"status": "error", "error": "no terminal marker"}
    try:
        async for raw in _iter_ndjson_lines(proc.stdout):
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue  # 非 JSON 日志行(Parsed args / Loaded env 等)skip

            status = chunk.get("__deepsearch_status__")
            if status in ("started", "resuming"):
                cid = chunk.get("conversation_id", "")
                if cid:
                    outcome_cid = cid
                initial_stage = 1
                if action == "resume" and node == "outline_interaction":
                    initial_stage = 2
                elif action == "resume" and node == "user_feedback_processor":
                    initial_stage = 6
                stage_update = advance_stage(state, initial_stage)
                if stage_update is not None:
                    await _send(stage_update)
                await _send({"event_type": "chat.processing_status",
                             "is_processing": True,
                             "current_task": status})
                continue
            if status == "interrupted":
                node_id = chunk.get("agent", state.interrupt_node_id)
                # marker 结构化透传:agent 按 (1) §Stage3 读 marker.content/questions/prompt
                # 建 free_input/preview 卡(OutlineContent→Markdown、outline_ref=conversation_id、meta 轮次)
                marker = {k: v for k, v in chunk.items() if k != "__deepsearch_status__"}
                if node_id == "feedback_handler" and not marker.get("questions"):
                    questions = collected_questions(state)
                    if questions:
                        marker["questions"] = questions
                if (
                    node_id == "outline_interaction"
                    and not marker.get("outline")
                    and (
                        not marker.get("content")
                        or is_outline_status_placeholder(marker.get("content"))
                    )
                    and state.outline_parts
                ):
                    marker["outline"] = "".join(state.outline_parts)
                resolved_cid = (
                    chunk.get("conversation_id", outcome_cid)
                    or state.interrupt_conversation_id
                )
                if node_id == "outline_interaction":
                    outline_content = marker.get("outline") or marker.get("content")
                    if outline_content:
                        section_titles = _get_task_manager_cls()._extract_section_titles(
                            outline_content if isinstance(outline_content, str)
                            else json.dumps(outline_content, ensure_ascii=False)
                        )
                        if section_titles and resolved_cid:
                            outline_title_cache[str(resolved_cid)] = section_titles
                # UFP 报告不在 marker(脚本不透传),tool 注入累积的 report_parts(截断 6000)
                if node_id == "user_feedback_processor" and state.report_parts:
                    rpt = "".join(state.report_parts)
                    marker["report"] = rpt[:6000] + ("…\n(完整报告见最终产物)" if len(rpt) > 6000 else "")
                outcome = {"status": "interrupted",
                           "conversation_id": resolved_cid,
                           "node_id": node_id,
                           "marker": marker,
                           "prompt": build_interrupt_prompt(node_id, state, marker, query)}
                # The runner emits this marker before its async cleanup persists the
                # graph checkpoint. Keep consuming to EOF so it can exit naturally;
                # breaking here makes finally terminate the resumable subprocess.
                continue
            if status == "completed":
                delivery_update = advance_stage(state, 6)
                if delivery_update is not None:
                    await _send(delivery_update)
                final_result = chunk.get("final_result")
                response_content = (
                    final_result.get("response_content", "")
                    if isinstance(final_result, dict)
                    else ""
                )
                has_chat_route = bool(route.get("session_id") and route.get("channel_id"))
                if response_content and has_chat_route:
                    citation_artifacts = _normalize_citation_artifacts(chunk)
                    try:
                        artifacts = await _write_report_artifacts_stream(
                            final_result,
                            file_name,
                            chunk.get("conversation_id", outcome_cid),
                            citation_artifacts,
                        )
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        outcome = {
                            "status": "error",
                            "conversation_id": chunk.get("conversation_id", outcome_cid),
                            "error_code": "report_file_write_failed",
                            "error": str(exc),
                        }
                        break
                    # Deliver all generated artifacts (MD + HTML)
                    files_to_deliver = [
                        {"path": v, "name": os.path.basename(v)}
                        for v in artifacts.values()
                    ]
                    file_payload = {
                        "event_type": "chat.file",
                        "files": files_to_deliver,
                    }
                    markdown_index = list(artifacts).index("md")
                    bundle = _build_related_artifact_bundle(
                        citation_artifacts, markdown_index
                    )
                    if bundle:
                        file_payload["metadata"] = {"artifactBundle": bundle}
                    report_delivered = await _send(file_payload)
                elif not response_content:
                    outcome = {
                        "status": "error",
                        "conversation_id": chunk.get("conversation_id", outcome_cid),
                        "error_code": "empty_report",
                        "error": "completed marker missing final_result.response_content",
                    }
                    break
                else:
                    outcome = {
                        "status": "error",
                        "conversation_id": chunk.get("conversation_id", outcome_cid),
                        "error_code": "report_file_delivery_failed",
                        "error": "Markdown report file route is unavailable",
                    }
                    break

                if report_delivered:
                    completed_update = advance_stage(state, 6, complete=True)
                    if completed_update is not None:
                        await _send(completed_update)
                    outcome = {
                        "status": "completed",
                        "conversation_id": chunk.get("conversation_id", outcome_cid),
                        "report_delivered": True,
                        "report_chars": len(response_content),
                    }
                elif response_content and has_chat_route:
                    outcome = {
                        "status": "error",
                        "conversation_id": chunk.get("conversation_id", outcome_cid),
                        "error_code": "report_file_delivery_failed",
                        "error": "Report files could not be delivered",
                        "report_path": artifacts.get("md", ""),
                    }
                break
            if status == "error":
                outcome = {
                    "status": "error",
                    "conversation_id": chunk.get("conversation_id", outcome_cid),
                    "error_code": chunk.get("error_code", "workflow_error"),
                    "error": chunk.get("error", "deepresearch workflow failed"),
                }
                break
            # progress chunk
            if chunk.get("agent") == "outline":
                outline_content = chunk.get("content")
                if outline_content:
                    section_titles = _get_task_manager_cls()._extract_section_titles(
                        outline_content if isinstance(outline_content, str)
                        else json.dumps(outline_content, ensure_ascii=False)
                    )
                    if section_titles:
                        state.section_titles.update(section_titles)
                        state.authoritative_section_indices.update(section_titles)
            for payload in route_chunk(chunk, state):
                await _send(payload)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                proc.kill()
        try:
            await asyncio.wait_for(stderr_task, timeout=2)
        except (asyncio.TimeoutError, Exception):  # pylint: disable=broad-exception-caught
            stderr_task.cancel()
        if outcome.get("status") == "error":
            outcome["returncode"] = proc.returncode
            captured_stderr = stderr_tail.decode("utf-8", errors="replace")
            if captured_stderr.strip():
                outcome["stderr_tail"] = captured_stderr
        try:
            os.remove(progress_file)
        except OSError:
            pass

    return json.dumps(outcome, ensure_ascii=False)


def _resolve_skill_root() -> str:
    """定位 deepsearch-research skill 目录。

    优先 JIUWENCLAW_SHARED_SKILLS_DIRS(sidecar 的 cwd 不一定是仓根——sidecar 常跑在
    vendor/jiuwenclaw 下,os.getcwd() 会落空);使用当前平台的路径分隔符。
    fallback: cwd/office-claw-skills(仅当 sidecar cwd 恰为仓根时命中)。
    """
    candidates: list[str] = []
    dirs_env = os.environ.get("JIUWENCLAW_SHARED_SKILLS_DIRS", "")
    if dirs_env:
        for d in dirs_env.split(os.pathsep):
            d = d.strip()
            if d:
                candidates.append(os.path.join(d, "deepsearch-research"))
    # fallback:cwd 相对(仅仓根 cwd 命中)
    candidates.append(os.path.join(os.getcwd(), "office-claw-skills", "deepsearch-research"))
    for c in candidates:
        if os.path.exists(os.path.join(c, "scripts", "run_deepsearch.py")):
            return c
    return ""


def _resolve_jiuwenclaw_python() -> str:
    """复用当前 JiuwenClaw 进程的 Python 解释器。"""
    return sys.executable


def _resolve_run_script() -> str:
    """定位 run_deepsearch.py。"""
    root = _resolve_skill_root()
    if not root:
        return ""
    p = os.path.join(root, "scripts", "run_deepsearch.py")
    return p if os.path.exists(p) else ""


def enable_deepresearch() -> bool:
    """检查 DeepResearch 工具是否启用.

    Returns:
        是否启用 DeepResearch 工具
    """

    try:
        cfg = get_config()
        if not bool(cfg.get("enable_deepresearch", True)):
            return False
        return True
    except Exception:
        return False


def get_deepresearch_tools() -> list:
    """获取 DeepResearch 工具列表.

    Returns:
        工具列表
    """
    if not enable_deepresearch():
        return []
    if not _deepresearch_dependency_available():
        logger.warning(
            "DeepResearch tools disabled because optional dependency is missing: %s",
            _DEEPRESEARCH_DEPENDENCY,
        )
        return []
    from jiuwenclaw.agentserver.tools.deepresearch_rewrite_tools import (  # pylint: disable=import-outside-toplevel
        deepresearch_commit_rewrite,
        deepresearch_prepare_rewrite,
    )

    return [
        deepresearch_stream,
        deepresearch_prepare_rewrite,
        deepresearch_commit_rewrite,
    ]


__all__ = [
    "deepresearch_create_task",
    "deepresearch_get_status",
    "deepresearch_list_tasks",
    "deepresearch_cancel_task",
    "deepresearch_get_result",
    "deepresearch_run_task",
    "deepresearch_stream",
    "get_deepresearch_tools",
]
