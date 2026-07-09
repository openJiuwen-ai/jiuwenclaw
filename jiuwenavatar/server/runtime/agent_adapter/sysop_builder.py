# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SysOperationCard 构建工具 — 抽离自 interface_deep.py。

`interface_deep.py` 和 `interface_code.py` 都依赖此模块构造 sys_operation 卡片，
不再通过类继承调用，避免静态方法依赖耦合。

公开 API:
- ``create_sandbox_sysop_card``: 构造 jiuwenbox 沙箱模式 SysOperationCard
- ``create_local_sysop_card``: 构造本地模式 SysOperationCard
- ``build_filesystem_policy``: 组装沙箱 filesystem policy（固有目录 + 用户自定义）

关于固有文件共享 (``preserve_file_sharing_mode``):

当前只支持 ``mount``: 把固有 agent 文件 (AGENT.md / HEARTBEAT.md /
IDENTITY.md / SOUL.md / USER.md) 与 ``daily_memory`` 目录通过 ``bind_mounts``
直接挂载进沙箱 (``host_path == sandbox_path``, mode=rw), 沙箱内外实时同步,
无需上传。 类型别名 :data:`PreserveFileSharingMode` 收窄为 ``Literal["mount"]``,
将来若引入新模式 (例如 overlay) 时再扩成 union。

关于 "project_dir":

- ``build_filesystem_policy`` 默认会把当前进程 cwd (代码 agent 运行起来时通常就是
  用户的工程根目录) 以 rw 模式 bind 进沙箱, 同名同路径; 同时把该目录登记进
  allow 列表方便用户/前端展示。
- 同时, jiuwenavatar 自己的 ``config.yaml`` (含模型 API key 等敏感凭据) 永远会
  作为 auto-deny (``bind_mount rw`` + ``read_only`` patch, 末尾 bwrap
  ``--remount-ro``) 加入策略, 防止 sandbox 内的 agent 写改它; 读不被屏蔽
  (这是 ``files.deny`` 的 deny_write 语义)。

关于 ``files.allow`` / ``files.deny`` 的对称设计:

- 两者共用同一套"检查 path → 翻译成 bind_mount + (read_write|read_only) patch"
  流程, 只是 patch 字段不同。
- path 在 host 上必须存在, 否则抛 ``FileNotFoundError`` 让上层
  (``_handle_sandbox_files_set`` / ``_handle_sandbox_files_remove`` 的 dry-run)
  转成 ``ValueError`` 回 TUI。 不存在的 path 既无意义又会让 bwrap 启动失败,
  早 fail 早提示。
- file / dir 一律用 ``Path.is_file()`` / ``is_dir()`` 实际 stat 磁盘判断, 不
  再依赖路径尾斜杠。
- allow 翻成 ``bind_mount mode=rw`` + 把 path 写进 patch ``read_write`` (防
  base policy 的 ``read_only`` 在 bwrap 末尾把 mount 推回 ro); deny 翻成
  ``bind_mount mode=rw`` + 把 path 写进 patch ``read_only`` (让 bwrap 末尾
  ``--remount-ro`` 把这条 bind 翻成 ro)。 deny 故意用 ``mode=rw`` 而非
  ``mode=ro``: bwrap 输出顺序是 ro_binds 在 rw_binds 之前, 子 deny 若用
  mode=ro 会被父 allow 的 mode=rw 后到覆盖, deny 失效; 子 deny 用 mode=rw
  跟父挂同处于 rw_binds 阶段, 按 list 顺序在父后, 再靠 ``--remount-ro``
  兜底翻 ro, 才能在 "父 rw + 子 deny" 拓扑下让 deny 真生效。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal

from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SandboxGatewayConfig,
    SysOperationCard,
)
from openjiuwen.core.sys_operation.config import (
    ContainerScope,
    PreDeployLauncherConfig,
    SandboxIsolationConfig,
)

from jiuwenavatar.common.utils import (
    get_agent_memory_dir,
    get_agent_skills_dir,
    get_config_file,
    get_deepagent_agent_md_path,
    get_deepagent_heartbeat_path,
    get_deepagent_identity_md_path,
    get_deepagent_soul_md_path,
    get_deepagent_user_md_path,
)

logger = logging.getLogger(__name__)


PreserveFileSharingMode = Literal["mount"]
_PRESERVE_FILE_SHARING_MODE: PreserveFileSharingMode = "mount"


# Read-write intrinsic 文件: deep agent 在沙箱里持续读写的 markdown state 文件.
# 这些路径由 _record_rw_bind 处理 (mode=rw + 0666), 不存在时由 _ensure_intrinsic_file
# 自动 touch 空文件.
_INTRINSIC_RW_FILE_PATH_FUNCS = (
    get_deepagent_agent_md_path,
    get_deepagent_heartbeat_path,
    get_deepagent_identity_md_path,
    get_deepagent_soul_md_path,
    get_deepagent_user_md_path,
)

# Read-only intrinsic 文件: 沙箱必须能读但绝不能写的 host 资源.
# 当前只含 ``config.yaml`` (jiuwenavatar 主配置, 含模型 API key / OAuth secret /
# 用户偏好等), 即使整个 ``/`` 已被 ro-bind / project_dir 把 ``~/.jiuwenavatar/``
# 暴露进沙箱, 这条 ro bind 也能配合末尾 ``--remount-ro`` 兜住, 让沙箱内的 agent
# 不能写回任何修改。
#
# 与 _INTRINSIC_RW_FILE_PATH_FUNCS 的差异:
#   - 走 _record_ro_resource_bind (mode=ro bind + read_only_promote 兜底), 既让
#     首挂语义直观, 又能在出现 rw 父挂覆盖时由末尾 --remount-ro 翻回 ro;
#   - **不**自动创建空文件: config.yaml 缺失意味着 jiuwenavatar 装得有问题,
#     塞一份空 yaml 会让 loadconfig 端解析失败更早夯死 + 真实问题被掩盖。
#     这里改成 warn + 跳过, 让上层早点暴露。
_INTRINSIC_RO_FILE_PATH_FUNCS = (
    get_config_file,
)


