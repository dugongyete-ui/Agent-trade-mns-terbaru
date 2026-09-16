from typing import Dict, Any, List, AsyncGenerator, Optional
import json
import logging
from app.domain.models.plan import Plan, Step
from app.domain.models.message import Message, VisionImage
from app.domain.services.agents.base import BaseAgent
from app.domain.models.memory import Memory
from app.domain.services.prompts.system import SYSTEM_PROMPT
from app.domain.services.prompts.planner import (
    CREATE_PLAN_PROMPT, 
    UPDATE_PLAN_PROMPT,
    PLANNER_SYSTEM_PROMPT
)
from app.domain.models.event import (
    BaseEvent,
    PlanEvent,
    PlanStatus,
    ErrorEvent,
    MessageEvent,
    MessageChunkEvent,
    ThinkingEvent,
    DoneEvent,
)
from langchain.messages import HumanMessage as LCHumanMessage
import httpx
from langchain.chat_models import init_chat_model
from app.core.config import get_settings
from app.domain.services.tools.base import BaseToolkit
from app.domain.repositories.agent_repository import AgentRepository

logger = logging.getLogger(__name__)

class PlannerAgent(BaseAgent):
    """
    Planner agent class, defining the basic behavior of planning
    """

    name: str = "planner"
    system_prompt: str = SYSTEM_PROMPT + PLANNER_SYSTEM_PROMPT
    format: Optional[str] = "json_object"
    tool_choice: Optional[str] = "none"

    def __init__(
        self,
        agent_id: str,
        agent_repository: AgentRepository,
        tools: List[BaseToolkit],
    ):
        super().__init__(
            agent_id=agent_id,
            agent_repository=agent_repository,
            tools=tools,
        )

        # Override self._model with a dedicated planner model if configured.
        # This allows using a larger/different model for planning vs execution.
        settings = get_settings()
        if settings.planner_model_name:
            try:
                provider = settings.planner_model_provider or settings.model_provider
                pkwargs = dict(
                    model=settings.planner_model_name,
                    model_provider=provider,
                    temperature=settings.temperature,
                    base_url=settings.planner_api_base or settings.api_base,
                )
                if settings.planner_api_key:
                    if provider in ("openai",):
                        pkwargs["openai_api_key"] = settings.planner_api_key
                    else:
                        pkwargs["api_key"] = settings.planner_api_key
                if settings.extra_headers:
                    pkwargs["default_headers"] = settings.extra_headers
                planner_base = settings.planner_api_base or settings.api_base
                if planner_base:
                    pkwargs["http_client"] = httpx.Client(verify=settings.ssl_verify)
                    pkwargs["http_async_client"] = httpx.AsyncClient(verify=settings.ssl_verify)
                from langchain.chat_models import init_chat_model as _init
                self._model = _init(**pkwargs)
                # The override model is a generic chat model — drop the
                # thinking variants so _current_model() falls back to it
                # directly instead of silently using the MAIN model.
                self._model_think = None
                self._model_no_think = None
                logger.info(
                    f"Planner model overridden: {settings.planner_model_name} "
                    f"(provider={provider})"
                )
            except Exception as e:
                logger.warning(f"Failed to initialise planner model, using main model: {e}")

        # Initialize a dedicated vision model if configured, otherwise None.
        # When None we try the main model directly (works for GPT-4o etc.).
        self._vision_model = None
        if settings.vision_model_name:
            try:
                provider = settings.vision_model_provider or settings.model_provider
                kwargs = dict(
                    model=settings.vision_model_name,
                    model_provider=provider,
                    temperature=settings.temperature,
                    base_url=settings.vision_api_base or settings.api_base,
                )
                # Pass the vision-specific API key explicitly so it is not
                # confused with the main model's key.
                if settings.vision_api_key:
                    # LangChain's init_chat_model forwards kwargs to the
                    # underlying ChatModel constructor.  For the "openai"
                    # provider (incl. OpenAI-compat endpoints like Cohere)
                    # the accepted parameter is openai_api_key.
                    if provider in ("openai",):
                        kwargs["openai_api_key"] = settings.vision_api_key
                    else:
                        # Generic fallback — works for some providers
                        kwargs["api_key"] = settings.vision_api_key
                if settings.extra_headers:
                    kwargs["default_headers"] = settings.extra_headers
                vision_base = settings.vision_api_base or settings.api_base
                if vision_base:
                    kwargs["http_client"] = httpx.Client(verify=False)
                    kwargs["http_async_client"] = httpx.AsyncClient(verify=False)
                self._vision_model = init_chat_model(**kwargs)
                logger.info(
                    f"Vision model initialised: {settings.vision_model_name} "
                    f"(provider={provider}, base_url={kwargs.get('base_url')})"
                )
            except Exception as e:
                logger.warning(f"Failed to initialise vision model, falling back to main model: {e}")

    def _build_vision_content(self, text: str, images: List[VisionImage]) -> list:
        """Build a multimodal message content list with text + images."""
        content = [{"type": "text", "text": text}]
        for img in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img.content_type};base64,{img.data}"}
            })
        return content

    async def _analyze_images(self, images: List[VisionImage], context: str) -> str:
        """Use the dedicated vision model to describe images as text."""
        prompt = (
            f"The user sent you these images as part of this request: {context}\n\n"
            "Describe each image in detail. Focus on what is visually present, "
            "any text visible, the overall content, and anything relevant to the user's request."
        )
        content = self._build_vision_content(prompt, images)
        try:
            response = await self._vision_model.ainvoke([LCHumanMessage(content=content)])
            return response.content if isinstance(response.content, str) else ""
        except Exception as e:
            logger.warning(f"Vision model image analysis failed: {e}")
            return ""

    async def _get_previous_file_names(self) -> list:
        """Scan conversation memory for file names analyzed in previous turns.

        Looks for <file name="..."> patterns in stored HumanMessages so that
        follow-up questions about previously uploaded files can reference them.
        Returns a deduplicated list of file names in order of first appearance.
        """
        import re
        await self._ensure_memory()
        names = []
        seen = set()
        for msg in self.memory.get_messages():
            content = ""
            if hasattr(msg, "content"):
                if isinstance(msg.content, str):
                    content = msg.content
                elif isinstance(msg.content, list):
                    content = " ".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in msg.content
                    )
            for n in re.findall(r'<file name="([^"]+)">', content):
                if n not in seen:
                    seen.add(n)
                    names.append(n)
        return names

    async def acknowledge(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
        """Stream an acknowledgment in < 1 s before full JSON planning begins.

        Uses LangChain astream() so the very first token reaches the client as
        soon as the model starts responding — typically well under one second.
        """
        import re

        # Collect context clues (files, images) so the AI is aware of what's present.
        # We do NOT prescribe the exact wording — let the model respond naturally.
        context_note = ""

        # Pre-extracted files (<file name="..."> tags in the message text)
        pre_extracted = re.findall(r'<file name="([^"]+)">', message.message)
        if pre_extracted:
            context_note = f"\n[Files available: {', '.join(pre_extracted)}]"
        # Raw file attachments
        elif message.attachments:
            file_names = [a.split("/")[-1] for a in message.attachments if a]
            if file_names:
                context_note = f"\n[Files available: {', '.join(file_names)}]"
        # Vision images only
        elif message.vision_images:
            context_note = "\n[Image(s) attached]"
        # No current file — check prior conversation turns
        else:
            prev_files = await self._get_previous_file_names()
            if prev_files:
                context_note = f"\n[Previously shared files: {', '.join(prev_files)}]"

        prompt = (
            f"{message.message}{context_note}\n\n"
            "Give a short, natural opening reply in the same language as the user. "
            "Just react to what they asked — no rigid format, no lists, no bullet points."
        )
        try:
            full_text = ""
            reasoning_text = ""
            # Use full conversation history so the model can actually read previously
            # uploaded file content — not just a hint that the content "exists".
            await self._ensure_memory()
            context = list(self.memory.get_messages())
            context.append(LCHumanMessage(content=prompt))
            async for chunk in self._current_model().astream(context):
                # Live reasoning stream for the collapsible Thinking UI.
                reasoning_delta = (getattr(chunk, "additional_kwargs", {}) or {}).get("reasoning_content")
                if reasoning_delta:
                    reasoning_text += reasoning_delta
                    yield ThinkingEvent(content=reasoning_delta, done=False)
                text = chunk.content if isinstance(chunk.content, str) else ""
                if text:
                    full_text += text
                    yield MessageChunkEvent(content=text, done=False)
            if reasoning_text:
                yield ThinkingEvent(content=reasoning_text, done=True)
            if full_text:
                yield MessageChunkEvent(content="", done=True)
                # Persist the acknowledgment so it survives page refresh.
                # MessageChunkEvent is transient (not saved to DB), so we follow up
                # with a MessageEvent that IS saved.  The frontend replaces the
                # streaming bubble with this rather than creating a duplicate.
                yield MessageEvent(role="assistant", message=full_text, source="ack")
        except Exception as e:
            logger.warning(f"Acknowledge streaming failed, skipping: {e}")

    async def create_plan(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
        # If the current message has no pre-extracted files, check conversation
        # history for files from earlier turns.  Injecting their names helps the
        # planner understand that follow-up questions ("tell me more", "kirim isi
        # nya") refer to those previously analyzed documents.
        prev_files_note = ""
        if "<file name=" not in message.message:
            prev_file_names = await self._get_previous_file_names()
            if prev_file_names:
                prev_files_note = (
                    f"\n\nConversation context — files analyzed earlier in this session "
                    f"(their full content is in your conversation history): "
                    f"{', '.join(prev_file_names)}. "
                    "If the user's request is about any of these files, answer using the "
                    "content already in your memory. Do NOT say there is no file available."
                )
                logger.info(
                    f"Injecting previous file context into create_plan: {prev_file_names}"
                )

        base_prompt = CREATE_PLAN_PROMPT.format(
            message=message.message + prev_files_note,
            attachments="\n".join(message.attachments)
        )

        content = base_prompt

        if message.vision_images:
            if self._vision_model:
                # Dedicated vision model: analyse images separately, inject description as text.
                logger.info("Using dedicated vision model to analyse images")
                description = await self._analyze_images(message.vision_images, message.message)
                if description:
                    content = base_prompt + f"\n\n[Image Analysis]\n{description}"
                # content stays text-only — main model never sees raw images
            else:
                # No dedicated vision model — try passing images directly to the main model.
                # This works for multimodal models like GPT-4o. If it fails we retry text-only.
                content = self._build_vision_content(base_prompt, message.vision_images)

        async def _run(c):
            async for event in self.execute(c):
                yield event

        # First attempt
        failed_with_vision = False
        events_buffer = []
        try:
            async for event in _run(content):
                if isinstance(event, MessageEvent):
                    logger.info(event.message)
                    parsed_response = await self._parse_json(event.message)
                    if parsed_response is None:
                        raise ValueError("Planner returned non-JSON response during create_plan")
                    plan = Plan.model_validate(parsed_response)
                    yield PlanEvent(status=PlanStatus.CREATED, plan=plan)
                    return
                else:
                    events_buffer.append(event)
                    yield event
        except Exception as e:
            error_str = str(e).lower()
            if message.vision_images and not self._vision_model and (
                "image" in error_str or "vision" in error_str or "multimodal" in error_str
                or "unsupported" in error_str or "invalid request" in error_str
            ):
                logger.warning(f"Main model rejected image content, retrying text-only: {e}")
                failed_with_vision = True
            else:
                raise

        # Fallback: retry without images (text-only)
        if failed_with_vision:
            logger.info("Retrying create_plan without vision images")
            note = (
                "\n\n[Note: The user attached image(s) but the current model does not support "
                "image analysis. Please proceed based on the text request only, "
                "or set VISION_MODEL_NAME to enable image understanding.]"
            )
            fallback_content = base_prompt + note
            async for event in self.execute(fallback_content):
                if isinstance(event, MessageEvent):
                    logger.info(event.message)
                    parsed_response = await self._parse_json(event.message)
                    if parsed_response is None:
                        raise ValueError("Planner returned non-JSON response during create_plan (text-only fallback)")
                    plan = Plan.model_validate(parsed_response)
                    yield PlanEvent(status=PlanStatus.CREATED, plan=plan)
                else:
                    yield event

    async def update_plan(self, plan: Plan, step: Step) -> AsyncGenerator[BaseEvent, None]:
        message = UPDATE_PLAN_PROMPT.format(plan=plan.dump_json(), step=step.model_dump_json())
        async for event in self.execute(message):
            if isinstance(event, MessageEvent):
                logger.debug(f"Planner agent update plan: {event.message}")
                parsed_response = await self._parse_json(event.message)
                if parsed_response is None:
                    logger.warning("Planner returned non-JSON response during update_plan, keeping current plan")
                    yield PlanEvent(status=PlanStatus.UPDATED, plan=plan)
                    return
                updated_plan = Plan.model_validate(parsed_response)
                new_steps = [Step.model_validate(s) for s in updated_plan.steps]
                
                # Find the index of the first pending step
                first_pending_index = None
                for i, step in enumerate(plan.steps):
                    if not step.is_done():
                        first_pending_index = i
                        break
                
                # If there are pending steps, replace all pending steps
                if first_pending_index is not None:
                    # Keep completed steps
                    updated_steps = plan.steps[:first_pending_index]
                    # Add new steps
                    updated_steps.extend(new_steps)
                    # Update steps in plan
                    plan.steps = updated_steps
                
                yield PlanEvent(status=PlanStatus.UPDATED, plan=plan)
            else:
                yield event
