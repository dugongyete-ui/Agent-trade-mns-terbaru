from fastapi import APIRouter, Depends, Query
from sse_starlette.sse import EventSourceResponse
from typing import AsyncGenerator, List, Optional
from sse_starlette.event import ServerSentEvent
from datetime import datetime
import asyncio
import logging

from app.application.services.agent_service import AgentService
from app.application.services.token_service import TokenService
from app.application.errors.exceptions import NotFoundError, UnauthorizedError
from app.interfaces.dependencies import get_agent_service, get_current_user, get_optional_current_user, get_token_service
from app.interfaces.schemas.base import APIResponse
from app.interfaces.schemas.session import (
    ChatRequest, CreateSessionResponse, GetSessionResponse,
    ListSessionItem, ListSessionResponse,
    ShareSessionResponse, SharedSessionResponse
)
from app.interfaces.schemas.event import EventMapper
from app.interfaces.schemas.file import FileInfoResponse
from app.domain.models.file import FileInfo
from app.domain.models.user import User

logger = logging.getLogger(__name__)
SESSION_POLL_INTERVAL = 5

router = APIRouter(prefix="/sessions", tags=["sessions"])

@router.put("", response_model=APIResponse[CreateSessionResponse])
async def create_session(
    current_user: User = Depends(get_current_user),
    agent_service: AgentService = Depends(get_agent_service)
) -> APIResponse[CreateSessionResponse]:
    session = await agent_service.create_session(current_user.id)
    return APIResponse.success(
        CreateSessionResponse(
            session_id=session.id,
        )
    )

@router.get("/{session_id}", response_model=APIResponse[GetSessionResponse])
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    agent_service: AgentService = Depends(get_agent_service)
) -> APIResponse[GetSessionResponse]:
    session = await agent_service.get_session(session_id, current_user.id)
    if not session:
        raise NotFoundError("Session not found")
    return APIResponse.success(GetSessionResponse(
        session_id=session.id,
        title=session.title,
        status=session.status,
        events=await EventMapper.events_to_sse_events(session.events),
        is_shared=session.is_shared
    ))

@router.delete("", response_model=APIResponse[None])
async def delete_all_sessions(
    current_user: User = Depends(get_current_user),
    agent_service: AgentService = Depends(get_agent_service)
) -> APIResponse[None]:
    await agent_service.delete_all_sessions(current_user.id)
    return APIResponse.success()

@router.delete("/{session_id}", response_model=APIResponse[None])
async def delete_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    agent_service: AgentService = Depends(get_agent_service)
) -> APIResponse[None]:
    await agent_service.delete_session(session_id, current_user.id)
    return APIResponse.success()

@router.post("/{session_id}/stop", response_model=APIResponse[None])
async def stop_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    agent_service: AgentService = Depends(get_agent_service)
) -> APIResponse[None]:
    await agent_service.stop_session(session_id, current_user.id)
    return APIResponse.success()

@router.post("/{session_id}/clear_unread_message_count", response_model=APIResponse[None])
async def clear_unread_message_count(
    session_id: str,
    current_user: User = Depends(get_current_user),
    agent_service: AgentService = Depends(get_agent_service)
) -> APIResponse[None]:
    await agent_service.clear_unread_message_count(session_id, current_user.id)
    return APIResponse.success()

@router.get("", response_model=APIResponse[ListSessionResponse])
async def get_all_sessions(
    current_user: User = Depends(get_current_user),
    agent_service: AgentService = Depends(get_agent_service)
) -> APIResponse[ListSessionResponse]:
    summaries = await agent_service.get_all_sessions(current_user.id)
    session_items = [
        ListSessionItem(
            session_id=s.id,
            title=s.title,
            status=s.status,
            unread_message_count=s.unread_message_count,
            latest_message=s.latest_message,
            latest_message_at=int(s.latest_message_at.timestamp()) if s.latest_message_at else None,
            is_shared=s.is_shared
        ) for s in summaries
    ]
    return APIResponse.success(ListSessionResponse(sessions=session_items))