def _normalize_fs_entry(entry: Any, default_permissions: str) -> dict[str, Any] | None:
    """归一化 {path, permissions} 项；接受 str/dict，过滤空值。"""
    if entry is None:
        return None
    if isinstance(entry, str):
        path = entry.strip()
        if not path:
            return None
        return {"path": path, "permissions": default_permissions}
    if isinstance(entry, dict):
        path = str(entry.get("path") or "").strip()
        if not path:
            return None
        permissions = str(entry.get("permissions") or default_permissions)
        return {"path": path, "permissions": permissions}
    return None


def _ensure_intrinsic_file(path: Path) -> bool:
    """确保固有文件存在; 不存在则建空文件 (含父目录)。返回是否就绪可用。

    创建失败 (例如父目录不可写) 时记录 warning 并返回 ``False``, 让调用方
    把该条目从 sandbox 的 bind-mount / upload 列表里剔出去——bwrap 在 mount
    时要求 ``host_path`` 必须存在, 提前剔掉避免后续沙箱启动直接 fail。
    """
    try:
        if path.exists():
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        logger.info("[sysop_builder] created empty intrinsic file: %s", path)
        return True
    except OSError as exc:
        logger.warning(
            "[sysop_builder] could not ensure intrinsic file %s: %s; "
            "skipping from sandbox bind/upload list",
            path,
            exc,
        )
        return False


def _collect_intrinsic_targets() -> tuple[list[str], list[str], list[str]]:
    """收集 deep agent 固有路径 (host 上的绝对路径), 分 rw / ro 两类返回.

    分类规则:

    - **rw_files** (``mode=rw``, ``0666``): agent 在沙箱里持续读写的 state 文件
      (``_INTRINSIC_RW_FILE_PATH_FUNCS``). 不存在时由 :func:`_ensure_intrinsic_file`
      自动 touch 空文件, 保证 bwrap ``--bind`` 的 host_src 存在。
    - **rw_dirs** (``mode=rw``, ``0777``): agent 需要读写的目录 (``daily_memory``).
      **不自动创建**——host 上不存在 (例如该 agent 还没生成过 daily_memory)
      时, 跳过该条目, 不进沙箱 bind list, 也不主动 ``mkdir``。 这样可避免:
      (a) 因 ``get_agent_memory_dir()`` 解析出意外路径而误建目录;
      (b) 用户清理本地 memory 后, sandbox 启动反而把它静默重建。
    - **ro_files** (``mode=ro``, ``0444``): 沙箱必须只读访问的 host 资源
      (``_INTRINSIC_RO_FILE_PATH_FUNCS``, 当前只有 ``config.yaml``). **绝不**自动
      创建——主配置缺失意味着 jiuwenavatar 没装好, 创建空文件只会让真问题晚些时候
      在 loadconfig 时炸出, 加大排查难度; 这里改成 warn + 跳过。

    解析或副作用失败的条目都被静默剔除 (rw) / warn-跳过 (ro), 防止污染 sandbox 启动路径。

    Returns:
        ``(rw_files, rw_dirs, ro_files)``.
    """
    rw_files: list[str] = []
    rw_dirs: list[str] = []
    ro_files: list[str] = []

    for func in _INTRINSIC_RW_FILE_PATH_FUNCS:
        try:
            raw = func()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[sysop_builder] intrinsic rw file path func failed: %s", exc,
            )
            continue
        if raw is None:
            continue
        path = Path(raw)
        if _ensure_intrinsic_file(path):
            rw_files.append(str(path))

    try:
        daily_memory = Path(get_agent_memory_dir()) / "daily_memory"
    except Exception as exc:  # noqa: BLE001
        logger.debug("[sysop_builder] daily_memory dir resolve failed: %s", exc)
    else:
        # mount 前显式检查 host 上是否存在 daily_memory 目录; 不存在则跳过, 不
        # 自动 ``mkdir``。 bwrap 在 ``--bind`` 时要求 host_src 必须存在, 这里
        # 提前剔掉避免后续沙箱启动失败; 同时拒绝"沙箱启动反而把已被用户清理
        # 的 memory 目录静默重建"的行为。
        if daily_memory.is_dir():
            rw_dirs.append(str(daily_memory))
        else:
            logger.info(
                "[sysop_builder] daily_memory %s does not exist on host; "
                "skipping from sandbox bind list (mount only when present)",
                daily_memory,
            )

    for func in _INTRINSIC_RO_FILE_PATH_FUNCS:
        try:
            raw = func()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[sysop_builder] intrinsic ro file path func failed: %s", exc,
            )
            continue
        if raw is None:
            continue
        try:
            resolved = Path(raw).expanduser().resolve()
        except OSError as exc:
            logger.warning(
                "[sysop_builder] intrinsic ro file %s could not be resolved: %s; "
                "skipping from sandbox bind list",
                raw, exc,
            )
            continue
        if not resolved.exists():
            # 故意不 _ensure_intrinsic_file: 见函数 docstring 关于 ro 类不自动建空文件
            # 的原因。这里 warn 不报错让 sandbox 仍能启动 (沙箱内 agent 只是看不到
            # 这条 ro 文件); 真正的 jiuwenavatar 主进程会在 loadconfig 阶段炸出更准确
            # 的错误。
            logger.warning(
                "[sysop_builder] intrinsic ro file %s does not exist on host; "
                "skipping from sandbox bind list",
                resolved,
            )
            continue
        ro_files.append(str(resolved))

    return rw_files, rw_dirs, ro_files


def _resolve_project_dir(override: str | Path | None) -> Path | None:
    """Resolve the host directory to bind into the sandbox as ``rw``.

    Priority:
      1. ``override`` argument (caller-supplied; useful for tests / explicit
         project pinning when ``cwd`` is not what we want).
      2. ``JIUSWARM_SANDBOX_PROJECT_DIR`` env (allows operations to pin a
         project dir without code changes).
      3. ``Path.cwd()``: the process working directory at the time
         ``build_filesystem_policy`` is called.

    Returns ``None`` when the resolved path doesn't exist, isn't a directory,
    or is the filesystem root (we refuse to ``rw``-bind ``/``; that would
    expose every other host file the user didn't intend to share).
    """
    candidates: list[Path] = []
    if override is not None:
        candidates.append(Path(override))
    env_override = os.getenv("JIUSWARM_SANDBOX_PROJECT_DIR")
    if env_override:
        candidates.append(Path(env_override))
    candidates.append(Path.cwd())

    for cand in candidates:
        try:
            resolved = cand.expanduser().resolve()
        except OSError as exc:
            logger.debug(
                "[sysop_builder] project_dir candidate %s could not be resolved: %s",
                cand, exc,
            )
            continue
        if not resolved.is_dir():
            logger.debug(
                "[sysop_builder] project_dir candidate %s is not a directory; skipping",
                resolved,
            )
            continue
        if resolved == Path(resolved.anchor):
            # Filesystem root (``/`` on POSIX, ``C:\`` on Windows). Refusing
            # to rw-bind root is non-negotiable: it would shadow every other
            # ro mount the policy carefully set up, plus expose host secrets
            # that the user never intended the sandbox to see.
            logger.warning(
                "[sysop_builder] refusing to mount filesystem root %s as rw "
                "project directory; pick a more specific cwd or set "
                "JIUSWARM_SANDBOX_PROJECT_DIR",
                resolved,
            )
            return None
        return resolved
    return None


