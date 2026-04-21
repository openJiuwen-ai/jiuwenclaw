"""CLI：将运行时数据初始化到用户数据根目录（与 ``get_user_workspace_dir()`` 一致）。

默认根目录为 ``~/.jiuwenclaw``；若进程环境中已设置 ``JIUWENCLAW_DATA_DIR``（须为可用绝对路径，
且应在启动本脚本前注入，见 ``jiuwenclaw.utils`` 中的 ``USER_WORKSPACE_DIR``），则初始化到该路径下。

无论是通过 pip/whl 安装，还是在源码目录里直接运行：
- 运行本脚本会先询问语言偏好（zh/en），写入 config 的 preferred_language，
  并将对应语言的 PRINCIPLE/TONE/HEARTBEAT 模板复制为 ``<用户数据根>/agent/home/`` 下 PRINCIPLE.md、TONE.md、HEARTBEAT.md；
- 同时复制 config.yaml、将 ``.env.template`` 复制为 ``<用户数据根>/config/.env``，以及 agent 其余模板等到 ``<用户数据根>``。
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
