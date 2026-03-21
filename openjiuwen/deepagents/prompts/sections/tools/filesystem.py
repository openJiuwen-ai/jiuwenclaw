# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual descriptions and input params for filesystem tools."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

# ---------------------------------------------------------------------------
# Tool-level descriptions
# ---------------------------------------------------------------------------
READ_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": "读取文件内容。这是查看文件的主要工具。",
    "en": "Read file contents. This is the primary tool for viewing files.",
}

WRITE_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": "写入文件内容。如果文件已存在，将完全覆盖。",
    "en": "Write file contents. Overwrites the file if it already exists.",
}

EDIT_FILE_DESCRIPTION: Dict[str, str] = {
    "cn": "编辑文件的指定部分。使用字符串替换方式修改文件。",
    "en": "Edit a specific part of a file using string replacement.",
}

GLOB_DESCRIPTION: Dict[str, str] = {
    "cn": "使用 glob 模式查找文件。",
    "en": "Find files using glob patterns.",
}

LIST_DIR_DESCRIPTION: Dict[str, str] = {
    "cn": "列出目录内容。",
    "en": "List directory contents.",
}

GREP_DESCRIPTION: Dict[str, str] = {
    "cn": "在文件中搜索内容。支持正则表达式。",
    "en": "Search file contents. Supports regular expressions.",
}

# ---------------------------------------------------------------------------
# Parameter-level bilingual descriptions
# ---------------------------------------------------------------------------
READ_FILE_PARAMS: Dict[str, Dict[str, str]] = {
    "file_path": {"cn": "要读取的文件路径", "en": "Path of the file to read"},
    "offset": {"cn": "开始读取的行号（默认1）", "en": "Line number to start reading from (default 1)"},
    "limit": {"cn": "读取的最大行数", "en": "Maximum number of lines to read"},
}

WRITE_FILE_PARAMS: Dict[str, Dict[str, str]] = {
    "file_path": {"cn": "要写入的文件路径", "en": "Path of the file to write"},
    "content": {"cn": "要写入的内容", "en": "Content to write"},
}

EDIT_FILE_PARAMS: Dict[str, Dict[str, str]] = {
    "file_path": {"cn": "要编辑的文件路径", "en": "Path of the file to edit"},
    "old_string": {"cn": "要替换的原始字符串", "en": "Original string to replace"},
    "new_string": {"cn": "替换后的新字符串", "en": "New string to replace with"},
    "replace_all": {"cn": "是否替换所有匹配项", "en": "Whether to replace all occurrences"},
}

GLOB_PARAMS: Dict[str, Dict[str, str]] = {
    "pattern": {"cn": "glob 模式（如 *.py, **/*.js）", "en": "Glob pattern (e.g. *.py, **/*.js)"},
    "path": {"cn": "搜索根目录", "en": "Root directory to search"},
}

LIST_DIR_PARAMS: Dict[str, Dict[str, str]] = {
    "path": {"cn": "目录路径", "en": "Directory path"},
    "show_hidden": {"cn": "显示隐藏文件", "en": "Show hidden files"},
}

GREP_PARAMS: Dict[str, Dict[str, str]] = {
    "pattern": {"cn": "搜索模式（正则表达式）", "en": "Search pattern (regular expression)"},
    "path": {"cn": "搜索路径（文件或目录）", "en": "Search path (file or directory)"},
    "ignore_case": {"cn": "忽略大小写", "en": "Ignore case"},
}


# ---------------------------------------------------------------------------
# Builder functions
# ---------------------------------------------------------------------------
def _desc(params: Dict[str, Dict[str, str]], key: str, lang: str) -> str:
    return params[key].get(lang, params[key]["cn"])


def get_read_file_input_params(language: str = "cn") -> Dict[str, Any]:
    p = READ_FILE_PARAMS
    return {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": _desc(p, "file_path", language)},
            "offset": {"type": "integer", "description": _desc(p, "offset", language)},
            "limit": {"type": "integer", "description": _desc(p, "limit", language)},
        },
        "required": ["file_path"],
    }


def get_write_file_input_params(language: str = "cn") -> Dict[str, Any]:
    p = WRITE_FILE_PARAMS
    return {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": _desc(p, "file_path", language)},
            "content": {"type": "string", "description": _desc(p, "content", language)},
        },
        "required": ["file_path", "content"],
    }


def get_edit_file_input_params(language: str = "cn") -> Dict[str, Any]:
    p = EDIT_FILE_PARAMS
    return {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": _desc(p, "file_path", language)},
            "old_string": {"type": "string", "description": _desc(p, "old_string", language)},
            "new_string": {"type": "string", "description": _desc(p, "new_string", language)},
            "replace_all": {"type": "boolean", "description": _desc(p, "replace_all", language)},
        },
        "required": ["file_path", "old_string", "new_string"],
    }


def get_glob_input_params(language: str = "cn") -> Dict[str, Any]:
    p = GLOB_PARAMS
    return {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": _desc(p, "pattern", language)},
            "path": {"type": "string", "description": _desc(p, "path", language)},
        },
        "required": ["pattern"],
    }


def get_list_dir_input_params(language: str = "cn") -> Dict[str, Any]:
    p = LIST_DIR_PARAMS
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": _desc(p, "path", language)},
            "show_hidden": {"type": "boolean", "description": _desc(p, "show_hidden", language)},
        },
        "required": [],
    }


def get_grep_input_params(language: str = "cn") -> Dict[str, Any]:
    p = GREP_PARAMS
    return {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": _desc(p, "pattern", language)},
            "path": {"type": "string", "description": _desc(p, "path", language)},
            "ignore_case": {"type": "boolean", "description": _desc(p, "ignore_case", language)},
        },
        "required": ["pattern", "path"],
    }


class ReadFileMetadataProvider(ToolMetadataProvider):
    """ReadFile 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "read_file"

    def get_description(self, language: str = "cn") -> str:
        return READ_FILE_DESCRIPTION.get(
            language, READ_FILE_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_read_file_input_params(language)


class WriteFileMetadataProvider(ToolMetadataProvider):
    """WriteFile 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "write_file"

    def get_description(self, language: str = "cn") -> str:
        return WRITE_FILE_DESCRIPTION.get(
            language, WRITE_FILE_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_write_file_input_params(language)


class EditFileMetadataProvider(ToolMetadataProvider):
    """EditFile 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "edit_file"

    def get_description(self, language: str = "cn") -> str:
        return EDIT_FILE_DESCRIPTION.get(
            language, EDIT_FILE_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_edit_file_input_params(language)


class GlobMetadataProvider(ToolMetadataProvider):
    """Glob 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "glob"

    def get_description(self, language: str = "cn") -> str:
        return GLOB_DESCRIPTION.get(language, GLOB_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_glob_input_params(language)


class ListDirMetadataProvider(ToolMetadataProvider):
    """ListDir 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "list_files"

    def get_description(self, language: str = "cn") -> str:
        return LIST_DIR_DESCRIPTION.get(
            language, LIST_DIR_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_list_dir_input_params(language)


class GrepMetadataProvider(ToolMetadataProvider):
    """Grep 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "grep"

    def get_description(self, language: str = "cn") -> str:
        return GREP_DESCRIPTION.get(language, GREP_DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_grep_input_params(language)
