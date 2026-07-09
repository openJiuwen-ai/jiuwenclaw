#!/usr/bin/env python3
"""bench-runner 共享上下文：路径推断、gitcode 配置、占位符展开。"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_GIT_TIMEOUT_SEC = 60

SCRIPT_DIR = Path(__file__).resolve().parent
BENCH_RUNNER_ROOT = SCRIPT_DIR.parent
SKILLS_ROOT = BENCH_RUNNER_ROOT.parent

CONFIG_NAMES = ("gitcode-repo.json", "issue-resolver.json")
INSTALLED_SKILL_ROOTS = (
    Path.home() / ".agents" / "skills",
    Path.home() / ".claude" / "skills",
    Path.home() / ".cursor" / "skills",
)
ANALYSIS_TYPES = frozenset({"Bug", "Feature", "Refactor", "Docs"})


class BenchContextError(ValueError):
    pass


@dataclass
class RepoRootResult:
    path: str
    source: str  # "path" | "name"
    workspace_name: str
    git_valid: bool
    remotes: Dict[str, str]
    current_branch: str
    notes: List[str] = field(default_factory=list)


@dataclass
class BenchContext:
    repo_root: RepoRootResult
    skills_root: str
    bench_runner_root: str
    gitcode_config: str
    placeholders: Dict[str, str]


def _load_gitcode_config_loader():
    candidates = [
        SKILLS_ROOT / "gitcode-repo" / "scripts",
        *(root / "gitcode-repo" / "scripts" for root in INSTALLED_SKILL_ROOTS),
    ]
    for scripts_dir in candidates:
        module_path = scripts_dir / "config_loader.py"
        if not module_path.is_file():
            continue
        spec = importlib.util.spec_from_file_location(
            "gitcode_config_loader", module_path
        )
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


def find_gitcode_config(explicit: str = "") -> Path:
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_file():
            raise BenchContextError(f"gitcode_config 不存在：{candidate}")
        return candidate.resolve()

    loader = _load_gitcode_config_loader()
    if loader is not None:
        found = loader.find_config_path("")
        if found:
            return Path(found).resolve()

    search_dirs: List[Path] = [Path.cwd()]
    search_dirs.append(SKILLS_ROOT / "gitcode-repo")
    for root in INSTALLED_SKILL_ROOTS:
        search_dirs.append(root / "gitcode-repo")

    for directory in search_dirs:
        for name in CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate.resolve()

    raise BenchContextError(
        "未找到 gitcode-repo.json / issue-resolver.json；"
        "请在 inputs.repo_root.gitcode_config 指定路径，"
        "或在 skills/gitcode-repo/、当前目录、~/.agents|~/.claude/skills/gitcode-repo/ 放置配置"
    )


def _local_repo_path_from_config(raw: Dict[str, Any], workspace_name: str) -> tuple[str, str]:
    """返回 (path, resolved_workspace_name)。对齐 gitcode-repo config_loader。"""
    loader = _load_gitcode_config_loader()
    if loader is not None:
        try:
            effective = loader.resolve_workspace_config(
                raw, workspace_name or None
            )
        except loader.ConfigError as exc:
            raise BenchContextError(str(exc)) from exc
        name = str(effective.get("_workspace_name") or workspace_name or "")
        local_repo = effective.get("local_repo") or {}
        path = (local_repo.get("path") or "").strip()
        if not path:
            raise BenchContextError(
                f"工作区 {name!r} 未配置 local_repo.path"
            )
        return path, name

    workspaces = raw.get("workspaces") or []
    if workspaces:
        if workspace_name:
            matched = [w for w in workspaces if w.get("name") == workspace_name]
            if not matched:
                names = [str(w.get("name") or f"<unnamed-{i}>") for i, w in enumerate(workspaces)]
                raise BenchContextError(
                    f"未找到工作区 {workspace_name!r}，可用: {names}"
                )
            ws = matched[0]
        elif len(workspaces) == 1:
            ws = workspaces[0]
        else:
            names = [str(w.get("name") or f"<unnamed-{i}>") for i, w in enumerate(workspaces)]
            raise BenchContextError(
                f"配置含 {len(workspaces)} 个工作区，请填写 repo_root.name，可用: {names}"
            )
        local_repo = ws.get("local_repo") or {}
        path = (local_repo.get("path") or "").strip()
        name = str(ws.get("name") or workspace_name or "")
        if not path:
            raise BenchContextError(f"工作区 {name!r} 未配置 local_repo.path")
        return path, name

    upstream = raw.get("upstream") or {}
    if upstream.get("owner") and upstream.get("repo"):
        local_repo = raw.get("local_repo") or {}
        path = (local_repo.get("path") or "").strip()
        if not path:
            raise BenchContextError("扁平配置缺少 local_repo.path")
        return path, workspace_name

    raise BenchContextError(
        "配置无效：请填写 workspaces[] 或顶层 upstream.owner/upstream.repo"
    )


def lookup_path_by_workspace_name(name: str, gitcode_config: Path) -> tuple[str, str]:
    with gitcode_config.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise BenchContextError(f"{gitcode_config} 根节点必须是 JSON 对象")
    return _local_repo_path_from_config(raw, name)


def _run_git(cwd: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        raise BenchContextError(
            f"git {' '.join(args)} timed out after {_GIT_TIMEOUT_SEC}s（{cwd}）"
        ) from None
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise BenchContextError(f"git {' '.join(args)} 失败（{cwd}）：{stderr}")
    return (result.stdout or "").strip()


def validate_git_repo(path: Path) -> tuple[Dict[str, str], str]:
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise BenchContextError(f"repo_root.path 不是有效目录：{root}")

    inside = _run_git(str(root), "rev-parse", "--is-inside-work-tree")
    if inside != "true":
        raise BenchContextError(f"路径不是 Git 工作区：{root}")

    remotes: Dict[str, str] = {}
    for line in _run_git(str(root), "remote", "-v").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] not in remotes:
            remotes[parts[0]] = parts[1]

    branch = _run_git(str(root), "branch", "--show-current")
    return remotes, branch


def resolve_skills_root(explicit: str = "") -> Path:
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not root.is_dir():
            raise BenchContextError(f"paths.skills_root 不是有效目录：{root}")
        return root

    if SKILLS_ROOT.is_dir() and (SKILLS_ROOT / "gitcode-repo").is_dir():
        return SKILLS_ROOT.resolve()

    for parent in INSTALLED_SKILL_ROOTS:
        if parent.is_dir():
            return parent.resolve()

    return SKILLS_ROOT.resolve()


def resolve_bench_runner_root(explicit: str = "") -> Path:
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if not root.is_dir():
            raise BenchContextError(f"paths.bench_runner_root 不是有效目录：{root}")
        return root

    for parent in INSTALLED_SKILL_ROOTS:
        for dirname in ("bench-runner", "run-bench"):
            candidate = parent / dirname
            if candidate.is_dir():
                return candidate.resolve()

    return BENCH_RUNNER_ROOT.resolve()


def resolve_repo_root(spec: Dict[str, Any]) -> RepoRootResult:
    name = (spec.get("name") or "").strip()
    path_text = (spec.get("path") or "").strip()
    gitcode_config_text = (spec.get("gitcode_config") or "").strip()
    notes: List[str] = []

    if not name and not path_text:
        raise BenchContextError("repo_root.name 与 repo_root.path 至少填其一")

    workspace_name = name
    source = "path"
    resolved: Optional[Path] = None

    if path_text:
        resolved = Path(path_text).expanduser().resolve()

    if name:
        gitcode_config = find_gitcode_config(gitcode_config_text)
        name_path, workspace_name = lookup_path_by_workspace_name(name, gitcode_config)
        name_resolved = Path(name_path).expanduser().resolve()
        if resolved is None:
            resolved = name_resolved
            source = "name"
        else:
            if name_resolved != resolved:
                notes.append(
                    f"同时填写 name 与 path，已采用 path={resolved}；"
                    f"name={name!r} 解析为 {name_resolved}"
                )
            source = "path"

    assert resolved is not None
    remotes, branch = validate_git_repo(resolved)
    return RepoRootResult(
        path=str(resolved),
        source=source,
        workspace_name=workspace_name,
        git_valid=True,
        remotes=remotes,
        current_branch=branch,
        notes=notes,
    )


def load_bench_file(bench_path: Path) -> Dict[str, Any]:
    with bench_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise BenchContextError(f"{bench_path} 根节点必须是 JSON 对象")
    return data


def load_bench_inputs(bench_path: Path) -> Dict[str, Any]:
    data = load_bench_file(bench_path)
    inputs = data.get("inputs")
    if not isinstance(inputs, dict):
        raise BenchContextError(f"{bench_path} 缺少 inputs 对象")
    return inputs


def analysis_type_from_inputs(inputs: Dict[str, Any], side: str = "baseline") -> str:
    shared = inputs.get("shared_context") or {}
    value = (shared.get("analysis_type") or "").strip()
    if not value:
        party = inputs.get(side) or {}
        value = (party.get("analysis_type") or "").strip()
    if value and value not in ANALYSIS_TYPES:
        raise BenchContextError(
            f"analysis_type 必须是 {sorted(ANALYSIS_TYPES)} 之一，当前: {value!r}"
        )
    return value


def expand_placeholders(template: str, values: Dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return result


def build_placeholder_map(
    *,
    repo_root: str,
    skills_root: str,
    module: str,
    analysis_type: str,
) -> Dict[str, str]:
    skills = skills_root.rstrip("/\\")
    return {
        "repo_root": repo_root,
        "skills_root": skills,
        "module": module,
        "analysis_type": analysis_type,
        "type": analysis_type,
    }


def expand_gate_checks(
    gate_checks: Dict[str, str],
    placeholders: Dict[str, str],
) -> Dict[str, str]:
    return {
        key: expand_placeholders(cmd, placeholders)
        for key, cmd in gate_checks.items()
    }


def resolve_bench_context(
    bench_path: Path,
    *,
    side: str = "baseline",
) -> BenchContext:
    data = load_bench_file(bench_path)
    inputs = data.get("inputs")
    if not isinstance(inputs, dict):
        raise BenchContextError(f"{bench_path} 缺少 inputs 对象")

    repo_spec = inputs.get("repo_root")
    if not isinstance(repo_spec, dict):
        raise BenchContextError("inputs.repo_root 必须是对象")

    paths = inputs.get("paths") or {}
    if not isinstance(paths, dict):
        raise BenchContextError("inputs.paths 必须是对象")

    repo_result = resolve_repo_root(repo_spec)
    skills_root = resolve_skills_root((paths.get("skills_root") or "").strip())
    bench_runner_explicit = (
        (paths.get("bench_runner_root") or paths.get("run_bench_root") or "").strip()
    )
    bench_runner_root = resolve_bench_runner_root(bench_runner_explicit)

    gitcode_config = ""
    if (repo_spec.get("gitcode_config") or "").strip():
        gitcode_config = str(find_gitcode_config(repo_spec["gitcode_config"]))
    elif (repo_spec.get("name") or "").strip():
        gitcode_config = str(find_gitcode_config())

    party = inputs.get(side) or {}
    module = (party.get("module") or "").strip()
    analysis_type = analysis_type_from_inputs(inputs, side)

    placeholders = build_placeholder_map(
        repo_root=repo_result.path,
        skills_root=str(skills_root),
        module=module,
        analysis_type=analysis_type,
    )

    return BenchContext(
        repo_root=repo_result,
        skills_root=str(skills_root),
        bench_runner_root=str(bench_runner_root),
        gitcode_config=gitcode_config,
        placeholders=placeholders,
    )


def emit_error(exc: BenchContextError, file: Any = sys.stderr) -> None:
    print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=file)
