from typing import Any, Dict, Optional, List
from pydantic import BaseModel, Field, field_validator
from app.interfaces.schemas.event import AgentSSEEvent
from app.domain.models.session import SessionStatus


class ChatRequest(BaseModel):
    """Bounded chat request schema for the agent boundary."""
    timestamp: Optional[int] = None
    message: Optional[str] = Field(default=None, max_length=20_000)
    attachments: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=10)
    event_id: Optional[str] = Field(default=None, max_length=128)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        return value or None


class CreateSessionResponse(BaseModel):
    """Create session response schema"""
    session_id: str


class GetSessionResponse(BaseModel):
    """Get session response schema"""
    session_id: str
    title: Optional[str] = None
    status: SessionStatus
    events: List[AgentSSEEvent] = []
    is_shared: bool = False


class ListSessionItem(BaseModel):
    """List session item schema"""
    session_id: str
    title: Optional[str] = None
    latest_message: Optional[str] = None
    latest_message_at: Optional[int] = None
    status: SessionStatus
    unread_message_count: int
    is_shared: bool = False


class ListSessionResponse(BaseModel):
    """List session response schema"""
    sessions: List[ListSessionItem]


class ShareSessionResponse(BaseModel):
    """Share session response schema"""
    session_id: str
    is_shared: bool
    share_token: Optional[str] = None
    expires_at: Optional[int] = None


class SharedSessionResponse(BaseModel):
    """Shared session response schema (for public access)"""
    session_id: str
    title: Optional[str] = None
    status: SessionStatus
    events: List[AgentSSEEvent] = []
    is_shared: bool
