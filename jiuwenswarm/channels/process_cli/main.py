# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Entry point for the process-style JiuwenSwarm CLI."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import re
import sys


def _translate_argparse_error(message: str) -> str:
    invalid_choice = re.fullmatch(
        r"argument (?P<argument>\S+): invalid choice: (?P<value>.+) "
        r"\(choose from (?P<choices>.+)\)",
        message,
    )
    if invalid_choice:
        return (
            f"参数 {invalid_choice.group('argument')} 的值无效："
            f"{invalid_choice.group('value')}"
            f"（可选值：{invalid_choice.group('choices')}）"
        )

    required = re.fullmatch(r"the following arguments are required: (.+)", message)
    if required:
        return f"缺少必需参数：{required.group(1)}"

    unrecognized = re.fullmatch(r"unrecognized arguments: (.+)", message)
    if unrecognized:
        return f"无法识别的参数：{unrecognized.group(1)}"

    expected = re.fullmatch(r"argument (\S+): expected one argument", message)
    if expected:
        return f"参数 {expected.group(1)} 需要一个值"

    invalid_value = re.fullmatch(
        r"argument (\S+): invalid (\S+) value: (.+)",
        message,
    )
    if invalid_value:
        return f"参数 {invalid_value.group(1)} 的值无效：{invalid_value.group(3)}"

    return message


class ChineseArgumentParser(argparse.ArgumentParser):
    """Keep argparse behavior while presenting its fixed labels in Chinese."""

    def format_usage(self) -> str:
        return super().format_usage().replace("usage:", "用法：", 1)

    def format_help(self) -> str:
        return (
            super()
            .format_help()
            .replace("usage:", "用法：", 1)
            .replace("positional arguments:", "位置参数：", 1)
            .replace("options:", "选项：", 1)
            .replace(
                "show this help message and exit",
                "显示帮助信息并退出。",
                1,
            )
        )

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: 错误：{_translate_argparse_error(message)}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(
        prog="jiuwenswarm-process",
        description="在独立的本地 Runtime 进程中运行 JiuwenSwarm 指令。",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="要执行的指令；省略后进入交互式 CLI。",
    )
    parser.add_argument("--session", help="恢复已有的 Runtime 会话 ID。")
    parser.add_argument("--cwd", help="工作目录；默认为当前目录。")
    parser.add_argument("--project-dir", help="稳定的项目目录；默认与工作目录相同。")
    parser.add_argument(
        "--trusted-dir",
        action="append",
        default=[],
        help="可信目录；可重复指定多个目录。",
    )
    parser.add_argument("--mode", default="code.normal", help="Runtime 运行模式。")
    parser.add_argument(
        "--work-mode",
        choices=("code", "work"),
        default="code",
        help="Runtime 工作模式配置。",
    )
    parser.add_argument(
        "--output",
        choices=("human", "json", "jsonl"),
        default="human",
        help="Runtime 事件流的输出格式。",
    )
    parser.add_argument("--timeout", type=float, help="总执行超时时间，单位为秒。")
    parser.add_argument("--show-reasoning", action="store_true", help="显示思考过程。")
    parser.add_argument(
        "--show-tools", action="store_true", help="显示工具调用和结果。"
    )
    parser.add_argument(
        "--_interactive-worker",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--_session-result-file",
        help=argparse.SUPPRESS,
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.timeout is not None and args.timeout <= 0:
        parser.error("--timeout 必须大于零")
    if args.prompt is None and args.output != "human":
        parser.error("交互模式仅支持 --output human")
    try:
        if args.prompt is None:
            from jiuwenswarm.channels.process_cli.repl import run_repl

            code = asyncio.run(run_repl(args))
            sys.exit(code)

        # Some existing Runtime dependencies still log to stdout.  Keep the
        # CLI data stream clean by routing those diagnostics to stderr while
        # the renderer retains the original stdout handle.
        data_stdout = sys.stdout
        diagnostic_stderr = sys.stderr
        with contextlib.redirect_stdout(diagnostic_stderr):
            from jiuwenswarm.channels.process_cli.app import run

            code = asyncio.run(
                run(
                    args,
                    stdout=data_stdout,
                    stderr=(
                        data_stdout if args._interactive_worker else diagnostic_stderr
                    ),
                )
            )
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
