from pydantic import BaseModel, Field, RootModel, model_validator
from typing import Annotated, Dict, Any, Literal, Optional, Union, List, get_args
from datetime import datetime
import time
import uuid
from enum import Enum
from app.domain.models.plan import Plan, Step
from app.domain.models.file import FileInfo
import json
from app.domain.models.search import SearchResultItem
class PlanStatus(str, Enum):
    """Plan status enum"""
    CREATED = "created"
    UPDATED = "updated"
    COMPLETED = "completed"


class StepStatus(str, Enum):
    """Step status enum"""
    STARTED = "started"
    FAILED = "failed"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class ToolStatus(str, Enum):
    """Tool status enum"""
    CALLING = "calling"
    CALLED = "called"


class BaseEvent(BaseModel):
    """Base class for agent events"""
    type: Literal[""] = ""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now())

class ErrorEvent(BaseEvent):
    """Error event"""
    type: Literal["error"] = "error"
    error: str

class PlanEvent(BaseEvent):
    """Plan related events"""
    type: Literal["plan"] = "plan"
    plan: Plan
    status: PlanStatus
    step: Optional[Step] = None

class SearchToolContent(BaseModel):
    """Search tool content"""
    results: List[SearchResultItem]

class McpToolContent(BaseModel):
    """MCP tool content"""
    result: Any
    chart: Optional[Dict[str, Any]] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_content(cls, value: Any) -> Any:
        """Keep previously stored MCP tool events readable.

        Older sessions stored MCP content directly as ``{"console": [...]}``
        instead of under the current ``result`` field. This is a storage
        compatibility boundary, not an instruction for the agent.
        """
        if isinstance(value, dict) and "result" not in value and "console" in value:
            return {"result": value}
        return value

ToolContent = Union[SearchToolContent, McpToolContent]

class ToolEvent(BaseEvent):
    """Tool related events"""
    type: Literal["tool"] = "tool"
    tool_call_id: str
    tool_name: str
    tool_content: Optional[ToolContent] = None
    function_name: str
    function_args: Dict[str, Any]
    status: ToolStatus
    function_result: Optional[Any] = None

class TitleEvent(BaseEvent):
    """Title event"""
    type: Literal["title"] = "title"
    title: str

class StepEvent(BaseEvent):
    """Step related events"""
    type: Literal["step"] = "step"
    step: Step
    status: StepStatus

class MessageEvent(BaseEvent):
    """Message event with an explicit display source."""
    type: Literal["message"] = "message"
    role: Literal["user", "assistant"] = "assistant"
    message: str
    attachments: Optional[List[FileInfo]] = None
    source: Literal["user", "ack", "notification", "step_result", "final", "assistant"] = "assistant"

class DoneEvent(BaseEvent):
    """Done event"""
    type: Literal["done"] = "done"

class WaitEvent(BaseEvent):
    """Wait event"""
    type: Literal["wait"] = "wait"

class MessageChunkEvent(BaseEvent):
    """Streaming message chunk — emitted token-by-token for the acknowledgment.
    The frontend accumulates these into a single message bubble.
    When done=True the stream for this message is complete."""
    type: Literal["message_chunk"] = "message_chunk"
    role: Literal["user", "assistant"] = "assistant"
    content: str = ""
    done: bool = False

AgentEvent = Annotated[
    Union[
        ErrorEvent,
        PlanEvent,
        ToolEvent,
        StepEvent,
        MessageEvent,
        MessageChunkEvent,
        DoneEvent,
        TitleEvent,
        WaitEvent,
    ],
    Field(discriminator="type"),
]
