"""Create immutable, task-private inputs for a Harness Validation run."""

from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from jiuwenswarm.agents.harness.common.rsi.errors import (
    RsiDatasetInvalid,
    RsiInvalidHarness,
    RsiPathInvalid,
    RsiPathNotAllowed,
)

VALIDATION_PROFILE_NAME = "validation-general-v1"


@dataclass(frozen=True, slots=True)
class RsiTaskMaterialization:
    """Paths and non-secret hashes created for one task."""

    dataset: dict[str, Any]
    harness: dict[str, Any]
    models: dict[str, dict[str, Any]]
    profile: dict[str, Any]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "input_snapshot": {
                "source_path": self.dataset.get("source_path"),
                "path": self.dataset.get("path"),
                "sha256": self.dataset.get("sha256"),
            },
            "harness_snapshot": {
                "source_path": self.harness.get("source_path"),
                "refs_path": self.harness.get("path"),
                "sha256": self.harness.get("sha256"),
                "source_sha256": self.harness.get("source_sha256"),
            },
            "models": deepcopy(self.models),
            "profile": {
                "name": VALIDATION_PROFILE_NAME,
                "path": self.profile.get("path"),
                "sha256": self.profile.get("sha256"),
            },
        }


class RsiTaskMaterializer:
    """Materialize dataset, Harness refs, and the hidden Validation profile.

    ``dataset_root`` and ``harness_root`` are optional because existing local
    callers may already have performed trust checks.  Production composition
    roots should provide them (or configure ``RSI_DATASET_ROOT`` /
    ``RSI_HARNESS_ROOT``) to reject browser-supplied paths outside approved
    trees.
    """

    def __init__(
        self,
        tasks_root: str | Path,
        *,
        dataset_root: str | Path | None = None,
        harness_root: str | Path | None = None,
        dataset_validator: Callable[[str], Any] | None = None,
    ) -> None:
        self.tasks_root = Path(tasks_root).expanduser().resolve()
        self.dataset_root = _optional_root(dataset_root)
        self.harness_root = _optional_root(harness_root)
        self.dataset_validator = dataset_validator

    def task_dir(self, task_id: str) -> Path:
        task = str(task_id or "").strip()
        if not task or Path(task).name != task or task in {".", ".."}:
            raise RsiPathInvalid(f"task_id 非法: {task_id}")
        path = (self.tasks_root / task).resolve()
        _ensure_within(path, self.tasks_root, label="任务目录")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def materialize_dataset(
        self,
        task_id: str,
        source_path: str | Path,
        *,
        validator: Callable[[str], Any] | None = None,
    ) -> dict[str, Any]:
        source = self._source_file(source_path, self.dataset_root, label="数据集")
        check = validator or self.dataset_validator
        if check is not None:
            try:
                result = check(str(source))
            except (RsiDatasetInvalid, RsiPathInvalid, RsiPathNotAllowed):
                raise
            except Exception as exc:  # noqa: BLE001
                raise RsiDatasetInvalid(f"数据集校验失败: {exc}") from exc
            if not _validation_ok(result):
                raise RsiDatasetInvalid(
                    "Provider 输入校验失败",
                    errors=_validation_errors(result),
                )
        else:
            _validate_json_dataset(source)

        target_dir = self.task_dir(task_id) / "input"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / _safe_filename(source.name, default="validation.json")
        shutil.copy2(source, target)
        return {
            "source_path": str(source),
            "path": str(target.resolve()),
            "sha256": _sha256(target),
        }

    def materialize_harness_refs(
        self,
        task_id: str,
        source_path: str | Path,
        *,
        role: str = "validation_harness",
    ) -> dict[str, Any]:
        source = self._source_path(source_path, self.harness_root, label="Harness")
        config_path = _harness_config_path(source)
        try:
            source_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise RsiInvalidHarness(f"Harness 配置不可读: {config_path}") from exc
        if not isinstance(source_data, dict) or not source_data:
            raise RsiInvalidHarness(f"Harness 配置必须是非空 mapping: {config_path}")
        try:
            # Reuse the AutoHarness import-time allow-list/path guards for the
            # source package.  This prevents a trusted-ref wrapper from
            # bypassing the existing ``mcps`` and path traversal checks.
            from jiuwenswarm.agents.harness.common.auto_harness.service import (
                validate_harness_config,
            )

            validate_harness_config(config_path, package_dir=source if source.is_dir() else source.parent)
        except RsiInvalidHarness:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize package guards
            raise RsiInvalidHarness(f"Harness 配置安全校验失败: {config_path}") from exc
        role_name = str(role or "").strip()
        if not role_name or Path(role_name).name != role_name:
            raise RsiInvalidHarness(f"Harness role 非法: {role}")

        target_dir = self.task_dir(task_id) / "harness"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "harness_refs.yaml"
        payload = {"harness_refs": {role_name: str(source.resolve())}}
        target.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return {
            "source_path": str(source),
            "path": str(target.resolve()),
            "sha256": _sha256(target),
            "source_sha256": _path_sha256(source),
            "source_config_path": str(config_path),
            "role": role_name,
        }

    def materialize_validation_profile(
        self,
        task_id: str,
        model_paths: dict[str, str],
        *,
        profile_name: str = VALIDATION_PROFILE_NAME,
    ) -> dict[str, Any]:
        required_roles = {"evaluation", "analysis", "member_optimization"}
        missing = sorted(required_roles - set(model_paths))
        if missing:
            raise RsiPathInvalid(f"Validation profile 缺少模型文件: {', '.join(missing)}")
        normalized_paths: dict[str, str] = {}
        task_dir = self.task_dir(task_id)
        models_dir = (task_dir / "models").resolve()
        for role in required_roles:
            path = Path(str(model_paths.get(role) or "")).expanduser().resolve()
            _ensure_within(path, models_dir, label=f"模型文件({role})")
            if not path.is_file():
                raise RsiPathInvalid(f"模型文件不存在: {path}")
            normalized_paths[role] = str(path)

        run_dir = (task_dir / "run").resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        target_dir = task_dir / "config"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "harness_orchestrator.yaml"
        payload = {
            "workspace_dir": str(run_dir),
            "max_epochs": 1,
            "model_configs": normalized_paths,
            "data_loader": {
                "file_pattern": "*.json",
                "batch_size": 8,
                "batch_balance_keys": ["dimension", "difficulty", "source", "task_type"],
            },
            "evaluator": {
                "backend": "single_harness",
                "evaluation_method": "script-based",
                "transient_case_retry_limit": 2,
            },
            "evaluation_result_analyzer": {
                "diagnosis_agent_max_retries": 2,
                "diagnosis_agent_max_concurrency": 5,
                "diagnosis_agent_max_iterations": 20,
                "max_issues": 8,
                "evidence_limit_per_issue": 3,
                "output_filename": "issues.yaml",
            },
            "member_optimizer": {
                "action_group_configs": ["prompt", "skill", "tool", "rail"],
                "allowed_action_groups": ["prompt", "skill", "tool", "rail"],
                "allowed_prompt_surfaces": ["prompt_section"],
                "max_roles_per_run": 1,
                "execution_concurrency": 1,
                "role_execution_concurrency": 1,
                "action_execution_concurrency_per_role": 1,
                "sibling_candidate_count": 2,
                "max_issue_attempts_per_batch": 8,
                "max_repair_rounds_per_batch": 1,
                "candidate_holdout_cases": 0,
            },
            "scheduling": {
                "evaluation_strategy": "hybrid",
                "coordination_strategy": "team_first_single_pass",
                "promotion_policy": "epoch_full_evaluation",
                "full_evaluation_enabled": True,
            },
        }
        target.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        # Validate against the installed openjiuwen dataclasses immediately;
        # malformed internal fields must fail during create, not worker startup.
        try:
            from openjiuwen.rsi.harness_rsi.config import load_auto_coordinating_harness_config

            load_auto_coordinating_harness_config(str(target))
        except Exception as exc:  # noqa: BLE001
            raise RsiPathInvalid(f"Validation profile 无效: {exc}") from exc
        return {
            "name": str(profile_name or VALIDATION_PROFILE_NAME),
            "path": str(target.resolve()),
            "sha256": _sha256(target),
        }

    def materialize(
        self,
        task_id: str,
        dataset_path: str | Path,
        harness_path: str | Path,
        *,
        model_refs: dict[str, str],
        model_resolver: Any,
        validator: Callable[[str], Any] | None = None,
    ) -> RsiTaskMaterialization:
        """Materialize all task inputs in a deterministic order."""

        dataset = self.materialize_dataset(task_id, dataset_path, validator=validator)
        harness = self.materialize_harness_refs(task_id, harness_path)
        models_dir = self.task_dir(task_id) / "models"
        optimizer_ref = str(model_refs.get("optimizer") or "").strip()
        tester_ref = str(model_refs.get("tester") or "").strip()
        models = {
            "evaluation": model_resolver.resolve_to_file(tester_ref, "evaluation", models_dir),
            "analysis": model_resolver.resolve_to_file(tester_ref, "analysis", models_dir),
            "member_optimization": model_resolver.resolve_to_file(
                optimizer_ref, "member_optimization", models_dir
            ),
        }
        profile = self.materialize_validation_profile(
            task_id,
            {role: str(item["path"]) for role, item in models.items()},
        )
        return RsiTaskMaterialization(dataset, harness, models, profile)

    def _source_path(
        self,
        raw_path: str | Path,
        allowed_root: Path | None,
        *,
        label: str,
    ) -> Path:
        raw = str(raw_path or "").strip()
        if not raw:
            raise RsiPathInvalid(f"{label}路径不能为空")
        path = Path(raw).expanduser().resolve()
        if allowed_root is not None:
            _ensure_within(path, allowed_root, label=label)
        if not path.exists():
            raise RsiPathInvalid(f"{label}路径不存在: {path}")
        return path

    def _source_file(
        self,
        raw_path: str | Path,
        allowed_root: Path | None,
        *,
        label: str,
    ) -> Path:
        path = self._source_path(raw_path, allowed_root, label=label)
        if not path.is_file():
            raise RsiPathInvalid(f"{label}必须是文件: {path}")
        return path


