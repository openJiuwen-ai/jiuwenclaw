"""JiuwenSwarm's in-process owner of the embedded PCS runtime.

The Host owns the configuration file and Core lifecycle, and delegates Context
queries to Core.  It does not expose a transport, create a second service
object, or read any Context file itself.
"""

from __future__ import annotations

import asyncio
import contextlib
from copy import deepcopy
import os
from pathlib import Path
import tempfile
from typing import NoReturn, cast

import yaml

from openjiuwen.core.proactive_context import PCS

from jiuwenswarm.common.config import get_default_models


_CONFIG_FILENAME = "pcs.yaml"
_STOP_TIMEOUT_SECONDS = 30.0


def _host_error(
    message: str,
    *,
    status_name: str = "CONTEXT_PROACTIVE_CONFIG_INVALID",
    cause: BaseException | None = None,
) -> PCS.Error:
    """Create the existing Core error type without adding a Host exception."""

    # PCS.Config.from_dict() is the Core's public error-construction boundary.
    # Deliberately use it instead of importing another Core error class here:
    # the JiuwenSwarm side has exactly one Core import, PCS.
    try:
        PCS.Config.from_dict({})
    except PCS.Error as baseline:
        # Core intentionally keeps PCS-owned status values behind the single
        # PCS import; this Host compatibility bridge therefore uses its
        # protected resolver without importing a second Core symbol.
        status = PCS._status_for_name(status_name)  # pylint: disable=protected-access
        return type(baseline)(status, msg=message, cause=cause)
    return PCS.Error(PCS.Error.status, msg=message, cause=cause)


def _raise_host_error(
    message: str,
    *,
    status_name: str = "CONTEXT_PROACTIVE_CONFIG_INVALID",
    cause: BaseException | None = None,
) -> NoReturn:
    raise _host_error(message, status_name=status_name, cause=cause) from None


def _as_host_error(
    exc: BaseException,
    message: str,
    *,
    status_name: str = "CONTEXT_PROACTIVE_STATE_INVALID",
) -> PCS.Error:
    if isinstance(exc, PCS.Error):
        return exc
    return _host_error(message, status_name=status_name, cause=exc)


def _reject_symlink_chain(path: Path) -> None:
    current = path
    while True:
        if current.is_symlink():
            _raise_host_error(
                "PCS configuration path must not traverse a symlink",
                status_name="CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR",
            )
        parent = current.parent
        if parent == current:
            return
        current = parent


def _serialize_config(config: dict[str, object]) -> bytes:
    try:
        text = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        return text.encode("utf-8")
    except PCS.Error:
        raise
    except Exception as exc:
        _raise_host_error("PCS configuration could not be serialized", cause=exc)


