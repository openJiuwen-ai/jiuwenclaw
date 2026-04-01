from __future__ import annotations

import argparse
import logging
import sys

from jiuwenclaw.channel.acp_channel import AcpChannel, AcpChannelConfig
from jiuwenclaw.channel.base import RobotMessageRouter

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jiuwenclaw-cli",
        description="JiuwenClaw CLI 入口（子命令分发）。",
    )
    subparsers = parser.add_subparsers(dest="command")

    acp_parser = subparsers.add_parser("acp", help="ACP 命令入口。")
    acp_parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="ACP 子命令参数（第一阶段全部本地拦截）。",
    )
    return parser


def _run_acp(args: argparse.Namespace) -> int:
    argv = list(args.args or [])
    logger.info("[CLI] acp 子命令收到参数: %s", argv)
    channel = AcpChannel(AcpChannelConfig(), RobotMessageRouter())
    output = channel.intercept_cli_output(argv)
    print(output)
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "acp":
        raise SystemExit(_run_acp(args))

    parser.print_help(sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
