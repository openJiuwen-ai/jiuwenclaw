# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Root ``jiuwenswarm`` CLI entry point."""

from __future__ import annotations

import logging
import os
import sys

from jiuwenswarm.dotenv_early import parse_dotenv_early

parse_dotenv_early("jiuwenswarm")

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)


def main() -> None:
    from jiuwenswarm.cli.chat import build_parser as build_chat_parser
    from jiuwenswarm.cli.chat import run_chat

    if os.environ.get("JIUWENSWARM_SKIP_DOTENV", "").strip() != "1":
        try:
            from dotenv import load_dotenv
            from jiuwenswarm.common.utils import get_env_file

            load_dotenv(dotenv_path=get_env_file(), override=False)
        except ImportError:
            pass

    argv = sys.argv[1:]
    if argv and argv[0] == "chat":
        argv = argv[1:]

    chat_parser = build_chat_parser()
    chat_args = chat_parser.parse_args(argv)
    sys.exit(run_chat(chat_args))


if __name__ == "__main__":
    main()