@router.post("")
async def stream_sessions(
    current_user: User = Depends(get_current_user),
    agent_service: AgentService = Depends(get_agent_service)
) -> EventSourceResponse:
    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        while True:
            summaries = await agent_service.get_all_sessions(current_user.id)
            session_items = [
                ListSessionItem(
                    session_id=s.id,
                    title=s.title,
                    status=s.status,
                    unread_message_count=s.unread_message_count,
                    latest_message=s.latest_message,
                    latest_message_at=int(s.latest_message_at.timestamp()) if s.latest_message_at else None,
                    is_shared=s.is_shared
                ) for s in summaries
            ]
            yield ServerSentEvent(
                event="sessions",
                data=ListSessionResponse(sessions=session_items).model_dump_json()
            )
            await asyncio.sleep(SESSION_POLL_INTERVAL)
    return EventSourceResponse(event_generator())

@router.post("/{session_id}/chat")
async def chat(
    session_id: str,
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    agent_service: AgentService = Depends(get_agent_service)
) -> EventSourceResponse:
    async def event_generator() -> AsyncGenerator[ServerSentEvent, None]:
        async for event in agent_service.chat(
            session_id=session_id,
            user_id=current_user.id,
            message=request.message,
            timestamp=datetime.fromtimestamp(request.timestamp / 1000 if request.timestamp > 1e10 else request.timestamp) if request.timestamp else None,
            event_id=request.event_id,
            attachments=request.attachments
        ):
            logger.debug(f"Received event from chat: {event}")
            sse_event = await EventMapper.event_to_sse_event(event)
            logger.debug(f"Received event: {sse_event}")
            if sse_event:
                yield ServerSentEvent(
                    event=sse_event.event,
                    data=sse_event.data.model_dump_json() if sse_event.data else None
                )

    return EventSourceResponse(event_generator(), ping=20, ping_message_factory=lambda: ServerSentEvent(comment="keepalive"))

@router.get("/{session_id}/files")
async def get_session_files(
    session_id: str,
    current_user: User = Depends(get_current_user),
    agent_service: AgentService = Depends(get_agent_service)
) -> APIResponse[List[FileInfo]]:
    files = await agent_service.get_session_files(session_id, current_user.id)
    return APIResponse.success(files)


@router.post("/{session_id}/share", response_model=APIResponse[ShareSessionResponse])
async def share_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    agent_service: AgentService = Depends(get_agent_service)
) -> APIResponse[ShareSessionResponse]:
    share_token = await agent_service.share_session(session_id, current_user.id)
    session = await agent_service.get_session(session_id, current_user.id)
    return APIResponse.success(ShareSessionResponse(
        session_id=session_id,
        is_shared=True,
        share_token=share_token,
        expires_at=int(session.share_expires_at.timestamp()) if session and session.share_expires_at else None,
    ))

@router.get("/{session_id}/share/files")
async def get_shared_session_files(
    session_id: str,
    share_token: str = Query(..., min_length=32, max_length=256),
    agent_service: AgentService = Depends(get_agent_service)
) -> APIResponse[List[FileInfoResponse]]:
    files = await agent_service.get_shared_session_files(session_id, share_token)
    redacted_files = [
        await FileInfoResponse.from_file_info(file_info)
        for file_info in files
    ]
    return APIResponse.success(redacted_files)


@router.delete("/{session_id}/share", response_model=APIResponse[ShareSessionResponse])
async def unshare_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    agent_service: AgentService = Depends(get_agent_service)
) -> APIResponse[ShareSessionResponse]:
    await agent_service.unshare_session(session_id, current_user.id)
    return APIResponse.success(ShareSessionResponse(
        session_id=session_id,
        is_shared=False
    ))


@router.get("/shared/{session_id}", response_model=APIResponse[SharedSessionResponse])
async def get_shared_session(
    session_id: str,
    share_token: str = Query(..., min_length=32, max_length=256),
    agent_service: AgentService = Depends(get_agent_service)
) -> APIResponse[SharedSessionResponse]:
    session = await agent_service.get_shared_session(session_id, share_token)
    if not session:
        raise NotFoundError("Shared session not found")

    return APIResponse.success(SharedSessionResponse(
        session_id=session.id,
        title=session.title,
        status=session.status,
        events=await EventMapper.events_to_sse_events(session.events),
        is_shared=session.is_shared
    ))
