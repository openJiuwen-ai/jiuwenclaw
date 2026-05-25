# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SysOperationCard 构建工具 — 由 ``interface_deep.py`` 使用。

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

关于沙箱"主写入根":

- 本工程 (deep agent / 通用形态) **不会自动**把任何"主写入根"挂进沙箱:
  历史上 ``build_filesystem_policy`` 会把 ``project_dir`` / cwd / 环境变量
  指向的目录以 rw 模式 bind 进沙箱; 现已删除该行为, 沙箱里只剩
  intrinsic 文件 (AGENT/HEARTBEAT/IDENTITY/SOUL/USER.md) +
  ``daily_memory`` + ``agent_skills`` + 用户显式 ``files.allow`` 这几类。
  想让沙箱里访问额外 host 路径请通过 ``JIUWENCLAW_SANDBOX_FILES_ALLOW``
  显式声明, 而不是依赖 cwd/workspace 自动暴露。
- jiuwenclaw 自己的 ``config.yaml`` (含模型 API key 等敏感凭据) **不会**
  被挂进沙箱 (历史版本曾以 ro intrinsic 形式挂进去, 现已移除); 沙箱里
  根本看不到这个文件, 任何沙箱内子工具都无法读取/泄露其中的凭据。
  jiuwenclaw 主进程在沙箱外部已经把 config.yaml 加载到内存, 沙箱跑命令
  / code 执行不需要原始文件存在。

关于 ``files.allow`` / ``files.deny`` 的对称设计:

- 两者共用同一套"检查 path → 翻译成 bind_mount + (read_write|read_only) patch"
  流程, 只是 patch 字段不同。
- path 在 host 上必须存在, 否则抛 ``FileNotFoundError``。 不存在的 path 既无
  意义又会让 bwrap 启动失败, 早 fail 早提示。
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

from jiuwenclaw.utils import (
    get_agent_memory_dir,
    get_agent_skills_dir,
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


def _collect_intrinsic_targets() -> tuple[list[str], list[str]]:
    """收集 deep agent 固有路径 (host 上的绝对路径), 分 rw_files / rw_dirs 返回.

    分类规则:

    - **rw_files** (``mode=rw``, ``0666``): agent 在沙箱里持续读写的 state 文件
      (``_INTRINSIC_RW_FILE_PATH_FUNCS``). 不存在时由 :func:`_ensure_intrinsic_file`
      自动 touch 空文件, 保证 bwrap ``--bind`` 的 host_src 存在。
    - **rw_dirs** (``mode=rw``, ``0777``): agent 需要读写的目录 (``daily_memory``).
      **不自动创建**——host 上不存在 (例如该 agent 还没生成过 daily_memory)
      时, 跳过该条目, 不进沙箱 bind list, 也不主动 ``mkdir``。 这样可避免:
      (a) 因 ``get_agent_memory_dir()`` 解析出意外路径而误建目录;
      (b) 用户清理本地 memory 后, sandbox 启动反而把它静默重建。

    解析或副作用失败的条目都被静默剔除, 防止污染 sandbox 启动路径。

    历史: 曾返回 3-tuple, 第三段是 ``ro_files`` (装 ``config.yaml`` 等敏感凭据
    走 ``mode=ro`` bind 进沙箱); 现已删除 ro intrinsic 通道, ``config.yaml``
    根本不进沙箱, 因此回到 2-tuple。

    Returns:
        ``(rw_files, rw_dirs)``.
    """
    rw_files: list[str] = []
    rw_dirs: list[str] = []

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

    return rw_files, rw_dirs


def _resolve_agent_skills_dir() -> Path | None:
    """Resolve the package-bundled built-in skills dir for sandbox mounting.

    :func:`get_agent_skills_dir` returns the path under
    ``~/.jiuwenclaw/agent/jiuwenclaw_workspace/skills`` (single-tenant
    layout). The sandboxed agent both reads bundled skill templates and
    may mutate / install new skills under this directory at runtime, so
    :func:`build_filesystem_policy` binds it rw (intentionally **not**
    added to ``filesystem_policy.read_only``). Mutations therefore
    propagate back to the host install, which is by design for the
    skills lifecycle.

    Returns ``None`` when:
      - resolution itself raises (broken install / unusual layout);
      - the resolved path doesn't exist on disk;
      - the resolved path exists but isn't a directory.

    A ``None`` return is treated by ``build_filesystem_policy`` as "skip
    silently" (development checkouts without the skills dir provisioned
    will keep working; the sandbox just won't see the built-in skills,
    same as before this hook was added).
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
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """组装 jiuwenbox 沙箱的 filesystem policy.

    Args:
        files_runtime: ``config.yaml::sandbox.files`` 的字典，可能包含
            ``allow`` / ``deny`` 列表（项支持 str 或 {path, permissions} 形式）

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

    def _record_rw_bind(
        host_path: str,
        sandbox_path: str,
        *,
        is_dir: bool,
        permissions: str,
    ) -> None:
        """Register an rw bind mount (used by intrinsic / agent_skills paths).

        We deliberately do NOT also append the path to ``allow_files`` /
        ``allow_dirs``: jiuwenbox's policy validator rejects any
        ``bind_mount.sandbox_path`` that also appears in ``filesystem_policy
        .files`` (or ``.directories``).

        ``permissions`` is intentionally unused -- bind mounts carry their
        own mode; the previous ``permissions`` value is kept in the signature
        so the callers (intrinsic-file / intrinsic-dir / agent_skills) stay
        symmetric and self-documenting at the call site.
        """
        del permissions  # retained on the signature for caller-side symmetry
        del is_dir  # bind_mounts carry no kind distinction; kept for symmetry
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

    intrinsic_files, intrinsic_dirs = _collect_intrinsic_targets()

    for path in intrinsic_files:
        _record_rw_bind(path, path, is_dir=False, permissions="0666")

    for path in intrinsic_dirs:
        _record_rw_bind(path, path, is_dir=True, permissions="0777")

    agent_skills = _resolve_agent_skills_dir()
    if agent_skills is not None:
        skills_str = str(agent_skills)
        _record_rw_bind(skills_str, skills_str, is_dir=True, permissions="0777")

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
    files_runtime: dict[str, Any] | None = None,
    excluded_commands: list[str] | None = None,
    idle_ttl_seconds: int | None = None,
    idle_check_interval: int | None = None,
) -> SysOperationCard | None:
    """构造 jiuwenbox 沙箱模式 SysOperationCard.

    Args:
        sandbox_url: jiuwenbox HTTP 服务的 base url, 例如 ``http://127.0.0.1:8321``
        sandbox_type: jiuwenbox provider 名, 通常为 ``jiuwenbox``
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
        policy, upload_list = build_filesystem_policy(files_runtime)
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
