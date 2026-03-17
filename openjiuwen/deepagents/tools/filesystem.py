# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import sys
import shutil
from typing import Dict, Any, AsyncIterator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.deepagents.tools.base_tool import ToolOutput


class ReadFileTool(Tool):

    def __init__(self, operation: SysOperation):
        super().__init__(
            ToolCard(id="ReadFileTool", name="read_file", description="读取文件内容。这是查看文件的主要工具。"))
        self.operation = operation
        self.card.input_params = {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "要读取的文件路径"},
                "offset": {"type": "integer", "description": "开始读取的行号（默认1）"},
                "limit": {"type": "integer", "description": "读取的最大行数"},
            },
            "required": ["file_path"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        path = inputs.get("file_path")
        offset = inputs.get("offset", 1)
        limit = inputs.get("limit")

        line_range = None
        if offset > 1 or limit is not None:
            start = max(1, offset)
            end = (start + limit - 1) if limit is not None else sys.maxsize
            line_range = (start, end)

        res = await self.operation.fs().read_file(path, line_range=line_range)
        if res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=res.message)

        content = res.data.content
        if isinstance(content, bytes):
            content = content.decode('utf-8', errors='replace')

        return ToolOutput(
            success=True,
            data={
                "content": content,
                "file_path": path,
                "line_count": len(content.splitlines()) if content else 0
            }
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass


class WriteFileTool(Tool):

    def __init__(self, operation: SysOperation):
        super().__init__(
            ToolCard(id="WriteFileTool", name="write_file", description="写入文件内容。如果文件已存在，将完全覆盖。"))
        self.operation = operation
        self.card.input_params = {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "要写入的文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
            },
            "required": ["file_path", "content"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        path = inputs.get("file_path")
        content = inputs.get("content", "")

        res = await self.operation.fs().write_file(path, content, prepend_newline=False, create_if_not_exist=True)
        if res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=res.message)

        return ToolOutput(
            success=True,
            data={
                "file_path": path,
                "bytes_written": len(content.encode('utf-8'))
            }
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass


class EditFileTool(Tool):

    def __init__(self, operation: SysOperation):
        super().__init__(
            ToolCard(id="EditFileTool", name="edit_file", description="编辑文件的指定部分。使用字符串替换方式修改文件。"))
        self.operation = operation
        self.card.input_params = {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "要编辑的文件路径"},
                "old_string": {"type": "string", "description": "要替换的原始字符串"},
                "new_string": {"type": "string", "description": "替换后的新字符串"},
                "replace_all": {"type": "boolean", "description": "是否替换所有匹配项"},
            },
            "required": ["file_path", "old_string", "new_string"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        path = inputs.get("file_path")
        old_str = inputs.get("old_string")
        new_str = inputs.get("new_string")
        replace_all = inputs.get("replace_all", False)

        read_res = await self.operation.fs().read_file(path)
        if read_res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=f"读取文件失败: {read_res.message}")

        content = read_res.data.content

        if old_str not in content:
            return ToolOutput(success=False, error=f"未找到要替换的字符串: '{old_str}'")

        if replace_all:
            new_content = content.replace(old_str, new_str)
            count = content.count(old_str)
        else:
            new_content = content.replace(old_str, new_str, 1)
            count = 1

        write_res = await self.operation.fs().write_file(path, new_content, prepend_newline=False)
        if write_res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=f"写入文件失败: {write_res.message}")

        return ToolOutput(
            success=True,
            data={
                "file_path": path,
                "replacements": count
            }
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass


class GlobTool(Tool):

    def __init__(self, operation: SysOperation):
        super().__init__(ToolCard(id="GlobTool", name="glob", description="使用 glob 模式查找文件。"))
        self.operation = operation
        self.card.input_params = {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式（如 *.py, **/*.js）"},
                "path": {"type": "string", "description": "搜索根目录"},
            },
            "required": ["pattern"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        pattern = inputs.get("pattern")
        path = inputs.get("path", ".")

        res = await self.operation.fs().search_files(path, pattern)
        if res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=res.message)

        return ToolOutput(
            success=True,
            data={
                "matching_files": [item.path for item in res.data.matching_files] if res.data else [],
                "count": len(res.data.matching_files) if res.data else 0
            }
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass


class ListDirTool(Tool):

    def __init__(self, operation: SysOperation):
        super().__init__(ToolCard(id="ListDirTool", name="list_files", description="列出目录内容。"))
        self.operation = operation
        self.card.input_params = {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径"},
                "show_hidden": {"type": "boolean", "description": "显示隐藏文件"},
            },
            "required": []
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        path = inputs.get("path", ".")
        show_hidden = inputs.get("show_hidden", False)

        files_res = await self.operation.fs().list_files(path)
        dirs_res = await self.operation.fs().list_directories(path)

        if files_res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=f"列出文件失败: {files_res.message}")
        if dirs_res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=f"列出目录失败: {dirs_res.message}")

        files = [item.name for item in files_res.data.list_items] if files_res.data else []
        dirs = [item.name for item in dirs_res.data.list_items] if dirs_res.data else []

        if not show_hidden:
            files = [f for f in files if not f.startswith('.')]
            dirs = [d for d in dirs if not d.startswith('.')]

        files.sort()
        dirs.sort()

        return ToolOutput(
            success=True,
            data={
                "files": files,
                "dirs": dirs
            }
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass


class GrepTool(Tool):

    def __init__(self, operation: SysOperation):
        super().__init__(ToolCard(id="GrepTool", name="grep", description="在文件中搜索内容。支持正则表达式。"))
        self.operation = operation
        self.card.input_params = {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "搜索模式（正则表达式）"},
                "path": {"type": "string", "description": "搜索路径（文件或目录）"},
                "ignore_case": {"type": "boolean", "description": "忽略大小写"},
            },
            "required": ["pattern", "path"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        pattern = inputs.get("pattern")
        path = inputs.get("path")
        ignore_case = inputs.get("ignore_case", False)

        if shutil.which("rg"):
            cmd = f"rg --line-number --color=never {'-i' if ignore_case else ''} \"{pattern}\" \"{path}\""
            res = await self.operation.shell().execute_cmd(cmd, timeout=30)
        else:
            cmd = f"grep -rn {'-i' if ignore_case else ''} \"{pattern}\" \"{path}\""
            res = await self.operation.shell().execute_cmd(cmd, timeout=30)

        if res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=res.message)

        stdout = res.data.stdout if res.data else ""
        stderr = res.data.stderr if res.data else ""
        exit_code = res.data.exit_code if res.data else -1
        success = (exit_code in [0, 1])

        return ToolOutput(
            success=success,
            data={
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
                "count": len([line for line in stdout.splitlines() if line.strip()])
            },
            error=stderr if not success else None
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass
