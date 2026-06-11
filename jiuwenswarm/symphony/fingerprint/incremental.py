"""Incremental fingerprint reuse support."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from jiuwenswarm.symphony.fingerprint.models import SkillFolder, SkillFingerprint
from jiuwenswarm.symphony.fingerprint.normalize import (
    IONameVocabulary,
    SkillFingerprintNormalizer,
)
from jiuwenswarm.symphony.score_state import load_score_state

IndexedSkillFolder = tuple[int, SkillFolder]


@dataclass(frozen=True)
class FingerprintReusePlan:
    changed_folders: list[IndexedSkillFolder]
    fingerprints: list[SkillFingerprint]
    fingerprints_by_path: dict[str, SkillFingerprint]
    removed_paths: set[str]
    reused_count: int


class IncrementalFingerprintStore:
    """Load prior Score artifacts and decide what must be re-extracted."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.old_state = load_score_state(output_dir)
        self.old_fingerprints = load_fingerprints(output_dir)
        self.old_fingerprints_by_id = {item.id: item for item in self.old_fingerprints}

    def plan(
        self,
        folders: list[SkillFolder],
        current_hashes: dict[str, str],
        *,
        force: bool,
        on_reuse: Callable[[int, SkillFolder, SkillFingerprint], None] | None = None,
    ) -> FingerprintReusePlan:
        old_active_entries = self.old_state.active_entries()
        removed_paths = {
            relative_path
            for relative_path in old_active_entries
            if relative_path not in current_hashes
        }
        changed_folders: list[IndexedSkillFolder] = []
        fingerprints: list[SkillFingerprint] = []
        fingerprints_by_path: dict[str, SkillFingerprint] = {}
        reused_count = 0

        for folder_index, folder in enumerate(folders):
            old_entry = old_active_entries.get(folder.relative_path)
            if (
                not force
                and old_entry is not None
                and old_entry.skill_md_sha256 == current_hashes[folder.relative_path]
            ):
                old_fingerprint = self.old_fingerprints_by_id.get(old_entry.skill_id)
                if old_fingerprint is not None:
                    fingerprints.append(old_fingerprint)
                    fingerprints_by_path[folder.relative_path] = old_fingerprint
                    reused_count += 1
                    if on_reuse is not None:
                        on_reuse(folder_index, folder, old_fingerprint)
                    continue
            changed_folders.append((folder_index, folder))

        return FingerprintReusePlan(
            changed_folders=changed_folders,
            fingerprints=fingerprints,
            fingerprints_by_path=fingerprints_by_path,
            removed_paths=removed_paths,
            reused_count=reused_count,
        )


def load_fingerprints(output_dir: Path) -> list[SkillFingerprint]:
    path = output_dir / "fingerprints.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        SkillFingerprint.from_dict(item)
        for item in payload.get("fingerprints", [])
        if isinstance(item, dict)
    ]


def load_io_name_vocabulary(
    output_dir: Path,
    normalizer: SkillFingerprintNormalizer,
) -> IONameVocabulary:
    path = output_dir / "io_name_vocab.json"
    if not path.exists():
        return IONameVocabulary.from_config(normalizer.config)
    return IONameVocabulary.load(path, normalizer.config)
