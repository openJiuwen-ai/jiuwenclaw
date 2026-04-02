"""CLI for initializing runtime data into ~/.jiuwenclaw.

无论是通过 pip/whl 安装，还是在源码目录里直接运行：
- 运行本脚本会先询问语言偏好（zh/en），写入 config 的 preferred_language；
- 同时复制 config.yaml、.env.template、agent 模板等到 ~/.jiuwenclaw；
- PRINCIPLE.md、TONE.md、HEARTBEAT.md 已被 SOUL.md 和新的心跳机制替代，
  不再由 JiuwenClaw 复制。这些旧版模板仅保留在 resources 中供参考。
"""

from __future__ import annotations

import logging
import sys

from jiuwenclaw.utils import init_user_workspace


def run_init() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    target = init_user_workspace(overwrite=True)
    if target == "cancelled":
        return 1
    print(f"[jiuwenclaw-init] initialized: {target}")
    return 0


def main() -> int:
    return run_init()

if __name__ == "__main__":
    sys.exit(main())
