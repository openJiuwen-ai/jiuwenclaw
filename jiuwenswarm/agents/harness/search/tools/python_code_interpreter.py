import base64
import logging
import os
from datetime import timedelta
from typing import Optional

from opensandbox import Sandbox
from opensandbox.config import ConnectionConfig

from jiuwenswarm.agents.harness.search.tools.tool_registry import tool


class PythonCodeInterpreter:
    """基于 opensandbox 的 Python 代码解释器工具。

    在隔离沙箱环境中执行 Python 代码，返回执行结果（stdout、stderr、exit_code）。
    支持配置沙箱镜像、超时时间、连接参数等。
    """

    def __init__(
        self,
        domain: str = os.getenv("SANDBOX_DOMAIN", ""),
        protocol: str = "http",
        api_key: str = os.getenv("SANDBOX_API_KEY", ""),
        image: str = "23.10-trtllm-python-py3:zhihan_cuda122_verl_0508",
        request_timeout: int = 120,
        sandbox_timeout: int = 5,
        logger: Optional[logging.Logger] = None,
    ):
        """
        初始化 Python 代码解释器。

        Args:
            domain: opensandbox 服务端地址:端口，不要加 http:// 和路径
            protocol: 协议，本地部署默认 http
            api_key: 服务端认证密钥
            image: 沙箱镜像名称
            request_timeout: 单次请求超时时间（秒）
            sandbox_timeout: 沙箱生命周期超时（分钟）
            logger: 日志记录器
        """
        self.domain = domain
        self.protocol = protocol
        self.api_key = api_key
        self.image = image
        self.request_timeout = request_timeout
        self.sandbox_timeout = sandbox_timeout
        self.logger = logger or logging.getLogger(__name__)

        self._conn_config = ConnectionConfig(
            domain=self.domain,
            protocol=self.protocol,
            api_key=self.api_key,
            request_timeout=timedelta(seconds=self.request_timeout),
        )

    async def _create_sandbox(self) -> Sandbox:
        """创建并返回一个沙箱实例。"""
        sandbox = await Sandbox.create(
            self.image,
            entrypoint=["sleep", "infinity"],
            timeout=timedelta(minutes=self.sandbox_timeout),
            connection_config=self._conn_config,
        )
        return sandbox

    @tool(
        name="python_code_interpreter",
        description=(
            "Execute Python code in an isolated sandbox environment and return the output. "
            "This tool allows running arbitrary Python code safely. "
            "The code runs in a sandbox with common scientific computing libraries available. "
            "Use this tool when you need to perform calculations, data analysis, "
            "or test Python code snippets."
        ),
        timeout=120.0,
    )
    async def run_code(self, code: str) -> str:
        """
        Execute Python code in a sandbox and return the result.

        Args:
            code: Python code string to execute. Supports multiline code, imports, function definitions, etc.

        Returns:
            Formatted string containing execution results, including stdout, stderr, and exit code.
        """
        if not code or not isinstance(code, str):
            return (
                "[PythonCodeInterpreter Error] code parameter must be a non-empty string, but received an empty string"
            )

        sandbox = None
        try:
            sandbox = await self._create_sandbox()

            # Use base64 encoding to pass code, avoiding quote escaping issues
            encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
            result = await sandbox.commands.run(
                f'python -c "import base64; exec(base64.b64decode(\'{encoded}\'))"'
            )
            self.logger.debug(f"Code executed result: {result}")
            stdout_text = ""
            stderr_text = ""
            if result.logs.stdout:
                stdout_text = "".join(entry.text for entry in result.logs.stdout)
            if result.logs.stderr:
                stderr_text = "".join(entry.text for entry in result.logs.stderr)

            exit_code = result.exit_code

            output_parts = []
            if stdout_text:
                output_parts.append(f"[stdout]\n{stdout_text.rstrip()}")
            if stderr_text:
                output_parts.append(f"[stderr]\n{stderr_text.rstrip()}")
            if exit_code != 0:
                output_parts.append(f"[exit_code] {exit_code}")

            if not output_parts:
                return "[PythonCodeInterpreter] Code execution completed with no output."

            return "\n\n".join(output_parts)

        except Exception as e:
            self.logger.error(f"python_code_interpreter execution failed: {e}")
            return f"[PythonCodeInterpreter Error] Code execution exception: {e}"
        finally:
            if sandbox is not None:
                try:
                    await sandbox.close()
                except Exception as close_err:
                    self.logger.warning(f"Error closing sandbox: {close_err}")