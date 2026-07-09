#!/usr/bin/env python3
# coding: utf-8
"""
本地仓库管理工具。

处理 GitCode 仓库的本地 clone、更新和代码查看。
- 检查本地是否存在仓库 clone
- 自动 clone 仓库（如果不存在）
- 更新本地仓库到最新代码
- 获取代码文件内容

用法:
    # 检查并确保本地仓库存在
    python repo_manager.py --ensure-clone --config gitcode-repo.json

    # 获取文件内容
    python repo_manager.py --get-file openjiuwen/core/config.py --config gitcode-repo.json

    # 更新本地仓库
    python repo_manager.py --update --config gitcode-repo.json
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_GIT_TIMEOUT_SEC = 60
_GIT_HEAVY_TIMEOUT_SEC = 300

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from gitcode_client import GitCodeClient, _redact_secrets
from config_loader import (
    ConfigError,
    find_config_path,
    load_resolved_config,
)


def _is_git_work_tree(path: str) -> bool:
    """path 是否为 Git 工作区根（含普通仓与 worktree）。"""
    git_meta = os.path.join(path, ".git")
    return os.path.isdir(git_meta) or os.path.isfile(git_meta)


def _resolve_path_within_repo(
    repo_path: str,
    relative: str,
) -> Optional[Path]:
    """解析相对路径并确保不越出仓库根目录。"""
    root = Path(repo_path).resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def get_default_clone_dir() -> str:
    """获取默认的仓库 clone 目录。

    Returns:
        默认 clone 目录路径（用户 home 目录下的 .jiuwenclaw/repos）。
    """
    home = Path.home()
    return str(home / ".jiuwenclaw" / "repos")


def get_repo_path(owner: str, repo: str, clone_dir: str = "") -> str:
    """获取仓库在本地的路径。

    Args:
        owner: 仓库所有者。
        repo: 仓库名称。
        clone_dir: 自定义 clone 目录，为空则使用默认目录。
            若非空且该路径已存在且为 Git 工作区根，则直接作为仓库路径
            （不再拼接 ``{owner}_{repo}``），避免在已有克隆旁重复 clone。

    Returns:
        仓库本地路径。
    """
    if not clone_dir:
        clone_dir = get_default_clone_dir()
    else:
        clone_dir = os.path.abspath(os.path.expanduser(clone_dir))
        if os.path.isdir(clone_dir) and _is_git_work_tree(clone_dir):
            return clone_dir
    return os.path.join(clone_dir, f"{owner}_{repo}")


def is_repo_cloned(owner: str, repo: str, clone_dir: str = "") -> bool:
    """检查仓库是否已在本地 clone。

    Args:
        owner: 仓库所有者。
        repo: 仓库名称。
        clone_dir: 自定义 clone 目录。

    Returns:
        如果仓库已存在且是有效的 git 仓库则返回 True。
    """
    repo_path = get_repo_path(owner, repo, clone_dir)
    return os.path.isdir(repo_path) and _is_git_work_tree(repo_path)


def clone_repo(
    owner: str,
    repo: str,
    clone_dir: str = "",
    token: str = "",
) -> Dict[str, Any]:
    """Clone 仓库到本地。

    Args:
        owner: 仓库所有者。
        repo: 仓库名称。
        clone_dir: 自定义 clone 目录，为空则使用默认目录。
        token: GitCode Token（用于私有仓库）。

    Returns:
        操作结果字典。
    """
    if not clone_dir:
        clone_dir = get_default_clone_dir()

    os.makedirs(clone_dir, exist_ok=True)
    repo_path = get_repo_path(owner, repo, clone_dir)

    if is_repo_cloned(owner, repo, clone_dir):
        return {
            "success": True,
            "message": f"仓库已存在: {repo_path}",
            "path": repo_path,
            "already_exists": True,
        }

    clean_url = f"https://gitcode.com/{owner}/{repo}.git"
    if token:
        clone_url = f"https://oauth2:{token}@gitcode.com/{owner}/{repo}.git"
    else:
        clone_url = clean_url

    try:
        result = subprocess.run(
            ["git", "clone", clone_url, repo_path],
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_HEAVY_TIMEOUT_SEC,
        )
        if token:
            scrub = subprocess.run(
                [
                    "git", "-C", repo_path,
                    "remote", "set-url", "origin", clean_url,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=_GIT_TIMEOUT_SEC,
            )
            if scrub.returncode != 0:
                safe_scrub_err = _redact_secrets(
                    scrub.stderr or "", token
                )
                return {
                    "success": False,
                    "message": (
                        "Clone 成功但未能从 remote 移除 token，"
                        f"请手动执行: git -C {repo_path!r} "
                        f"remote set-url origin {clean_url!r}"
                    ),
                    "path": repo_path,
                    "error": safe_scrub_err,
                }
        return {
            "success": True,
            "message": f"成功 clone 仓库到: {repo_path}",
            "path": repo_path,
            "stdout": result.stdout,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": f"Clone 超时（>{_GIT_HEAVY_TIMEOUT_SEC}s）",
            "path": repo_path,
        }
    except subprocess.CalledProcessError as e:
        safe_stderr = (
            _redact_secrets(e.stderr or "", token)
            if token
            else (e.stderr or "")
        )
        return {
            "success": False,
            "message": f"Clone 失败: {safe_stderr}",
            "path": repo_path,
            "error": safe_stderr,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "message": "未找到 git 命令，请确保 git 已安装",
            "path": repo_path,
        }


def update_repo(
    owner: str,
    repo: str,
    clone_dir: str = "",
    remote: str = "origin",
    branch: str = "main",
) -> Dict[str, Any]:
    """更新本地仓库到最新代码。

    Args:
        owner: 仓库所有者。
        repo: 仓库名称。
        clone_dir: 自定义 clone 目录。
        remote: 远程名称。
        branch: 分支名称。

    Returns:
        操作结果字典。
    """
    repo_path = get_repo_path(owner, repo, clone_dir)

    if not is_repo_cloned(owner, repo, clone_dir):
        return {
            "success": False,
            "message": f"仓库不存在: {repo_path}",
            "path": repo_path,
        }

    try:
        # 获取当前分支
        result = subprocess.run(
            ["git", "-C", repo_path, "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_TIMEOUT_SEC,
        )
        current_branch = result.stdout.strip()

        # 拉取最新代码
        result = subprocess.run(
            ["git", "-C", repo_path, "pull", remote, current_branch],
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_HEAVY_TIMEOUT_SEC,
        )
        return {
            "success": True,
            "message": f"成功更新仓库: {result.stdout}",
            "path": repo_path,
            "stdout": result.stdout,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "message": f"更新超时（>{_GIT_HEAVY_TIMEOUT_SEC}s）",
            "path": repo_path,
        }
    except subprocess.CalledProcessError as e:
        return {
            "success": False,
            "message": f"更新失败: {e.stderr}",
            "path": repo_path,
            "error": e.stderr,
        }


def get_file_content(
    owner: str,
    repo: str,
    file_path: str,
    clone_dir: str = "",
) -> Dict[str, Any]:
    """获取仓库中文件的内容。

    Args:
        owner: 仓库所有者。
        repo: 仓库名称。
        file_path: 文件在仓库中的相对路径。
        clone_dir: 自定义 clone 目录。

    Returns:
        包含文件内容的结果字典。
    """
    repo_path = get_repo_path(owner, repo, clone_dir)

    if not is_repo_cloned(owner, repo, clone_dir):
        return {
            "success": False,
            "message": f"仓库不存在，请先 clone: {repo_path}",
            "path": repo_path,
        }

    resolved = _resolve_path_within_repo(repo_path, file_path)
    if resolved is None:
        return {
            "success": False,
            "message": f"路径越界: {file_path}",
            "repo_path": repo_path,
            "file_path": file_path,
        }

    full_path = str(resolved)

    if not os.path.exists(full_path):
        return {
            "success": False,
            "message": f"文件不存在: {file_path}",
            "repo_path": repo_path,
            "file_path": file_path,
        }

    if not os.path.isfile(full_path):
        return {
            "success": False,
            "message": f"路径不是文件: {file_path}",
            "repo_path": repo_path,
            "file_path": file_path,
        }

    try:
        with open(full_path, encoding="utf-8") as f:
            content = f.read()
        return {
            "success": True,
            "content": content,
            "repo_path": repo_path,
            "file_path": file_path,
            "full_path": full_path,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"读取文件失败: {e}",
            "repo_path": repo_path,
            "file_path": file_path,
        }


def list_files(
    owner: str,
    repo: str,
    directory: str = "",
    clone_dir: str = "",
) -> Dict[str, Any]:
    """列出仓库中的文件。

    Args:
        owner: 仓库所有者。
        repo: 仓库名称。
        directory: 子目录路径，为空则列出根目录。
        clone_dir: 自定义 clone 目录。

    Returns:
        包含文件列表的结果字典。
    """
    repo_path = get_repo_path(owner, repo, clone_dir)

    if not is_repo_cloned(owner, repo, clone_dir):
        return {
            "success": False,
            "message": f"仓库不存在，请先 clone: {repo_path}",
            "path": repo_path,
        }

    if directory:
        resolved = _resolve_path_within_repo(repo_path, directory)
        if resolved is None:
            return {
                "success": False,
                "message": f"目录路径越界: {directory}",
                "repo_path": repo_path,
                "directory": directory,
            }
        target_dir = str(resolved)
    else:
        target_dir = repo_path

    if not os.path.exists(target_dir):
        return {
            "success": False,
            "message": f"目录不存在: {directory}",
            "repo_path": repo_path,
            "directory": directory,
        }

    try:
        files = []
        dirs = []
        for item in os.listdir(target_dir):
            item_path = os.path.join(target_dir, item)
            if os.path.isdir(item_path):
                if not item.startswith("."):
                    dirs.append(item)
            else:
                files.append(item)

        return {
            "success": True,
            "files": sorted(files),
            "directories": sorted(dirs),
            "repo_path": repo_path,
            "directory": directory or "/",
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"列出文件失败: {e}",
            "repo_path": repo_path,
            "directory": directory,
        }


def ensure_clone_from_config(
    config_path: str = "",
    workspace_name: str = "",
) -> Dict[str, Any]:
    """根据配置文件确保仓库已 clone。

    Args:
        config_path: 配置文件路径。
        workspace_name: 工作区名称（多工作区配置时使用）。

    Returns:
        操作结果字典。
    """
    try:
        config = load_resolved_config(config_path, workspace_name or None)
    except ConfigError as exc:
        return {"success": False, "message": str(exc)}

    upstream = config.get("upstream", {})
    owner = upstream.get("owner", config.get("owner", ""))
    repo = upstream.get("repo", config.get("repo", ""))

    if not owner or not repo:
        return {
            "success": False,
            "message": "配置文件中缺少 upstream.owner 或 upstream.repo",
        }

    # 获取 clone 目录配置
    local_repo = config.get("local_repo", {})
    clone_dir = local_repo.get("path", "")

    # 获取 token
    token = os.environ.get("GITCODE_TOKEN", "")
    if not token:
        token = config.get("gitcode_token", "")

    if is_repo_cloned(owner, repo, clone_dir):
        # 仓库已存在，尝试更新
        return update_repo(owner, repo, clone_dir)
    else:
        # 仓库不存在，执行 clone
        return clone_repo(owner, repo, clone_dir, token)


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="GitCode 本地仓库管理工具",
    )
    parser.add_argument(
        "--ensure-clone",
        action="store_true",
        help="确保仓库已 clone（如果不存在则 clone，存在则更新）",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="更新本地仓库",
    )
    parser.add_argument(
        "--get-file",
        default="",
        help="获取指定文件的内容",
    )
    parser.add_argument(
        "--list-files",
        default=None,
        nargs="?",
        const=".",
        help="列出文件（可指定子目录）",
    )
    parser.add_argument(
        "--config",
        default="",
        help="配置文件路径",
    )
    parser.add_argument(
        "--workspace",
        default="",
        help="工作区名称（workspaces[].name；多条时必填）",
    )
    parser.add_argument(
        "--clone-dir",
        default="",
        help="自定义 clone 目录",
    )
    return parser


def main() -> None:
    """CLI 入口。"""
    parser = _build_parser()
    args = parser.parse_args()

    config_path = find_config_path(args.config)

    try:
        config = load_resolved_config(
            config_path,
            args.workspace or None,
        )
    except ConfigError as exc:
        print(
            json.dumps({"error": str(exc)}, ensure_ascii=False)
        )
        sys.exit(1)

    upstream = config.get("upstream", {})
    owner = upstream.get("owner", config.get("owner", ""))
    repo = upstream.get("repo", config.get("repo", ""))

    if not owner or not repo:
        print(
            json.dumps(
                {
                    "error": "配置文件中缺少 upstream.owner 或 upstream.repo"
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)

    # 获取 clone 目录
    local_repo = config.get("local_repo", {})
    clone_dir = args.clone_dir or local_repo.get("path", "")

    # 获取 token
    token = os.environ.get("GITCODE_TOKEN", "")
    if not token:
        token = config.get("gitcode_token", "")

    result = None

    if args.ensure_clone:
        if is_repo_cloned(owner, repo, clone_dir):
            result = update_repo(owner, repo, clone_dir)
        else:
            result = clone_repo(owner, repo, clone_dir, token)
    elif args.update:
        result = update_repo(owner, repo, clone_dir)
    elif args.get_file:
        result = get_file_content(owner, repo, args.get_file, clone_dir)
    elif args.list_files is not None:
        result = list_files(owner, repo, args.list_files, clone_dir)
    else:
        # 默认行为：检查仓库状态
        repo_path = get_repo_path(owner, repo, clone_dir)
        if is_repo_cloned(owner, repo, clone_dir):
            result = {
                "success": True,
                "message": f"仓库已存在: {repo_path}",
                "path": repo_path,
                "exists": True,
            }
        else:
            result = {
                "success": True,
                "message": f"仓库不存在: {repo_path}",
                "path": repo_path,
                "exists": False,
            }

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result.get("success", False):
        sys.exit(1)


if __name__ == "__main__":
    main()
