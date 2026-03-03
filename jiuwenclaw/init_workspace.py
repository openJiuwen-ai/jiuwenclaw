"""CLI for initializing package data into ~/.jiuwenclaw."""

from __future__ import annotations

import logging

from jiuwenclaw.paths import init_user_workspace, is_package_installation


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not is_package_installation():
        raise SystemExit("jiuwenclaw-init 仅支持 pip/whl 安装模式")

    target = init_user_workspace(overwrite=True)
    print(f"[jiuwenclaw-init] initialized: {target}")


if __name__ == "__main__":
    main()
