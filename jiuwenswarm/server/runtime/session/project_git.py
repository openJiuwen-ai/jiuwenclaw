"""ProjectGitService: 项目目录的 Git 仓库探测与分支操作服务(设计文档 §3.4 / §6)。

提供 ``ensure_on_project_create`` / ``probe`` / ``status`` / ``init`` /
``switch_branch`` / ``create_branch`` 等接口。

安全边界:禁止 ``shell=True``;分支名用 ``git check-ref-format --branch`` 校验;
路径必须来自已登记 project 的 ``project_dir``;写操作默认 10 秒超时
(``GIT_COMMAND_TIMEOUT_SEC`` / ``GIT_DIFF_TIMEOUT_SEC``),超时返回
``GIT_COMMAND_TIMEOUT``。merge/rebase/cherry-pick 中间状态下 ``status``/``probe``
返回 ``transient=true``,仅写操作返回 ``GIT_TRANSIENT_STATE``。
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jiuwenswarm.server.runtime.session.project_store import Project, save_project

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float, *, min_value: float = 0.1) -> float:
    """Read a float environment variable with a safe fallback.

    非法值回退到 default;有效但低于 ``min_value`` 的值也会被钳到下限,
    避免 0/负值导致 ``subprocess.run(timeout=0)`` 立即触发 TimeoutExpired。
    """
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "[ProjectGit] invalid %s=%r, falling back to %.1f",
            name, raw, default,
        )
        return default
    if value < min_value:
        logger.warning(
            "[ProjectGit] %s=%.3f below min %.1f, clamped",
            name, value, min_value,
        )
        return min_value
    return value


# 写操作默认 10 秒超时,避免阻塞 watcher 主循环;可通过环境变量覆盖
GIT_COMMAND_TIMEOUT_SEC: float = _env_float("JIUWEN_GIT_COMMAND_TIMEOUT_SEC", 10.0)
GIT_DIFF_TIMEOUT_SEC: float = _env_float("JIUWEN_GIT_DIFF_TIMEOUT_SEC", 10.0)

# 输出截断上限(设计文档 §3.4 GitError: stdout/stderr ≤ 4000 字符)
_GIT_OUTPUT_TRUNCATE = 4000


@dataclass(slots=True)
class GitError:
    """Git 操作失败时的结构化错误对象。"""

    code: str
    message: str
    command: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    hint: str = ""
    retryable: bool = False
    repo: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout[:_GIT_OUTPUT_TRUNCATE],
            "stderr": self.stderr[:_GIT_OUTPUT_TRUNCATE],
            "hint": self.hint,
            "retryable": self.retryable,
            "repo": self.repo,
        }


@dataclass(slots=True)
class GitRepoStatus:
    """某一时刻项目目录的 Git 仓库完整状态。"""

    is_git: bool = False
    repo_root: str | None = None
    branch: str | None = None
    head: str | None = None
    detached: bool = False
    transient: bool = False
    upstream: str | None = None
    is_dirty: bool = False
    staged: int = 0
    unstaged: int = 0
    untracked: int = 0
    conflicted: int = 0
    local_branches: list[str] = field(default_factory=list)
    remote_branches: list[str] = field(default_factory=list)
    error: GitError | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_git": self.is_git,
            "repo_root": self.repo_root,
            "branch": self.branch,
            "head": self.head,
            "detached": self.detached,
            "transient": self.transient,
            "upstream": self.upstream,
            "is_dirty": self.is_dirty,
            "staged": self.staged,
            "unstaged": self.unstaged,
            "untracked": self.untracked,
            "conflicted": self.conflicted,
            "local_branches": list(self.local_branches),
            "remote_branches": list(self.remote_branches),
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass(slots=True)
class GitProbeResult:
    """``ensure_on_project_create()`` 返回值。"""

    status: str  # ready | not_git | git_missing | transient | error | disabled
    repo_root: str | None = None
    branch: str | None = None
    initialized_by_jiuwenswarm: bool = False
    error: GitError | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "repo_root": self.repo_root,
            "branch": self.branch,
            "initialized_by_jiuwenswarm": self.initialized_by_jiuwenswarm,
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass(slots=True)
class GitOperationResult:
    """``switch_branch()`` / ``create_branch()`` 等写操作的返回值。"""

    success: bool
    repo_status: GitRepoStatus
    previous_branch: str | None = None
    error: GitError | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "repo_status": self.repo_status.to_dict(),
            "previous_branch": self.previous_branch,
            "error": self.error.to_dict() if self.error else None,
        }


class GitOperationError(RuntimeError):
    """Git 操作失败,携带结构化 ``GitError`` 供 handler 层映射错误码。"""

    def __init__(self, git_error: GitError) -> None:
        self.git_error = git_error
        super().__init__(git_error.message)


def resolve_git_project(
    project_id: str, *, cache_bust: bool = False,
) -> tuple[Any, str | None, str | None]:
    """校验并加载可用于 Git 操作的 code 项目(共享 helper)。

    被 ``app_web_handlers.py`` 的 Git RPC handler 和 ``git_ws_handler.py`` 的
    /ws/git handler 共用,确保校验逻辑一致。

    Args:
        project_id: 项目 ID;空/默认项目/不存在/隐藏/work 模式 → 拒绝。
        cache_bust: ``False`` 用于只读操作(避免绕过缓存);``True`` 用于写操作。

    Returns:
        ``(project, error_message, error_code)``: 成功时后两项为 ``None``;
        失败时 project 为 ``None``,调用方应直接 ``send_response`` 返回错误。
    """
    from jiuwenswarm.common.work_mode import is_default_project_id
    if not project_id:
        return None, "project_id is required", "BAD_REQUEST"
    if is_default_project_id(project_id):
        # 默认项目(default / default_code)禁止 Git 操作
        return None, "git operations not available for this project", "FORBIDDEN"
    from jiuwenswarm.server.runtime.session import project_store
    proj = project_store.get_project_by_id(project_id, cache_bust=cache_bust)
    if proj is None or proj.hidden:
        return None, "project not found", "NOT_FOUND"
    if proj.work_mode != "code":
        # work 模式项目不开放 Git 接口
        return None, "git operations not available for this project", "FORBIDDEN"
    return proj, None, None


def send_git_error_response(
    channel: Any, ws: Any, req_id: str, error: Any,
) -> Any:
    """发送 Git 结构化错误响应(共享 helper)。

    ``error`` 可以是:
      - ``GitOperationError`` 异常(通过 ``.git_error`` 属性提取 ``GitError``)
      - ``GitError`` 对象直接传入(如 ``repo_status.error``)
      - 其他异常(返回 ``INTERNAL_ERROR``)

    ``GitError`` 映射为 ``payload.detail``;非 Git 异常返回 ``INTERNAL_ERROR``。

    Returns:
        coroutine: 调用方需 ``await``。
    """
    if isinstance(error, GitError):
        git_error = error
    else:
        git_error = getattr(error, "git_error", None)
    if git_error is not None:
        detail = git_error.to_dict() if hasattr(git_error, "to_dict") else dict(git_error)
        return channel.send_response(
            ws, req_id, ok=False,
            payload={"detail": detail},
            error=git_error.message,
            code=git_error.code,
        )
    logger.warning("[GitHandler] error: %s", error)
    return channel.send_response(
        ws, req_id, ok=False,
        error=f"handler error: {error}", code="INTERNAL_ERROR",
    )


def _find_git_executable() -> str | None:
    """查找 git 可执行文件,找不到返回 ``None``。"""
    import shutil

    return shutil.which("git")


def _is_transient_state(project_dir: str) -> tuple[bool, str]:
    """检测 merge/rebase/cherry-pick 中间状态。

    Returns:
        ``(is_transient, kind)``: ``kind`` 为 "merge" / "rebase" / "cherry-pick" 等
    """
    dot_git = Path(project_dir) / ".git"
    if not dot_git.exists():
        return False, ""
    git_dir = dot_git if dot_git.is_dir() else None
    if git_dir is None:
        try:
            content = dot_git.read_text(encoding="utf-8").strip()
            if content.startswith("gitdir:"):
                git_dir = Path(project_dir) / content.split("gitdir:", 1)[1].strip()
                git_dir = git_dir.resolve()
        except Exception:  # noqa: BLE001
            return False, ""
    if git_dir is None or not git_dir.exists():
        return False, ""
    for kind in ("merge", "rebase-merge", "rebase-apply", "cherry-pick", "revert"):
        if (git_dir / kind).exists():
            return True, kind
    return False, ""


def _run_git(
    args: list[str],
    *,
    cwd: str,
    timeout: float = GIT_COMMAND_TIMEOUT_SEC,
) -> subprocess.CompletedProcess[str]:
    """执行 git 命令,禁止 ``shell=True``。

    使用 ``_find_git_executable()`` 返回的完整路径调用 git,避免仅依赖
    ``PATH`` 中的 ``"git"`` 字符串。

    Raises:
        FileNotFoundError: git 可执行文件不存在
        subprocess.TimeoutExpired: 命令超时
    """
    git_exe = _find_git_executable()
    if git_exe is None:
        raise FileNotFoundError("git executable not found")
    cmd_str = "git " + " ".join(args)
    logger.debug("[ProjectGit] run: %s (cwd=%s)", cmd_str, cwd)
    return subprocess.run(
        [git_exe, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        # 显式指定 UTF-8 解码:git 输出通常为 UTF-8(含中文文件名/分支名/commit message),
        # 默认 locale.getpreferredencoding() 在 Windows 上是 cp1252/cp936,会触发
        # UnicodeDecodeError 导致整个 _git_to_repo_status 失败。errors="replace"
        # 保证极端情况下不抛解码异常(牺牲少量字符精度换可用性)。
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        check=False,  # 不抛 CalledProcessError,由调用方判断 returncode
    )


def _truncate(s: str) -> str:
    return (s or "")[:_GIT_OUTPUT_TRUNCATE]


def _make_error(
    code: str,
    message: str,
    *,
    command: str = "",
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    hint: str = "",
    retryable: bool = False,
    project: Project | None = None,
) -> GitError:
    repo_ctx: dict[str, Any] | None = None
    if project is not None:
        repo_ctx = {
            "project_id": project.project_id,
            "repo_root": project.project_dir,
            "branch": None,
            "transient": False,
        }
    return GitError(
        code=code,
        message=message,
        command=command,
        exit_code=exit_code,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        hint=hint,
        retryable=retryable,
        repo=repo_ctx,
    )


def _make_repo_error(
    code: str,
    message: str,
    project: Project,
    *,
    command: str = "",
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    hint: str = "",
    retryable: bool = False,
    branch: str | None = None,
    transient: bool = False,
) -> GitError:
    """构造带完整 repo 上下文的 GitError。"""
    return GitError(
        code=code,
        message=message,
        command=command,
        exit_code=exit_code,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        hint=hint,
        retryable=retryable,
        repo={
            "project_id": project.project_id,
            "repo_root": project.project_dir,
            "branch": branch,
            "transient": transient,
        },
    )


def _file_not_found_error(
    project: Project,
    project_dir: str,
    *,
    branch: str | None = None,
    command: str = "",
) -> GitError:
    """区分 FileNotFoundError 来源:cwd 不存在 → PROJECT_DIR_MISSING,git 可执行文件缺失 → GIT_NOT_FOUND。

    ``subprocess.run(cwd=missing_dir)`` 也会抛 ``FileNotFoundError``,与 git 可执行文件
    缺失的异常同型。此处通过二次检查目录是否存在来消歧(TOCTOU 窗口收窄)。
    """
    if not project_dir or not Path(project_dir).exists():
        return _make_repo_error(
            "PROJECT_DIR_MISSING",
            "project directory does not exist",
            project,
            command=command,
            branch=branch,
            hint="请检查项目目录是否存在或路径是否正确",
            retryable=False,
        )
    return _make_repo_error(
        "GIT_NOT_FOUND",
        "git executable not found",
        project,
        command=command,
        branch=branch,
        hint="请安装 Git 后调用 project.git.probe 重新探测",
        retryable=True,
    )


def _git_to_repo_status(
    project: Project,
    *,
    persist: bool = False,
) -> GitRepoStatus:
    """读取项目目录的 Git 状态,返回 ``GitRepoStatus``。

    Args:
        project: 项目实体
        persist: 是否在探测后写回 ``Project.git`` 快照(含错误状态;仅 ``probe``/``init``/写操作使用)
    """

    def _err_status(err: GitError) -> GitRepoStatus:
        """构造错误状态;persist=True 时同时写回 ``Project.git`` 快照(设计文档要求 probe() 持久化错误)。"""
        status = GitRepoStatus(is_git=False, error=err)
        if persist:
            _persist_git_snapshot(project, status)
        return status

    project_dir = project.project_dir
    if not project_dir or not Path(project_dir).exists():
        return _err_status(
            _make_repo_error(
                "PROJECT_DIR_MISSING",
                "project directory does not exist",
                project,
                hint="请检查项目目录是否存在或路径是否正确",
                retryable=False,
            )
        )
    git_exe = _find_git_executable()
    if git_exe is None:
        return _err_status(
            _make_repo_error(
                "GIT_NOT_FOUND",
                "git executable not found",
                project,
                hint="请安装 Git 后调用 project.git.probe 重新探测",
                retryable=True,
            )
        )
    try:
        cp = _run_git(["rev-parse", "--show-toplevel"], cwd=project_dir)
    except FileNotFoundError:
        return _err_status(_file_not_found_error(project, project_dir, command="git rev-parse --show-toplevel"))
    except subprocess.TimeoutExpired:
        return _err_status(
            _make_repo_error(
                "GIT_COMMAND_TIMEOUT",
                "git command timed out",
                project,
                command="git rev-parse --show-toplevel",
                hint="Git 响应过慢,请稍后重试或检查仓库大小",
                retryable=True,
            )
        )
    if cp.returncode != 0:
        return _err_status(
            _make_repo_error(
                "NOT_GIT_REPOSITORY",
                "not a git repository",
                project,
                command="git rev-parse --show-toplevel",
                exit_code=cp.returncode,
                stderr=cp.stderr,
                hint="调用 project.git.init 初始化仓库",
                retryable=False,
            )
        )
    repo_root = cp.stdout.strip()
    is_transient, _transient_kind = _is_transient_state(project_dir)
    branch: str | None = None
    head: str | None = None
    detached = False
    upstream: str | None = None
    try:
        cp_b = _run_git(["symbolic-ref", "--short", "HEAD"], cwd=project_dir)
        if cp_b.returncode == 0:
            branch = cp_b.stdout.strip()
        else:
            detached = True
            cp_d = _run_git(["rev-parse", "--short", "HEAD"], cwd=project_dir)
            if cp_d.returncode == 0:
                head = cp_d.stdout.strip()
                branch = head  # detached 时 branch 字段填 head 短哈希
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    if branch and not detached:
        try:
            cp_u = _run_git(
                ["rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"],
                cwd=project_dir,
            )
            if cp_u.returncode == 0:
                upstream = cp_u.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    if not head:
        try:
            cp_h = _run_git(["rev-parse", "--short", "HEAD"], cwd=project_dir)
            if cp_h.returncode == 0:
                head = cp_h.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    staged = unstaged = untracked = conflicted = 0
    is_dirty = False
    try:
        cp_s = _run_git(
            ["status", "--porcelain", "--no-renames"],
            cwd=project_dir,
            timeout=GIT_DIFF_TIMEOUT_SEC,
        )
        if cp_s.returncode == 0:
            for line in cp_s.stdout.splitlines():
                if not line:
                    continue
                xy = line[:2]
                if xy in ("DD", "AU", "UD", "UA", "DU", "AA", "UU"):
                    conflicted += 1
                elif xy[0] == "?":
                    untracked += 1
                else:
                    if xy[0] in ("A", "M", "D", "R", "C"):
                        staged += 1
                    if xy[1] in ("M", "D"):
                        unstaged += 1
            is_dirty = staged > 0 or unstaged > 0 or untracked > 0 or conflicted > 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    local_branches: list[str] = []
    remote_branches: list[str] = []
    try:
        cp_lb = _run_git(
            ["for-each-ref", "--format=%(refname:short)", "refs/heads/"],
            cwd=project_dir,
        )
        if cp_lb.returncode == 0:
            local_branches = [b for b in cp_lb.stdout.splitlines() if b]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # 处理 unborn HEAD:刚 git init 的仓库 HEAD 指向 refs/heads/<branch> 但 ref 尚未创建,
    # 此时 symbolic-ref 能取到分支名但 for-each-ref 返回空,需补回未生成的分支
    if (
        branch
        and not detached
        and local_branches == []
    ):
        local_branches = [branch]
    try:
        cp_rb = _run_git(
            ["for-each-ref", "--format=%(refname:short)", "refs/remotes/"],
            cwd=project_dir,
        )
        if cp_rb.returncode == 0:
            remote_branches = [b for b in cp_rb.stdout.splitlines() if b and not b.endswith("/HEAD")]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    status = GitRepoStatus(
        is_git=True,
        repo_root=repo_root,
        branch=branch,
        head=head,
        detached=detached,
        transient=is_transient,
        upstream=upstream,
        is_dirty=is_dirty,
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        conflicted=conflicted,
        local_branches=local_branches,
        remote_branches=remote_branches,
        error=None,
    )
    if persist:
        _persist_git_snapshot(project, status)
    return status


def _persist_git_snapshot(project: Project, status: GitRepoStatus) -> None:
    """将 ``GitRepoStatus`` 写回 ``Project.git`` 子对象并持久化。"""
    if status.error is not None:
        git_snapshot: dict[str, Any] = {
            "enabled": False,
            "repo_root": status.repo_root or "",
            "initialized_by_jiuwenswarm": bool(
                project.git.get("initialized_by_jiuwenswarm", False)
            ),
            "detected_at": project.git.get("detected_at") or time.time(),
            "branch": status.branch or "",
            "status": _map_status_string(status),
            "error": status.error.message,
            "is_dirty": status.is_dirty,
        }
    else:
        git_snapshot = {
            "enabled": True,
            "repo_root": status.repo_root or "",
            "initialized_by_jiuwenswarm": bool(
                project.git.get("initialized_by_jiuwenswarm", False)
            ),
            "detected_at": project.git.get("detected_at") or time.time(),
            "branch": status.branch or "",
            "status": "ready" if not status.transient else "transient",
            "error": "",
            "is_dirty": status.is_dirty,
        }
    project.git = git_snapshot
    try:
        save_project(project)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[ProjectGit] failed to persist git snapshot for project=%s: %s",
            project.project_id, exc,
        )


def _persist_probe_result(project: Project, result: GitProbeResult) -> None:
    """将 ``GitProbeResult`` 写回 ``Project.git`` 子对象并持久化。

    用于 ``ensure_on_project_create`` 的所有探测路径(空目录 init 路径
    除外,该路径已通过 ``init() → _git_to_repo_status(persist=True)`` 持久化)。
    """
    git_snapshot: dict[str, Any] = {
        "enabled": result.status in ("ready", "transient"),
        "repo_root": result.repo_root or "",
        "initialized_by_jiuwenswarm": result.initialized_by_jiuwenswarm,
        "detected_at": project.git.get("detected_at") or time.time(),
        "branch": result.branch or "",
        "status": result.status,
        "error": result.error.message if result.error else "",
        # 保留 init() 已持久化的 is_dirty;非 init 路径(未探测 dirty)默认 False
        "is_dirty": project.git.get("is_dirty", False),
    }
    project.git = git_snapshot
    try:
        save_project(project)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[ProjectGit] failed to persist probe result for project=%s: %s",
            project.project_id, exc,
        )


def _map_status_string(status: GitRepoStatus) -> str:
    """从 GitRepoStatus 推断 Project.git.status 字符串。"""
    if status.error is None:
        return "ready" if not status.transient else "transient"
    code = status.error.code
    if code == "NOT_GIT_REPOSITORY":
        return "not_git"
    if code == "GIT_NOT_FOUND":
        return "git_missing"
    if code == "GIT_COMMAND_TIMEOUT":
        return "error"
    return "error"


def _validate_branch_name(branch: str, project: Project) -> str:
    """分支名校验,非法时抛 ``GitOperationError(BRANCH_INVALID)``。

    Returns:
        规范化后的分支名(``git check-ref-format --branch`` 的 stdout 输出,
        会去除 ``refs/heads/`` 前缀)。stdout 为空时回退到原始输入。
    """
    if not branch or not branch.strip():
        raise GitOperationError(
            _make_repo_error(
                "BRANCH_INVALID",
                "invalid branch name",
                project,
                hint="分支名不能为空",
                retryable=False,
            )
        )
    # 先检查目录是否存在,避免 subprocess.run(cwd=不存在) 的 FileNotFoundError
    # 被误判为 GIT_NOT_FOUND(实际应返回 PROJECT_DIR_MISSING)
    project_dir = project.project_dir
    if not project_dir or not Path(project_dir).exists():
        raise GitOperationError(
            _make_repo_error(
                "PROJECT_DIR_MISSING",
                "project directory does not exist",
                project,
                hint="请检查项目目录是否存在或路径是否正确",
                retryable=False,
            )
        )
    try:
        cp = _run_git(["check-ref-format", "--branch", branch], cwd=project_dir)
    except FileNotFoundError as exc:
        raise GitOperationError(
            _file_not_found_error(project, project_dir, command="git check-ref-format --branch")
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GitOperationError(
            _make_repo_error(
                "GIT_COMMAND_TIMEOUT",
                "git command timed out",
                project,
                command="git check-ref-format --branch",
                retryable=True,
            )
        ) from exc
    if cp.returncode != 0:
        raise GitOperationError(
            _make_repo_error(
                "BRANCH_INVALID",
                "invalid branch name",
                project,
                command="git check-ref-format --branch",
                exit_code=cp.returncode,
                stderr=cp.stderr,
                hint="请使用合法的 Git 分支名",
                retryable=False,
            )
        )
    # ``git check-ref-format --branch`` 的 stdout 是规范化后的分支名(去除 refs/heads/ 前缀),
    # 调用方应使用此值以保证与 git 内部解析一致
    normalized = cp.stdout.strip()
    return normalized or branch


class ProjectGitService:
    """项目 Git 服务(设计文档 §3.4 / §6)。

    所有方法均同步执行(无 await),可被 async handler 直接调用。
    写操作超时由 ``GIT_COMMAND_TIMEOUT_SEC`` / ``GIT_DIFF_TIMEOUT_SEC`` 控制,
    超时返回 ``GIT_COMMAND_TIMEOUT`` 不阻塞主循环。
    """

    def ensure_on_project_create(self, project: Project) -> GitProbeResult:
        """新建项目时探测/初始化 Git(设计文档 §6)。

        所有探测结果(除 ``disabled`` 外)均通过 ``_persist_probe_result``
        写回 ``Project.git`` 快照并持久化。调用方可直接重新读取 project
        获取最新 git 字段,无需额外转换。

        规则:
          - work_mode="work": 不执行 Git 探测,返回 ``status="disabled"``
          - work_mode="code":
            - git 可执行文件缺失 → ``status="git_missing"``
            - 目录不存在 → ``status="error"``(error.code=PROJECT_DIR_MISSING)
            - 已是 Git 仓库 → ``status="ready"``,不执行 ``git init``
            - 目录非空但不是 Git 仓库 → 不自动 init,返回 ``status="not_git"``
            - 目录为空(或仅含 .git) → 主动 ``git init``,返回 ``initialized_by_jiuwenswarm=True``
            - 中间状态 → ``status="transient"``

        Returns:
            GitProbeResult: 探测结果,供调用方决定 ``Project.git`` 初始值
        """
        result = self._probe_on_project_create(project)
        if result.status != "disabled":
            _persist_probe_result(project, result)
        return result

    def _probe_on_project_create(self, project: Project) -> GitProbeResult:
        """``ensure_on_project_create`` 的纯探测逻辑,不持久化。"""
        if project.work_mode != "code":
            return GitProbeResult(status="disabled")
        project_dir = project.project_dir
        if not project_dir or not Path(project_dir).exists():
            err = _make_repo_error(
                "PROJECT_DIR_MISSING",
                "project directory does not exist",
                project,
                hint="请检查项目目录",
                retryable=False,
            )
            return GitProbeResult(status="error", error=err)
        if _find_git_executable() is None:
            err = _make_repo_error(
                "GIT_NOT_FOUND",
                "git executable not found",
                project,
                hint="请安装 Git",
                retryable=True,
            )
            return GitProbeResult(status="git_missing", error=err)
        try:
            cp = _run_git(["rev-parse", "--show-toplevel"], cwd=project_dir)
        except FileNotFoundError:
            err = _file_not_found_error(project, project_dir, command="git rev-parse --show-toplevel")
            if err.code == "GIT_NOT_FOUND":
                return GitProbeResult(status="git_missing", error=err)
            return GitProbeResult(status="error", error=err)
        except subprocess.TimeoutExpired:
            err = _make_repo_error(
                "GIT_COMMAND_TIMEOUT",
                "git command timed out",
                project,
                command="git rev-parse --show-toplevel",
                retryable=True,
            )
            return GitProbeResult(status="error", error=err)
        if cp.returncode == 0:
            is_transient, _ = _is_transient_state(project_dir)
            if is_transient:
                return GitProbeResult(
                    status="transient",
                    repo_root=cp.stdout.strip(),
                )
            branch = self._read_branch(project_dir)
            return GitProbeResult(
                status="ready",
                repo_root=cp.stdout.strip(),
                branch=branch,
            )
        try:
            entries = list(Path(project_dir).iterdir())
        except OSError:
            entries = []
        non_git_entries = [e for e in entries if e.name != ".git"]
        if not non_git_entries:
            init_result = self.init(project, initial_branch="main")
            if init_result.error is None:
                return GitProbeResult(
                    status="ready",
                    repo_root=init_result.repo_root,
                    branch=init_result.branch,
                    initialized_by_jiuwenswarm=True,
                )
            return GitProbeResult(
                status="error",
                repo_root=init_result.repo_root,
                error=init_result.error,
            )
        return GitProbeResult(status="not_git")

    @staticmethod
    def probe(project: Project) -> GitRepoStatus:
        """重新探测项目 Git 状态并刷新 ``Project.git`` 快照,不执行 ``git init``。"""
        status = _git_to_repo_status(project, persist=True)
        return status

    @staticmethod
    def status(project: Project) -> GitRepoStatus:
        """查询项目 Git 状态(不持久化)。"""
        return _git_to_repo_status(project, persist=False)

    @staticmethod
    def init(
        project: Project,
        initial_branch: str = "main",
    ) -> GitRepoStatus:
        """初始化 Git 仓库,写回 ``Project.git`` 快照。"""
        project_dir = project.project_dir
        if not project_dir or not Path(project_dir).exists():
            err = _make_repo_error(
                "PROJECT_DIR_MISSING",
                "project directory does not exist",
                project,
                retryable=False,
            )
            return GitRepoStatus(is_git=False, error=err)
        if _find_git_executable() is None:
            err = _make_repo_error(
                "GIT_NOT_FOUND",
                "git executable not found",
                project,
                hint="请安装 Git 后重试",
                retryable=True,
            )
            return GitRepoStatus(is_git=False, error=err)
        try:
            initial_branch = _validate_branch_name(initial_branch, project)
        except GitOperationError as exc:
            return GitRepoStatus(is_git=False, error=exc.git_error)
        try:
            cp = _run_git(
                ["init", "-b", initial_branch, project_dir],
                cwd=project_dir,
            )
        except FileNotFoundError:
            err = _file_not_found_error(project, project_dir, command="git init -b")
            return GitRepoStatus(is_git=False, error=err)
        except subprocess.TimeoutExpired:
            err = _make_repo_error(
                "GIT_COMMAND_TIMEOUT",
                "git command timed out",
                project,
                command="git init",
                retryable=True,
            )
            return GitRepoStatus(is_git=False, error=err)
        if cp.returncode != 0:
            if "unknown switch" in cp.stderr or "invalid option" in cp.stderr:
                # git < 2.28 不支持 ``init -b``,回退到 ``init`` + ``symbolic-ref``。
                # 用 ``symbolic-ref HEAD refs/heads/<branch>`` 替代 ``checkout -b``:
                # 后者在无 commit 的全新仓库上会因找不到 HEAD 指向的 commit 而失败。
                try:
                    cp2 = _run_git(["init"], cwd=project_dir)
                    cp = cp2
                    if cp2.returncode == 0 and initial_branch:
                        cp_sr = _run_git(
                            ["symbolic-ref", "HEAD", f"refs/heads/{initial_branch}"],
                            cwd=project_dir,
                        )
                        if cp_sr.returncode != 0:
                            err = _make_repo_error(
                                "GIT_COMMAND_FAILED",
                                "git command failed",
                                project,
                                command=f"git symbolic-ref HEAD refs/heads/{initial_branch}",
                                exit_code=cp_sr.returncode,
                                stdout=cp_sr.stdout,
                                stderr=cp_sr.stderr,
                                hint="Git 初始化成功但设置初始分支失败",
                                retryable=True,
                            )
                            return GitRepoStatus(is_git=False, error=err)
                except FileNotFoundError:
                    err = _file_not_found_error(project, project_dir, command="git init")
                    return GitRepoStatus(is_git=False, error=err)
                except subprocess.TimeoutExpired:
                    err = _make_repo_error(
                        "GIT_COMMAND_TIMEOUT",
                        "git command timed out",
                        project,
                        command="git init",
                        retryable=True,
                    )
                    return GitRepoStatus(is_git=False, error=err)
        if cp.returncode != 0:
            err = _make_repo_error(
                "GIT_COMMAND_FAILED",
                "git command failed",
                project,
                command="git init",
                exit_code=cp.returncode,
                stderr=cp.stderr,
                hint="请检查目录权限或 Git 版本",
                retryable=True,
            )
            return GitRepoStatus(is_git=False, error=err)
        project.git["initialized_by_jiuwenswarm"] = True
        return _git_to_repo_status(project, persist=True)

    @staticmethod
    def switch_branch(
        project: Project,
        branch: str,
        *,
        require_clean: bool = False,
    ) -> GitOperationResult:
        """切换分支。

        Args:
            project: 项目实体
            branch: 目标分支名
            require_clean: True 时要求工作区干净,否则返回 ``WORKTREE_DIRTY``
        """
        try:
            branch = _validate_branch_name(branch, project)
        except GitOperationError as exc:
            return GitOperationResult(
                success=False,
                repo_status=GitRepoStatus(error=exc.git_error),
                error=exc.git_error,
            )
        pre_status = _git_to_repo_status(project, persist=False)
        if pre_status.error is not None:
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=pre_status.error,
            )
        if pre_status.transient:
            err = _make_repo_error(
                "GIT_TRANSIENT_STATE",
                "git is in transient state (merge/rebase)",
                project,
                branch=pre_status.branch,
                transient=True,
                hint="请先解决中间状态(merge/rebase/cherry-pick)后重试",
                retryable=False,
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        if require_clean and pre_status.is_dirty:
            err = _make_repo_error(
                "WORKTREE_DIRTY",
                "working tree is dirty",
                project,
                branch=pre_status.branch,
                hint="请先提交或 stash 改动",
                retryable=False,
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        try:
            cp_show = _run_git(
                ["show-ref", "--verify", f"refs/heads/{branch}"],
                cwd=project.project_dir,
            )
        except FileNotFoundError:
            err = _file_not_found_error(
                project, project.project_dir,
                branch=pre_status.branch,
                command=f"git show-ref --verify refs/heads/{branch}",
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        except subprocess.TimeoutExpired:
            err = _make_repo_error(
                "GIT_COMMAND_TIMEOUT",
                "git command timed out",
                project,
                command=f"git show-ref --verify refs/heads/{branch}",
                branch=pre_status.branch,
                retryable=True,
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        if cp_show.returncode != 0:
            err = _make_repo_error(
                "BRANCH_NOT_FOUND",
                "branch not found",
                project,
                command=f"git show-ref --verify refs/heads/{branch}",
                exit_code=cp_show.returncode,
                stderr=cp_show.stderr,
                branch=pre_status.branch,
                hint=f"分支 {branch} 不存在,请先创建",
                retryable=False,
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        previous_branch = pre_status.branch
        try:
            cp_co = _run_git(
                ["checkout", branch],
                cwd=project.project_dir,
            )
        except FileNotFoundError:
            err = _file_not_found_error(
                project, project.project_dir,
                branch=previous_branch,
                command=f"git checkout {branch}",
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        except subprocess.TimeoutExpired:
            err = _make_repo_error(
                "GIT_COMMAND_TIMEOUT",
                "git command timed out",
                project,
                command=f"git checkout {branch}",
                branch=previous_branch,
                retryable=True,
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        if cp_co.returncode != 0:
            err = _make_repo_error(
                "GIT_COMMAND_FAILED",
                "切换分支失败:本地改动阻止切换" if "would be overwritten" in cp_co.stderr else "git command failed",
                project,
                command=f"git checkout {branch}",
                exit_code=cp_co.returncode,
                stdout=cp_co.stdout,
                stderr=cp_co.stderr,
                branch=previous_branch,
                hint="请先提交或 stash 改动后重试",
                retryable=True,
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                previous_branch=previous_branch,
                error=err,
            )
        post_status = _git_to_repo_status(project, persist=True)
        return GitOperationResult(
            success=True,
            repo_status=post_status,
            previous_branch=previous_branch,
        )

    @staticmethod
    def create_branch(
        project: Project,
        branch: str,
        *,
        checkout: bool = True,
        start_point: str | None = None,
    ) -> GitOperationResult:
        """新建分支,可选同时切换。

        Args:
            project: 项目实体
            branch: 新分支名
            checkout: True 时创建后切换到新分支
            start_point: 起始点(commit/branch),None 时从当前 HEAD
        """
        try:
            branch = _validate_branch_name(branch, project)
        except GitOperationError as exc:
            return GitOperationResult(
                success=False,
                repo_status=GitRepoStatus(error=exc.git_error),
                error=exc.git_error,
            )
        pre_status = _git_to_repo_status(project, persist=False)
        if pre_status.error is not None:
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=pre_status.error,
            )
        if pre_status.transient:
            err = _make_repo_error(
                "GIT_TRANSIENT_STATE",
                "git is in transient state (merge/rebase)",
                project,
                branch=pre_status.branch,
                transient=True,
                hint="请先解决中间状态后重试",
                retryable=False,
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        try:
            cp_show = _run_git(
                ["show-ref", "--verify", f"refs/heads/{branch}"],
                cwd=project.project_dir,
            )
        except FileNotFoundError:
            err = _file_not_found_error(
                project, project.project_dir,
                branch=pre_status.branch,
                command=f"git show-ref --verify refs/heads/{branch}",
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        except subprocess.TimeoutExpired:
            err = _make_repo_error(
                "GIT_COMMAND_TIMEOUT",
                "git command timed out",
                project,
                command=f"git show-ref --verify refs/heads/{branch}",
                branch=pre_status.branch,
                retryable=True,
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        if cp_show.returncode == 0:
            err = _make_repo_error(
                "BRANCH_ALREADY_EXISTS",
                "branch already exists",
                project,
                command=f"git show-ref --verify refs/heads/{branch}",
                branch=pre_status.branch,
                hint=f"分支 {branch} 已存在",
                retryable=False,
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        previous_branch = pre_status.branch
        # start_point 以 "-" 开头会被 git 解析为选项(选项注入),显式拒绝
        if start_point and start_point.startswith("-"):
            err = _make_repo_error(
                "BRANCH_INVALID",
                f"invalid start_point: {start_point}",
                project,
                branch=previous_branch,
                hint="start_point 不能以 '-' 开头",
                retryable=False,
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        branch_args = ["branch", branch]
        if start_point:
            branch_args.append(start_point)
        try:
            cp_b = _run_git(branch_args, cwd=project.project_dir)
        except FileNotFoundError:
            err = _file_not_found_error(
                project, project.project_dir,
                branch=previous_branch,
                command="git branch",
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        except subprocess.TimeoutExpired:
            err = _make_repo_error(
                "GIT_COMMAND_TIMEOUT",
                "git command timed out",
                project,
                command="git branch",
                branch=previous_branch,
                retryable=True,
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        if cp_b.returncode != 0:
            err = _make_repo_error(
                "GIT_COMMAND_FAILED",
                "git command failed",
                project,
                command=" ".join(["git", *branch_args]),
                exit_code=cp_b.returncode,
                stdout=cp_b.stdout,
                stderr=cp_b.stderr,
                branch=previous_branch,
                hint="请检查 start_point 是否存在",
                retryable=True,
            )
            return GitOperationResult(
                success=False,
                repo_status=pre_status,
                error=err,
            )
        if checkout:
            try:
                cp_co = _run_git(
                    ["checkout", branch],
                    cwd=project.project_dir,
                )
            except FileNotFoundError:
                err = _file_not_found_error(
                    project, project.project_dir,
                    branch=previous_branch,
                    command=f"git checkout {branch}",
                )
                return GitOperationResult(
                    success=False,
                    repo_status=pre_status,
                    previous_branch=previous_branch,
                    error=err,
                )
            except subprocess.TimeoutExpired:
                err = _make_repo_error(
                    "GIT_COMMAND_TIMEOUT",
                    "git command timed out",
                    project,
                    command=f"git checkout {branch}",
                    branch=previous_branch,
                    retryable=True,
                )
                return GitOperationResult(
                    success=False,
                    repo_status=pre_status,
                    previous_branch=previous_branch,
                    error=err,
                )
            if cp_co.returncode != 0:
                err = _make_repo_error(
                    "GIT_COMMAND_FAILED",
                    "git command failed",
                    project,
                    command=f"git checkout {branch}",
                    exit_code=cp_co.returncode,
                    stdout=cp_co.stdout,
                    stderr=cp_co.stderr,
                    branch=previous_branch,
                    hint="分支已创建但切换失败,请手动切换",
                    retryable=True,
                )
                return GitOperationResult(
                    success=False,
                    repo_status=pre_status,
                    previous_branch=previous_branch,
                    error=err,
                )
        post_status = _git_to_repo_status(project, persist=True)
        return GitOperationResult(
            success=True,
            repo_status=post_status,
            previous_branch=previous_branch,
        )

    @staticmethod
    def _read_branch(project_dir: str) -> str | None:
        """读取当前分支名(辅助 ensure_on_project_create)。"""
        try:
            cp = _run_git(["symbolic-ref", "--short", "HEAD"], cwd=project_dir)
            if cp.returncode == 0:
                return cp.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None


_service_instance: ProjectGitService | None = None


def get_project_git_service() -> ProjectGitService:
    """返回 ``ProjectGitService`` 单例。"""
    global _service_instance
    if _service_instance is None:
        _service_instance = ProjectGitService()
    return _service_instance


def reset_project_git_service() -> None:
    """重置单例(仅供测试)。"""
    global _service_instance
    _service_instance = None
