"""Compatibility loader for the existing A2A ingress environment variables."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .models import A2AIngressConfig, A2AIngressError

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

A2A_INGRESS_ENV_MAP = {
    "enabled": "A2A_SERVER_ENABLED", "host": "A2A_SERVER_HOST", "port": "A2A_SERVER_PORT",
    "rpc_path": "A2A_SERVER_PATH", "protocol_version": "A2A_SERVER_PROTOCOL_VERSION",
    "card_path": "A2A_SERVER_CARD_PATH", "extended_card_path": "A2A_SERVER_EXTENDED_CARD_PATH",
    "app_name": "A2A_SERVER_APP_NAME", "app_description": "A2A_SERVER_APP_DESCRIPTION",
    "app_version": "A2A_SERVER_APP_VERSION", "expose_reasoning": "A2A_SERVER_EXPOSE_REASONING",
}
_ENV_ASSIGNMENT_RE = re.compile(r"^(?P<indent>\s*)(?P<export>export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=")


def _value(env: Mapping[str, str], name: str, default: str) -> str:
    return str(env.get(name, default)).strip() or default


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = str(env.get(name, str(default))).strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def load_a2a_ingress_config(env: Mapping[str, str] | None = None) -> A2AIngressConfig:
    """Load ``A2A_SERVER_*`` without changing current deployment defaults."""
    source = os.environ if env is None else env
    port_text = _value(source, "A2A_SERVER_PORT", "19100")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise A2AIngressError("A2A_CONFIG_INVALID", "A2A_SERVER_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise A2AIngressError("A2A_CONFIG_INVALID", "A2A_SERVER_PORT must be between 1 and 65535")

    return A2AIngressConfig(
        enabled=_bool(source, "A2A_SERVER_ENABLED", False),
        host=_value(source, "A2A_SERVER_HOST", "127.0.0.1"),
        port=port,
        rpc_path=_value(source, "A2A_SERVER_PATH", "/a2a"),
        protocol_version=_value(source, "A2A_SERVER_PROTOCOL_VERSION", "1.0.0"),
        card_path=_value(source, "A2A_SERVER_CARD_PATH", "/.well-known/agent-card.json"),
        extended_card_path=_value(
            source, "A2A_SERVER_EXTENDED_CARD_PATH", "/agent/authenticatedExtendedCard"
        ),
        app_name=_value(source, "A2A_SERVER_APP_NAME", "JiuwenSwarm Gateway A2A Server"),
        app_description=_value(source, "A2A_SERVER_APP_DESCRIPTION", "A2A ingress for JiuwenSwarm Gateway"),
        app_version=_value(source, "A2A_SERVER_APP_VERSION", "0.1.0"),
        expose_reasoning=_bool(source, "A2A_SERVER_EXPOSE_REASONING", True),
    ).validate()


def load_a2a_ingress_config_safely(
    env: Mapping[str, str] | None = None,
) -> tuple[A2AIngressConfig, A2AIngressError | None]:
    """Return a disabled fallback so an invalid optional A2A config cannot stop Gateway boot."""
    try:
        return load_a2a_ingress_config(env), None
    except A2AIngressError as exc:
        return A2AIngressConfig(), exc


class A2AIngressConfigRepository:
    """Dedicated, allow-listed .env persistence for A2A ingress settings."""

    def __init__(self, env_path: Path | None = None) -> None:
        if env_path is None:
            from jiuwenswarm.common.utils import get_env_file

            env_path = get_env_file()
        self._env_path = Path(env_path)

    def save(self, config: A2AIngressConfig) -> None:
        updates = self._to_env_updates(config.validate())
        temp_path: Path | None = None
        try:
            lines = (
                self._env_path.read_text(encoding="utf-8").splitlines(keepends=True)
                if self._env_path.is_file()
                else []
            )
            pending = dict(updates)
            output: list[str] = []
            for line in lines:
                match = _ENV_ASSIGNMENT_RE.match(line)
                key = match.group("key") if match else ""
                if key not in pending:
                    output.append(line)
                    continue
                output.append(
                    f'{match.group("indent")}{match.group("export") or ""}{key}='
                    f'"{self._quote_dotenv_value(pending.pop(key))}"\n'
                )
            output.extend(
                f'{key}="{self._quote_dotenv_value(value)}"\n' for key, value in pending.items()
            )
            self._env_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self._env_path.parent,
                prefix=f".{self._env_path.name}.", suffix=".tmp", delete=False,
            ) as temp_file:
                temp_file.write("".join(output))
                temp_path = Path(temp_file.name)
            os.replace(temp_path, self._env_path)
        except OSError as exc:
            raise A2AIngressError("A2A_CONFIG_INVALID", f"Failed to persist A2A ingress config: {exc}") from exc
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)
        os.environ.update(updates)

    @staticmethod
    def _quote_dotenv_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")

    @staticmethod
    def _to_env_updates(config: A2AIngressConfig) -> dict[str, str]:
        values = {
            "enabled": "true" if config.enabled else "false", "host": config.host,
            "port": str(config.port), "rpc_path": config.rpc_path,
            "protocol_version": config.protocol_version, "card_path": config.card_path,
            "extended_card_path": config.extended_card_path, "app_name": config.app_name,
            "app_description": config.app_description, "app_version": config.app_version,
            "expose_reasoning": "true" if config.expose_reasoning else "false",
        }
        return {A2A_INGRESS_ENV_MAP[name]: value for name, value in values.items()}
