# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persona YAML template loader — 从 resources/personas/ 加载身份模板."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from jiuwenavatar.server.runtime.persona.models import PersonaConfig

logger = logging.getLogger(__name__)

_BUILTIN_PERSONAS_DIR_NAME = "personas"


def _find_package_root() -> Path:
    """Locate the package root directory (containing resources/)."""
    # Start from this file's location and walk up
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "resources").is_dir():
            return parent
    raise FileNotFoundError("Cannot locate package root (resources/ directory)")


def get_builtin_personas_dir() -> Path:
    """Get the built-in personas directory from package resources."""
    package_root = _find_package_root()
    return package_root / "resources" / _BUILTIN_PERSONAS_DIR_NAME


def get_user_personas_dir() -> Path:
    """Get the user's custom personas directory."""
    from jiuwenavatar.common.utils import get_user_workspace_dir

    return get_user_workspace_dir() / "personas"


def load_persona_yaml(path: Path) -> PersonaConfig:
    """Load a single persona YAML file and return a PersonaConfig.

    The YAML file should have the structure:
    ```yaml
    persona:
      id: committer
      display_name: "Committer 分身"
      ...
    ```
    """
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)

    if not isinstance(data, dict) or "persona" not in data:
        raise ValueError(f"Invalid persona YAML: {path} — missing top-level 'persona' key")

    persona_data: dict[str, Any] = data["persona"]

    # Flatten nested structures for Pydantic
    flat: dict[str, Any] = {}

    # Simple fields
    for key in (
        "id", "display_name", "description", "icon", "version",
        "coding_capable", "coding_engines", "default_coding_engine",
        "skills", "system_prompt", "tags", "builtin",
    ):
        if key in persona_data:
            flat[key] = persona_data[key]

    # Trigger templates
    if "trigger_templates" in persona_data:
        flat["trigger_templates"] = persona_data["trigger_templates"]

    # Report template
    if "report_template" in persona_data:
        flat["report_template"] = persona_data["report_template"]

    return PersonaConfig(**flat)


def scan_personas_from_dir(directory: Path, *, builtin: bool = True) -> list[PersonaConfig]:
    """Scan a directory for persona YAML files and return loaded configs.

    Each subdirectory or .yaml file in the directory is treated as a persona.
    """
    personas: list[PersonaConfig] = []

    if not directory.is_dir():
        return personas

    for item in sorted(directory.iterdir()):
        if item.is_dir():
            # Subdirectory mode: look for persona.yaml inside
            yaml_path = item / "persona.yaml"
            if not yaml_path.exists():
                # Also try <dir_name>.yaml
                yaml_path = item / f"{item.name}.yaml"
            if not yaml_path.exists():
                continue
        elif item.is_file() and item.suffix in (".yaml", ".yml"):
            yaml_path = item
        else:
            continue

        try:
            config = load_persona_yaml(yaml_path)
            config.builtin = builtin
            personas.append(config)
            logger.debug("Loaded persona: %s from %s", config.id, yaml_path)
        except Exception:
            logger.warning("Failed to load persona from %s", yaml_path, exc_info=True)

    return personas


def load_all_personas() -> list[PersonaConfig]:
    """Load all personas from both builtin and user directories."""
    personas: list[PersonaConfig] = []

    # Built-in personas
    builtin_dir = get_builtin_personas_dir()
    if builtin_dir.is_dir():
        personas.extend(scan_personas_from_dir(builtin_dir, builtin=True))

    # User custom personas
    user_dir = get_user_personas_dir()
    if user_dir.is_dir():
        personas.extend(scan_personas_from_dir(user_dir, builtin=False))

    # Deduplicate by id (user overrides builtin with same id)
    seen: dict[str, PersonaConfig] = {}
    for p in personas:
        seen[p.id] = p  # Last one wins (user > builtin)

    return list(seen.values())
