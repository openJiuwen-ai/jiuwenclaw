# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, List, Optional, Dict

from pydantic import BaseModel, Field

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import tool_logger, LogEventType
from openjiuwen.core.foundation.tool import Tool, ToolCard, Input, Output
from openjiuwen.core.session.agent import Session
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.deepagents.prompts.sections.tools import build_tool_card


class TodoStatus(str, Enum):
    """Todo Task Status Enumeration

    PENDING: Tasks that have not been started
    IN_PROGRESS: Tasks currently being worked on
    COMPLETED: Tasks that have been finished
    """
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

    @classmethod
    def all_values(cls) -> List[str]:
        return [v.value for v in cls]


STATUS_ICONS = {
    TodoStatus.PENDING: "[ ]",
    TodoStatus.IN_PROGRESS: "[>]",
    TodoStatus.COMPLETED: "[√]"
}


class TodoItem(BaseModel):
    """Data class for representing a single todo item with core attributes"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()),
                    description="Unique identifier for the todo item (UUID)")
    content: str = Field(default="", description="Detailed description of todo task")
    activeForm: str = Field(default="", description="Present-tense description of current task execution state")
    status: TodoStatus = Field(..., description="Current status of the todo task (pending/in_progress/completed)")
    createdAt: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(.\d+)?([+-]\d{2}:\d{2})?$',
                           description="Task creation timestamp (ISO 8601 format)")
    updatedAt: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(.\d+)?([+-]\d{2}:\d{2})?$',
                           description="Task last update timestamp (ISO 8601 format)")

    def to_dict(self) -> Dict[str, Any]:
        """Convert TodoItem instance to dictionary for persistence"""
        return {
            "id": self.id,
            "content": self.content,
            "activeForm": self.activeForm,
            "status": self.status.value,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TodoItem':
        """Create TodoItem instance from dictionary data"""
        return cls(
            id=data["id"],
            content=data["content"],
            activeForm=data["activeForm"],
            status=TodoStatus(data["status"]),
            createdAt=data["createdAt"],
            updatedAt=data["updatedAt"]
        )

    @classmethod
    def create(cls, content: str, active_form: str = "", status: TodoStatus = TodoStatus.PENDING) -> 'TodoItem':
        """Create a new TodoItem with auto-generated timestamps

        Args:
            content: Task description content
            active_form: Present-tense description (auto-generated if empty)
            status: Initial task status (default: PENDING)

        Returns:
            Newly created TodoItem instance
        """
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            content=content,
            activeForm=active_form or f"Executing {content}",
            status=status,
            createdAt=now,
            updatedAt=now
        )


class TodoTool(Tool):
    """Base class for Todo tools with common persistence operations"""

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        pass

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        pass

    def __init__(self, card: ToolCard, operation: SysOperation, workspace: Optional[str] = None):
        """Initialize TodoTool with persistence layer

        Args:
            card: Tool metadata card with description and parameter schema
            operation: System operation for file system access
            workspace: Path for file system operations
        """
        super().__init__(card)
        self.workspace = workspace if workspace else "./"
        self.fs = operation.fs()
        self.file = f"{str(uuid.uuid4())}.json"

    async def load_todos(self) -> List[TodoItem]:
        """Load todo items from session-specific JSON file

        Returns:
            List of TodoItem instances loaded from file

        Raises:
            ToolError: If file loading/parsing fails
        """
        try:
            read_res = await self.fs.read_file(self.file, mode="text")
            if read_res.code == 0:
                data = json.loads(read_res.data.content)
                todos = [TodoItem.from_dict(item) for item in data]
                tool_logger.info(
                    "Successfully loaded todo items",
                    event_type=LogEventType.TOOL_CALL_START
                )
                return todos
            else:
                tool_logger.error(
                    "Failed to load todo list, because read_file fail",
                    event_type=LogEventType.TOOL_CALL_ERROR,
                )
                raise build_error(
                    StatusCode.TOOL_TODOS_LOAD_FAILED,
                    reason=f"Failed to load todo list, because read_file fail"
                )
        except Exception as e:
            tool_logger.error(
                "Failed to load todo list",
                event_type=LogEventType.TOOL_CALL_ERROR,
                exception=str(e)
            )
            raise build_error(
                StatusCode.TOOL_TODOS_LOAD_FAILED,
                reason=f"Failed to load todo list: {str(e)}"
            ) from e

    async def save_todos(self, todos: List[TodoItem]):
        """Save todo items to session-specific JSON file

        Args:
            todos: List of TodoItem instances to persist

        Raises:
            ToolError: If file writing fails
        """
        try:
            data = [todo.to_dict() for todo in todos]
            json_content = json.dumps(data, ensure_ascii=False, indent=2)
            write_res = await self.fs.write_file(self.file, json_content, mode="text")
            if write_res.code == 0:
                tool_logger.info(
                    "Successfully saved todo items",
                    event_type=LogEventType.TOOL_CALL_END
                )
            else:
                tool_logger.error(
                    "Failed to save todo list, because write_file fail",
                    event_type=LogEventType.TOOL_CALL_ERROR
                )
                raise build_error(
                    StatusCode.TOOL_TODOS_SAVE_FAILED,
                    reason=f"Failed to save todo list, because write_file fail"
                )
        except Exception as e:
            tool_logger.error(
                "Failed to save todo list",
                event_type=LogEventType.TOOL_CALL_ERROR,
                exception=str(e)
            )
            raise build_error(
                StatusCode.TOOL_TODOS_SAVE_FAILED,
                reason=f"Failed to save todo list: {str(e)}"
            ) from e


class TodoCreateTool(TodoTool):
    """Todo Write Tool - Create new todo items with session"""

    def __init__(self, card: ToolCard, operation: SysOperation, workspace: Optional[str] = None):
        """Initialize TodoWrite tool

        Args:
            card: Tool metadata card with description and parameter schema
            operation: System operation for file system access
            workspace: Path for file system operations
        """
        super().__init__(card, operation, workspace)

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        """Asynchronous invocation handler for for TodoWrite tool operations

        Args:
            inputs: Input parameters dictionary containing task creation data
            **kwargs:：Additional operation parameters (supports session_id override)

        Returns:
            Execution result with human-readable creation message

        Raises:
            ToolError: If invalid parameters or operation fails
        """
        session = kwargs.get("session", None)
        if session and isinstance(session, Session) and session.get_session_id() not in self.file:
            self.file = f"{session.get_session_id()}.json"

        results = dict()
        try:
            tasks_str = inputs.get("tasks")
            if tasks_str and isinstance(tasks_str, str):
                results["message"] = await self._create_from_string(tasks_str)
                return results

            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason=(f"Invalid task data: either 'tasks' (simplified) or 'todos' (complete) parameter "
                        f"is required for create action")
            )

        except Exception as e:
            tool_logger.error(
                "Todo write tool invocation failed",
                event_type=LogEventType.TOOL_CALL_ERROR,
                exception=str(e)
            )
            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason=str(e)
            ) from e

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        """Stream response handler (not supported for TodoWrite tool)"""
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)

    def _format_create_result(self, todos: List[TodoItem]) -> str:
        """Format task creation result into human-readable string

        Args:
            todos: List of created TodoItem instances

        Returns:
            Formatted success message with task details
        """
        result = f"Successfully created {len(todos)} task(s):\n"
        for todo in todos:
            status_icon = STATUS_ICONS.get(todo.status, "[ ]")
            result += f"  {status_icon} task_id: {todo.id} , content: {todo.content}\n"

        first_task = todos[0].content if todos else ""
        result += f"\nNext step: Immediately execute task '{first_task}'"
        return result.strip()

    def _parse_task_string(self, tasks_str: str) -> List[str]:
        """Parse delimited task string into individual task list.

        Supported delimiters: newline, semicolon, Chinese semicolon.
        Note: do not split on Chinese enumeration comma ``、`` because
        it is commonly used inside a single task sentence.

        Args:
            tasks_str: Delimited string of task descriptions

        Returns:
            List of trimmed non-empty task descriptions
        """
        parsed_tasks = re.split(r'[\n;；]', tasks_str)
        parsed_tasks = [t.strip() for t in parsed_tasks if t.strip()]
        tool_logger.info(
            "Parsed single task string",
            event_type=LogEventType.TOOL_CALL_START
        )
        return parsed_tasks

    async def _create_from_string(self, tasks_str: str) -> str:
        """Create todo items from simplified delimited string

        Args:
            tasks_str: Delimited string of task descriptions

        Returns:
            Formatted creation success message

        Raises:
            ToolError: If no valid tasks provided
        """
        tasks = self._parse_task_string(tasks_str)

        if not tasks:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason=f"Task content cannot be empty - no valid tasks found in input string"
            )

        new_todos = []
        for i, task_content in enumerate(tasks):
            if not task_content:
                continue
            # Set first task to IN_PROGRESS, others to PENDING
            status = TodoStatus.IN_PROGRESS if i == 0 else TodoStatus.PENDING
            new_todos.append(TodoItem.create(content=task_content, status=status))

        if not new_todos:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason=f"No valid task content provided - all parsed tasks were empty"
            )

        await self.save_todos(new_todos)
        tool_logger.info(
            "Created todo items from simplified string",
            event_type=LogEventType.TOOL_CALL_END
        )
        return self._format_create_result(new_todos)


class TodoListTool(TodoTool):
    """Todo Read Tool - Retrieve and display todo items with session

    Core functionality for listing todo items organized by status:
    1. Group tasks by IN_PROGRESS/PENDING/COMPLETED status
    2. Format output for human-readable display
    """

    def __init__(self, card: ToolCard, operation: SysOperation, workspace: Optional[str] = None):
        """Initialize TodoRead tool with persistence layer

        Args:
            card: Tool metadata card with description and parameter schema
            operation: System operation for file system access
            workspace: Path for file system operations
        """
        super().__init__(card, operation, workspace)

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        """Asynchronous invocation handler for TodoRead tool operations

        Args:
            inputs: Input parameters dictionary (no additional params required)
            **kwargs: Additional operation parameters (supports session_id override)

        Returns:
            Execution result with formatted todo list

        Raises:
            ToolError: If no todos exist or operation fails
        """
        session = kwargs.get("session", None)
        if session and isinstance(session, Session) and session.get_session_id() not in self.file:
            self.file = f"{session.get_session_id()}.json"

        results = dict()

        try:
            current_todos = await self.load_todos()
            results["message"] = await self._list_todos(current_todos)
            return results

        except Exception as e:
            tool_logger.error(
                "Todo read tool invocation failed",
                event_type=LogEventType.TOOL_CALL_ERROR,
                exception=str(e)
            )
            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason=str(e)
            ) from e

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        """Stream response handler (not supported for TodoRead tool)"""
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)

    async def _list_todos(self, todos: List[TodoItem]) -> str:
        """Format todo items into human-readable list

        Args:
            todos: List of todo items to format

        Returns:
            Formatted todo list string

        Raises:
            ToolError: If no todos exist to list
        """
        if not todos:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason=f"No todo items found in current session - nothing to list"
            )

        # Group todos by status for organized display
        grouped = {
            TodoStatus.IN_PROGRESS: [],
            TodoStatus.PENDING: [],
            TodoStatus.COMPLETED: []
        }

        for todo in todos:
            if todo.status in grouped:
                grouped[todo.status].append(todo)

        # Define display sections
        sections = [
            (TodoStatus.IN_PROGRESS, f"{STATUS_ICONS[TodoStatus.IN_PROGRESS]} In Progress Task",
             lambda t: f" [{t.id}] {t.activeForm}"),
            (TodoStatus.PENDING, f"{STATUS_ICONS[TodoStatus.PENDING]} Pending Tasks",
             lambda t: f" [{t.id}] {t.content}"),
            (TodoStatus.COMPLETED, f"{STATUS_ICONS[TodoStatus.COMPLETED]} Completed Tasks",
             lambda t: f" [{t.id}] {t.content}")
        ]

        result_lines = [f"Todo List (Total: {len(todos)} items):\n"]

        for status, title, formatter in sections:
            items = grouped.get(status, [])
            if items:
                result_lines.append(title)
                result_lines.extend(formatter(todo) for todo in items)
                result_lines.append("")  # Empty line between sections

        formatted_list = "\n".join(result_lines).strip()
        tool_logger.info(
            "Generated formatted todo list",
            event_type=LogEventType.TOOL_CALL_END
        )
        return formatted_list


class TodoModifyTool(TodoTool):
    """
    Todo Modify Tool

    update/delete/append/insert_after/insert_before action operations todo items with session
    """

    def __init__(self, card: ToolCard, operation: SysOperation, workspace: Optional[str] = None):
        """Initialize TodoModify tool with persistence layer

        Args:
            card: Tool metadata card with description and parameter schema
            operation: System operation for file system access
            workspace: Path for file system operations
        """
        super().__init__(card, operation, workspace)

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        """Asynchronous invocation handler for TodoModify tool operations

        Args:
            inputs: Input parameters dictionary containing action and corresponding fields
            **kwargs: Additional operation parameters (supports session_id override)

        Returns:
            Execution result with human-readable operation message

        Raises:
            ToolError: If invalid parameters or operation fails
        """
        session = kwargs.get("session", None)
        if session and isinstance(session, Session) and session.get_session_id() not in self.file:
            self.file = f"{session.get_session_id()}.json"

        results = dict()
        try:
            action = inputs.get("action")
            if not action:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason="Invalid input: 'action' field is required"
                )

            current_todos = await self.load_todos()

            if action == "delete":
                ids = inputs.get("ids")
                if not ids or not isinstance(ids, list) or not all(isinstance(id_str, str) for id_str in ids):
                    raise build_error(
                        StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                        reason="Invalid input for delete action: 'ids' must be a non-empty list of task IDs (strings)"
                    )
                results["message"] = await self._delete_todos(ids, current_todos)

            elif action == "update":
                todos_data = inputs.get("todos")
                results["message"] = await self._update_todos(todos_data, current_todos)

            elif action == "append":
                todos_data = inputs.get("todos")
                results["message"] = await self._append_todos(todos_data, current_todos)

            elif action == "insert_after":
                todo_data = inputs.get("todo_data")
                self._validate_todo_data_structure(todo_data)
                target_id, insert_todos = todo_data
                results["message"] = await self._insert_after_todos(target_id, insert_todos, current_todos)

            elif action == "insert_before":
                todo_data = inputs.get("todo_data")
                self._validate_todo_data_structure(todo_data)
                target_id, insert_todos = todo_data
                results["message"] = await self._insert_before_todos(target_id, insert_todos, current_todos)

            else:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Invalid action: {action}"
                )
            return results

        except Exception as e:
            tool_logger.error(
                "Todo modify tool invocation failed",
                event_type=LogEventType.TOOL_CALL_ERROR,
                exception=str(e)
            )
            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason=str(e)
            ) from e

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        """Stream response handler (not supported for TodoModify tool)"""
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)

    def _validate_todo_data_structure(self, todo_data: List):
        """Validate structure of todo_data array for insert_after/insert_before actions

        Args:
            todo_data: [target_id, todo_list] array to validate

        Raises:
            ToolError: If todo_data is invalid (incorrect length/type)
        """
        if not todo_data or not isinstance(todo_data, list) or len(todo_data) != 2:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason="Invalid input for insert action: 'todo_data' must be a 2-element array [target_id, todo_list]"
            )

        target_id, insert_todos = todo_data
        if not target_id or not isinstance(target_id, str):
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason="Invalid input: todo_data first element must be a non-empty target task ID (string)"
            )

        if not insert_todos or not isinstance(insert_todos, list):
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason="Invalid input: todo_data second element must be a non-empty list of todo objects"
            )

        # Validate each todo item in the insert list
        for todo_item in insert_todos:
            self._validate_single_todo_item(todo_item)

    def _validate_target_task_status(self, target_id: str, current_todos: List[TodoItem], allowed_statuses: List[str]):
        """Validate target task exists and has allowed status

        Args:
            target_id: Target task ID to check
            current_todos: Current list of todo items
            allowed_statuses: List of allowed status values for target task

        Returns:
            Index of target task in current_todos list

        Raises:
            ToolError: If target task not found or has invalid status
        """
        target_index = -1
        target_todo = None

        for idx, todo in enumerate(current_todos):
            if todo.id == target_id:
                target_index = idx
                target_todo = todo
                break

        if not target_todo:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason=f"Target task with ID '{target_id}' not found in current todo list"
            )

        if target_todo.status not in allowed_statuses:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason=f"Target task status '{target_todo.status}' doesn't allow insertion."
            )

        return target_index

    def _validate_single_in_progress(self, todos_data: List[TodoItem]):
        """Validate that only one task is marked as IN_PROGRESS

        Args:
            todos_data: List of TodoItem instances to check

        Raises:
            ToolError: If multiple IN_PROGRESS tasks found
        """
        in_progress_count = sum(1 for todo in todos_data if todo.status == TodoStatus.IN_PROGRESS)
        if in_progress_count > 1:
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason=f"More than one task is marked as 'in_progress' (only one allowed)"
            )

    def _validate_single_todo_item(self, todo_data: Dict):
        """Validate single todo item dictionary

        Args:
            todo_data: Todo item dictionary to validate

        Raises:
            ToolError: If required fields missing or invalid status
        """
        validation_errors = []
        required_fields = ["content", "activeForm", "status", "id"]
        for field in required_fields:
            if field not in todo_data:
                validation_errors.append(f"Missing required field: '{field}'")

        # Validate status value
        try:
            TodoStatus(todo_data.get("status", ""))
        except ValueError:
            validation_errors.append(
                f"Invalid status '{todo_data.get('status')}'. Valid values: {TodoStatus.all_values()}")

        if validation_errors:
            error_msg = "; ".join(validation_errors)
            tool_logger.error(
                "Todo data validation failed",
                event_type=LogEventType.TOOL_CALL_ERROR
            )
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason=f"Todo data validation error: {error_msg}"
            )

    def _convert_to_todo_item(self, todo_data: Dict) -> TodoItem:
        """Convert dictionary todo data to TodoItem instance

        Args:
            todo_data: Todo item dictionary

        Returns:
            TodoItem instance with populated fields
        """
        now = datetime.now(timezone.utc).isoformat()
        return TodoItem(
            id=todo_data["id"],
            content=todo_data["content"],
            activeForm=todo_data["activeForm"],
            status=TodoStatus(todo_data["status"]),
            createdAt=now,
            updatedAt=now
        )

    async def _delete_todos(self, ids: List[str], current_todos: List[TodoItem]) -> str:
        """Batch delete todo items by ID

        Args:
            ids: List of task IDs to delete
            current_todos: Current list of todo items

        Returns:
            Delete success message

        Raises:
            ToolError: If no tasks found to delete
        """
        deleted_count = 0
        remaining_todos = []
        delete_ids = set(ids)

        # Filter out todos to delete
        for todo in current_todos:
            if todo.id in delete_ids:
                deleted_count += 1
            else:
                remaining_todos.append(todo)

        if deleted_count == 0:
            tool_logger.warning(
                "No tasks found for deletion",
                event_type=LogEventType.TOOL_CALL_END
            )
            return f"No tasks deleted: None of the provided IDs ({', '.join(ids)}) were found"

        await self.save_todos(remaining_todos)

        result_msg = f"Successfully deleted {deleted_count} task(s) (IDs: {', '.join(delete_ids)})"
        tool_logger.info(
            f"Batch deleted {deleted_count} todo items",
            event_type=LogEventType.TOOL_CALL_END
        )
        return result_msg

    async def _update_todos(self, todos_data: List[Dict], current_todos: List[TodoItem]) -> str:
        """Batch update todo items.

        Args:
            todos_data: List of updated todo item dictionaries.
                Supports partial update; missing fields
                fall back to current values.
            current_todos: Current list of todo items

        Returns:
            Update success message

        Raises:
            ToolError: If no todos exist or invalid data provided
        """
        todo_map = {todo.id: todo for todo in current_todos}
        updated_count = 0
        now = datetime.now(timezone.utc).isoformat()

        for todo_data in todos_data:
            todo_id = todo_data.get("id")
            if not todo_id:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason="Batch update failed: Missing required field: 'id'"
                )

            if todo_id not in todo_map:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Batch update failed: Task with ID '{todo_id}' not found in current todo list"
                )

            current_todo = todo_map[todo_id]
            normalized_todo_data = {
                "id": todo_id,
                "content": todo_data.get(
                    "content", current_todo.content
                ),
                "activeForm": todo_data.get(
                    "activeForm", current_todo.activeForm
                ),
                "status": todo_data.get(
                    "status", current_todo.status.value
                ),
            }
            self._validate_single_todo_item(normalized_todo_data)

            # Update all fields
            current_todo.content = normalized_todo_data["content"]
            current_todo.activeForm = normalized_todo_data[
                "activeForm"
            ]
            current_todo.status = TodoStatus(
                normalized_todo_data["status"]
            )
            current_todo.updatedAt = now
            updated_count += 1

        # Validate single IN_PROGRESS constraint after updates
        self._validate_single_in_progress(current_todos)
        await self.save_todos(current_todos)

        result_msg = f"Successfully updated {updated_count} task(s)"
        tool_logger.info(
            "Batch updated todo items",
            event_type=LogEventType.TOOL_CALL_END
        )
        return result_msg

    async def _append_todos(self, todos_data: List[Dict], current_todos: List[TodoItem]) -> str:
        """Append new todo items to the end of the list

        Args:
            todos_data: List of new todo item dictionaries
            current_todos: Current list of todo items

        Returns:
            Append success message

        Raises:
            ToolError: If duplicate IDs found or validation fails
        """
        todo_ids = [todo.id for todo in current_todos]
        for todo_data in todos_data:
            self._validate_single_todo_item(todo_data)
            todo_id = todo_data.get("id")

            if todo_id in todo_ids:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Batch append failed: Task with ID '{todo_id}' is duplicated in current todo list"
                )

            current_todos.append(self._convert_to_todo_item(todo_data))

        # Validate single IN_PROGRESS constraint after updates
        self._validate_single_in_progress(current_todos)
        await self.save_todos(current_todos)

        result_msg = f"Successfully append {len(todos_data)} task(s)"
        tool_logger.info(
            "Batch updated todo items",
            event_type=LogEventType.TOOL_CALL_END
        )
        return result_msg

    async def _insert_after_todos(self, target_id: str, insert_todos_data: List[Dict],
                                  current_todos: List[TodoItem]) -> str:
        """Insert new todo items after the specified target task

        Args:
            target_id: Target task ID to insert after
            insert_todos_data: List of new todo item dictionaries to insert
            current_todos: Current list of todo items

        Returns:
            Insert success message

        Raises:
            ToolError: If target task invalid or duplicate IDs found
        """
        # Validate target task (allowed statuses: in_progress, pending)
        target_index = self._validate_target_task_status(
            target_id,
            current_todos,
            [TodoStatus.IN_PROGRESS, TodoStatus.PENDING]
        )

        # Check for duplicate IDs in insert list
        existing_ids = {todo.id for todo in current_todos}
        insert_todos = []

        for todo_data in insert_todos_data:
            todo_id = todo_data["id"]
            if todo_id in existing_ids:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Insert failed: Task with ID '{todo_id}' already exists (duplicate ID)"
                )

            # Convert to TodoItem and add to insert list
            insert_todo = self._convert_to_todo_item(todo_data)
            insert_todos.append(insert_todo)
            existing_ids.add(todo_id)

        # Insert new todos after target index
        updated_todos = (
                current_todos[:target_index + 1] +
                insert_todos +
                current_todos[target_index + 1:]
        )

        # Validate single IN_PROGRESS constraint
        self._validate_single_in_progress(updated_todos)

        await self.save_todos(updated_todos)

        result_msg = f"Successfully inserted {len(insert_todos)} task(s) after target task, id: '{target_id}'"
        tool_logger.info(
            f"Inserted {len(insert_todos)} todo items after target ID {target_id}",
            event_type=LogEventType.TOOL_CALL_END
        )
        return result_msg

    async def _insert_before_todos(self, target_id: str, insert_todos_data: List[Dict],
                                   current_todos: List[TodoItem]) -> str:
        """Insert new todo items before the specified target task

        Args:
            target_id: Target task ID to insert before
            insert_todos_data: List of new todo item dictionaries to insert
            current_todos: Current list of todo items

        Returns:
            Insert success message

        Raises:
            ToolError: If target task invalid or duplicate IDs found
        """
        # Validate target task (allowed status: pending only)
        target_index = self._validate_target_task_status(
            target_id,
            current_todos,
            [TodoStatus.PENDING]
        )

        # Check for duplicate IDs in insert list
        existing_ids = {todo.id for todo in current_todos}
        insert_todos = []

        for todo_data in insert_todos_data:
            todo_id = todo_data["id"]
            if todo_id in existing_ids:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Insert failed: Task with ID '{todo_id}' already exists (duplicate ID)"
                )

            # Convert to TodoItem and add to insert list
            insert_todo = self._convert_to_todo_item(todo_data)
            insert_todos.append(insert_todo)
            existing_ids.add(todo_id)

        # Insert new todos before target index
        updated_todos = (
                current_todos[:target_index] +
                insert_todos +
                current_todos[target_index:]
        )

        # Validate single IN_PROGRESS constraint
        self._validate_single_in_progress(updated_todos)

        await self.save_todos(updated_todos)

        result_msg = f"Successfully inserted {len(insert_todos)} task(s) before target task, id: '{target_id}'"
        tool_logger.info(
            f"Inserted {len(insert_todos)} todo items before target ID {target_id}",
            event_type=LogEventType.TOOL_CALL_END
        )
        return result_msg


def create_todo_create_tool(
    operation: SysOperation,
    workspace: Optional[str] = None,
    language: str = "cn",
) -> TodoCreateTool:
    """Create a configured instance of the TodoWriteTool

    Args:
        operation: Implement persistence for the todo list
        workspace: Path for file system operations
        language: Prompt language ("cn" or "en")

    Returns:
        Configured TodoWriteTool instance
    """
    card = build_tool_card("todo_write", "TodoCreateTool", language)
    return TodoCreateTool(card, operation, workspace)


def create_todo_list_tool(
    operation: SysOperation,
    workspace: Optional[str] = None,
    language: str = "cn",
) -> TodoListTool:
    """Create a configured instance of the TodoReadTool

    Args:
        operation: Implement persistence for the todo list
        workspace: Path for file system operations
        language: Prompt language ("cn" or "en")

    Returns:
        Configured TodoReadTool instance
    """
    card = build_tool_card("todo_read", "TodoListTool", language)
    return TodoListTool(card, operation, workspace)


def create_todo_modify_tool(
    operation: SysOperation,
    workspace: Optional[str] = None,
    language: str = "cn",
) -> TodoModifyTool:
    """Create a configured instance of the TodoModifyTool

    Args:
        operation: Implement persistence for the todo list
        workspace: Path for file system operations
        language: Prompt language ("cn" or "en")

    Returns:
        Configured TodoModifyTool instance
    """
    card = build_tool_card("todo_modify", "TodoModifyTool", language)
    return TodoModifyTool(card, operation, workspace)


def create_todos_tool(operation: SysOperation, workspace: Optional[str] = None, language: str = "cn") -> List[TodoTool]:
    return [create_todo_create_tool(operation, workspace, language),
            create_todo_list_tool(operation, workspace, language),
            create_todo_modify_tool(operation, workspace, language)]
