"""本地拉起 Gateway + AgentServer（开发/联调用），并写入 per-instance 工作区配置。"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.config import Settings
from jiuwenclaw_manager.core.instance.instance_service import (
    create_instance_row,
    dumps_auth_config,
    generate_unique_jiuwenclaw_id,
    get_instance_row,
    merge_instance_data,
)


def _repo_root() -> Path:
    # .../claw_manager/src/jiuwenclaw_manager/core/instance/instance_provisioner.py -> repo root
    return Path(__file__).resolve().parents[7]


def _resolve_provision_python(repo_root: Path, settings: Settings) -> str:
    """解析用于拉起 Gateway / AgentServer 的 Python 解释器。

    优先级：``CLAWMANAGER_PROVISION_PYTHON`` > 仓库根 ``.venv`` > 当前进程的 ``sys.executable``。
    Manager 常在 ``claw_manager/.venv`` 中运行，而 jiuwenclaw 依赖（如 openjiuwen）安装在仓库根 ``.venv``。
    """
    if settings.provision_python:
        explicit = Path(settings.provision_python).expanduser()
        if not explicit.is_file():
            raise FileNotFoundError(f"provision_python not found: {explicit}")
        return str(explicit.resolve())

    if os.name == "nt":
        venv_candidates = (
            repo_root / ".venv" / "Scripts" / "python.exe",
            repo_root / ".venv" / "Scripts" / "python",
        )
    else:
        venv_candidates = (
            repo_root / ".venv" / "bin" / "python",
            repo_root / ".venv" / "bin" / "python3",
        )
    for candidate in venv_candidates:
        if candidate.is_file():
            return str(candidate.resolve())

    return sys.executable


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _materialize_instance_config(
    *,
    instance_dir: Path,
    template_path: Path,
    rest_port: int,
    agent_sqlite: Path,
) -> None:
    instance_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir = instance_dir / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raw = {}
    ext = raw.get("extensions")
    if not isinstance(ext, dict):
        ext = {}
        raw["extensions"] = ext
    # 由子进程环境变量 EXTENSION_DIRS 决定；Gateway 在 base_env 中注入路径，AgentServer 置空不加载扩展
    ext["extension_dirs"] = "${EXTENSION_DIRS}"
    ac = ext.get("agent_client_rest")
    if not isinstance(ac, dict):
        ac = {}
        ext["agent_client_rest"] = ac
    db = ac.get("database")
    if not isinstance(db, dict):
        db = {}
        ac["database"] = db

    # 支持通过环境变量配置数据库类型
    db_type = os.getenv("JIUWENCLAW_GATEWAY_DB_TYPE", "mysql").strip().lower()
    if db_type == "mysql":
        db["db_type"] = "mysql"
        db["db"] = {
            "host": os.getenv("DB_HOST", os.getenv("JIUWENCLAW_GATEWAY_DB_HOST", "localhost")),
            "port": int(os.getenv("DB_PORT", os.getenv("JIUWENCLAW_GATEWAY_DB_PORT", "3306"))),
            "user": os.getenv("DB_USER", os.getenv("JIUWENCLAW_GATEWAY_DB_USER", "root")),
            "password": os.getenv("DB_PASSWORD", os.getenv("JIUWENCLAW_GATEWAY_DB_PASSWORD", "123456")),
            "db_name": os.getenv("DB_NAME", os.getenv("JIUWENCLAW_GATEWAY_DB_NAME", "gateway")),
        }
    else:
        # 默认使用 SQLite
        db["db_type"] = "sqlite"
        db["sqlite_path"] = str(agent_sqlite.resolve())

    # 保留模板中的 enabled: ${AGENT_CLIENT_REST_ENABLED:-true}，便于 AgentServer 进程关闭 REST、仅 Gateway 监听端口
    ac["host"] = "127.0.0.1"
    ac["port"] = rest_port
    (cfg_dir / "config.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _child_env_common(
    *,
    settings: Settings,
    jiuwenclaw_id: str,
    instance_dir: Path,
    management_api_base: str,
    extension_dirs: str,
) -> dict[str, str]:
    out = os.environ.copy()
    out["JIUWENCLAW_DATA_DIR"] = str(instance_dir.resolve())
    out["JIUWENCLAW_CONFIG_DIR"] = str((instance_dir / "config").resolve())
    out["EXTENSION_DIRS"] = extension_dirs
    out["JIUWENCLAW_ID"] = jiuwenclaw_id
    out["MANAGEMENT_API_BASE"] = management_api_base

    db_type = os.getenv("JIUWENCLAW_GATEWAY_DB_TYPE", "mysql").strip().lower()
    out["JIUWENCLAW_GATEWAY_DB_TYPE"] = db_type
    out["AGENT_CLIENT_DB_TYPE"] = db_type
    out["MANAGER_WS_CLIENT_DB_TYPE"] = db_type
    if db_type == "mysql":
        host = os.getenv("DB_HOST", os.getenv("JIUWENCLAW_GATEWAY_DB_HOST", "localhost"))
        port = os.getenv("DB_PORT", os.getenv("JIUWENCLAW_GATEWAY_DB_PORT", "3306"))
        user = os.getenv("DB_USER", os.getenv("JIUWENCLAW_GATEWAY_DB_USER", "root"))
        password = os.getenv("DB_PASSWORD", os.getenv("JIUWENCLAW_GATEWAY_DB_PASSWORD", "123456"))
        db_name = os.getenv("DB_NAME", os.getenv("JIUWENCLAW_GATEWAY_DB_NAME", "gateway"))
        out["DB_HOST"] = host
        out["DB_PORT"] = str(port)
        out["DB_USER"] = user
        out["DB_PASSWORD"] = password
        out["DB_NAME"] = db_name
        out["JIUWENCLAW_GATEWAY_DB_HOST"] = host
        out["JIUWENCLAW_GATEWAY_DB_PORT"] = str(port)
        out["JIUWENCLAW_GATEWAY_DB_USER"] = user
        out["JIUWENCLAW_GATEWAY_DB_PASSWORD"] = password
        out["JIUWENCLAW_GATEWAY_DB_NAME"] = db_name

    return out


async def provision_local_jiuwenclaw(
    handler: DBHandler,
    settings: Settings,
    *,
    jiuwenclaw_name: str,
    creator_id: str,
    description: str | None,
) -> dict[str, Any]:
    if not settings.allow_local_provision:
        raise ValueError("local provision disabled (set MANAGER_ALLOW_LOCAL_PROVISION=true)")

    repo_root = Path(settings.provision_repo_root or _repo_root()).resolve()
    gateway_ext = (repo_root / "packages" / "jiuwenclaw-ee" / "gateway" / "extensions").resolve()
    ext_dirs = settings.provision_extension_dirs or str(gateway_ext)
    tpl = Path(
        settings.instance_config_template or (repo_root / "docker" / "config" / "config.yaml")
    ).resolve()
    if not tpl.is_file():
        raise FileNotFoundError(f"config template not found: {tpl}")

    root = Path(settings.provision_workspace_root).expanduser().resolve()
    jiuwenclaw_id = await generate_unique_jiuwenclaw_id(handler)
    instance_dir = root / jiuwenclaw_id
    if instance_dir.exists():
        raise FileExistsError(f"workspace already exists: {instance_dir}")

    agent_port = _pick_port()
    web_port = _pick_port()
    rest_port = _pick_port()
    agent_db = instance_dir / "agent_client.db"

    _materialize_instance_config(
        instance_dir=instance_dir,
        template_path=tpl,
        rest_port=rest_port,
        agent_sqlite=agent_db,
    )

    management_api_base = f"http://127.0.0.1:{rest_port}"

    await create_instance_row(
        handler,
        {
            "jiuwenclaw_id": jiuwenclaw_id,
            "jiuwenclaw_name": jiuwenclaw_name,
            "creator_id": creator_id,
            "description": description,
            "k8s_master_host": "local-provision",
            "k8s_auth_type": "none",
            "k8s_auth_config": dumps_auth_config({}),
            "k8s_namespace": "local",
            # Gateway 经 Manager WS 注册成功后由 register_gateway_via_ws 置为 online
            "status": "pending",
            "resource_quota": None,
            "data": {
                "management_api_base": management_api_base,
                "provision": {
                    "workspace": str(instance_dir),
                    "ports": {
                        "agent_server": agent_port,
                        "web": web_port,
                        "agent_client_rest": rest_port,
                    },
                },
            },
            "group_id": "default",
            "space_id": "default",
        }
    )

    py = _resolve_provision_python(repo_root, settings)
    py_path = settings.provision_pythonpath or f"{repo_root}:{repo_root / 'packages'}"
    base_env = _child_env_common(
        settings=settings,
        jiuwenclaw_id=jiuwenclaw_id,
        instance_dir=instance_dir,
        management_api_base=management_api_base,
        extension_dirs=ext_dirs,
    )
    base_env["PYTHONPATH"] = py_path
    base_env["PYTHONUNBUFFERED"] = "1"
    base_env["PYTHONUTF8"] = "1"
    base_env["PYTHONIOENCODING"] = "utf-8"

    env_as = dict(base_env)
    env_as["AGENT_SERVER_HOST"] = "127.0.0.1"
    env_as["AGENT_SERVER_PORT"] = str(agent_port)
    # AgentServer 不加载 Gateway 扩展（如 manager_ws_client），避免与 Gateway 争抢 Manager WS 连接
    env_as["EXTENSION_DIRS"] = ""
    # 与 Gateway 共用同一 REST 端口时，仅由 Gateway 挂载 agent_client_rest，避免双进程抢端口导致 62160 无监听
    env_as["AGENT_CLIENT_REST_ENABLED"] = "false"

    env_gw = dict(base_env)
    env_gw["AGENT_SERVER_URL"] = f"ws://127.0.0.1:{agent_port}"
    env_gw["WEB_HOST"] = "127.0.0.1"
    env_gw["WEB_PORT"] = str(web_port)
    env_gw["AGENT_CLIENT_REST_ENABLED"] = "true"

    log_dir = instance_dir / "provision_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_as = open(  # noqa: SIM115
        log_dir / "agentserver.log",
        "a",
        encoding="utf-8",
        errors="replace",
        buffering=1,
    )
    log_gw = open(  # noqa: SIM115
        log_dir / "gateway.log",
        "a",
        encoding="utf-8",
        errors="replace",
        buffering=1,
    )

    proc_as = subprocess.Popen(
        [py, "-m", "jiuwenclaw.app_agentserver", "--port", str(agent_port)],
        env=env_as,
        cwd=str(repo_root),
        stdout=log_as,
        stderr=subprocess.STDOUT,
    )
    time.sleep(1.5)
    proc_gw = subprocess.Popen(
        [py, "-m", "jiuwenclaw.app_gateway", "--port", str(web_port), "-u", f"ws://127.0.0.1:{agent_port}"],
        env=env_gw,
        cwd=str(repo_root),
        stdout=log_gw,
        stderr=subprocess.STDOUT,
    )

    full_prov: dict[str, Any] = {
        "workspace": str(instance_dir),
        "ports": {"agent_server": agent_port, "web": web_port, "agent_client_rest": rest_port},
        "pids": {"agentserver": proc_as.pid, "gateway": proc_gw.pid},
        "python": py,
        "logs": {
            "agentserver": str(log_dir / "agentserver.log"),
            "gateway": str(log_dir / "gateway.log"),
        },
    }
    await merge_instance_data(handler, jiuwenclaw_id, {"provision": full_prov})

    return {
        "jiuwenclaw_id": jiuwenclaw_id,
        "status": "pending",
        "management_api_base": management_api_base,
        "ports": {"agent_server": agent_port, "web": web_port, "agent_client_rest": rest_port},
        "workspace": str(instance_dir),
        "pids": {"agentserver": proc_as.pid, "gateway": proc_gw.pid},
        "python": py,
        "logs": {
            "agentserver": str(log_dir / "agentserver.log"),
            "gateway": str(log_dir / "gateway.log"),
        },
    }


async def terminate_local_if_present(handler: DBHandler, jiuwenclaw_id: str) -> None:
    row = await get_instance_row(handler, jiuwenclaw_id)
    if row is None:
        return
    prov = (row.data or {}).get("provision") if isinstance(row.data, dict) else None
    pids = prov.get("pids") if isinstance(prov, dict) else None
    if not isinstance(pids, dict):
        return
    import signal

    for pid in pids.values():
        if pid is None:
            continue
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (ProcessLookupError, ValueError, OSError):
            pass
    time.sleep(0.5)
    for pid in pids.values():
        if pid is None:
            continue
        try:
            os.kill(int(pid), 0)
            os.kill(int(pid), signal.SIGKILL)
        except (ProcessLookupError, ValueError, OSError):
            pass
