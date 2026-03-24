# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual descriptions and input params for Todo tools."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

# ---------------------------------------------------------------------------
# Todo-create description
# ---------------------------------------------------------------------------
TODO_CREATE_DESCRIPTION_CN = """
用于给智能体创建待办事项的工具
核心用途：创建新的待办事项
支持格式：用tasks参数传递带分隔符'；'的任务集：
    {
        "tasks": "设计用户界面；实现接口集成；添加单元测试"
    }
核心规则：
    - 同一时间待办任务中只能有一个处于 'in_progress' 状态
    - 提供的任务集就是任务实际执行的顺序，第一个任务会自动设为'in_progress'，其余任务默认为 'pending'
"""

TODO_CREATE_DESCRIPTION_EN = """
Tool for creating todo items for the agent.
Core purpose: Create new todo items.
Supported format: Pass a delimited task set via the tasks parameter using ';' as delimiter:
    {
        "tasks": "Design user interface;Implement API integration;Add unit tests"
    }
Core rules:
    - Only one todo item can be 'in_progress' at a time
    - The provided task set defines the actual execution order; the first task is automatically set to 'in_progress', the rest default to 'pending'
"""

TODO_CREATE_DESCRIPTION: Dict[str, str] = {
    "cn": TODO_CREATE_DESCRIPTION_CN,
    "en": TODO_CREATE_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Todo-list description
# ---------------------------------------------------------------------------
TODO_LIST_DESCRIPTION_CN = """
检索并显示所有待办事项
"""

TODO_LIST_DESCRIPTION_EN = """
Retrieve and display all todo items
"""

TODO_LIST_DESCRIPTION: Dict[str, str] = {
    "cn": TODO_LIST_DESCRIPTION_CN,
    "en": TODO_LIST_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Todo-modify description
# ---------------------------------------------------------------------------
TODO_MODIFY_DESCRIPTION_CN = """
用于给智能体的待办事项修改的工具
核心用途：修改待办事项，执行动作包含：更新（update）、删除（delete）、取消（cancel）、追加（append）、在其后插入（insert_after）、在其前插入（insert_before）
重要说明：
    - 本工具支持通过 'action' 与对应字段组合来修改待办事项
    - 若需重新规划待办事项列表，请调用 todo_create 工具
    - action 字段决定操作类型及对应的必填字段
action 支持的操作类型：
update：修改现有待办任务属性（任务 id 不可修改）：
    {
        "action": "update",
        "todos": [
            {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "content": "更新后的任务内容",
                "activeForm": "执行更新后的任务",
                "status": "in_progress"
            }
        ]
    }
delete：根据任务 id 删除指定待办任务：
    {
        "action": "delete",
        "ids": [
            "123e4567-e89b-12d3-a456-426614174000",
            "890e4567-e89b-12d3-a456-426614174001"
        ]
    }
cancel：根据任务 id 取消指定待办任务：
    {
        "action": "cancel",
        "ids": [
            "123e4567-e89b-12d3-a456-426614174000",
            "890e4567-e89b-12d3-a456-426614174001"
        ]
    }
append：在待办事项列表末尾新增任务（按传入顺序）：
    {
        "action": "append",
        "todos": [
            {
                "id": "456e4567-e89b-12d3-a456-426614174002",
                "content": "新任务内容",
                "activeForm": "执行新任务",
                "status": "pending"
            }
        ]
    }
insert_after：在指定任务 id 之后插入任务（按传入顺序）：
    {
        "action": "insert_after",
        "todo_data": [
            "123e4567-e89b-12d3-a456-426614174000",
            [
                {
                    "id": "789e4567-e89b-12d3-a456-426614174003",
                    "content": "插入的任务内容",
                    "activeForm": "执行插入的任务",
                    "status": "pending"
                }
            ]
        ]
    }
insert_before：在指定任务 id 之前插入任务（按传入顺序）：
    {
        "action": "insert_before",
        "todo_data": [
            "123e4567-e89b-12d3-a456-426614174000",
            [
                {
                    "id": "012e4567-e89b-12d3-a456-426614174004",
                    "content": "插入的任务内容",
                    "activeForm": "执行插入的任务",
                    "status": "pending"
                }
            ]
        ]
    }
核心规则：
    - 同一时间只能有一个任务处于 'in_progress' 状态
    - 'update' 操作：id 字段不可修改
    - 'delete' 操作：id对应的目标任务从待办事项中删除
    - 'cancel' 操作： id对应的目标任务的状态将被设置成 'cancel', 该任务之后将被忽略，不会被执行
    - 'insert_after' 操作：id对应的目标任务状态必须为 'in_progress' 或 'pending'
    - 'insert_before' 操作：id对应的目标任务状态必须为 'pending'
"""

TODO_MODIFY_DESCRIPTION_EN = """
Tool for modifying the agent's todo items.
Core purpose: Modify todo items. Supported actions: update, delete, cancel, append, insert_after, insert_before.
Important notes:
    - This tool supports modifying todo items by combining 'action' with corresponding fields
    - To re-plan the todo list, call the todo_create tool
    - The action field determines the operation type and required fields
Supported action types:
update: Modify existing todo item attributes (task id cannot be changed):
    {
        "action": "update",
        "todos": [
            {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "content": "Updated task content",
                "activeForm": "Executing updated task",
                "status": "in_progress"
            }
        ]
    }
delete: Delete specified todo items by task id:
    {
        "action": "delete",
        "ids": [
            "123e4567-e89b-12d3-a456-426614174000",
            "890e4567-e89b-12d3-a456-426614174001"
        ]
    }
cancel: Cancel specified todo items by task id:
    {
        "action": "cancel",
        "ids": [
            "123e4567-e89b-12d3-a456-426614174000",
            "890e4567-e89b-12d3-a456-426614174001"
        ]
    }
append: Add new tasks at the end of the todo list (in order):
    {
        "action": "append",
        "todos": [
            {
                "id": "456e4567-e89b-12d3-a456-426614174002",
                "content": "New task content",
                "activeForm": "Executing new task",
                "status": "pending"
            }
        ]
    }
insert_after: Insert tasks after the specified task id (in order):
    {
        "action": "insert_after",
        "todo_data": [
            "123e4567-e89b-12d3-a456-426614174000",
            [
                {
                    "id": "789e4567-e89b-12d3-a456-426614174003",
                    "content": "Inserted task content",
                    "activeForm": "Executing inserted task",
                    "status": "pending"
                }
            ]
        ]
    }
insert_before: Insert tasks before the specified task id (in order):
    {
        "action": "insert_before",
        "todo_data": [
            "123e4567-e89b-12d3-a456-426614174000",
            [
                {
                    "id": "012e4567-e89b-12d3-a456-426614174004",
                    "content": "Inserted task content",
                    "activeForm": "Executing inserted task",
                    "status": "pending"
                }
            ]
        ]
    }
Core rules:
    - Only one task can be 'in_progress' at a time
    - 'update' action: id field cannot be modified
    - 'delete' action: delete target tasks corresponding to the ID from the to-do list
    - 'cancel' action: target tasks will be set to 'cancel', and tasks will be ignored and will not be executed.
    - 'insert_after' action: target task status must be 'in_progress' or 'pending'
    - 'insert_before' action: target task status must be 'pending'
"""

TODO_MODIFY_DESCRIPTION: Dict[str, str] = {
    "cn": TODO_MODIFY_DESCRIPTION_CN,
    "en": TODO_MODIFY_DESCRIPTION_EN,
}

# ---------------------------------------------------------------------------
# Parameter-level bilingual descriptions
# ---------------------------------------------------------------------------
TODO_CREATE_PARAMS: Dict[str, Dict[str, str]] = {
    "tasks": {
        "cn": "简化任务列表（用换行符/分号分隔）。示例：'创建登录表单；实现表单验证；添加错误处理'",
        "en": ("Simplified task list (delimited by newlines/semicolons). "
               "Example: 'Create login form;Implement form validation;Add error handling'"),
    },
}

_TODO_ITEM_PARAMS: Dict[str, Dict[str, str]] = {
    "id": {"cn": "任务唯一标识符", "en": "Unique task identifier"},
    "content": {"cn": "任务详细描述", "en": "Detailed task description"},
    "activeForm": {"cn": "任务进行时描述", "en": "Present-tense task description"},
    "status": {"cn": "任务状态", "en": "Task status"},
}

TODO_MODIFY_PARAMS: Dict[str, Dict[str, str]] = {
    "action": {"cn": "要执行的操作类型", "en": "Operation type to perform"},
    "ids": {"cn": "要操作的任务 ID 列表", "en": "List of task IDs to operate on"},
    "ids_item": {"cn": "任务唯一标识符", "en": "Unique task identifier"},
    "todos": {"cn": "根据 action 字段处理的待办事项数组", "en": "Array of todo items to process based on the action field"},
    "todo_data": {"cn": "用于 insert_after/insert_before 操作的数组", "en": "Array for insert_after/insert_before actions"},
    "todo_data_target_id": {"cn": "目标任务 ID", "en": "Target task ID"},
    "todo_data_items": {"cn": "要插入的待办事项列表", "en": "List of todo objects to insert"},
}


def _todo_item_properties(language: str = "cn") -> Dict[str, Any]:
    """Return the shared todo item sub-schema properties."""
    p = _TODO_ITEM_PARAMS

    def _d(key: str) -> str:
        return p[key].get(language, p[key]["cn"])

    return {
        "id": {"type": "string", "description": _d("id")},
        "content": {"type": "string", "description": _d("content")},
        "activeForm": {"type": "string", "description": _d("activeForm")},
        "status": {
            "type": "string",
            "description": _d("status"),
            "enum": ["pending", "in_progress", "completed", "cancelled"],
        },
    }


def get_todo_create_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for todo_write tool input_params."""
    p = TODO_CREATE_PARAMS
    return {
        "type": "object",
        "properties": {
            "tasks": {"type": "string", "description": p["tasks"].get(language, p["tasks"]["cn"])},
        },
        "required": ["tasks"],
    }


def get_todo_list_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for todo_read tool input_params."""
    return {
        "type": "object",
        "properties": {},
        "required": [],
    }


def get_todo_modify_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for todo_modify tool input_params."""
    p = TODO_MODIFY_PARAMS

    def _d(key: str) -> str:
        return p[key].get(language, p[key]["cn"])
    item_props = _todo_item_properties(language)
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": _d("action"),
                "enum": ["update", "delete", "cancel", "append", "insert_after", "insert_before"],
            },
            "ids": {
                "type": "array",
                "description": _d("ids"),
                "items": {"type": "string", "description": _d("ids_item")},
            },
            "todos": {
                "type": "array",
                "description": _d("todos"),
                "items": {
                    "type": "object",
                    "properties": item_props,
                    "required": ["id", "content", "activeForm", "status"],
                },
            },
            "todo_data": {
                "type": "array",
                "description": _d("todo_data"),
                "items": [
                    {"type": "string", "description": _d("todo_data_target_id")},
                    {
                        "type": "array",
                        "description": _d("todo_data_items"),
                        "items": {
                            "type": "object",
                            "properties": item_props,
                            "required": ["id", "content", "activeForm", "status"],
                        },
                    },
                ],
            },
        },
        "required": ["action"],
    }


class TodoCreateMetadataProvider(ToolMetadataProvider):
    """TodoCreate 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "todo_create"

    def get_description(self, language: str = "cn") -> str:
        return TODO_CREATE_DESCRIPTION.get(
            language, TODO_CREATE_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_todo_create_input_params(language)


class TodoListMetadataProvider(ToolMetadataProvider):
    """TodoList 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "todo_list"

    def get_description(self, language: str = "cn") -> str:
        return TODO_LIST_DESCRIPTION.get(
            language, TODO_LIST_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_todo_list_input_params(language)


class TodoModifyMetadataProvider(ToolMetadataProvider):
    """TodoModify 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "todo_modify"

    def get_description(self, language: str = "cn") -> str:
        return TODO_MODIFY_DESCRIPTION.get(
            language, TODO_MODIFY_DESCRIPTION["cn"]
        )

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_todo_modify_input_params(language)