def _optional_root(value: str | Path | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    return Path(value).expanduser().resolve()


def _ensure_within(path: Path, root: Path, *, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise RsiPathNotAllowed(f"{label}路径超出允许根目录: {path}") from exc


def _safe_filename(name: str, *, default: str) -> str:
    candidate = Path(str(name or "")).name.strip()
    if not candidate or candidate in {".", ".."}:
        return default
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _harness_config_path(source: Path) -> Path:
    """Resolve the legacy Harness YAML used to validate a package directory."""

    if source.is_file():
        return source
    for name in ("harness_config.yaml", "expert_harness.yaml", "harness.yaml"):
        candidate = source / name
        if candidate.is_file():
            return candidate
    raise RsiInvalidHarness(f"Harness 包缺少配置文件: {source}")


def _path_sha256(path: Path) -> str:
    """Hash a file or a Harness package directory deterministically."""

    if path.is_file():
        return _sha256(path)
    digest = hashlib.sha256()
    for item in sorted((candidate for candidate in path.rglob("*") if candidate.is_file()), key=lambda p: p.relative_to(path).as_posix()):
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256(item)))
    return digest.hexdigest()


def _validate_json_dataset(path: Path) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RsiDatasetInvalid(f"数据集 JSON 解析失败: {exc}") from exc
    cases = raw.get("cases") if isinstance(raw, dict) else raw
    if not isinstance(cases, list) or not cases:
        raise RsiDatasetInvalid("数据集必须包含非空 cases 数组")
    seen: set[str] = set()
    for item in cases:
        if not isinstance(item, dict):
            raise RsiDatasetInvalid("数据集 case 必须是 mapping")
        case_id = str(item.get("case_id") or "").strip()
        if not case_id:
            raise RsiDatasetInvalid("数据集 case_id 不能为空")
        if case_id in seen:
            raise RsiDatasetInvalid(f"数据集 case_id 重复: {case_id}")
        seen.add(case_id)


def _validation_ok(result: Any) -> bool:
    return bool(result.get("valid")) if isinstance(result, dict) else bool(getattr(result, "valid", False))


def _validation_errors(result: Any) -> list[dict[str, str]]:
    raw = result.get("errors") if isinstance(result, dict) else getattr(result, "errors", None)
    errors: list[dict[str, str]] = []
    for item in raw or []:
        if isinstance(item, dict):
            errors.append({
                "code": str(item.get("code") or "DATASET_INVALID"),
                "reason": str(item.get("reason") or item.get("message") or "数据集校验失败"),
            })
        else:
            errors.append({"code": "DATASET_INVALID", "reason": str(item)})
    return errors


__all__ = [
    "RsiTaskMaterialization",
    "RsiTaskMaterializer",
    "VALIDATION_PROFILE_NAME",
]
