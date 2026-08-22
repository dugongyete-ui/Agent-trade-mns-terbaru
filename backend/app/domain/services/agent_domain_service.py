from typing import Optional, AsyncGenerator, List
import asyncio
import logging
from datetime import datetime, timezone
from app.domain.models.session import Session, SessionStatus
from app.domain.external.search import SearchEngine
from app.domain.models.event import BaseEvent, ErrorEvent, DoneEvent, MessageEvent, WaitEvent, AgentEvent, PlanEvent, PlanStatus
from pydantic import TypeAdapter
from app.domain.repositories.agent_repository import AgentRepository
from app.domain.repositories.session_repository import SessionRepository
from app.domain.services.agent_task_runner import AgentTaskRunner
from app.domain.external.task import Task
from typing import Type
from app.domain.external.file import FileStorage
from app.domain.models.file import FileInfo
from app.domain.repositories.mcp_repository import MCPRepository
from app.domain.models.plan import ExecutionStatus

logger = logging.getLogger(__name__)

class AgentDomainService:
    """
    Agent domain service, responsible for coordinating the work of planning agent and execution agent
    """

    def __init__(
        self,
        agent_repository: AgentRepository,
        session_repository: SessionRepository,
        task_cls: Type[Task],
        file_storage: FileStorage,
        mcp_repository: MCPRepository,
        search_engine: Optional[SearchEngine] = None,
    ):
        self._repository = agent_repository
        self._session_repository = session_repository
        self._search_engine = search_engine
        self._task_cls = task_cls
        self._file_storage = file_storage
        self._mcp_repository = mcp_repository
        logger.info("AgentDomainService initialization completed")

    async def shutdown(self) -> None:
        """Clean up all Agent's resources"""
        logger.info("Starting to close all Agents")
        await self._task_cls.destroy()
        logger.info("All agents closed successfully")

    async def _create_task(self, session: Session) -> Task:
        """Create a new agent task."""
        task_runner = AgentTaskRunner(
            session_id=session.id,
            agent_id=session.agent_id,
            user_id=session.user_id,
            file_storage=self._file_storage,
            search_engine=self._search_engine,
            session_repository=self._session_repository,
            agent_repository=self._repository,
            mcp_repository=self._mcp_repository,
        )

        task = self._task_cls.create(task_runner)
        session.task_id = task.id
        await self._session_repository.save(session)

        return task

    async def _get_task(self, session: Session) -> Optional[Task]:
        """Get a task for the given session"""
        task_id = session.task_id
        if not task_id:
            return None
        return self._task_cls.get(task_id)

    async def stop_session(self, session_id: str) -> None:
        """Stop a session"""
        session = await self._session_repository.find_by_id(session_id)
        if not session:
            logger.error(f"Attempted to stop non-existent Session {session_id}")
            raise RuntimeError("Session not found")
        task = await self._get_task(session)
        if task:
            # Publish terminal events before cancelling the runner. This keeps
            # the progress panel synchronized instead of leaving pending steps
            # visible after the user stops the analysis.
            plan = session.get_last_plan()
            if plan and not plan.is_done():
                for step in plan.steps:
                    if not step.is_done():
                        step.status = ExecutionStatus.SKIPPED
                        step.error = "Skipped because the user stopped the analysis."
                plan.status = ExecutionStatus.COMPLETED
                language = plan.language or "en"
                stop_message = (
                    "Analysis stopped by your request."
                    if language == "en"
                    else "Analisis dihentikan atas permintaan Anda."
                )
                for event in (
                    MessageEvent(role="assistant", message=stop_message),
                    PlanEvent(status=PlanStatus.COMPLETED, plan=plan),
                ):
                    event_id = await task.output_stream.put(event.model_dump_json())
                    event.id = event_id
                    await self._session_repository.add_event(session_id, event)
            task.cancel()
        await self._session_repository.update_status(session_id, SessionStatus.COMPLETED)

    async def chat(
        self,
        session_id: str,
        user_id: str,
        message: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        latest_event_id: Optional[str] = None,
        attachments: Optional[List[dict]] = None
    ) -> AsyncGenerator[BaseEvent, None]:
        """
        Chat with an agent
        """

        try:
            session = await self._session_repository.find_by_id_and_user_id(session_id, user_id)
            if not session:
                logger.error(f"Attempted to chat with non-existent Session {session_id} for user {user_id}")
                raise RuntimeError("Session not found")

            task = await self._get_task(session)

            if message:
                def _to_utc_naive(dt: datetime) -> datetime:
                    if dt.tzinfo is not None:
                        return dt.astimezone(timezone.utc).replace(tzinfo=None)
                    return dt

                incoming_ts = _to_utc_naive(timestamp) if timestamp else None
                stored_ts   = _to_utc_naive(session.latest_message_at) if session.latest_message_at else None

                is_reconnect = (
                    session.status == SessionStatus.RUNNING
                    and task is not None
                    and incoming_ts is not None
                    and stored_ts is not None
                    and abs((incoming_ts - stored_ts).total_seconds()) < 10
                )

                if is_reconnect:
                    logger.info(
                        "[Dedup] Session %s: duplicate message detected (reconnect) — "
                        "skipping re-queue, re-subscribing to existing task output",
                        session_id,
                    )
                else:
                    if session.status != SessionStatus.RUNNING or task is None:
                        task = await self._create_task(session)
                        if not task:
                            raise RuntimeError("Failed to create task")

                    assert task is not None, "task must not be None after creation guard"
                    await self._session_repository.update_latest_message(session_id, message, timestamp or datetime.now())

                    message_event = MessageEvent(
                        message=message,
                        role="user",
                        attachments=[
                            FileInfo(
                                file_id=attachment.get("file_id"),
                                filename=attachment.get("filename"),
                                content_type=attachment.get("content_type"),
                                size=attachment.get("size"),
                            )
                            for attachment in attachments
                        ] if attachments else None
                    )

                    event_id = await task.input_stream.put(message_event.model_dump_json())
                    message_event.id = event_id
                    await self._session_repository.add_event(session_id, message_event)

                    await task.run()
                    logger.debug(f"Put message into Session {session_id}'s event queue: {message[:50]}...")

            logger.info(f"Session {session_id} started")
            logger.debug(f"Session {session_id} task: {task}")

            while task and not task.done:
                event_id, event_str = await task.output_stream.get(
                    start_id=latest_event_id or "0-0",
                    block_ms=1000,
                )
                latest_event_id = event_id
                if event_str is None:
                    logger.debug(f"No event found in Session {session_id}'s event queue")
                    continue
                event = TypeAdapter(AgentEvent).validate_json(event_str)
                event.id = event_id
                logger.debug(f"Got event from Session {session_id}'s event queue: {type(event).__name__}")
                await self._session_repository.update_unread_message_count(session_id, user_id, 0)
                yield event
                if isinstance(event, (DoneEvent, ErrorEvent, WaitEvent)):
                    break

            logger.info(f"Session {session_id} completed")

        except Exception as e:
            logger.exception(f"Error in Session {session_id}")
            event = ErrorEvent(error=str(e))
            await self._session_repository.add_event(session_id, event)
            yield event
        finally:
            await self._session_repository.update_unread_message_count(session_id, user_id, 0)