def _stage_yaml(path: Path, payload: bytes) -> Path:
    temporary: Path | None = None
    try:
        _reject_symlink_chain(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        with contextlib.suppress(OSError):
            os.chmod(temporary, 0o600)
        return temporary
    except PCS.Error:
        if temporary is not None:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
        raise
    except Exception as exc:
        if temporary is not None:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
        _raise_host_error(
            "PCS configuration temporary file could not be written",
            status_name="CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR",
            cause=exc,
        )


def _replace_yaml(temporary: Path, path: Path) -> None:
    try:
        _reject_symlink_chain(path)
        os.replace(temporary, path)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
    except PCS.Error:
        raise
    except Exception as exc:
        _raise_host_error(
            "PCS configuration file could not be replaced",
            status_name="CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR",
            cause=exc,
        )


def _cleanup_temporary(path: Path | None) -> None:
    if path is not None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def _read_yaml(path: Path) -> dict[str, object] | None:
    try:
        _reject_symlink_chain(path)
        if not path.exists():
            return None
        if not path.is_file():
            _raise_host_error(
                "PCS configuration path is not a file",
                status_name="CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR",
            )
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            _raise_host_error(
                "PCS configuration YAML could not be read",
                status_name="CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR",
                cause=exc,
            )
        try:
            loaded = yaml.safe_load(text)
        except Exception as exc:
            _raise_host_error("PCS configuration YAML is invalid", cause=exc)
        if not isinstance(loaded, dict):
            _raise_host_error("PCS configuration YAML must contain an object")
        return loaded
    except PCS.Error:
        raise
    except Exception as exc:
        _raise_host_error("PCS configuration YAML could not be read", cause=exc)


def _is_runtime_active(status: object) -> bool:
    return getattr(status, "state", None) in {"STARTING", "RUNNING"}


def _resolve_model_reference(
    model_index: object,
) -> tuple[dict[str, object], dict[str, object]]:
    if type(model_index) is not int or model_index < 0:
        _raise_host_error("model_index must be a non-negative integer")
    models = get_default_models()
    if model_index >= len(models):
        _raise_host_error("selected JiuwenSwarm model no longer exists")
    entry = models[model_index]
    if not isinstance(entry, dict):
        _raise_host_error("selected JiuwenSwarm model is invalid")
    client_raw = entry.get("model_client_config")
    request_raw = entry.get("model_config_obj")
    if not isinstance(client_raw, dict) or not isinstance(request_raw, dict):
        _raise_host_error("selected JiuwenSwarm model is invalid")
    client = deepcopy(client_raw)
    request = deepcopy(request_raw)
    model_name = str(client.pop("model_name", "")).strip()
    if not model_name:
        _raise_host_error("selected JiuwenSwarm model is invalid")
    request["model"] = model_name
    return client, request


def _build_core_config(stored: dict[str, object]) -> PCS.Config:
    raw = deepcopy(stored)
    model_index = raw.pop("model_index", None)
    raw.pop("model_client", None)
    raw.pop("model_request", None)
    if model_index is not None:
        client, request = _resolve_model_reference(model_index)
        raw["model_client"] = client
        raw["model_request"] = request
    try:
        return PCS.Config.from_dict(raw)
    except PCS.Error:
        raise
    except Exception as exc:
        _raise_host_error("PCS configuration is invalid", cause=exc)


def _prepare_stored_config(
    config: dict[str, object],
) -> tuple[dict[str, object], PCS.Config]:
    if not isinstance(config, dict):
        _raise_host_error("PCS configuration must be an object")
    stored = deepcopy(config)
    stored.pop("model_client", None)
    stored.pop("model_request", None)
    candidate = _build_core_config(stored)
    normalized = candidate.model_dump(mode="json", by_alias=True)
    normalized.pop("model_client", None)
    normalized.pop("model_request", None)
    if "model_index" in stored:
        normalized["model_index"] = stored["model_index"]
    return normalized, candidate


class PCSHostAPI:
    """The only JiuwenSwarm API for configuring and controlling embedded PCS."""

    def __init__(self, *, home: str | Path) -> None:
        self._home = Path(home).expanduser().resolve()
        self._config_path = self._home / _CONFIG_FILENAME
        self._pcs = PCS(home=self._home)
        self._config: PCS.Config | None = None
        self._stored_config: dict[str, object] | None = None
        self._operation_lock = asyncio.Lock()

    async def configure(self, config: dict[str, object]) -> None:
        """Validate, save, and apply one complete configuration."""

        stored, candidate = _prepare_stored_config(config)
        payload = _serialize_config(stored)

        async with self._operation_lock:
            await self._apply_configuration_locked(candidate, stored, payload)

    async def _apply_configuration_locked(
        self,
        candidate: PCS.Config,
        stored: dict[str, object],
        payload: bytes,
    ) -> None:
        """Apply one validated complete configuration while the Host lock is held."""

        previous = self._config
        previous_stored = self._stored_config
        temporary: Path | None
        if previous is not None and previous == candidate:
            temporary = _stage_yaml(self._config_path, payload)
            try:
                _replace_yaml(temporary, self._config_path)
            finally:
                _cleanup_temporary(temporary)
            self._stored_config = deepcopy(stored)
            return

        temporary = _stage_yaml(self._config_path, payload)
        try:
            previous_active = False
            if previous is not None:
                with contextlib.suppress(Exception):
                    previous_active = _is_runtime_active(await self._pcs.snapshot())

            if previous is not None:
                try:
                    await self._pcs.deactivate_runtime(
                        timeout_seconds=_STOP_TIMEOUT_SECONDS
                    )
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    raise _as_host_error(
                        exc,
                        "PCS previous runtime could not be stopped",
                    ) from None

            try:
                await self._pcs.set_configuration(candidate)
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                error = _as_host_error(
                    exc,
                    "PCS configuration could not be applied",
                    status_name="CONTEXT_PROACTIVE_CONFIG_INVALID",
                )
                await self._restore_previous(
                    previous,
                    previous_stored,
                    previous_active,
                )
                raise error from None

            try:
                if candidate.enabled:
                    await self._pcs.activate_runtime()
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                error = _as_host_error(
                    exc,
                    "PCS runtime could not be started",
                )
                await self._restore_previous(
                    previous,
                    previous_stored,
                    previous_active,
                )
                raise error from None

            try:
                _replace_yaml(temporary, self._config_path)
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                error = _as_host_error(
                    exc,
                    "PCS configuration file could not be replaced",
                    status_name="CONTEXT_PROACTIVE_FILE_EXECUTION_ERROR",
                )
                await self._restore_previous(
                    previous,
                    previous_stored,
                    previous_active,
                )
                raise error from None
            temporary = None
            self._config = candidate
            self._stored_config = deepcopy(stored)
        finally:
            _cleanup_temporary(temporary)

    async def get_overview(self) -> dict[str, object]:
        """Return one consistent copy of the full configuration and Core status."""

        async with self._operation_lock:
            config = deepcopy(self._stored_config)
            status = await self._pcs.snapshot()
            return {
                "configured": self._stored_config is not None,
                "config": config,
                "status": status.model_dump(mode="json"),
            }

    async def get_runtime_config(self) -> dict[str, object]:
        """Return the complete persistent PCS configuration."""

        async with self._operation_lock:
            if self._stored_config is None:
                _raise_host_error("PCS is not configured")
            return deepcopy(self._stored_config)

    async def patch_runtime_config(
        self,
        patch: dict[str, object],
    ) -> dict[str, object]:
        """Atomically patch the allowed runtime configuration fields."""

        if not isinstance(patch, dict):
            _raise_host_error("patch must be an object")
        unknown = set(patch) - {"strategy_profile"}
        if unknown:
            _raise_host_error("runtime patch contains unsupported fields")
        async with self._operation_lock:
            if self._stored_config is None:
                _raise_host_error("PCS is not configured")
            stored = deepcopy(self._stored_config)
            stored.update(deepcopy(patch))
            stored, candidate = _prepare_stored_config(stored)
            await self._apply_configuration_locked(
                candidate,
                stored,
                _serialize_config(stored),
            )
            return deepcopy(stored)

    async def select_model(self, model_index: int) -> dict[str, object]:
        """Select one current JiuwenSwarm model by its models.list index."""

        if type(model_index) is not int or model_index < 0:
            _raise_host_error("model_index must be a non-negative integer")
        async with self._operation_lock:
            if self._stored_config is None:
                _raise_host_error("PCS is not configured")
            stored = deepcopy(self._stored_config)
            stored["model_index"] = model_index
            stored, candidate = _prepare_stored_config(stored)
            await self._apply_configuration_locked(
                candidate,
                stored,
                _serialize_config(stored),
            )
            return deepcopy(stored)

    async def set_runtime_enabled(self, enabled: bool) -> dict[str, object]:
        """Persist and apply the whole PCS runtime enable switch."""

        if type(enabled) is not bool:
            _raise_host_error("enabled must be a boolean")
        async with self._operation_lock:
            if self._stored_config is None:
                _raise_host_error("PCS is not configured")
            stored = deepcopy(self._stored_config)
            stored["enabled"] = enabled
            stored, candidate = _prepare_stored_config(stored)
            await self._apply_configuration_locked(
                candidate,
                stored,
                _serialize_config(stored),
            )
            return deepcopy(stored)

    async def list_fetch_services(self) -> list[dict[str, object]]:
        """Return every fixed fetch service with current state and last error."""

        async with self._operation_lock:
            if self._stored_config is None:
                return []
            status = await self._pcs.snapshot()
            states = getattr(status, "fetch_service_states", {})
            errors = getattr(status, "fetch_service_errors", {})
            services = cast(
                list[dict[str, object]],
                deepcopy(self._stored_config["fetch_services"]),
            )
            for service in services:
                service_id = cast(str, service["service_id"])
                service["state"] = states.get(service_id, "STOPPED")
                service["last_error"] = errors.get(service_id)
            return services

    async def patch_fetch_service(
        self,
        service_id: str,
        patch: dict[str, object],
    ) -> dict[str, object]:
        """Atomically patch one existing fixed fetch service."""

        if not isinstance(service_id, str) or not service_id.strip():
            _raise_host_error("service_id must be a non-empty string")
        if not isinstance(patch, dict):
            _raise_host_error("patch must be an object")
        allowed = {
            "interval_seconds",
            "max_items_per_run",
            "source",
            "credentials",
        }
        if set(patch) - allowed:
            _raise_host_error("fetch service patch contains unsupported fields")
        normalized_id = service_id.strip()
        async with self._operation_lock:
            if self._stored_config is None:
                _raise_host_error("PCS is not configured")
            stored = deepcopy(self._stored_config)
            services = cast(list[dict[str, object]], stored["fetch_services"])
            target = next(
                (
                    service
                    for service in services
                    if service["service_id"] == normalized_id
                ),
                None,
            )
            if target is None:
                _raise_host_error("unknown PCS fetch service")
            target.update(deepcopy(patch))
            stored, candidate = _prepare_stored_config(stored)
            await self._apply_configuration_locked(
                candidate,
                stored,
                _serialize_config(stored),
            )
            updated_services = cast(
                list[dict[str, object]],
                stored["fetch_services"],
            )
            updated = next(
                service
                for service in updated_services
                if service["service_id"] == normalized_id
            )
            return deepcopy(updated)

    async def get_fetch_run_status(
        self,
        service_id: str | None = None,
    ) -> dict[str, object]:
        """Return current run state and last retained error for fetch services."""

        if service_id is not None and (
            not isinstance(service_id, str) or not service_id.strip()
        ):
            _raise_host_error("service_id must be a non-empty string")
        normalized_id = service_id.strip() if service_id is not None else None
        async with self._operation_lock:
            configured_ids: list[str] = []
            if self._stored_config is not None:
                services = cast(
                    list[dict[str, object]],
                    self._stored_config["fetch_services"],
                )
                configured_ids = [cast(str, item["service_id"]) for item in services]
            if normalized_id is not None and normalized_id not in configured_ids:
                _raise_host_error("unknown PCS fetch service")
            status = await self._pcs.snapshot()
            states = getattr(status, "fetch_service_states", {})
            errors = getattr(status, "fetch_service_errors", {})

            def project(item_id: str) -> dict[str, object]:
                return {
                    "service_id": item_id,
                    "state": states.get(item_id, "STOPPED"),
                    "last_error": errors.get(item_id),
                }

            if normalized_id is not None:
                return project(normalized_id)
            return {"services": [project(item_id) for item_id in configured_ids]}

    async def set_fetching(
        self,
        *,
        enabled: bool,
        service_id: str | None = None,
    ) -> None:
        """Persist and hot-apply the global or one-service fetch switch."""

        if type(enabled) is not bool:
            _raise_host_error("enabled must be a boolean")
        if service_id is not None and not isinstance(service_id, str):
            _raise_host_error("service_id must be a string")
        async with self._operation_lock:
            if self._stored_config is None:
                _raise_host_error(
                    "PCS configuration must be set before changing fetching"
                )
            raw = deepcopy(self._stored_config)
            if service_id is None:
                raw["fetching_enabled"] = enabled
            else:
                normalized_id = service_id.strip()
                if not normalized_id:
                    _raise_host_error("service_id must not be empty")
                services = cast(list[dict[str, object]], raw["fetch_services"])
                target = next(
                    (item for item in services if item["service_id"] == normalized_id),
                    None,
                )
                if target is None:
                    _raise_host_error("unknown PCS fetch service")
                target["enabled"] = enabled
            raw, candidate = _prepare_stored_config(raw)
            await self._apply_configuration_locked(
                candidate,
                raw,
                _serialize_config(raw),
            )

    async def run_fetch(
        self,
        *,
        service_id: str | None = None,
    ) -> dict[str, object]:
        """Delegate one immediate fetch request without changing configuration."""

        async with self._operation_lock:
            return await self._pcs.run_fetch(service_id=service_id)

    async def get_graph(self) -> dict[str, object]:
        """Read the last published Context structure without starting Core."""

        return await self._pcs.get_graph()

    async def search_graph(self, query: str) -> dict[str, object]:
        """Search the last published Context pages without starting Core."""

        return await self._pcs.search_graph(query)

    async def get_graph_page(self, node_id: str) -> dict[str, object]:
        """Read one published Context page without starting Core."""

        return await self._pcs.get_graph_page(node_id)

    async def authorize_provider(self, provider: str) -> dict[str, object]:
        """Check or begin user authorization for a configured provider."""

        async with self._operation_lock:
            if self._config is None:
                _raise_host_error(
                    "PCS configuration must be set before provider authorization"
                )
            try:
                return await self._pcs.authorize_provider(provider)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                raise _as_host_error(exc, "PCS provider authorization failed") from None

    async def start(self) -> None:
        """Load the file once when needed and start the configured Core."""

        async with self._operation_lock:
            if self._config is None:
                raw = _read_yaml(self._config_path)
                if raw is None:
                    return
                stored, config = _prepare_stored_config(raw)
                try:
                    await self._pcs.set_configuration(config)
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    raise _as_host_error(
                        exc,
                        "PCS configuration could not be applied",
                        status_name="CONTEXT_PROACTIVE_CONFIG_INVALID",
                    ) from None
                self._config = config
                self._stored_config = stored
            config = self._config
            if config is None or not config.enabled:
                return
            try:
                await self._pcs.activate_runtime()
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise _as_host_error(exc, "PCS runtime could not be started") from None

    async def get_status(self) -> PCS.Status:
        """Return the Core's bounded, credential-free status snapshot."""

        return await self._pcs.snapshot()

    async def stop(self, *, timeout_seconds: float = _STOP_TIMEOUT_SECONDS) -> None:
        """Stop Core runtime while preserving configuration and published files."""

        if timeout_seconds <= 0:
            _raise_host_error(
                "timeout_seconds must be greater than zero",
                status_name="CONTEXT_PROACTIVE_RUNTIME_TIMEOUT",
            )
        async with self._operation_lock:
            try:
                await self._pcs.deactivate_runtime(timeout_seconds=timeout_seconds)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                raise _as_host_error(exc, "PCS runtime could not be stopped") from None

    async def _restore_previous(
        self,
        previous: PCS.Config | None,
        previous_stored: dict[str, object] | None,
        was_active: bool,
    ) -> None:
        """Best-effort restore after a failed candidate configuration operation."""

        with contextlib.suppress(Exception):
            await self._pcs.deactivate_runtime(timeout_seconds=_STOP_TIMEOUT_SECONDS)
        if previous is None:
            self._pcs = PCS(home=self._home)
            self._config = None
            self._stored_config = None
            return
        try:
            await self._pcs.set_configuration(previous)
            if was_active and previous.enabled:
                await self._pcs.activate_runtime()
            self._config = previous
            self._stored_config = deepcopy(previous_stored)
        except Exception:
            # The candidate error is the useful public failure.  A later
            # explicit stop/start can recover a Core left in FAILED state.
            return
