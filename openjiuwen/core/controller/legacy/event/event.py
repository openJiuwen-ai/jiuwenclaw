# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional, Union, List

from openjiuwen.core.session import InteractiveInput


class EventType(Enum):
    """Event type enum"""
    # User interaction
    USER_INPUT = "user_input"  # User input event
    
    # Agent interaction
    AGENT_RESPONSE = "agent_response"  # Agent response event
    AGENT_HANDOFF = "agent_handoff"  # Agent handoff
    
    # Task execution
    TASK_COMPLETED = "task_completed"  # Task completed
    TASK_INTERRUPTED = "task_interrupted"  # Task interrupted
    
    # Event notification
    ERROR = "error"  # Error event
    INFO = "info"  # Info event


class EventPriority(Enum):
    """Event priority enum"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


class SourceType(Enum):
    """Event source type enum"""
    USER = "user"  # User
    AGENT = "single_agent"  # Agent
    TASK = "task"  # Task
    WORKFLOW = "workflow"  # Workflow
    SYSTEM = "system"  # System


@dataclass
class EventSource:
    """Event source info"""
    conversation_id: str  # Conversation ID
    source_type: SourceType  # Source type
    user_id: Optional[str] = None


@dataclass
class EventContent:
    """Event content - explicit fields, no magic strings, clear types"""
    # Text content
    query: Optional[str] = None
    
    # Interactive input (for interrupt resume)
    interactive_input: Optional['InteractiveInput'] = None
    
    # Stream data - unified list type, no type inconsistency
    stream_data: List[Any] = field(default_factory=list)  # List[OutputSchema]
    
    # Task result - explicit type, not Any
    task_result: Optional[Any] = None  # TaskResult, use Any to avoid circular import
    
    # Extension fields (for truly uncertain data)
    extensions: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Ensure list and dict fields are not None"""
        if self.stream_data is None:
            self.stream_data = []
        if self.extensions is None:
            self.extensions = {}
    
    def get_query(self) -> str:
        """Get query text - unified handling for all cases"""
        # Prefer query
        if self.query is not None:
            return self.query
        
        # Extract text from interactive_input if present
        if self.interactive_input is not None:
            return self._extract_interactive_text(self.interactive_input)
        
        # Default to empty string
        return ""
    
    @staticmethod
    def _extract_interactive_text(interactive_input: 'InteractiveInput') -> str:
        """Extract text from InteractiveInput"""
        if interactive_input.raw_inputs is not None:
            return str(interactive_input.raw_inputs)
        
        if interactive_input.user_inputs:
            # Get first value
            return str(list(interactive_input.user_inputs.values())[0])
        
        return ""


@dataclass
class EventContext:
    """Event context info"""
    correlation_id: Optional[str] = None  # Correlation ID (for event chain tracking)
    conversation_id: Optional[str] = None  # Conversation ID
    task_id: Optional[str] = None  # Related task ID
    workflow_id: Optional[str] = None  # Related workflow ID

    def __post_init__(self):
        pass


