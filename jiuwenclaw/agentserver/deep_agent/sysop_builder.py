# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SysOperationCard 构建工具 — 由 ``interface_deep.py`` 使用。

公开 API:
- ``create_sandbox_sysop_card``: 构造 jiuwenbox 沙箱模式 SysOperationCard
- ``create_local_sysop_card``: 构造本地模式 SysOperationCard
- ``build_filesystem_policy``: 组装沙箱 filesystem policy（agent 工作区 + 用户自定义）

"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


PreserveFileSharingMode = Literal["mount"]
_PRESERVE_FILE_SHARING_MODE: PreserveFileSharingMode = "mount"


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


def _relax_workspace_perms(root: Path) -> None:
    """递归把工作区放权到 sandbox uid 可写, 兜底 bwrap userns 不能映射 owner 的场景.

    """
    try:
        if root.is_symlink():
            return
        is_dir = root.is_dir()
    except OSError as exc:
        logger.debug("[sysop_builder] cannot stat %s: %s; skip chmod", root, exc)
        return
    extra = 0o007 if is_dir else 0o006
    try:
        st_mode = root.stat().st_mode & 0o7777
    except OSError as exc:
        logger.debug("[sysop_builder] cannot read mode of %s: %s; skip chmod", root, exc)
    else:
        new_mode = st_mode | extra
        if new_mode != st_mode:
            try:
                root.chmod(new_mode)
            except OSError as exc:
                logger.warning(
                    "[sysop_builder] could not relax perms on %s (%s -> %s): %s; "
                    "sandbox writes through bind mount may still hit Permission denied",
                    root, oct(st_mode), oct(new_mode), exc,
                )
    if not is_dir:
        return
    try:
        children = list(root.iterdir())
    except OSError as exc:
        logger.debug("[sysop_builder] cannot list %s: %s; skip recurse", root, exc)
        return
    for child in children:
        _relax_workspace_perms(child)


def _resolve_workspace_dir(workspace_dir: str | Path | None) -> Path | None:
    """Resolve and ensure the deep agent workspace dir exists & is sandbox-writable.

    The deep agent's :class:`Workspace` (``root_path``) is the sandbox's
    main writable root: ``init_workspace`` writes ``.workspace`` markers
    plus ``MEMORY.md`` / ``HEARTBEAT.md`` / ... inside it. bwrap requires
    the bind source to exist before launch, so we ``mkdir -p`` it here
    (matches what :class:`DirectoryBuilder` would do on first run, just
    earlier so the sandbox bind can succeed). After ensuring the dir
    exists we also recursively relax perms via :func:`_relax_workspace_perms`
    so the sandbox uid can actually write through the bind mount; see that
    function's docstring for the userns-drop-to-sandbox-uid rationale.

    Returns ``None`` when:
      - ``workspace_dir`` is empty / not provided;
      - the path can't be created (e.g. permission denied on the parent).

    A ``None`` return is treated by :func:`build_filesystem_policy` as
    "skip silently": the sandbox launches without an agent-workspace
    bind, which lets non-deep-agent callers reuse this builder without
    being forced to provide a workspace path.
    """
    if not workspace_dir:
        return None
    try:
        resolved = Path(workspace_dir).expanduser()
    except (TypeError, ValueError) as exc:
        logger.warning(
            "[sysop_builder] workspace_dir %r invalid: %s; skipping bind",
            workspace_dir, exc,
        )
        return None
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "[sysop_builder] could not ensure workspace dir %s: %s; "
            "skipping from sandbox bind list",
            resolved, exc,
        )
        return None
    if not resolved.is_dir():
        logger.warning(
            "[sysop_builder] workspace dir %s exists but is not a directory; "
            "skipping from sandbox bind list",
            resolved,
        )
        return None
    _relax_workspace_perms(resolved)
    return resolved


