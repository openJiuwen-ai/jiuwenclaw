from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Union

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger


DirectoryNode = Dict[str, Any]


@dataclass
class Workspace:
    """Workspace schema definition for DeepAgents.

    - root_path: workspace root directory (string or Path).
    - directories: list of directory node definitions at the workspace root.

    Directories behavior:
    - Required top-level directories are defined by DEFAULT_WORKSPACE_SCHEMA
      (currently: agent, user, skills, sessions).
    - If user-provided `directories` misses any of them, they will be
      auto-supplemented in __post_init__ and logged.
    - get_directory(name) falls back to DEFAULT_WORKSPACE_SCHEMA when the
      directory is not explicitly provided.
    """

    root_path: str | Path = "./"
    # Default value is wired through __post_init__ to avoid referencing
    # DEFAULT_WORKSPACE_SCHEMA before it is defined.
    directories: List[DirectoryNode] = field(default_factory=list)

    def get_directory(self, name: str) -> str | None:
        """Return the `path` field of the directory node with the given name.

        Top-level entries in `directories` are inspected first. If not found
        but the name exists in DEFAULT_WORKSPACE_SCHEMA, returns that default path.
        Otherwise returns None.
        """
        for node in self.directories:
            if node.get("name") == name:
                return node.get("path")
        for node in DEFAULT_WORKSPACE_SCHEMA:
            if node.get("name") == name:
                return node.get("path")
        return None

    def set_directory(
        self, nodes: Union[DirectoryNode, List[DirectoryNode]]
    ) -> None:
        """Add or update top-level directory node(s) by name.

        Accepts a single directory node (dict) or a list of nodes. Each node
        is validated; if a node with the same `name` exists it is replaced,
        otherwise the node is appended.
        """
        if isinstance(nodes, dict):
            nodes = [nodes]
        elif isinstance(nodes, list):
            nodes = nodes
        else:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="set_directory expects a directory node (dict) or list of nodes.",
            )
        for node in nodes:
            _validate_directory_node(node)
            name = node.get("name")
            for i, existing in enumerate(self.directories):
                if existing.get("name") == name:
                    self.directories[i] = dict(node)
                    break
            else:
                self.directories.append(dict(node))

    @classmethod
    def get_default_directory(cls) -> List[DirectoryNode]:
        """Return a deep copy of the default directory schema (DEFAULT_WORKSPACE_SCHEMA)."""
        return deepcopy(DEFAULT_WORKSPACE_SCHEMA)

    def __post_init__(self) -> None:
        # Fill in default schema when no directories are explicitly provided.
        if not self.directories:
            # Use a deepcopy so that callers can mutate their instance safely.
            self.directories = deepcopy(DEFAULT_WORKSPACE_SCHEMA)

        if not isinstance(self.directories, list):
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="`directories` must be a list of directory definitions.",
            )
        for node in self.directories:
            _validate_directory_node(node)

        # Supplement any default directories missing from user-provided list.
        existing_names = {node.get("name") for node in self.directories}
        for default_node in DEFAULT_WORKSPACE_SCHEMA:
            name = default_node.get("name")
            if name and name not in existing_names:
                self.directories.append(deepcopy(default_node))
                existing_names.add(name)
                logger.info(
                    "Workspace: supplemented missing default directory %r (path=%r).",
                    name,
                    default_node.get("path"),
                )


DEFAULT_WORKSPACE_SCHEMA: List[DirectoryNode] = [
    {
        "name": "agent",
        "description": "Agent configuration, persona and behavior docs.",
        "path": "agent",
        "children": [],
    },
    {
        "name": "user",
        "description": "User profile and preference docs.",
        "path": "user",
        "children": [],
    },
    {
        "name": "skills",
        "description": "Agent skills definitions and metadata.",
        "path": "skills",
        "children": [],
    },
    {
        "name": "sessions",
        "description": "Per-session messages and context window snapshots (e.g. messages.json / window.json).",
        "path": "sessions",
        "children": [],
    },
]


def _validate_directory_node(node: DirectoryNode) -> None:
    """Validate a single directory node (dict). Raises on invalid format or fields."""
    if not isinstance(node, dict):
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="Each directory entry must be a dict.",
        )

    name = node.get("name")
    if not isinstance(name, str) or not name:
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="Directory `name` must be a non-empty string.",
        )
    if "/" in name or "\\" in name:
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg=f"Directory `name` must not contain path separators: {name!r}",
        )

    path = node.get("path")
    if path is not None and not isinstance(path, str):
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="Directory `path` must be a string when provided.",
        )

    description = node.get("description")
    if description is not None and not isinstance(description, str):
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="Directory `description` must be a string when provided.",
        )

    children = node.get("children")
    if children is not None and not isinstance(children, list):
        raise build_error(
            StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
            error_msg="Directory `children` must be a list when provided.",
        )

    if isinstance(children, list):
        for child in children:
            _validate_directory_node(child)


__all__ = [
    "Workspace",
]