@dataclass
class Event:
    """Unified event class"""
    # Basic info
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType = EventType.USER_INPUT
    priority: EventPriority = EventPriority.NORMAL

    # Source
    source: EventSource = field(default_factory=lambda: EventSource("unknown", SourceType.SYSTEM))

    # Content
    content: EventContent = field(default_factory=EventContent)

    # Context
    context: EventContext = field(default_factory=EventContext)

    # Time info
    created_at: datetime = field(default_factory=datetime.now)

    # Extended metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    # AgentGroup routing support
    receiver_id: Optional[str] = None  # Target Agent ID (for point-to-point)
    custom_event_type: Optional[str] = None  # Custom event type (for subscription routing)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    # ========== Factory methods ==========

    @classmethod
    def create_user_event(cls, content: Union[str, InteractiveInput], conversation_id: str = "default",
                          user_id: Optional[str] = None, extensions: Dict[str, Any] = None) -> 'Event':
        """Create user event - unified handling for str and InteractiveInput"""
        source = EventSource(
            conversation_id=conversation_id,
            source_type=SourceType.USER,
            user_id=user_id
        )
        
        # Assign to different fields by type
        if isinstance(content, InteractiveInput):
            event_content = EventContent(interactive_input=content)
        else:
            event_content = EventContent(query=str(content))

        if extensions:
            event_content.extensions = extensions

        context = EventContext(
            conversation_id=conversation_id,
            correlation_id=str(uuid.uuid4())
        )

        return cls(
            event_type=EventType.USER_INPUT,
            source=source,
            content=event_content,
            context=context
        )

    @classmethod
    def create_agent_response(cls, content: str, conversation_id: str,
                              reply_to_event_id: Optional[str] = None) -> 'Event':
        """Create Agent response event"""
        source = EventSource(
            conversation_id=conversation_id,
            source_type=SourceType.AGENT
        )
        event_content = EventContent(query=content)
        context = EventContext(
            conversation_id=conversation_id,
            correlation_id=reply_to_event_id
        )

        return cls(
            event_type=EventType.AGENT_RESPONSE,
            source=source,
            content=event_content,
            context=context
        )

    @classmethod
    def create_agent_handoff(cls, conversation_id: str, to_agent_id: str,
                             handoff_reason: str) -> 'Event':
        """Create Agent handoff event"""
        source = EventSource(
            conversation_id=conversation_id,
            source_type=SourceType.AGENT
        )
        event_content = EventContent(
            query=handoff_reason,
            extensions={"to_agent_id": to_agent_id}
        )
        context = EventContext(
            conversation_id=conversation_id
        )

        return cls(
            event_type=EventType.AGENT_HANDOFF,
            source=source,
            content=event_content,
            context=context
        )

    @classmethod
    def create_task_completed(cls, conversation_id: str, task_id: str,
                              task_result: Any,  # TaskResult, use Any to avoid circular import
                              workflow_id: Optional[str] = None,
                              stream_data: Optional[List[Any]] = None) -> 'Event':
        """Create task completed event"""
        source = EventSource(
            conversation_id=conversation_id,
            source_type=SourceType.TASK
        )

        # Handle stream_data default value
        if stream_data is None:
            stream_data = []
        
        event_content = EventContent(
            stream_data=stream_data,
            task_result=task_result
        )

        context = EventContext(
            conversation_id=conversation_id,
            task_id=task_id,
            workflow_id=workflow_id
        )

        return cls(
            event_type=EventType.TASK_COMPLETED,
            source=source,
            content=event_content,
            context=context
        )

    @classmethod
    def create_task_interrupted(cls, conversation_id: str, task_id: str, reason: str,
                                task_result: Any,  # TaskResult, use Any to avoid circular import
                                workflow_id: Optional[str] = None,
                                stream_data: Optional[List[Any]] = None) -> 'Event':
        """Create task interrupted event"""
        source = EventSource(
            conversation_id=conversation_id,
            source_type=SourceType.TASK
        )
        
        # Handle stream_data default value
        if stream_data is None:
            stream_data = []
        
        event_content = EventContent(
            query=reason,
            stream_data=stream_data,
            task_result=task_result
        )
        context = EventContext(
            conversation_id=conversation_id,
            task_id=task_id,
            workflow_id=workflow_id
        )

        return cls(
            event_type=EventType.TASK_INTERRUPTED,
            source=source,
            content=event_content,
            context=context,
            priority=EventPriority.HIGH
        )

    @classmethod
    def create_error_event(cls, conversation_id: str, error_info: str,
                           source_type: SourceType = SourceType.SYSTEM) -> 'Event':
        """Create error event"""
        source = EventSource(
            conversation_id=conversation_id,
            source_type=source_type
        )
        event_content = EventContent(query=error_info)

        return cls(
            event_type=EventType.ERROR,
            source=source,
            content=event_content,
            priority=EventPriority.HIGH
        )

    @classmethod
    def create_info_event(cls, conversation_id: str, info_text: str,
                          source_type: SourceType = SourceType.SYSTEM) -> 'Event':
        """Create info event"""
        source = EventSource(
            conversation_id=conversation_id,
            source_type=source_type
        )
        event_content = EventContent(query=info_text)

        return cls(
            event_type=EventType.INFO,
            source=source,
            content=event_content
        )

    # ========== Convenience methods ==========

    def set_correlation(self, correlation_id: str) -> None:
        """Set correlation ID"""
        self.context.correlation_id = correlation_id

    def set_conversation(self, conversation_id: str) -> None:
        """Set conversation ID"""
        self.context.conversation_id = conversation_id

    def is_from_user(self) -> bool:
        """Check if from user"""
        return self.source.source_type == SourceType.USER

    def is_from_agent(self) -> bool:
        """Check if from Agent"""
        return self.source.source_type == SourceType.AGENT

    def is_task_related(self) -> bool:
        """Check if task related"""
        return self.context.task_id is not None

    def is_workflow_related(self) -> bool:
        """Check if workflow related"""
        return self.context.workflow_id is not None

    def get_display_content(self) -> str:
        """Get display content"""
        return self.content.get_query()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict format"""

        def convert_enum(obj):
            if isinstance(obj, Enum):
                return obj.value
            return obj

        result = {}
        for key, value in self.__dict__.items():
            if hasattr(value, '__dict__'):  # Handle nested dataclass
                result[key] = {k: convert_enum(v) for k, v in value.__dict__.items()}
            else:
                result[key] = convert_enum(value)

        # Special handling for datetime
        if isinstance(self.created_at, datetime):
            result['created_at'] = self.created_at.isoformat()

        return result


# Backward compatibility aliases
Message = Event
MessageType = EventType
MessagePriority = EventPriority
MessageSource = EventSource
MessageContent = EventContent
MessageContext = EventContext
