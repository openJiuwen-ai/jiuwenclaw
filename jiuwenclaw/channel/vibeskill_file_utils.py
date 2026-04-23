from __future__ import annotations

from typing import Any


def _vibeskill_filenode_base_name(relative: str) -> str:
    relative = relative.rstrip("/")
    if not relative:
        return ""
    return relative.split("/")[-1]


def _vibeskill_filenode_absolute(task_id: str, relative: str) -> str:
    rel = str(relative or "").lstrip("/")
    root = f"/vibeskill/{task_id}/skill"
    if not rel:
        return root
    return f"{root}/{rel}"


def skilldev_tree_to_opencode_file_nodes(
    tree: list[Any],
    *,
    task_id: str,
) -> list[dict[str, Any]]:
    """SkillDev nested file tree to OpenCode FileNode list (pre-order)."""

    out: list[dict[str, Any]] = []

    def walk(level: list[Any]) -> None:
        for n in level:
            if not isinstance(n, dict):
                continue
            kind = str(n.get("type") or "")
            raw_path = str(n.get("path") or "")

            if kind == "dir":
                rel = raw_path.rstrip("/")
                name = _vibeskill_filenode_base_name(rel) or rel
                out.append(
                    {
                        "name": name,
                        "path": rel,
                        "absolute": _vibeskill_filenode_absolute(task_id, rel),
                        "type": "directory",
                        "ignored": False,
                    }
                )
                ch = n.get("children")
                if isinstance(ch, list) and ch:
                    walk(ch)
            elif kind == "file":
                rel = raw_path
                name = _vibeskill_filenode_base_name(rel) or rel
                out.append(
                    {
                        "name": name,
                        "path": rel,
                        "absolute": _vibeskill_filenode_absolute(task_id, rel),
                        "type": "file",
                        "ignored": False,
                    }
                )

    if isinstance(tree, list):
        walk(tree)
    return out
