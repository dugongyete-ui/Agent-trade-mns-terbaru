from typing import AsyncGenerator, Optional, List
from app.domain.models.plan import Plan, Step, ExecutionStatus
from app.domain.models.file import FileInfo
from app.domain.models.message import Message, VisionImage
from app.domain.services.agents.base import BaseAgent
from app.domain.repositories.agent_repository import AgentRepository
from app.domain.services.prompts.system import SYSTEM_PROMPT
from app.domain.services.prompts.execution import EXECUTION_SYSTEM_PROMPT, EXECUTION_PROMPT, SUMMARIZE_PROMPT, SUMMARIZE_STREAM_PROMPT
from app.domain.models.event import (
    BaseEvent,
    StepEvent,
    StepStatus,
    ErrorEvent,
    MessageEvent,
    MessageChunkEvent,
    DoneEvent,
    ToolEvent,
    ToolStatus,
    WaitEvent,
)
from app.domain.services.tools.base import BaseToolkit
from langchain.messages import HumanMessage as LCHumanMessage
import json
import logging

logger = logging.getLogger(__name__)


class ExecutionAgent(BaseAgent):
    """
    Execution agent class, defining the basic behavior of execution
    """

    name: str = "execution"
    system_prompt: str = SYSTEM_PROMPT + EXECUTION_SYSTEM_PROMPT
    format: Optional[str] = None

    def __init__(
        self,
        agent_id: str,
        agent_repository: AgentRepository,
        tools: List[BaseToolkit],
    ):
        super().__init__(
            agent_id=agent_id,
            agent_repository=agent_repository,
            tools=tools
        )
        self.market_closed_detected = False

    def _stop_after_tool_result(self, function_name, tool_result) -> Optional[str]:
        """Stop Forex/Gold execution when the live hours tool says closed."""
        if "forex-market-hours" not in function_name:
            return None

        artifact = getattr(tool_result, "artifact", None)
        data = getattr(artifact, "data", None)
        if isinstance(data, dict):
            text = str(data.get("text", ""))
        else:
            text = str(data or getattr(tool_result, "content", "") or "")

        normalized = text.upper()
        if "WEEKEND" in normalized and "CLOSED" in normalized:
            return text
        if "ALL FOREX MARKETS CLOSED" in normalized:
            return text
        return None

    def _build_vision_content(self, text: str, images: List[VisionImage]) -> list:
        content = [{"type": "text", "text": text}]
        for img in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img.content_type};base64,{img.data}"}
            })
        return content

    async def _handle_execution_events(self, step: Step, content) -> AsyncGenerator[BaseEvent, None]:
        async for event in self.execute(content):
            if isinstance(event, ErrorEvent):
                # Log tool lookup errors but do NOT mark the step as FAILED here.
                # When a tool is not found, base.py already appends a ToolMessage telling
                # the LLM "this tool is not available — use only listed tools", so the LLM
                # can adapt and retry with the correct name.  Prematurely setting FAILED +
                # emitting StepEvent(FAILED) caused a FAILED→COMPLETED race in the UI
                # and masked the real error.  The step result handler below will set the
                # correct final status once the LLM produces its text response.
                logger.debug(f"Step {step.id} tool error (handled by LLM retry): {event.error}")
            elif isinstance(event, MessageEvent):
                parsed_response = await self._parse_json(event.message)
                if parsed_response is None:
                    logger.warning("Execution agent returned non-JSON response for step result")
                    step.success = False
                    step.result = event.message or "No result returned."
                    step.error = "LLM returned a non-JSON response."
                    step.status = ExecutionStatus.FAILED
                    yield StepEvent(status=StepStatus.FAILED, step=step)
                    return
                if isinstance(parsed_response, list):
                    # LLM returned a list (e.g. raw tool-call objects) instead of
                    # a Step dict.  Treat the step as successfully completed and
                    # serialise the list as the result text so the agent can
                    # continue rather than crashing.
                    logger.warning(
                        "Execution agent returned a list instead of a Step dict — "
                        "salvaging as raw result"
                    )
                    step.success = True
                    step.result = json.dumps(parsed_response, ensure_ascii=False)
                    step.status = ExecutionStatus.COMPLETED
                    yield StepEvent(status=StepStatus.COMPLETED, step=step)
                    return
                try:
                    new_step = Step.model_validate(parsed_response)
                except Exception as val_err:
                    logger.warning(
                        f"Step validation failed, salvaging as raw result: {val_err}"
                    )
                    step.success = True
                    step.result = (
                        json.dumps(parsed_response, ensure_ascii=False)
                        if not isinstance(parsed_response, str)
                        else parsed_response
                    )
                    step.status = ExecutionStatus.COMPLETED
                    yield StepEvent(status=StepStatus.COMPLETED, step=step)
                    return
                step.success = new_step.success
                step.result = new_step.result
                step.error = new_step.error
                step.attachments = new_step.attachments
                step.status = (
                    ExecutionStatus.COMPLETED
                    if step.success
                    else ExecutionStatus.FAILED
                )
                yield StepEvent(
                    status=StepStatus.COMPLETED if step.success else StepStatus.FAILED,
                    step=step,
                )
                return
            elif isinstance(event, ToolEvent):
                if event.function_name == "message_ask_user":
                    if event.status == ToolStatus.CALLING:
                        yield MessageEvent(message=event.function_args.get("text", ""))
                    elif event.status == ToolStatus.CALLED:
                        yield WaitEvent()
                        return
                    continue
                elif event.function_name == "message_notify_user" and event.status == ToolStatus.CALLING:
                    # A notification is user-facing narration, not merely a
                    # tool chip. Convert text-only notifications as well as
                    # attachment notifications into a visible message event.
                    raw_att = event.function_args.get("attachments")
                    att_list = [raw_att] if isinstance(raw_att, str) else list(raw_att or [])
                    att_list = [p for p in att_list if p]
                    self._notification_emitted = True
                    yield MessageEvent(
                        message=event.function_args.get("text", ""),
                        attachments=[FileInfo(file_path=p) for p in att_list] or None,
                    )
                    continue
            yield event

        if self._stop_after_tool_reason:
            market_status = self._stop_after_tool_reason
            self._stop_after_tool_reason = None
            self.market_closed_detected = True
            message = (
                f"{market_status}\n\n"
                "Saya berhenti di sini karena market sedang tutup. "
                "Tidak ada analisis indikator atau rekomendasi entry yang dijalankan."
            )
            step.status = ExecutionStatus.COMPLETED
            step.success = True
            step.result = message
            step.error = None
            yield MessageEvent(role="assistant", message=message)
            yield StepEvent(status=StepStatus.COMPLETED, step=step)

    async def execute_step(self, plan: Plan, step: Step, message: Message) -> AsyncGenerator[BaseEvent, None]:
        prompt = EXECUTION_PROMPT.format(
            step=step.description,
            message=message.message,
            attachments="\n".join(message.attachments),
            language=plan.language
        )

        vision_content = None
        if message.vision_images:
            vision_content = self._build_vision_content(prompt, message.vision_images)

        step.status = ExecutionStatus.RUNNING
        self.market_closed_detected = False
        self._stop_after_tool_reason = None
        yield StepEvent(status=StepStatus.STARTED, step=step)

        content = vision_content if vision_content else prompt
        retry_text_only = False
        # Track whether any real market-analysis tool (non-message toolkit) was
        # actually called.  When the LLM skips all tool calls and fabricates a
        # success JSON ("ghost success"), this stays False so we can retry.
        real_tools_called = False
        # Track whether the LLM sent at least one user-visible narration
        # (message_notify_user) during this step.  If the step ultimately fails
        # with no narration, a fallback message is emitted so the user always
        # knows what happened.
        narration_sent = False
        self._notification_emitted = False

        def track_notification() -> None:
            nonlocal narration_sent
            if self._notification_emitted:
                narration_sent = True
                self._notification_emitted = False

        try:
            async for event in self._handle_execution_events(step, content):
                if isinstance(event, ToolEvent) and event.status == ToolStatus.CALLING:
                    if event.tool_name != "message":
                        real_tools_called = True
                    if event.function_name == "message_notify_user":
                        narration_sent = True
                track_notification()
                yield event
        except Exception as e:
            error_str = str(e).lower()
            if vision_content and (
                "image" in error_str or "vision" in error_str
                or "multimodal" in error_str or "unsupported" in error_str
                or "invalid request" in error_str or "not supported" in error_str
                or "400" in error_str
            ):
                logger.warning(f"Model rejected image content in execute_step, retrying text-only: {e}")
                retry_text_only = True
            else:
                raise

        if retry_text_only:
            logger.info("Retrying execute_step without vision images")
            real_tools_called = False
            narration_sent = False
            async for event in self._handle_execution_events(step, prompt):
                if isinstance(event, ToolEvent) and event.status == ToolStatus.CALLING:
                    if event.tool_name != "message":
                        real_tools_called = True
                    if event.function_name == "message_notify_user":
                        narration_sent = True
                track_notification()
                yield event

        # --- Case 1: LLM returned plain text with no tool calls at all ---
        # Detect: step failed + error is "non-JSON response" (set in
        # _handle_execution_events when _parse_json returns None).
        _is_skipped_tools = (
            not retry_text_only
            and not step.success
            and step.status == ExecutionStatus.COMPLETED
            and step.error == "LLM returned a non-JSON response."
        )
        if _is_skipped_tools:
            logger.warning(
                f"Step {step.id} completed with no tool calls (LLM returned plain text). "
                "Retrying once with a correction prompt."
            )
            step.status = ExecutionStatus.RUNNING
            step.result = None
            step.error = None
            step.success = False
            real_tools_called = False
            narration_sent = False

            correction_content = (
                prompt
                + "\n\n[CORRECTION — MANDATORY]: Your previous response was plain text instead of "
                "tool calls. You MUST begin by calling message_notify_user with your opening "
                "narration, then call the market analysis tools one by one. "
                "Do NOT write a text response — call tools first. "
                "Only return the final JSON result after completing all tool calls."
            )
            try:
                async for event in self._handle_execution_events(step, correction_content):
                    if isinstance(event, ToolEvent) and event.status == ToolStatus.CALLING:
                        if event.tool_name != "message":
                            real_tools_called = True
                        if event.function_name == "message_notify_user":
                            narration_sent = True
                    track_notification()
                    yield event
            except Exception as retry_err:
                logger.error(f"Retry of step {step.id} also raised: {retry_err}")

        # --- Case 2: Ghost success — LLM returned valid JSON {"success": true}
        # but never called any real market-analysis tools.  This happens when
        # accumulated context (from earlier steps with many tool calls) causes
        # the model to fabricate a completion response instead of using tools.
        # The step shows a checkmark in the UI with no tool chips or narration.
        _is_ghost_success = (
            not retry_text_only
            and not _is_skipped_tools
            and step.success
            and step.status == ExecutionStatus.COMPLETED
            and not real_tools_called
        )
        if _is_ghost_success:
            logger.warning(
                f"Step {step.id} reported success but called no market analysis tools "
                "(ghost success — LLM fabricated result). Retrying once with correction prompt."
            )
            step.status = ExecutionStatus.RUNNING
            step.result = None
            step.error = None
            step.success = False
            narration_sent = False

            correction_content = (
                prompt
                + "\n\n[CORRECTION — MANDATORY]: You reported this step as complete without "
                "calling any market analysis tools. You MUST actually call the required tools "
                "to gather real market data — do NOT fabricate or assume results. "
                "Send at most one concise progress update if useful, then call "
                "the market tools needed for this step. "
                "Only return the final JSON result after completing all tool calls."
            )
            try:
                async for event in self._handle_execution_events(step, correction_content):
                    if isinstance(event, ToolEvent) and event.status == ToolStatus.CALLING:
                        if event.function_name == "message_notify_user":
                            narration_sent = True
                    track_notification()
                    yield event
            except Exception as retry_err:
                logger.error(f"Ghost-success retry of step {step.id} also raised: {retry_err}")

        # --- Fallback: step failed with no user-visible narration at all ---
        # If the step ends in failure and the LLM never called message_notify_user,
        # the user sees a failed step in the plan panel with no explanation.
        # Emit a diagnostic notification that includes what was attempted and why
        # it failed, so the user always has actionable context.
        if not step.success and not narration_sent:
            _lang = getattr(plan, "language", "id") or "id"

            # Determine the most useful reason to surface
            _error = (step.error or "").strip()
            _step_desc = (step.description or "").strip()

            # Map internal error codes to human-readable causes
            _reason_en = _reason_id = ""
            if "non-JSON response" in _error:
                _reason_en = "The AI model produced a plain-text response instead of calling the required tools — this usually happens when the conversation context becomes very long."
                _reason_id = "Model AI menghasilkan teks biasa alih-alih memanggil tools yang diperlukan — ini biasanya terjadi ketika konteks percakapan sudah terlalu panjang."
            elif _error:
                _reason_en = f"Recorded error: {_error}"
                _reason_id = f"Error yang tercatat: {_error}"
            else:
                _reason_en = "The AI model completed the step without calling any tools and without providing an explanation."
                _reason_id = "Model AI menyelesaikan langkah tanpa memanggil tools apapun dan tanpa memberikan penjelasan."

            logger.warning(
                f"Step {step.id!r} failed silently (success=False, no narration). "
                f"desc={_step_desc!r} error={_error!r}"
            )

            if _lang == "en":
                _msg = (
                    f"⚠️ **Step could not be completed:** {_step_desc}\n\n"
                    f"**Reason:** {_reason_en}\n\n"
                    f"Analysis will continue with the data already collected from other steps."
                )
            else:
                _msg = (
                    f"⚠️ **Langkah tidak dapat diselesaikan:** {_step_desc}\n\n"
                    f"**Alasan:** {_reason_id}\n\n"
                    f"Analisis akan dilanjutkan dengan data yang sudah terkumpul dari langkah lain."
                )

            yield MessageEvent(role="assistant", message=_msg)

        if step.status not in {ExecutionStatus.FAILED, ExecutionStatus.SKIPPED}:
            step.status = (
                ExecutionStatus.COMPLETED
                if step.success
                else ExecutionStatus.FAILED
            )

    def _extract_text_from_json(self, text: str) -> str:
        """If LLM returned JSON wrapper instead of plain markdown, extract the text field."""
        clean = text.strip()
        # Strip markdown code fences if present
        if clean.startswith("```"):
            import re
            m = re.search(r"```(?:json)?\s*([\s\S]*?)```", clean)
            if m:
                clean = m.group(1).strip()
        if not clean.startswith("{"):
            return text
        try:
            parsed = json.loads(clean)
            if isinstance(parsed, dict):
                extracted = parsed.get("result") or parsed.get("message")
                if extracted and isinstance(extracted, str):
                    return extracted
        except (json.JSONDecodeError, ValueError):
            pass
        return text

    async def summarize(self) -> AsyncGenerator[BaseEvent, None]:
        await self._ensure_memory()
        context = list(self.memory.get_messages())

        stream_context = context + [LCHumanMessage(content=SUMMARIZE_STREAM_PROMPT)]

        # Collect the full response without streaming chunks yet.
        # The execution system prompt tells the model to return JSON, so the
        # model often returns {"success":true,"result":"..."} even in streaming
        # mode. We post-process the full text before deciding what to emit.
        full_text = ""
        try:
            async for chunk in self._model.astream(stream_context):
                token = chunk.content if isinstance(chunk.content, str) else ""
                if token:
                    full_text += token
            if full_text:
                clean_text = self._extract_text_from_json(full_text)
                # Emit in small chunks so the frontend's RAF buffer creates
                # a smooth progressive typing effect instead of a single pop-in.
                _CHUNK = 5
                for _i in range(0, len(clean_text), _CHUNK):
                    yield MessageChunkEvent(content=clean_text[_i:_i + _CHUNK], done=False)
                yield MessageChunkEvent(content="", done=True)
                yield MessageEvent(message=clean_text)
            return
        except Exception as e:
            logger.warning(f"Streaming summarize failed, falling back to JSON mode: {e}")

        # Fallback: original JSON-based summarize
        message = SUMMARIZE_PROMPT
        async for event in self.execute(message):
            if isinstance(event, MessageEvent):
                logger.debug(f"Execution agent summary: {event.message}")
                parsed_response = await self._parse_json(event.message)
                if parsed_response is None:
                    logger.warning("Summarize fallback returned non-JSON, using raw message")
                    yield MessageEvent(message=event.message)
                    continue
                msg_obj = Message.model_validate(parsed_response)
                yield MessageEvent(message=msg_obj.message)
                continue
            yield event
