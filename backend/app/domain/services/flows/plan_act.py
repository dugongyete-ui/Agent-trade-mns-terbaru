import logging
from app.domain.services.flows.base import BaseFlow
from app.domain.models.message import Message
from typing import AsyncGenerator, List, Optional
from enum import Enum
from app.domain.models.event import (
    BaseEvent,
    PlanEvent,
    PlanStatus,
    MessageEvent,
    DoneEvent,
    TitleEvent,
    ToolEvent,
    ToolStatus,
    WaitEvent,
)
from langchain.messages import HumanMessage as LCHumanMessage
from app.domain.models.plan import ExecutionStatus
from app.domain.services.agents.planner import PlannerAgent
from app.domain.services.agents.execution import ExecutionAgent
from app.domain.external.search import SearchEngine
from app.domain.repositories.agent_repository import AgentRepository
from app.domain.repositories.session_repository import SessionRepository
from app.domain.models.session import SessionStatus
from app.domain.services.tools.mcp import MCPToolkit
from app.domain.services.tools.message import MessageToolkit
from app.domain.services.tools.search import SearchToolkit
from app.core.config import get_settings

settings = get_settings()

logger = logging.getLogger(__name__)

class AgentStatus(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    UPDATING = "updating"

class PlanActFlow(BaseFlow):
    def __init__(
        self,
        agent_id: str,
        agent_repository: AgentRepository,
        session_id: str,
        session_repository: SessionRepository,
        mcp_tool: MCPToolkit,
        search_engine: Optional[SearchEngine] = None,
    ):
        self._agent_id = agent_id
        self._repository = agent_repository
        self._session_id = session_id
        self._session_repository = session_repository
        self.status = AgentStatus.IDLE
        self.plan = None
        self._step_history: List[dict] = []  # AgentHistoryList — tracks every step result

        settings = get_settings()
        self._max_steps: int = settings.max_steps

        tools = [
            MessageToolkit(),
            mcp_tool,
        ]

        # Only add search tool when search_engine is not None
        if search_engine:
            tools.append(SearchToolkit(search_engine))

        # Create planner and execution agents
        self.planner = PlannerAgent(
            agent_id=self._agent_id,
            agent_repository=self._repository,
            tools=tools,
        )
        logger.debug(f"Created planner agent for Agent {self._agent_id}")
            
        self.executor = ExecutionAgent(
            agent_id=self._agent_id,
            agent_repository=self._repository,
            tools=tools,
        )
        logger.debug(f"Created execution agent for Agent {self._agent_id}")

        # Inject extend_system_message — extra instructions added to both agents' prompts.
        # This is applied per-instance so the class attribute stays untouched.
        if settings.extend_system_message:
            extra = "\n\n" + settings.extend_system_message
            self.executor.system_prompt = self.executor.system_prompt + extra
            self.planner.system_prompt = self.planner.system_prompt + extra
            logger.debug("extend_system_message injected into executor and planner")

    @staticmethod
    def _notification_message(event: BaseEvent) -> Optional[MessageEvent]:
        """Convert planner narration tool calls into visible chat messages."""
        if not (
            isinstance(event, ToolEvent)
            and event.status == ToolStatus.CALLING
            and event.function_name == "message_notify_user"
        ):
            return None

        raw_attachments = event.function_args.get("attachments")
        attachments = (
            [raw_attachments]
            if isinstance(raw_attachments, str)
            else list(raw_attachments or [])
        )
        from app.domain.models.file import FileInfo
        return MessageEvent(
            role="assistant",
            message=event.function_args.get("text", ""),
            attachments=[FileInfo(file_path=p) for p in attachments if p] or None,
        )

    async def _preprocess_images(self, message: Message) -> Message:
        """Analyze vision images once using the dedicated vision model (if configured).

        Injects a rich text description into the message and clears raw image data so
        that downstream agents (planner + executor) only ever receive plain text — even
        when the main model does not support multimodal input.
        """
        if not message.vision_images:
            return message
        if not self.planner._vision_model:
            return message  # agents will fall back individually

        logger.info("Pre-processing vision images with dedicated vision model")
        try:
            description = await self.planner._analyze_images(
                message.vision_images, message.message
            )
        except Exception as e:
            logger.warning(f"Vision pre-processing failed, passing raw images to agents: {e}")
            return message

        if not description:
            return message

        from copy import deepcopy
        enriched = deepcopy(message)
        enriched.message = message.message + f"\n\n[Image Analysis]\n{description}"
        enriched.vision_images = []
        logger.info("Vision pre-processing complete — image descriptions injected into message")
        return enriched

    async def run(self, message: Message) -> AsyncGenerator[BaseEvent, None]:

        # Analyze vision images once up-front so every downstream agent
        # receives a consistent, text-enriched message without raw image data.
        message = await self._preprocess_images(message)

        # TODO: move to task runner
        session = await self._session_repository.find_by_id(self._session_id)
        if not session:
            raise ValueError(f"Session {self._session_id} not found")
        
        if session.status != SessionStatus.PENDING:
            logger.debug(f"Session {self._session_id} is not in PENDING status, rolling back")
            await self.executor.roll_back(message)
            await self.planner.roll_back(message)
        
        if session.status == SessionStatus.RUNNING:
            logger.debug(f"Session {self._session_id} is in RUNNING status")
            self.status = AgentStatus.PLANNING

        if session.status == SessionStatus.WAITING:
            logger.debug(f"Session {self._session_id} is in WAITING status")
            self.status = AgentStatus.EXECUTING

        await self._session_repository.update_status(self._session_id, SessionStatus.RUNNING)  
        self.plan = session.get_last_plan()

        logger.info(f"Agent {self._agent_id} started processing message: {message.message[:50]}...")
        step = None
        total_steps_executed = 0
        consecutive_failures = 0
        MAX_CONSECUTIVE_FAILURES = settings.max_consecutive_failures
        while True:
            if self.status == AgentStatus.IDLE:
                logger.info(f"Agent {self._agent_id} state changed from {AgentStatus.IDLE} to {AgentStatus.PLANNING}")
                self.status = AgentStatus.PLANNING
            elif self.status == AgentStatus.PLANNING:
                # ── Step 1: collect the plan first so we know whether steps exist ─────
                logger.info(f"Agent {self._agent_id} started creating plan")
                plan_events_buffer = []
                async for event in self.planner.create_plan(message):
                    plan_events_buffer.append(event)
                    if isinstance(event, PlanEvent) and event.status == PlanStatus.CREATED:
                        self.plan = event.plan

                        has_pre_extracted = "<file name=" in message.message

                        # Safety net A: 0 steps + raw file attachments (no <file> tags)
                        if len(self.plan.steps) == 0 and message.attachments and not has_pre_extracted:
                            from app.domain.models.plan import Step as PlanStep
                            file_list = "\n".join(message.attachments)
                            self.plan.steps = [PlanStep(
                                id="1",
                                description=(
                                    f"Extract and analyze the content of the uploaded file(s):\n"
                                    f"{file_list}\n"
                                    f"Save extracted text to /tmp/extracted_content.txt, "
                                    f"then read it and respond to the user's request."
                                )
                            )]
                            logger.warning(
                                f"Agent {self._agent_id}: planner returned 0 steps with "
                                f"{len(message.attachments)} raw attachment(s) — injected default step"
                            )

                        # Safety net B: 0 steps + pre-extracted <file> tags
                        if len(self.plan.steps) == 0 and has_pre_extracted:
                            from app.domain.models.plan import Step as PlanStep
                            import re as _re
                            fname_match = _re.search(r'<file name="([^"]+)"', message.message)
                            fname = fname_match.group(1) if fname_match else "the uploaded file"
                            self.plan.steps = [PlanStep(
                                id="1",
                                description=(
                                    f"The file \"{fname}\" content is already in the user message "
                                    f"inside <file> tags. Read it and respond to the user's request fully."
                                )
                            )]
                            logger.info(
                                f"Agent {self._agent_id}: routed pre-extracted file request "
                                f"through executor for a complete response"
                            )

                logger.info(f"Agent {self._agent_id} created plan with {len(self.plan.steps)} steps")

                # ── Step 2: now stream acknowledge OR direct answer based on step count ─
                if len(self.plan.steps) == 0:
                    # Simple / conversational query — plan.message IS the full answer.
                    # Skip acknowledge entirely to avoid a double-response bubble.
                    yield TitleEvent(title=self.plan.title)
                    if self.plan.message:
                        yield MessageEvent(role="assistant", message=self.plan.message)
                else:
                    # Complex query: send acknowledgment streaming NOW (gives quick feedback
                    # while the user watches the plan appear below it).
                    logger.info(f"Agent {self._agent_id} streaming acknowledgment")
                    async for ack_event in self.planner.acknowledge(message):
                        yield ack_event

                    # Emit buffered plan events so the UI can render the steps list
                    yield TitleEvent(title=self.plan.title)
                    for event in plan_events_buffer:
                        if isinstance(event, PlanEvent):
                            yield event
                        else:
                            notification = self._notification_message(event)
                            if notification:
                                yield notification

                logger.info(f"Agent {self._agent_id} state changed from {AgentStatus.PLANNING} to {AgentStatus.EXECUTING}")
                self.status = AgentStatus.EXECUTING
                if len(self.plan.steps) == 0:
                    logger.info(f"Agent {self._agent_id} no steps — moving directly to COMPLETED")
                    self.status = AgentStatus.COMPLETED
                    
            elif self.status == AgentStatus.EXECUTING:
                # Guard: max total steps across entire task
                if total_steps_executed >= self._max_steps:
                    logger.warning(
                        f"Agent {self._agent_id} reached max_steps limit ({self._max_steps}), "
                        f"stopping execution to prevent infinite loop"
                    )
                    _lang = getattr(self.plan, "language", "id") or "id"
                    yield MessageEvent(
                        role="assistant",
                        message=(
                            "I've reached the maximum number of analysis steps. "
                            "I'll now deliver the summary based on all data collected so far."
                            if _lang == "en" else
                            "Batas langkah analisis tercapai. "
                            "Saya akan menyampaikan ringkasan berdasarkan semua data yang sudah terkumpul."
                        ),
                    )
                    await self.executor._ensure_memory()
                    self.executor.memory.add_message(LCHumanMessage(content=(
                        "[INTERNAL CONTEXT — do not reference this directly in your response]: "
                        f"Analysis was stopped at the {self._max_steps}-step limit. "
                        "Not all planned investigations were completed. "
                        "Calibrate your conviction accordingly — do not present the summary as if every planned data point was collected."
                    )))
                    self.status = AgentStatus.SUMMARIZING
                    continue

                # Execute plan
                self.plan.status = ExecutionStatus.RUNNING
                step = self.plan.get_next_step()
                if not step:
                    logger.info(f"Agent {self._agent_id} has no more steps, state changed from {AgentStatus.EXECUTING} to {AgentStatus.COMPLETED}")
                    self.status = AgentStatus.SUMMARIZING
                    continue

                # Execute step
                logger.info(
                    f"Agent {self._agent_id} executing step {step.id} "
                    f"[{total_steps_executed + 1}/{self._max_steps}]: {step.description[:60]}..."
                )
                waiting_for_user = False
                async for event in self.executor.execute_step(self.plan, step, message):
                    yield event
                    if isinstance(event, WaitEvent):
                        waiting_for_user = True

                if waiting_for_user:
                    logger.info(
                        "Agent %s is waiting for user input at step %s",
                        self._agent_id,
                        step.id,
                    )
                    return

                # Record step in AgentHistoryList
                total_steps_executed += 1
                self._step_history.append({
                    "step_id": step.id,
                    "description": step.description,
                    "success": step.success,
                    "result": step.result,
                    "total": total_steps_executed,
                })
                logger.info(
                    f"Agent {self._agent_id} completed step {step.id} "
                    f"(success={step.success}, total_executed={total_steps_executed})"
                )

                if self.executor.market_closed_detected:
                    logger.info(
                        f"Agent {self._agent_id}: live market status is closed — "
                        "stopping analysis without summarization"
                    )
                    # Close the whole plan as a terminal safety outcome. The
                    # remaining steps were intentionally not run, not left
                    # pending; this keeps the persisted plan and UI in sync.
                    for remaining_step in self.plan.steps:
                        if not remaining_step.is_done():
                            remaining_step.status = ExecutionStatus.SKIPPED
                            remaining_step.error = "Skipped because the market is closed."
                    self.plan.status = ExecutionStatus.COMPLETED
                    self.status = AgentStatus.COMPLETED
                    continue

                # Track consecutive failures — if too many in a row, skip to summarize
                # to prevent the plan updater from creating an infinite retry storm.
                if not step.success:
                    consecutive_failures += 1
                    logger.warning(
                        f"Agent {self._agent_id} step {step.id} failed "
                        f"(consecutive_failures={consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})"
                    )
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        logger.warning(
                            f"Agent {self._agent_id} reached {MAX_CONSECUTIVE_FAILURES} consecutive "
                            f"step failures — skipping to SUMMARIZING to prevent retry storm"
                        )
                        _lang = getattr(self.plan, "language", "id") or "id"
                        yield MessageEvent(
                            role="assistant",
                            message=(
                                "Several consecutive steps encountered data retrieval issues. "
                                "I'll now summarize with the information that was successfully collected."
                                if _lang == "en" else
                                "Beberapa langkah berturut-turut mengalami kendala dalam mengambil data. "
                                "Saya akan melanjutkan dengan informasi yang berhasil dikumpulkan."
                            ),
                        )
                        await self.executor._ensure_memory()
                        self.executor.memory.add_message(LCHumanMessage(content=(
                            "[INTERNAL CONTEXT — do not reference this directly in your response]: "
                            f"Analysis stopped after {MAX_CONSECUTIVE_FAILURES} consecutive step failures. "
                            "Some planned data could not be retrieved. "
                            "Be honest about any gaps in the analysis and calibrate conviction accordingly."
                        )))
                        await self.executor.compact_memory()
                        self.status = AgentStatus.SUMMARIZING
                        continue
                else:
                    consecutive_failures = 0

                await self.executor.compact_memory()
                logger.debug(f"Agent {self._agent_id} compacted memory")
                self.status = AgentStatus.UPDATING
            elif self.status == AgentStatus.UPDATING:
                # Update plan
                logger.info(f"Agent {self._agent_id} started updating plan")
                async for event in self.planner.update_plan(self.plan, step):
                    notification = self._notification_message(event)
                    yield notification if notification else event
                logger.info(f"Agent {self._agent_id} plan update completed, state changed from {AgentStatus.UPDATING} to {AgentStatus.EXECUTING}")
                self.status = AgentStatus.EXECUTING
            elif self.status == AgentStatus.SUMMARIZING:
                # Conclusion
                logger.info(f"Agent {self._agent_id} started summarizing")
                async for event in self.executor.summarize():
                    yield event
                logger.info(f"Agent {self._agent_id} summarizing completed, state changed from {AgentStatus.SUMMARIZING} to {AgentStatus.COMPLETED}")
                self.status = AgentStatus.COMPLETED
            elif self.status == AgentStatus.COMPLETED:
                self.plan.status = ExecutionStatus.COMPLETED
                logger.info(f"Agent {self._agent_id} plan has been completed")
                yield PlanEvent(status=PlanStatus.COMPLETED, plan=self.plan)
                self.status = AgentStatus.IDLE
                break
        yield DoneEvent()
        
        logger.info(f"Agent {self._agent_id} message processing completed")
    
    def is_done(self) -> bool:
        return self.status == AgentStatus.IDLE