def _resolve_agent_skills_dir() -> Path | None:
    """Resolve the package-bundled built-in skills dir for sandbox mounting.

    :func:`get_agent_skills_dir` returns the path inside the installed
    jiuwenavatar package (``resources/agent/workspace/skills`` or its
    fallback ``resources/agent/skills``). The sandboxed agent both reads
    the bundled skill templates and may mutate / install new skills under
    this directory at runtime, so :func:`build_filesystem_policy` binds it
    rw (intentionally **not** added to ``filesystem_policy.read_only``).
    Mutations therefore propagate back to the host package install, which
    is by design for the skills lifecycle.

    Returns ``None`` when:
      - resolution itself raises (broken install / unusual layout);
      - the resolved path doesn't exist on disk;
      - the resolved path exists but isn't a directory.

    A ``None`` return is treated by ``build_filesystem_policy`` as "skip
    silently" (development checkouts without the ``resources/`` directory
    shipped will keep working; the sandbox just won't see the built-in
    skills, same as before this hook was added).
    """
    try:
        raw = get_agent_skills_dir()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[sysop_builder] builtin skills dir resolve failed: %s", exc)
        return None
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError as exc:
        logger.debug(
            "[sysop_builder] builtin skills dir %s could not be resolved: %s",
            raw, exc,
        )
        return None
    if not resolved.is_dir():
        logger.debug(
            "[sysop_builder] builtin skills dir %s is not a directory; skipping",
            resolved,
        )
        return None
    return resolved


