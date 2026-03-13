"""CLI for initializing runtime data into ~/.jiuwenclaw.

无论是通过 pip/whl 安装，还是在源码目录里直接运行：
- 都可以通过运行本脚本，把内置的 config.yaml / .env.template
  以及 workspace 模板复制到用户主目录 ~/.jiuwenclaw 下，
  之后运行 app.py 时会从该目录读取和写入配置。
"""

from __future__ import annotations

import logging

from jiuwenclaw.utils import init_user_workspace


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    target = init_user_workspace(overwrite=True)
    print(f"[jiuwenclaw-init] initialized: {target}")


if __name__ == "__main__":
    main()
