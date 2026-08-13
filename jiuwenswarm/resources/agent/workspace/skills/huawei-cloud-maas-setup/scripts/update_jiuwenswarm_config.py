#!/usr/bin/env python3
"""将华为云 MaaS 凭证写入 jiuwenswarm 配置。

用法:
    python update_jiuwenswarm_config.py \
        --api-base "https://api.modelarts-maas.com/openai/v1" \
        --api-key "xxx-xxx-xxx" \
        --model-name "openpangu-2.0-pro" \
        --model-provider "openai"

此脚本会:
1. 更新 ~/.jiuwenswarm/config/.env 中的 API_BASE/API_KEY/MODEL_NAME/MODEL_PROVIDER
2. 更新 ~/.jiuwenswarm/config/config.yaml 中的 models.defaults 列表
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def update_env_file(env_path: Path, updates: dict[str, str]) -> None:
    """更新 .env 文件中的环境变量（仅覆盖或追加对应 KEY=value 行）。"""
    lines: list[str] = []
    if env_path.is_file():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        found = False
        for env_key, value in updates.items():
            if stripped.startswith(env_key + "="):
                new_lines.append(f'{env_key}="{value}"\n' if value else f"{env_key}=\n")
                found = True
                break
        if not found:
            new_lines.append(line)

    for env_key, value in updates.items():
        if not any(s.strip().startswith(env_key + "=") for s in new_lines):
            new_lines.append(f'{env_key}="{value}"\n' if value else f"{env_key}=\n")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="将华为云 MaaS 凭证写入 jiuwenswarm 配置")
    parser.add_argument("--api-base", required=True, help="API 基础地址")
    parser.add_argument("--api-key", required=True, help="API Key")
    parser.add_argument("--model-name", required=True, help="模型名称")
    parser.add_argument("--model-provider", default="openai", help="模型提供方（默认 openai）")
    args = parser.parse_args()

    # 延迟导入，确保脚本可以独立运行
    try:
        from jiuwenswarm.common.config import update_default_models_in_config
        from jiuwenswarm.common.utils import get_env_file
    except ImportError:
        print("错误: 无法导入 jiuwenswarm 模块，请确保在 jiuwenswarm 环境中运行", file=sys.stderr)
        return 1

    # 1. 更新 .env 文件
    env_path = get_env_file()
    env_updates = {
        "API_BASE": args.api_base,
        "API_KEY": args.api_key,
        "MODEL_NAME": args.model_name,
        "MODEL_PROVIDER": args.model_provider,
    }
    update_env_file(env_path, env_updates)
    print(f"[OK] 已更新 .env: {list(env_updates.keys())}")

    # 2. 更新 config.yaml 中的 models.defaults
    models_list = [
        {
            "model_client_config": {
                "api_base": "${API_BASE}",
                "api_key": "${API_KEY}",
                "model_name": "${MODEL_NAME}",
                "client_provider": "${MODEL_PROVIDER}",
                "timeout": 360,
                "verify_ssl": True,
                "custom_headers": {},
            },
            "model_config_obj": {"temperature": 0.95},
            "is_default": True,
        }
    ]
    update_default_models_in_config(models_list)
    print("[OK] 已更新 config.yaml models.defaults")

    # 3. 同步环境变量到当前进程（供后续验证使用）
    import os
    for key, value in env_updates.items():
        os.environ[key] = value

    # 4. 打印配置摘要（API Key 脱敏）
    masked_key = args.api_key[:4] + "****" + args.api_key[-4:] if len(args.api_key) > 8 else "****"
    print("\n配置摘要:")
    print(f"  API 地址: {args.api_base}")
    print(f"  API Key:  {masked_key}")
    print(f"  模型名称: {args.model_name}")
    print(f"  接入方式: {args.model_provider}")
    print("\n配置写入完成。请运行 validate_config.py 验证连通性。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