def build_filesystem_policy(
    files_runtime: dict[str, Any] | None,
    *,
    workspace_dir: str | Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """组装 jiuwenbox 沙箱的 filesystem policy.

    Args:
        files_runtime: ``config.yaml::sandbox.files`` 的字典，可能包含
            ``allow`` / ``deny`` 列表（项支持 str 或 {path, permissions} 形式）
        workspace_dir: deep agent 的 ``Workspace.root_path`` (例如
            ``~/.jiuwenclaw/service_default/agent_default/agent/jiuwenclaw_workspace``).
            若提供则整体以 ``mode=rw`` bind 进沙箱, 使 ``init_workspace`` 能在
            其子目录 (memory / todo / messages / ...) 写 ``.workspace`` marker
            与 ``MEMORY.md`` 等模板文件; ``None`` 时不挂工作区 (允许非 deep
            agent 调用方复用本 builder).

    Returns:
        ``(policy_dict, upload_list)``:
        - ``policy_dict``: ``{"filesystem_policy": {"files": [...], "directories":
          [...], "bind_mounts": [...], "read_write": [...], "read_only": [...]}}``;
        - ``upload_list``: 当前一律返回 ``[]``。 该字段是 jiuwenbox provider
          的 ``preserve_files_upload`` 字段约定 (元素形如
          ``{host_path, sandbox_path, kind}``); ``mount`` 模式下 agent
          工作区走 ``bind_mounts``, 不需要 upload, 该列表始终为空。

    Raises:
        FileNotFoundError: 当 ``files.allow`` / ``files.deny`` 中任何一条 path
            在 host 上不存在时抛出。
    """
    files_runtime = files_runtime or {}

    allow_files: list[dict[str, Any]] = []
    allow_dirs: list[dict[str, Any]] = []
    bind_mounts: list[dict[str, Any]] = []
    upload_list: list[dict[str, str]] = []
    # 沙箱里 mode=="rw" 的 bind mount 列表 (用 sandbox_path 记账)。
    writable_paths: list[str] = []
    # 需要写进 patch 顶层 ``read_write`` 的 sandbox 路径。
    read_write_promote: list[str] = []
    # 需要写进 patch 顶层 ``read_only`` 的 sandbox 路径——给 ``files.deny``
    # (``deny_write`` 语义) 用。
    read_only_promote: list[str] = []

    def _record_rw_bind(host_path: str, sandbox_path: str) -> None:
        """Register an rw bind mount (used by agent workspace / files.allow).

        We deliberately do NOT also append the path to ``allow_files`` /
        ``allow_dirs``: jiuwenbox's policy validator rejects any
        ``bind_mount.sandbox_path`` that also appears in
        ``filesystem_policy.files`` (or ``.directories``).
        """
        bind_mounts.append({
            "host_path": host_path,
            "sandbox_path": sandbox_path,
            "mode": "rw",
        })
        if sandbox_path not in writable_paths:
            writable_paths.append(sandbox_path)

    def _record_user_deny_bind(host_path: str, sandbox_path: str) -> None:
        """Register a deny_write bind: ``bind_mount mode=rw`` + ``read_only`` patch.

        见模块顶部注释关于为什么必须 ``mode=rw`` 而非 ``mode=ro``。
        """
        bind_mounts.append({
            "host_path": host_path,
            "sandbox_path": sandbox_path,
            "mode": "rw",
        })
        if sandbox_path not in read_only_promote:
            read_only_promote.append(sandbox_path)

    workspace_root = _resolve_workspace_dir(workspace_dir)
    if workspace_root is not None:
        workspace_str = str(workspace_root)
        _record_rw_bind(workspace_str, workspace_str)

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
        _record_rw_bind(path, path)
        if path not in read_write_promote:
            read_write_promote.append(path)

    # 注意: jiuwenclaw 自己的 ``config.yaml`` (含模型 API key) 不会被自动挂进
    # 沙箱 — 没有任何 intrinsic ro 通道把它带进去, 沙箱内根本看不到该文件。
    # 用户若显式把 ``config.yaml`` 写进 ``files.allow`` 才会暴露 (此时由用户负
    # 责理解后果); 不在 deny 里特殊处理。
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
        fs_policy["read_write"] = read_write_promote
    if read_only_promote:
        fs_policy["read_only"] = read_only_promote

    return {"filesystem_policy": fs_policy}, upload_list


def create_sandbox_sysop_card(
    sandbox_url: str,
    sandbox_type: str,
    *,
    workspace_dir: str | Path | None = None,
    files_runtime: dict[str, Any] | None = None,
    excluded_commands: list[str] | None = None,
    idle_ttl_seconds: int | None = None,
    idle_check_interval: int | None = None,
) -> SysOperationCard | None:
    """构造 jiuwenbox 沙箱模式 SysOperationCard.

    Args:
        sandbox_url: jiuwenbox HTTP 服务的 base url, 例如 ``http://127.0.0.1:8321``
        sandbox_type: jiuwenbox provider 名, 通常为 ``jiuwenbox``
        workspace_dir: deep agent 的 ``Workspace.root_path`` (例如
            ``~/.jiuwenclaw/service_default/agent_default/agent/jiuwenclaw_workspace``);
            透传给 :func:`build_filesystem_policy` 让其作为 rw bind mount,
            使沙箱里 ``init_workspace`` / heartbeat / memory 等写入能直达 host。
        files_runtime: ``sandbox.files`` 字典, 含 allow/deny (来源现在是 env var,
            见 :func:`get_sandbox_runtime`)
        excluded_commands: 命令 glob 列表; 命中时 provider 直接走本地
        idle_ttl_seconds: 沙箱空闲超时 (秒). 默认 ``None`` 表示不进行 idle 驱逐;
            jiuwenbox provider 会把它转成 ``timeout.idle_timeout`` 注入到
            ``create_sandbox`` 的 policy 中。 历史上默认 600s, 切换到 env var
            驱动后改为 ``None`` (不淘汰), 与 ``TimeoutPolicy.idle_timeout`` 的
            默认语义保持一致。
        idle_check_interval: idle reaper 轮询间隔 (秒). 默认 ``None`` 让
            jiuwenbox 服务端使用自身默认值 (``TimeoutPolicy.idle_check_interval``,
            目前为 60s); jiuwenbox provider 会把它转成
            ``timeout.idle_check_interval`` 注入到 policy 中。

    Returns:
        构造成功返回 ``SysOperationCard``; 失败 (``build_filesystem_policy``
        抛出 ``FileNotFoundError`` / 其他 ``ValueError`` 之类) 返回 ``None``,
        异常被捕获记 warning。
    """
    # 触发 jiuwenbox provider 注册（@SandboxRegistry.provider 装饰器副作用）
    try:
        import openjiuwen.extensions.sys_operation.sandbox.providers  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[sysop_builder] openjiuwen sandbox providers import failed "
            "(continuing; provider may already be registered): %s",
            exc,
        )

    try:
        policy, upload_list = build_filesystem_policy(
            files_runtime,
            workspace_dir=workspace_dir,
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
            timeout_seconds=30,
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
            "  workspace_dir=%s\n"
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
            workspace_dir,
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


def create_local_sysop_card(work_dir: str | None = None) -> SysOperationCard:
    """构造本地模式 SysOperationCard.

    Args:
        work_dir: 本地 work_dir; 为空时不在 ``LocalWorkConfig`` 中显式设置,
            由 openjiuwen 内部按其默认行为处理 (与本工程历史行为兼容)。
    """
    work_config = (
        LocalWorkConfig(work_dir=work_dir, shell_allowlist=None)
        if work_dir
        else LocalWorkConfig(shell_allowlist=None)
    )
    logger.info(
        "[sysop_builder] local SysOperationCard created (mode=LOCAL, work_dir=%s)",
        work_dir or "<default>",
    )
    return SysOperationCard(
        mode=OperationMode.LOCAL,
        work_config=work_config,
    )


__all__ = [
    "PreserveFileSharingMode",
    "build_filesystem_policy",
    "create_sandbox_sysop_card",
    "create_local_sysop_card",
]
