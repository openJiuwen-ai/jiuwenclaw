#!/usr/bin/env python3
"""华为云 MaaS 模型配置写入脚本。

将华为云 MaaS 的 API Base / API Key 写入 .env（使用 HUAWEI_MAAS_ 前缀隔离），
并将模型条目以"追加并设为默认"的语义写入 config.yaml 的 models.defaults：

- 已存在同 alias 的条目就地更新；
- 新条目追加到列表；
- 通过 --default-alias 指定的条目会被置 is_default=true 并移到列表首位；
- 未涉及的已有条目保留，其 is_default 统一置 false（与前端 ConfigPanel
  的 handleSetActive 语义一致）。

用法示例::

    python config_writer.py add \\
        --api-base https://api.modelarts-maas.com/openai/v1 \\
        --api-key ABh8... \\
        --model name=openPangu-2.0-Pro,alias=huawei-pangu,default \\
        --model name=embedding-1,alias=huawei-embedding
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

# 当以脚本方式直接运行时，sys.path[0] 是脚本所在目录。优先把源码根
# 放到 sys.path 最前，避免命中 site-packages 中可能存在的旧版 jiuwenswarm。
def _ensure_jiuwenswarm_on_path() -> None:
    # scripts -> huawei-cloud-maas-setup -> skills -> workspace -> agent ->
    # resources -> jiuwenswarm -> <repo root>
    root = Path(__file__).resolve().parents[7]
    root_str = str(root)
    if root_str in sys.path:
        sys.path.remove(root_str)
    sys.path.insert(0, root_str)


_ensure_jiuwenswarm_on_path()

from jiuwenswarm.common.config import update_config  # noqa: E402
from jiuwenswarm.common.utils import get_env_file  # noqa: E402


ENV_PREFIX = "HUAWEI_MAAS_"
DEFAULT_PROVIDER = "openai"
DEFAULT_TIMEOUT = 360


def _escape_env_value(value: str) -> str:
    """转义 .env 双引号字符串内的特殊字符。

    - 反斜杠和双引号必须转义；
    - ``$`` 转义为 ``\\$``，避免被 shell/dotenv 解释为变量引用；
    - 反引号转义，避免命令替换；
    - 换行符替换为字面量 ``\\n``。
    """
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace('"', '\\"')
    escaped = escaped.replace("$", "\\$")
    escaped = escaped.replace("`", "\\`")
    escaped = escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
    return f'"{escaped}"'


def _is_env_key(line: str, key: str) -> bool:
    """判断一行是否为指定 key 的赋值（忽略前导 export、注释行除外）。"""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].lstrip()
    if "=" not in stripped:
        return False
    lhs = stripped.split("=", 1)[0].strip()
    return lhs == key


def safe_update_env(updates: dict[str, str], env_path: Path | None = None) -> Path:
    """原子地更新 .env 文件中的多个键值。

    - 已存在的 key 就地替换（保留其前后注释和其他行）；
    - 不存在的 key 追加到文件末尾；
    - 注释行不动，避免误匹配。

    返回写入的 .env 路径。
    """
    env_path = env_path or get_env_file()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    new_lines: list[str] = []
    for line in lines:
        matched_key = next((k for k in remaining if _is_env_key(line, k)), None)
        if matched_key is not None:
            new_lines.append(f"{matched_key}={_escape_env_value(remaining.pop(matched_key))}")
        else:
            new_lines.append(line)

    for key, value in remaining.items():
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(f"{key}={_escape_env_value(value)}")

    content = "\n".join(new_lines)
    if not content.endswith("\n"):
        content += "\n"

    # 原子写入：同目录临时文件 + os.replace
    fd, tmp_name = tempfile.mkstemp(prefix=env_path.name + ".", suffix=".tmp", dir=str(env_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp_name, env_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    return env_path


def _parse_model_spec(spec: str) -> dict[str, Any]:
    """解析 ``--model name=X,alias=Y`` 格式的参数。"""
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    data: dict[str, Any] = {}
    for part in parts:
        if "=" in part:
            k, v = part.split("=", 1)
            data[k.strip()] = v.strip()
        else:
            # 裸字段视作 model_name
            data["name"] = part
    if "name" not in data or not data["name"]:
        raise ValueError(f"--model 参数缺少 name: {spec!r}")
    data.setdefault("alias", data["name"])
    return data


def _build_model_entry(
    model_name: str,
    alias: str,
    provider: str,
    temperature: float = 0.95,
) -> dict[str, Any]:
    return {
        "alias": alias,
        "model_client_config": {
            "api_base": f"${{{ENV_PREFIX}API_BASE}}",
            "api_key": f"${{{ENV_PREFIX}API_KEY}}",
            "model_name": model_name,
            "client_provider": provider,
            "timeout": DEFAULT_TIMEOUT,
            "verify_ssl": True,
            "custom_headers": {},
        },
        "model_config_obj": {"temperature": temperature},
    }


def add_models(
    api_base: str,
    api_key: str,
    models: list[dict[str, Any]],
    provider: str = DEFAULT_PROVIDER,
) -> dict[str, Any]:
    """写入 .env + config.yaml，追加模型但不设默认。

    - .env 中使用 HUAWEI_MAAS_ 前缀
    - config.yaml 中按 alias 追加/更新条目，**不修改任何 is_default**
    """
    # 1. 写入 .env
    env_path = safe_update_env(
        {
            f"{ENV_PREFIX}API_BASE": api_base,
            f"{ENV_PREFIX}API_KEY": api_key,
        }
    )

    # 2. 构造模型条目
    entries = [
        _build_model_entry(
            model_name=m["name"],
            alias=m["alias"],
            provider=provider,
        )
        for m in models
    ]

    # 3. 写入 config.yaml：按 alias 追加/更新，不触碰 is_default
    def _mutate(data: dict[str, Any]) -> dict[str, Any]:
        models_cfg = data.setdefault("models", {})
        if not isinstance(models_cfg, dict):
            models_cfg = {}
            data["models"] = models_cfg
        existing = models_cfg.get("defaults")
        if not isinstance(existing, list):
            existing = []
        merged: list[Any] = list(existing)

        for entry in entries:
            alias = entry.get("alias", "")
            replaced = False
            for i, old in enumerate(merged):
                if isinstance(old, dict) and old.get("alias", "") == alias:
                    # 保留旧的 is_default 值
                    old_default = old.get("is_default", False)
                    merged[i] = entry
                    entry["is_default"] = old_default
                    replaced = True
                    break
            if not replaced:
                merged.append(entry)

        models_cfg["defaults"] = merged
        if "default" in models_cfg:
            del models_cfg["default"]
        return data

    update_config(_mutate)

    written_aliases = [m["alias"] for m in models]
    return {
        "env_path": str(env_path),
        "written_aliases": written_aliases,
        "api_base": api_base,
        "models": [m["name"] for m in models],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="华为云 MaaS 模型配置写入工具（追加，不设默认）。"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="追加模型配置")
    p_add.add_argument("--api-base", required=True, help="华为云 MaaS OpenAI 兼容 API base")
    p_add.add_argument("--api-key", required=True, help="华为云 MaaS API Key")
    p_add.add_argument(
        "--model",
        action="append",
        required=True,
        help=(
            "模型定义，格式 name=模型名[,alias=别名]，"
            "可多次指定。alias 缺省等于 name。"
        ),
    )
    p_add.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        help=f"client_provider，默认 {DEFAULT_PROVIDER}",
    )
    p_add.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出结果（供 skill 解析）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "add":
        try:
            models = [_parse_model_spec(s) for s in (args.model or [])]
        except ValueError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2

        # 校验 alias 不重复
        aliases = [m["alias"] for m in models]
        dup = {a for a in aliases if aliases.count(a) > 1}
        if dup:
            print(f"[FAIL] --model 中存在重复 alias: {sorted(dup)}", file=sys.stderr)
            return 2

        result = add_models(
            api_base=args.api_base,
            api_key=args.api_key,
            models=models,
            provider=args.provider,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            masked_key = args.api_key[:4] + "****" + args.api_key[-4:] if len(args.api_key) > 8 else "****"
            print("[OK] 配置写入成功：")
            print(f"  .env:       {result['env_path']}")
            print(f"  API Base:   {result['api_base']}")
            print(f"  API Key:    {masked_key}")
            print(f"  新增模型:   {', '.join(result['models'])}")
            print(f"  别名:       {', '.join(result['written_aliases'])}")
            print("  默认模型:   未调整（仅追加）")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
