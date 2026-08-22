from typing import AsyncGenerator, Optional, List
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from app.domain.models.session import Session, SessionSummary
from app.domain.repositories.session_repository import SessionRepository
from app.domain.models.agent import Agent
from app.domain.services.agent_domain_service import AgentDomainService
from app.domain.models.event import AgentEvent
from typing import Type
from app.domain.external.search import SearchEngine
from app.domain.external.file import FileStorage
from app.domain.repositories.agent_repository import AgentRepository
from app.domain.external.task import Task
from app.domain.models.file import FileInfo
from app.core.config import get_settings
from app.domain.repositories.mcp_repository import MCPRepository
from app.domain.models.session import SessionStatus
from app.application.errors.exceptions import BadRequestError, NotFoundError

logger = logging.getLogger(__name__)

class AgentService:
    def __init__(
        self,
        agent_repository: AgentRepository,
        session_repository: SessionRepository,
        task_cls: Type[Task],
        file_storage: FileStorage,
        mcp_repository: MCPRepository,
        search_engine: Optional[SearchEngine] = None,
    ):
        logger.info("Initializing AgentService")
        self._agent_repository = agent_repository
        self._session_repository = session_repository
        self._file_storage = file_storage
        self._agent_domain_service = AgentDomainService(
            agent_repository=self._agent_repository,
            session_repository=self._session_repository,
            task_cls=task_cls,
            file_storage=file_storage,
            mcp_repository=mcp_repository,
            search_engine=search_engine,
        )
        self._search_engine = search_engine

    async def create_session(self, user_id: str) -> Session:
        logger.info(f"Creating new session for user: {user_id}")
        agent = await self._create_agent()
        session = Session(agent_id=agent.id, user_id=user_id)
        logger.info(f"Created new Session with ID: {session.id} for user: {user_id}")
        await self._session_repository.save(session)
        return session

    async def _create_agent(self) -> Agent:
        logger.info("Creating new agent")
        settings = get_settings()
        agent = Agent(
            model_name=settings.model_name,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens or 2000,
        )
        logger.info(f"Created new Agent with ID: {agent.id}")
        await self._agent_repository.save(agent)
        logger.info(f"Agent created successfully with ID: {agent.id}")
        return agent

    async def _persist_attachments(
        self,
        session_id: str,
        user_id: str,
        attachments: Optional[List[dict]],
    ) -> None:
        """Attach uploaded files to an owned session exactly once.

        The chat payload contains only file IDs and browser metadata. The
        storage lookup is authoritative and owner-bound; client-supplied names
        and sizes are never persisted as session file metadata.
        """
        if not attachments:
            return
        session = await self._session_repository.find_by_id_and_user_id(session_id, user_id)
        if not session:
            raise NotFoundError("Session not found")
        known_file_ids = {file_info.file_id for file_info in session.files}
        for attachment in attachments:
            file_id = attachment.get("file_id") if isinstance(attachment, dict) else None
            if not file_id or not isinstance(file_id, str):
                raise BadRequestError("Invalid attachment")
            if file_id in known_file_ids:
                continue
            file_info = await self._file_storage.get_file_info(file_id, user_id)
            if not file_info:
                raise NotFoundError("File not found")
            await self._session_repository.add_file(session_id, file_info)
            known_file_ids.add(file_id)

    async def chat(
        self,
        session_id: str,
        user_id: str,
        message: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        event_id: Optional[str] = None,
        attachments: Optional[List[dict]] = None
    ) -> AsyncGenerator[AgentEvent, None]:
        logger.info(f"Starting chat with session {session_id}: {(message or '')[:50]}...")
        await self._persist_attachments(session_id, user_id, attachments)
        async for event in self._agent_domain_service.chat(session_id, user_id, message, timestamp, event_id, attachments):
            logger.debug(f"Received event: {event}")
            yield event
        logger.info(f"Chat with session {session_id} completed")

    async def get_session(self, session_id: str, user_id: Optional[str] = None) -> Optional[Session]:
        logger.info(f"Getting session {session_id} for user {user_id}")
        if not user_id:
            session = await self._session_repository.find_by_id(session_id)
        else:
            session = await self._session_repository.find_by_id_and_user_id(session_id, user_id)
        if not session:
            logger.error(f"Session {session_id} not found for user {user_id}")
        return session

    async def get_all_sessions(self, user_id: str) -> List[SessionSummary]:
        logger.debug(f"Getting all sessions for user {user_id}")
        return await self._session_repository.find_summaries_by_user_id(user_id)

    async def delete_session(self, session_id: str, user_id: str) -> None:
        logger.info(f"Deleting session {session_id} for user {user_id}")
        session = await self._session_repository.find_by_id_and_user_id(session_id, user_id)
        if not session:
            logger.error(f"Session {session_id} not found for user {user_id}")
            raise RuntimeError("Session not found")
        await self._session_repository.delete(session_id)
        logger.info(f"Session {session_id} deleted successfully")

    async def delete_all_sessions(self, user_id: str) -> int:
        logger.info(f"Deleting all sessions for user {user_id}")
        count = await self._session_repository.delete_all_by_user_id(user_id)
        logger.info(f"Deleted {count} sessions for user {user_id}")
        return count

    async def stop_session(self, session_id: str, user_id: str) -> None:
        logger.info(f"Stopping session {session_id} for user {user_id}")
        session = await self._session_repository.find_by_id_and_user_id(session_id, user_id)
        if not session:
            logger.error(f"Session {session_id} not found for user {user_id}")
            raise RuntimeError("Session not found")
        await self._agent_domain_service.stop_session(session_id)
        logger.info(f"Session {session_id} stopped successfully")

    async def clear_unread_message_count(self, session_id: str, user_id: str) -> None:
        logger.info(f"Clearing unread message count for session {session_id} for user {user_id}")
        session = await self._session_repository.find_by_id_and_user_id(session_id, user_id)
        if not session:
            raise RuntimeError("Session not found")
        await self._session_repository.update_unread_message_count(session_id, user_id, 0)
        logger.info(f"Unread message count cleared for session {session_id}")

    async def shutdown(self):
        logger.info("Closing all agents and cleaning up resources")
        await self._agent_domain_service.shutdown()
        logger.info("All agents closed successfully")

    async def get_session_files(self, session_id: str, user_id: Optional[str] = None) -> List[FileInfo]:
        logger.info(f"Getting files for session {session_id} for user {user_id}")
        session = await self.get_session(session_id, user_id)
        if not session:
            raise RuntimeError("Session not found")
        return session.files

    @staticmethod
    def _hash_share_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def get_shared_session_files(
        self, session_id: str, share_token: str
    ) -> List[FileInfo]:
        logger.info("Getting files for shared session %s", session_id)
        session = await self.get_shared_session(session_id, share_token)
        if not session:
            logger.error("Shared session %s not found or token invalid", session_id)
            raise RuntimeError("Session not found")
        return session.files

    async def share_session(self, session_id: str, user_id: str) -> str:
        logger.info("Sharing session %s for user %s", session_id, user_id)
        session = await self._session_repository.find_by_id_and_user_id(session_id, user_id)
        if not session:
            logger.error("Session %s not found for user %s", session_id, user_id)
            raise RuntimeError("Session not found")

        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=get_settings().share_token_expire_days
        )
        await self._session_repository.update_shared_status(
            session_id,
            user_id,
            True,
            self._hash_share_token(token),
            expires_at,
        )
        logger.info("Session %s shared successfully", session_id)
        return token

    async def unshare_session(self, session_id: str, user_id: str) -> None:
        logger.info("Unsharing session %s for user %s", session_id, user_id)
        session = await self._session_repository.find_by_id_and_user_id(session_id, user_id)
        if not session:
            logger.error("Session %s not found for user %s", session_id, user_id)
            raise RuntimeError("Session not found")
        await self._session_repository.update_shared_status(
            session_id, user_id, False, None, None
        )
        logger.info("Session %s unshared successfully", session_id)

    async def get_shared_session(
        self, session_id: str, share_token: str
    ) -> Optional[Session]:
        logger.info("Getting shared session %s", session_id)
        if not share_token:
            return None
        return await self._session_repository.find_shared_by_token(
            session_id, self._hash_share_token(share_token)
        )

    async def is_session_shared(self, session_id: str) -> bool:
        logger.info(f"Checking if session {session_id} is shared")
        session = await self._session_repository.find_by_id(session_id)
        if not session:
            logger.error(f"Session {session_id} not found")
            raise RuntimeError("Session not found")
        return session.is_shared