def build_filesystem_policy(
    files_runtime: dict[str, Any] | None,
    *,
    project_dir: str | Path | None = None,
    is_code_agent: bool = False,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """组装 jiuwenbox 沙箱的 filesystem policy.

    Args:
        files_runtime: ``config.yaml::sandbox.files`` 的字典，可能包含
            ``allow`` / ``deny`` 列表（项支持 str 或 {path, permissions} 形式）
        project_dir: 当前工程目录的覆盖值, 缺省时回落到 :func:`_resolve_project_dir`
            (优先级 override -> 环境变量 -> ``cwd``). 仅在 ``is_code_agent=True``
            时会被消费并作为 ``rw`` bind mount 进沙箱; ``is_code_agent=False``
            时该字段被完全忽略 (不会有 fallback 挂 workspace 之类), 沙箱里也
            不会出现对应 rw bind。 解析失败 (例如指向 ``/``) 时静默跳过。
        is_code_agent: 是否是 code-agent 形态. ``True`` → mount 用户工程目录
            (``project_dir`` / ``cwd``, 由 :func:`_resolve_project_dir` 解析),
            agent 在沙箱里直接编辑用户仓库; ``False`` (默认, deep agent 等
            通用形态) → **完全不挂主写入根**, 沙箱只保留 intrinsic 文件
            (AGENT/HEARTBEAT/...) + daily_memory + agent_skills + 用户
            显式 ``files.allow``。 不挂 workspace 是为了避免把 agent-server
            进程的工作区 (含历史 session checkpoint / 边角持久化文件) 整片
            暴露进沙箱; 真要带额外 host 路径请走 ``/sandbox files allow`` 显式
            声明。

    Returns:
        ``(policy_dict, upload_list)``:
        - ``policy_dict``: ``{"filesystem_policy": {"files": [...], "directories":
          [...], "bind_mounts": [...], "read_write": [...], "read_only": [...]}}``;
        - ``upload_list``: 当前一律返回 ``[]``。 该字段是 jiuwenbox provider
          的 ``preserve_files_upload`` 字段约定 (元素形如
          ``{host_path, sandbox_path, kind}``); ``mount`` 模式下所有 intrinsic
          路径都走 ``bind_mounts``, 不需要 upload, 该列表始终为空。

    Raises:
        FileNotFoundError: 当 ``files.allow`` / ``files.deny`` 中任何一条 path
            在 host 上不存在时抛出。``/sandbox files allow|deny`` 的语义是
            「把已经存在于 host 的路径以可写/只读形式带进沙箱」, 对一个不存在
            的 path 操作既无意义 (沙箱里也无东西可挂) 又会让 bwrap 启动失败
            (``--bind <host_src>`` 要求 ``host_src`` 真实存在), 因此前置直接
            拒绝。``_handle_sandbox_files_set`` / ``_handle_sandbox_files_remove``
            的 dry-run 会把它捕获成 ``ValueError`` 回 TUI 让用户看到。
    """
    files_runtime = files_runtime or {}

    allow_files: list[dict[str, Any]] = []
    allow_dirs: list[dict[str, Any]] = []
    bind_mounts: list[dict[str, Any]] = []
    upload_list: list[dict[str, str]] = []
    # 沙箱里 mode=="rw" 的 bind mount 列表 (用 sandbox_path 记账)。曾用于
    # ``_validate_deny_not_under_writable`` 的冲突自检; 现在 deny 用
    # ``mode=rw bind + 末尾 --remount-ro`` 实现, 「父 rw + 子 deny」是合法
    # 配置 (恰好就是用户想要的 "整目录可写、单子项禁写" 语义), 不再有冲突。
    # 该 list 留作其他诊断信息源, 不再驱动校验路径。
    writable_paths: list[str] = []
    # 需要写进 patch 顶层 ``read_write`` 的 sandbox 路径。 当用户通过
    # ``/sandbox files allow <path>`` 要求把一个**也出现在 base policy
    # ``read_only`` 白名单里**的路径 (例如 ``code-agent-policy.yaml`` 默认
    # 给 ``/tmp`` 配的 ro) 升级成可写时, 仅仅追加一条 rw ``bind_mount`` 还
    # 不够: bwrap 在所有 bind 完成后会按 ``read_only - read_write`` 差集
    # 执行 ``--remount-ro``, 把我们刚加的 rw bind 推回只读 (见
    # ``BwrapConfig._apply_filesystem`` 第 139-142 行)。把同一个 sandbox 路
    # 径也写进 patch ``read_write``, ``policy_engine.merge_policy`` 会把它
    # 并入 base 的 ``read_write`` (list dedupe append), bwrap 端的差集就把这
    # 条 remount-ro 抠掉, rw bind 才真正生效。Landlock 那边由 ``landlock.py``
    # 直接根据 ``bind_mounts.mode`` 推导, 不依赖这里。
    read_write_promote: list[str] = []
    # 需要写进 patch 顶层 ``read_only`` 的 sandbox 路径——给 ``files.deny``
    # (``deny_write`` 语义) 用。 deny 的实现路径是:
    #
    #   1. ``bind_mounts: {host: path, sandbox: path, mode: rw}`` 先把 path
    #      作为独立 bind_mount 挂上去, 让它进入 ``BwrapConfig.created_paths``
    #      (``bwrap.py::to_args`` 的 created_paths 集合, ``--remount-ro`` 仅
    #      对集合内 path 生效);
    #   2. ``read_only: [path]`` 通过 ``policy_engine.merge_policy`` 并入 base
    #      ``read_only``, ``BwrapConfig._apply_filesystem`` 把它推进 ``cfg
    #      .remount_ro``;
    #   3. bwrap to_args 末尾 ``--remount-ro <path>`` 把这条 mount 翻成 ro,
    #      实现"父挂 rw + 子项 deny_write"语义。
    #
    # 为什么 step 1 的 bind_mount 用 ``mode=rw`` 而不是 ``mode=ro``: bwrap
    # 输出顺序是 ``--ro-bind`` (line 381) 在 ``--bind`` (line 385) 之前;
    # 如果 deny 用 ``mode=ro``, 而父 allow 用 ``mode=rw``, 父挂后到达会覆盖
    # 子挂回到 rw, deny 失效。统一让 deny 的 bind_mount 跟父挂同处于
    # ``rw_binds`` 阶段 (按 list 顺序子在父后), 再靠 ``--remount-ro`` 兜底
    # 翻 ro 是唯一能在 bwrap 现有 mount-stage 框架下做出来的可靠方案。
    read_only_promote: list[str] = []

    def _record_rw_bind(
        host_path: str,
        sandbox_path: str,
        *,
        is_dir: bool,
        permissions: str,
    ) -> None:
        """Register an rw bind mount (used by intrinsic / project-dir paths).

        We deliberately do NOT also append the path to ``allow_files`` /
        ``allow_dirs``: jiuwenbox's policy validator rejects any
        ``bind_mount.sandbox_path`` that also appears in ``filesystem_policy
        .files`` (or ``.directories``) with
        ``"Filesystem file path '<x>' conflicts with a bind mount"``. Earlier
        revisions duplicated bind-mounted paths into the allow lists so that
        ``/sandbox status`` could read "what's writable" from a single
        place, but that view is now computed independently by
        :func:`list_effective_sandbox_files`, so the redundancy serves no
        purpose and actively breaks sandbox creation in mount mode.

        ``permissions`` is intentionally unused for the same reason -- bind
        mounts carry their own mode; the previous ``permissions`` value is
        kept in the signature so the callers (intrinsic / intrinsic-dir /
        project-dir) stay symmetric and self-documenting at the call site.
        """
        del permissions  # retained on the signature for caller-side symmetry
        bind_mounts.append({
            "host_path": host_path,
            "sandbox_path": sandbox_path,
            "mode": "rw",
        })
        if sandbox_path not in writable_paths:
            writable_paths.append(sandbox_path)

    def _record_user_deny_bind(host_path: str, sandbox_path: str) -> None:
        """Register a deny_write bind: ``bind_mount mode=rw`` + ``read_only`` patch.

        见模块顶部 ``read_only_promote`` 注释关于为什么必须 ``mode=rw`` 而非
        ``mode=ro``。这两条记录配合 bwrap 的 ``--bind`` + 末尾 ``--remount-ro``
        实现「先把 path bind 上去拿到 ``created_paths`` 通行证, 再 remount 成
        ro」, 在「父 rw + 子 deny」拓扑下也能让 deny 子项真生效。
        """
        bind_mounts.append({
            "host_path": host_path,
            "sandbox_path": sandbox_path,
            "mode": "rw",
        })
        if sandbox_path not in read_only_promote:
            read_only_promote.append(sandbox_path)

    def _record_ro_resource_bind(host_path: str, sandbox_path: str) -> None:
        """Register a built-in readonly resource bind (intrinsic host data).

        Used for resources the sandbox must be able to *read* but never
        write — currently only :func:`get_config_file` (sensitive
        credentials). The intent is distinct from :func:`_record_user_deny_bind`,
        which represents a user-driven ``files.deny`` entry where the
        underlying host path is normally rw and deny is just an extra
        constraint. Here the host path is *intrinsically* read-only from
        the sandbox's perspective and there is no user-side rw semantics
        to preserve.

        The built-in skills directory is intentionally **not** routed
        through this helper; it is rw-mountable so the sandboxed agent
        can also edit / install skills. See the call site in
        :func:`build_filesystem_policy`.

        Implementation: ``mode=ro`` bind + ``read_only_promote`` belt-and-
        suspenders.

        - ``mode=ro`` makes the first-pass mount land in bwrap's
          ``--ro-bind`` stage, which is the natural and self-documenting
          encoding for "ro intrinsic resource".
        - The :data:`read_only_promote` entry survives the case where a
          later rw parent bind (e.g. a user-configured ``files.allow`` on
          the parent directory of ``config.yaml``) overlays the same
          subtree and silently upgrades the mount back to rw. bwrap's
          ``created_paths`` set is the union of ro_binds + rw_binds
          destinations (see ``bwrap.py``), so the trailing
          ``--remount-ro <path>`` is guaranteed to fire on this dst and
          flip it back to read-only regardless of which stage owned the
          mount last.
        """
        bind_mounts.append({
            "host_path": host_path,
            "sandbox_path": sandbox_path,
            "mode": "ro",
        })
        if sandbox_path not in read_only_promote:
            read_only_promote.append(sandbox_path)

    intrinsic_files, intrinsic_dirs, intrinsic_ro_files = _collect_intrinsic_targets()

    for path in intrinsic_files:
        _record_rw_bind(path, path, is_dir=False, permissions="0666")

    for path in intrinsic_dirs:
        _record_rw_bind(path, path, is_dir=True, permissions="0777")

    # Readonly intrinsic 资源 (目前只有 ``config.yaml``): 跟 agent_skills 同类,
    # 沙箱必须能读但绝不能写。统一走 ``_record_ro_resource_bind``, 末尾
    # ``--remount-ro`` 兜底, 保证 "父 rw + 子 ro" 拓扑下 ro 仍然鲁棒。
    #
    # config.yaml 的过去实现是散落在 build_filesystem_policy 末尾的内联 try/except
    # 调用 ``_record_user_deny_bind``——但 config.yaml 是服务强制 deny 而非用户主动
    # deny, 跟 ``files.deny`` 用户配置完全是两种语义; 归到这里跟 agent_skills /
    # 未来其他 ro intrinsic 一起统一处理, 语义和入口都更清晰。
    for path in intrinsic_ro_files:
        _record_ro_resource_bind(path, path)

    agent_skills = _resolve_agent_skills_dir()
    if agent_skills is not None:
        skills_str = str(agent_skills)
        _record_rw_bind(skills_str, skills_str, is_dir=True, permissions="0777")

    # 仅 code-agent 才挂用户工程目录 (cwd / 显式 project_dir): code-agent 的
    # 核心场景就是在用户仓库里读写源码, 不挂工程目录 agent 几乎无事可做。
    # 解析失败 / 指向 ``/`` 由 _resolve_project_dir 内部 warn 并返回 ``None``,
    # 这里静默跳过。
    #
    # 非 code-agent (deep agent 等通用形态) **不挂任何"主写入根"**: 通用
    # agent 没有"用户工程"的概念, 它持续读写的状态都是 intrinsic 文件
    # (AGENT/HEARTBEAT/IDENTITY/SOUL/USER.md, 上面 _record_rw_bind 已分别
    # 挂入) + daily_memory + agent_skills + 用户显式配置的 files.allow,
    # 不需要再额外 rw-bind 一整个 workspace 目录; 把 workspace 整挂会顺势
    # 暴露 agent 不该让沙箱读写的边角文件 (历史 session checkpoint 等),
    # 安全收益不明确, 反而扩大 attack surface。 真要往沙箱带额外路径,
    # 走 ``/sandbox files allow`` 显式声明。
    if is_code_agent:
        resolved_project = _resolve_project_dir(project_dir)
        if resolved_project is not None:
            project_str = str(resolved_project)
            _record_rw_bind(project_str, project_str, is_dir=True, permissions="0777")

    # ``files.allow`` 与 ``files.deny`` 走完全对称的处理流程:
    #
    #   1. ``_normalize_fs_entry`` 拿到 ``{path, permissions}`` (permissions
    #      在新方案里不再被 bind_mount 消费, 但保留以兼容 effective_files
    #      view 的展示逻辑);
    #   2. 路径剥掉尾斜杠—— file/dir 不再用斜杠区分, 也不再走 lifecycle 空
    #      文件/空目录的兜底; 一律用磁盘 stat 判断, 不存在直接抛错;
    #   3. 存在 → ``_record_rw_bind`` (allow) 或 ``_record_user_deny_bind``
    #      (deny) 翻成 ``bind_mount`` 一条 + ``read_write`` / ``read_only``
    #      patch 字段。
    #
    # 为什么 host 上不存在的路径要拒绝, 而不是默默放过 / 走 lifecycle:
    #   - bwrap 启动时 ``--bind <host_src> <sandbox_dst>`` 要求 ``host_src``
    #     存在, 否则 bwrap 直接退出 sandbox 创建失败;
    #   - lifecycle ``directories`` / ``files`` 字段 (jiuwenbox 在沙箱内
    #     ``mkdir`` / ``touch`` 空占位) 会被父挂 (``bind_root_entries``
    #     展开 / 用户其他 allow) 覆盖回原内容, 既无法 "凭空多一个可写目录",
    #     又会在用户视角制造 "config 看似生效, 沙箱里却不见"的迷惑;
    #   - 用户语义是 "把 host 已有 path 带进沙箱", 不存在的 path 操作
    #     本身就是 no-op, 报错让用户立刻知道 path 拼错或文件还没建。
    for entry in files_runtime.get("allow") or []:
        normalized = _normalize_fs_entry(entry, default_permissions="0666")
        if normalized is None:
            continue
        path = normalized["path"].rstrip("/") or "/"
        normalized["path"] = path
        host = Path(path)
        if not host.exists():
            raise FileNotFoundError(
                f"sandbox files.allow path does not exist on host: {path!r}"
            )
        _record_rw_bind(
            path,
            path,
            is_dir=host.is_dir(),
            permissions=str(normalized.get("permissions") or "0666"),
        )
        if path not in read_write_promote:
            read_write_promote.append(path)

    # jiuwenavatar 自己的 config.yaml 走 ``_INTRINSIC_RO_FILE_PATH_FUNCS`` 路径,
    # 跟 agent_skills 一起在上面统一注册; 这里不再单独处理。 (见
    # ``_collect_intrinsic_targets`` 与 ``_record_ro_resource_bind`` 的注释.)
    for entry in files_runtime.get("deny") or []:
        normalized = _normalize_fs_entry(entry, default_permissions="0000")
        if normalized is None:
            continue
        path = normalized["path"].rstrip("/") or "/"
        normalized["path"] = path
        host = Path(path)
        if not host.exists():
            raise FileNotFoundError(
                f"sandbox files.deny path does not exist on host: {path!r}"
            )
        _record_user_deny_bind(path, path)

    fs_policy: dict[str, Any] = {
        "files": allow_files,
        "directories": allow_dirs,
    }
    if bind_mounts:
        fs_policy["bind_mounts"] = bind_mounts
    if read_write_promote:
        # patch 顶层 ``read_write``——``policy_engine.merge_policy`` 按
        # list-dedupe-append 并入 base, 用来把 base policy ``read_only`` 中
        # 同名条目带来的尾段 ``--remount-ro`` 抠掉, 否则上面新增的 rw
        # ``bind_mount`` 会被 bwrap 在所有 bind 之后再推回 ro (见
        # ``read_write_promote`` 字段注释)。
        fs_policy["read_write"] = read_write_promote
    if read_only_promote:
        # patch 顶层 ``read_only``——给 ``files.deny`` (deny_write 语义) 用,
        # merge 进 base 后由 bwrap 末尾 ``--remount-ro`` 把对应 bind_mount
        # 翻成 ro (见 ``read_only_promote`` 字段注释)。
        fs_policy["read_only"] = read_only_promote

    return {"filesystem_policy": fs_policy}, upload_list


def create_sandbox_sysop_card(
    sandbox_url: str,
    sandbox_type: str,
    *,
    files_runtime: dict[str, Any] | None = None,
    excluded_commands: list[str] | None = None,
    idle_ttl_seconds: int | None = None,
    idle_check_interval: int | None = None,
    project_dir: str | Path | None = None,
    is_code_agent: bool = False,
) -> SysOperationCard | None:
    """构造 jiuwenbox 沙箱模式 SysOperationCard.

    Args:
        sandbox_url: jiuwenbox HTTP 服务的 base url, 例如 ``http://127.0.0.1:8321``
        sandbox_type: jiuwenbox provider 名, 通常为 ``jiuwenbox``
        files_runtime: ``config.yaml::sandbox.files`` 字典, 含 allow/deny
        excluded_commands: 命令 glob 列表; 命中时 provider 直接走本地
        idle_ttl_seconds: 沙箱空闲超时 (秒). 默认 ``None`` 表示不进行 idle 驱逐;
            jiuwenbox provider 会把它转成 ``timeout.idle_timeout`` 注入到
            ``create_sandbox`` 的 policy 中。
        idle_check_interval: idle reaper 轮询间隔 (秒). 默认 ``None`` 让
            jiuwenbox 服务端使用自身默认值; jiuwenbox provider 会把它转成
            ``timeout.idle_check_interval`` 注入到 policy 中, 与
            ``idle_ttl_seconds`` 同样传递给沙箱.
        project_dir: 透传给 :func:`build_filesystem_policy` 的工程目录覆盖值;
            仅当 ``is_code_agent=True`` 时被消费, 否则忽略.
        is_code_agent: 是否是 code-agent. 透传给 :func:`build_filesystem_policy`:
            ``True`` 时把 ``project_dir`` 挂为 rw bind, ``False`` 时不挂任何
            "主写入根" (workspace 也不挂); 见该函数 docstring 的详细说明.

    Returns:
        构造成功返回 ``SysOperationCard``; 失败 (``build_filesystem_policy``
        抛出 ``FileNotFoundError`` / 其他 ``ValueError`` 之类) 返回 ``None``,
        异常被捕获记 warning。
    """
    # 触发 jiuwenbox provider 注册（@SandboxRegistry.provider 装饰器副作用）
    import openjiuwen.extensions.sys_operation.sandbox.providers  # noqa: F401

    try:
        policy, upload_list = build_filesystem_policy(
            files_runtime,
            project_dir=project_dir,
            is_code_agent=is_code_agent,
        )
        extra_params: dict[str, Any] = {
            "policy": policy,
            "policy_mode": "append",
            "excluded_commands": list(excluded_commands or []),
            # provider 契约: 沙箱 sysop 永远带这两个 key, mode 固定 ``mount``,
            # upload_list 当前固定为空 list。
            "preserve_file_sharing_mode": _PRESERVE_FILE_SHARING_MODE,
            "preserve_files_upload": upload_list,
        }
        # ``idle_check_interval`` 走 ``extra_params`` 而非 ``launcher_config`` 上
        # 的独立字段, 这样不需要给 ``SandboxLauncherConfig`` 加 jiuwenbox 私有的
        # schema —— ``idle_check_interval`` 是 jiuwenbox 服务端 reaper 轮询间隔,
        # 别的 provider 用不到, 只在 jiuwenbox provider 里读出来 PUT 到
        # ``/api/v1/timeout``。 ``None`` 时不写入 extra_params, 让 provider 端
        # 走 "缺省即沿用 server 默认值" 的语义。
        if idle_check_interval is not None:
            extra_params["idle_check_interval"] = idle_check_interval

        gateway_config = SandboxGatewayConfig(
            isolation=SandboxIsolationConfig(container_scope=ContainerScope.SYSTEM),
            launcher_config=PreDeployLauncherConfig(
                base_url=sandbox_url,
                sandbox_type=sandbox_type,
                idle_ttl_seconds=idle_ttl_seconds,
                extra_params=extra_params,
            ),
        )
        sysop_card = SysOperationCard(
            mode=OperationMode.SANDBOX,
            work_config=LocalWorkConfig(shell_allowlist=None),
            gateway_config=gateway_config,
        )

        fs_policy = policy.get("filesystem_policy", {}) if isinstance(policy, dict) else {}
        logger.info(
            "[sysop_builder] sandbox SysOperationCard created:\n"
            "  base_url=%s sandbox_type=%s\n"
            "  idle_ttl=%s idle_check_interval=%s\n"
            "  preserve_file_sharing_mode=%s\n"
            "  excluded_commands(%d)=%s\n"
            "  filesystem_policy.files(%d)=%s\n"
            "  filesystem_policy.directories(%d)=%s\n"
            "  filesystem_policy.bind_mounts(%d)=%s\n"
            "  filesystem_policy.read_write(%d)=%s\n"
            "  filesystem_policy.read_only(%d)=%s\n"
            "  preserve_files_upload(%d)=%s\n"
            "  policy_mode=%s",
            sandbox_url,
            sandbox_type,
            idle_ttl_seconds,
            idle_check_interval,
            _PRESERVE_FILE_SHARING_MODE,
            len(extra_params["excluded_commands"]),
            extra_params["excluded_commands"] or "[]",
            len(fs_policy.get("files") or []),
            fs_policy.get("files") or [],
            len(fs_policy.get("directories") or []),
            fs_policy.get("directories") or [],
            len(fs_policy.get("bind_mounts") or []),
            fs_policy.get("bind_mounts") or [],
            len(fs_policy.get("read_write") or []),
            fs_policy.get("read_write") or [],
            len(fs_policy.get("read_only") or []),
            fs_policy.get("read_only") or [],
            len(upload_list),
            upload_list or [],
            extra_params["policy_mode"],
        )
        return sysop_card
    except Exception as exc:  # noqa: BLE001
        logger.warning("[sysop_builder] create sandbox sysop card failed: %s", exc)
        return None


def create_local_sysop_card() -> SysOperationCard:
    """构造本地模式 SysOperationCard."""
    logger.info("[sysop_builder] local SysOperationCard created (mode=LOCAL)")
    return SysOperationCard(
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(shell_allowlist=None),
    )


def _append_unique(target: list[dict[str, str]], entry: dict[str, str]) -> None:
    """Append ``entry`` to ``target`` if no existing item shares its ``path``.

    Pulled out of :func:`list_auto_managed_sandbox_paths` / :func:`list_effective_
    sandbox_files` so both helpers (and any future caller) dedupe by path the
    same way: first-write-wins, comparison on the literal ``path`` string.
    Auto-managed entries always come first, so user entries cannot override
    them just by replaying the same path.
    """
    if not any(item.get("path") == entry["path"] for item in target):
        target.append(entry)


def _classify_host_kind(path: str) -> str:
    """以 host 文件系统 stat 判定 ``path`` 是 ``"directory"`` 还是 ``"file"``.

    给 :func:`list_effective_sandbox_files` 用来给用户配置的
    ``sandbox.files.{allow,deny}`` 条目打 ``kind`` 字段。

    yaml 里的 path 入库前已经被 :func:`_canonicalize_sandbox_files_path`
    展开成 absolute resolved 形式 (尾斜杠被 ``resolve()`` 吃掉了), 因此
    无法再用 ``endswith("/")`` 判断目录, 必须 stat。

    ``Path(...).is_dir()`` 抛 ``OSError`` (权限不足之类) 或 host 上根本不
    存在时, fallback 到 ``"file"``: 写入侧 :func:`build_filesystem_policy`
    的 dry-run 已经把不存在的 path 拦在 yaml 之外, 这里跑到 fallback 一般
    意味着 yaml 被手工编辑得不一致, 简单兜底比拒绝展示更友好。
    """
    try:
        return "directory" if Path(path).expanduser().is_dir() else "file"
    except OSError:
        return "file"


def _resolve_display_path(raw: str | Path | None) -> str | None:
    """Resolve ``raw`` into the canonical absolute path used in display/compare.

    Expands ``~`` and symlinks the same way :func:`build_filesystem_policy`
    does so that ``list_auto_managed_sandbox_paths`` and
    :func:`find_auto_managed_match` agree on what counts as the "same" entry.
    Returns ``None`` for blank or unresolvable inputs.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return str(Path(text).expanduser().resolve())
    except OSError as exc:
        logger.debug(
            "[sysop_builder] path %r could not be resolved for display: %s",
            text, exc,
        )
        return None


def list_auto_managed_sandbox_paths(
    project_dir: str | Path | None = None,
    *,
    is_code_agent: bool = False,
) -> dict[str, list[dict[str, str]]]:
    """Auto-configured sandbox entries that users cannot mutate via ``/sandbox``.

    These mirror exactly what :func:`build_filesystem_policy` seeds the policy
    with regardless of user-supplied ``files.allow`` / ``files.deny``:

    - intrinsic agent files (AGENT/HEARTBEAT/IDENTITY/SOUL/USER.md) +
      ``daily_memory`` dir + agent_skills dir are always rw bind-mounted
      from host. The user project directory is rw bind-mounted only when
      ``is_code_agent=True``; deep / generic adapters skip this mount
      entirely (workspace is **not** auto-mounted as a fallback).
    - jiuwenavatar's own ``config.yaml`` is auto-added to ``deny_write`` so
      Landlock blocks writes to the api-key file even when the bind mounts
      otherwise expose the parent directory.

    Args:
        project_dir: explicit project-root override (typically
            ``trusted_dirs[0]`` cached on the adapter); ``None`` skips the
            project entry rather than falling back to cwd. Only consumed
            when ``is_code_agent=True``.
        is_code_agent: 与 :func:`build_filesystem_policy` 同义——决定是否
            把 ``project_dir`` 加进 allow_write. ``False`` 时连 project
            条目也不会出现 (不存在 workspace fallback), 跟 build 侧保持
            完全一致; 否则 TUI 列出来的 ``files.allow_write`` 跟沙箱启动
            后实际 mount 的目录会脱节, 用户看到一条 "项目目录已挂"
            但实际并没有挂。

    Returns:
        ``{"allow_write": [...], "deny_write": [...]}`` where each entry is
        ``{"path": str, "permissions": str, "kind": "file" | "directory"}``.
        Directory paths carry a trailing ``/``.
    """
    allow: list[dict[str, str]] = []
    deny: list[dict[str, str]] = []

    for func in _INTRINSIC_RW_FILE_PATH_FUNCS:
        try:
            raw = func()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[sysop_builder] auto view: intrinsic rw path func failed: %s",
                exc,
            )
            continue
        if raw is None:
            continue
        _append_unique(
            allow,
            {"path": str(Path(raw)), "permissions": "0666", "kind": "file"},
        )

    try:
        daily_memory_path = Path(get_agent_memory_dir()) / "daily_memory"
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[sysop_builder] auto view: daily_memory resolve failed: %s",
            exc,
        )
    else:
        # 显示侧与 ``_collect_intrinsic_targets`` 的 mount 决策保持一致: host
        # 上没有 daily_memory 目录就不进 auto-managed allow_write 列表 (不会
        # 实际被 mount, UI 列出来会误导用户)。
        if daily_memory_path.is_dir():
            _append_unique(
                allow,
                {
                    "path": str(daily_memory_path) + "/",
                    "permissions": "0777",
                    "kind": "directory",
                },
            )

    # 显示侧的"主写入根"必须跟 :func:`build_filesystem_policy` 完全镜像:
    # 仅 code-agent 才挂 project_dir, 非 code-agent 这一栏直接为空 (没有
    # workspace fallback)。 不一致就会让 TUI 列的 allow_write 跟沙箱里实际
    # mount 的 rw bind 脱节, 用户看到 "项目目录已挂", 但 deep agent 模式
    # 下实际并没有挂 — 那条 entry 一来本来就不存在。
    if is_code_agent and project_dir is not None:
        try:
            resolved_project: Path | None = Path(project_dir).expanduser().resolve()
        except OSError as exc:
            logger.debug(
                "[sysop_builder] auto view: project_dir %r resolve failed: %s",
                project_dir, exc,
            )
            resolved_project = None
        # 同 _resolve_project_dir: 显示侧也拒绝把 filesystem root 当项目目录,
        # 否则 build_filesystem_policy 会在创建沙箱时拒绝, 让 /sandbox 面板
        # 显示的 project_dir 与最终实际生效的 rw bind 不一致。
        if (
            resolved_project is not None
            and resolved_project.is_dir()
            and resolved_project != Path(resolved_project.anchor)
        ):
            _append_unique(
                allow,
                {
                    "path": str(resolved_project) + "/",
                    "permissions": "0777",
                    "kind": "directory",
                },
            )

    # Readonly intrinsic 文件 (含 config.yaml) 统一从 _INTRINSIC_RO_FILE_PATH_FUNCS
    # 派生, 跟 :func:`build_filesystem_policy` 里 _record_ro_resource_bind 注册的
    # 集合一一对应。 路径不存在 / 解析失败时只 debug-log 跳过, 跟 build 侧的 warn
    # 是一回事 (build 侧负责报告状态, 展示侧静默)。
    for func in _INTRINSIC_RO_FILE_PATH_FUNCS:
        try:
            raw = func()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[sysop_builder] auto view: intrinsic ro path func failed: %s",
                exc,
            )
            continue
        if raw is None:
            continue
        try:
            resolved = Path(raw).expanduser().resolve()
        except OSError as exc:
            logger.debug(
                "[sysop_builder] auto view: intrinsic ro path %r resolve failed: %s",
                raw, exc,
            )
            continue
        _append_unique(
            deny,
            {"path": str(resolved), "permissions": "0444", "kind": "file"},
        )

    # Built-in skills directory mirrors what :func:`build_filesystem_policy`
    # registers via ``_record_rw_bind``. We surface it under ``allow_write``
    # so ``/sandbox files list`` makes the writable mount visible to
    # operators (otherwise the directory would appear inside the sandbox
    # without any breadcrumb in the file panel). The path resolution here
    # is silent on failure: a dev checkout without ``resources/`` shipped
    # should not raise from a display helper.
    agent_skills = _resolve_agent_skills_dir()
    if agent_skills is not None:
        _append_unique(
            allow,
            {
                "path": str(agent_skills) + "/",
                "permissions": "0777",
                "kind": "directory",
            },
        )

    return {"allow_write": allow, "deny_write": deny}


def list_effective_sandbox_files(
    files_runtime: dict[str, Any] | None,
    *,
    project_dir: str | Path | None = None,
    is_code_agent: bool = False,
) -> dict[str, list[dict[str, str]]]:
    """Read-only "what will the sandbox actually allow / deny writes to" view.

    The result is the union of :func:`list_auto_managed_sandbox_paths` and the
    user-configured ``files.allow`` / ``files.deny`` entries from
    ``config.yaml::sandbox.files``. Auto entries appear first so any
    duplicate user entry is suppressed (and stays out of the displayed list).

    Mirrors the union :func:`build_filesystem_policy` would assemble, but does
    NOT create empty files or directories on the host. Intended for display
    by ``/sandbox status`` / ``/sandbox files list``.

    Args:
        files_runtime: ``sandbox.files`` dict from ``get_sandbox_runtime()``.
        project_dir: explicit override for the rw bind-mount root. Callers
            should pass the trusted-directory project root cached on the
            adapter (i.e. ``trusted_dirs[0]``); ``None`` means "unknown",
            which suppresses the project-dir entry instead of falling back
            to ``cwd``.

    Returns:
        ``{"allow_write": [...], "deny_write": [...]}`` where every entry is
        ``{"path": str, "permissions": str, "kind": "file" | "directory"}``.
    """
    auto = list_auto_managed_sandbox_paths(
        project_dir=project_dir,
        is_code_agent=is_code_agent,
    )
    allow = list(auto["allow_write"])
    deny = list(auto["deny_write"])

    files_runtime = files_runtime or {}

    def _emit(bucket: list[dict[str, str]], entry: Any, default_permissions: str) -> None:
        normalized = _normalize_fs_entry(entry, default_permissions=default_permissions)
        if normalized is None:
            return
        stripped = str(normalized["path"]).rstrip("/") or "/"
        kind = _classify_host_kind(stripped)
        display = stripped + "/" if kind == "directory" and stripped != "/" else stripped
        _append_unique(
            bucket,
            {
                "path": display,
                "permissions": str(normalized["permissions"]),
                "kind": kind,
            },
        )

    for entry in files_runtime.get("allow") or []:
        _emit(allow, entry, "0666")
    for entry in files_runtime.get("deny") or []:
        _emit(deny, entry, "0000")

    return {"allow_write": allow, "deny_write": deny}


def find_auto_managed_match(
    path: str,
    *,
    project_dir: str | Path | None = None,
    is_code_agent: bool = False,
) -> tuple[str, str] | None:
    """Return ``(bucket, canonical_path)`` if ``path`` is auto-managed; else ``None``.

    Used by ``/sandbox files allow|deny`` to refuse mutations that would
    duplicate or contradict an auto-managed entry. Comparison normalizes
    ``~``, trailing slashes, and ``./`` segments so the user can't sneak the
    same path in by varying its surface form.

    Args:
        path: user-supplied path (may use ``~``, trailing slash, etc.).
        project_dir: project-root override forwarded to
            :func:`list_auto_managed_sandbox_paths`.

    Returns:
        ``(bucket, canonical_path)`` where ``bucket`` is ``"allow_write"`` or
        ``"deny_write"`` and ``canonical_path`` is the entry's displayed
        path (trailing-slash for directories). ``None`` when the path is
        not auto-managed.
    """
    target = _resolve_display_path(path)
    if target is None:
        return None
    auto = list_auto_managed_sandbox_paths(
        project_dir=project_dir,
        is_code_agent=is_code_agent,
    )
    for bucket in ("allow_write", "deny_write"):
        for entry in auto.get(bucket, []):
            candidate = _resolve_display_path(entry.get("path", ""))
            if candidate is not None and candidate == target:
                return bucket, str(entry.get("path", ""))
    return None


__all__ = [
    "PreserveFileSharingMode",
    "build_filesystem_policy",
    "create_sandbox_sysop_card",
    "create_local_sysop_card",
    "find_auto_managed_match",
    "list_auto_managed_sandbox_paths",
    "list_effective_sandbox_files",
]
