import logging
import asyncio
import json
import os
import re
import uuid
import difflib
import httpx
from abc import ABC
from datetime import datetime, UTC
from typing import List, Dict, Any, Optional, AsyncGenerator, Union
from app.domain.models.message import Message
from app.domain.services.tools.base import BaseToolkit
from app.domain.models.event import (
    BaseEvent,
    ToolEvent,
    ToolStatus,
    ErrorEvent,
    MessageEvent,
)
from app.domain.repositories.agent_repository import AgentRepository
from langchain.chat_models import init_chat_model
from langchain_classic.output_parsers.retry import RetryWithErrorOutputParser
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from app.core.config import get_settings
from langchain.messages import AIMessage, HumanMessage, ToolCall, ToolMessage, SystemMessage
from app.domain.services.tools.base import Tool
from app.domain.utils.robust_json_parser import RobustJsonParser, ToolCallParseError
from app.domain.models.event import ThinkingEvent
from app.domain.services.agents import thinking_state
import openai


logger = logging.getLogger(__name__)

class _StreamResult:
    """Sentinel wrapper for the final AIMessage at the end of a stream."""
    def __init__(self, message: AIMessage):
        self.message = message

class BaseAgent(ABC):
    """
    Base agent class, defining the basic behavior of the agent
    """

    name: str = ""
    system_prompt: str = ""
    format: Optional[str] = None
    max_iterations: int = 100
    max_retries: int = 6
    retry_interval: float = 5.0
    tool_choice: Optional[str] = None

    _JSON_PARSE_PROMPT = PromptTemplate.from_template(
        "Extract or repair the JSON from the following LLM output.\n\n{input}"
    )

    def __init__(
        self,
        agent_id: str,
        agent_repository: AgentRepository,
        tools: List[BaseToolkit] = []
    ):
        settings = get_settings()
        self._agent_id = agent_id
        self._repository = agent_repository
        kwargs = dict(
            model=settings.model_name,
            model_provider=settings.model_provider,
            temperature=settings.temperature,
            base_url=settings.api_base,
        )
        # Pass the configured application key explicitly. This is required for
        # OpenAI-compatible gateways such as OpenRouter because the process may
        # inherit an unrelated OPENAI_API_KEY from the host environment.
        if settings.api_key:
            if settings.model_provider == "openai":
                kwargs["openai_api_key"] = settings.api_key
            else:
                kwargs["api_key"] = settings.api_key
        # NOTE: max_tokens is intentionally NOT set — the model uses the
        # provider's maximum allowed completion budget.
        if settings.extra_headers:
            kwargs["default_headers"] = settings.extra_headers
        if settings.api_base:
            verify = settings.ssl_verify
            kwargs["http_client"] = httpx.Client(verify=verify)
            kwargs["http_async_client"] = httpx.AsyncClient(verify=verify)

        # Reasoning models served over OpenAI-compatible APIs (e.g. NVIDIA NIM
        # nemotron) return chain-of-thought in the non-standard
        # `reasoning_content` field. ChatOpenAI silently DROPS that field;
        # ChatDeepSeek (also an OpenAI-compatible client) preserves it in
        # additional_kwargs["reasoning_content"] for both invoke and astream.
        # Use it whenever the provider is an OpenAI-compatible endpoint so the
        # agent can stream its reasoning to the frontend.
        #
        # Two variants are built so the thinking mode can be flipped at
        # runtime (UI button / THINKING_MODE env):
        #   - _model_think : chat_template_kwargs {"thinking": true}
        #   - _model_no_think: chat_template_kwargs {"thinking": false}
        # Both share the same config; only the extra body differs. The
        # no-think variant was verified live against NVIDIA NIM: zero
        # reasoning_content deltas, normal content — no crash path.
        if settings.model_provider == "openai":
            from langchain_deepseek import ChatDeepSeek

            def _ds(extra_body: dict) -> ChatDeepSeek:
                ds_kwargs = dict(
                    model=settings.model_name,
                    api_key=settings.api_key,
                    api_base=settings.api_base,
                    temperature=settings.temperature,
                    extra_body=extra_body,
                )
                if settings.extra_headers:
                    ds_kwargs["default_headers"] = settings.extra_headers
                if settings.api_base:
                    ds_kwargs["http_client"] = httpx.Client(verify=settings.ssl_verify)
                    ds_kwargs["http_async_client"] = httpx.AsyncClient(verify=settings.ssl_verify)
                return ChatDeepSeek(**ds_kwargs)

            self._model_think = _ds({"chat_template_kwargs": {"thinking": True}})
            self._model_no_think = _ds({"chat_template_kwargs": {"thinking": False}})
            self._model = self._model_think
        else:
            self._model_think = None
            self._model_no_think = None
            self._model = init_chat_model(**kwargs)

        self._robust_parser = RobustJsonParser.from_llm(self._model)
        self._json_output_parser = RetryWithErrorOutputParser.from_llm(
            parser=JsonOutputParser(),
            llm=self._model,
            max_retries=self.max_retries,
        )
        self.toolkits = tools
        self.memory = None
        self._stop_after_tool_reason: Optional[str] = None

    async def _parse_json(self, text: str) -> dict:
        """Parse JSON from LLM output using RetryWithErrorOutputParser."""
        prompt_value = self._JSON_PARSE_PROMPT.format_prompt(input=text)
        return await self._json_output_parser.aparse_with_prompt(text, prompt_value)

    def _current_model(self):
        """Pick the model variant matching the runtime thinking mode.

        Read on EVERY LLM call so the UI toggle takes effect immediately.
        Falls back to the default model for non-OpenAI-compatible providers
        (no thinking switch available — reasoning simply never appears and
        nothing downstream breaks).
        """
        if not thinking_state.is_enabled() and self._model_no_think is not None:
            return self._model_no_think
        if self._model_think is not None:
            return self._model_think
        return self._model
    
    @staticmethod
    def _tool_entry_name(entry: Any) -> Optional[str]:
        """Extract the tool name from either a Tool wrapper or a schema dict."""
        if hasattr(entry, "name"):
            return entry.name
        if isinstance(entry, dict):
            fn = entry.get("function") or {}
            return entry.get("name") or (
                fn.get("name") if isinstance(fn, dict) else None
            )
        return None

    def _all_tool_names(self) -> List[str]:
        names: List[str] = []
        for toolkit in self.toolkits:
            for entry in toolkit.get_tools():
                n = self._tool_entry_name(entry)
                if n:
                    names.append(n)
        return names

    def _resolve_registered(self, matched: str) -> Optional[Tool]:
        for toolkit in self.toolkits:
            resolved = toolkit.get_tool(matched)
            if resolved:
                logger.info("Tool '%s' resolved to registered '%s'", self._last_query_name, matched)
                return resolved
        return None

    # Populated by get_tool() so execute() can attach "did you mean" hints
    # to the unknown-tool ToolMessage, letting the LLM self-correct in ONE turn.
    _last_tool_suggestions: List[str] = []
    _last_query_name: str = ""

    def get_tool(self, name: str) -> Optional[Tool]:
        """Get specified tool, with layered fuzzy fallbacks for sloppy LLM names.

        MCP tools are registered as ``mcp_{server}_{tool}``. Reasoning models
        occasionally produce near-miss names — the bare inner name, a dropped
        prefix, OR A DOUBLED server prefix (``mcp_deriv_deriv_deriv-get-candles``
        instead of ``mcp_deriv_deriv-get-candles``). Instead of bouncing the
        call back with an "unknown tool" error round-trip, resolve the match
        directly when it is unambiguous:

        1. exact + hyphen/underscore normalisation (via each toolkit)
        2. suffix match of simple name variants
        3. segment-stripped cores (peels duplicated ``server_`` prefixes)
        4. difflib close-match (typos) — only when a single clear winner

        Note: toolkit.get_tools() may return either Tool wrappers or raw schema
        dicts (MCPToolkit exposes dicts for bind_tools) — handle both shapes.
        """
        self._last_query_name = name
        self._last_tool_suggestions = []

        for toolkit in self.toolkits:
            tool = toolkit.get_tool(name)
            if tool:
                return tool

        registered = self._all_tool_names()

        # Name variants LLMs produce: bare inner name, hyphen/underscore
        # swaps in BOTH directions ("deriv_market_snapshot" ↔ "deriv-market-
        # snapshot"), or a stray "mcp_" prefix on message tools ("mcp_notify_user").
        variants = [name]
        if name.startswith("mcp_"):
            variants.append(name[4:])
        variants.append(name.replace("-", "_"))
        variants.append(name.replace("_", "-"))

        candidate_names: List[str] = []
        for variant in variants:
            for entry_name in registered:
                if entry_name.endswith(f"_{variant}") or entry_name.endswith(f"-{variant}"):
                    candidate_names.append(entry_name)
            if len(set(candidate_names)) == 1:
                return self._resolve_registered(candidate_names[0])
            candidate_names = []

        # Duplicated server-prefix hallucinations (e.g. mcp_deriv_deriv_deriv-
        # get-candles): peel leading ``segment_`` tokens one at a time and retry
        # the suffix match with the shortened core. Most specific core wins.
        core = name[4:] if name.startswith("mcp_") else name
        parts = core.split("_")
        stripped: set = set()
        while len(parts) > 1:
            parts = parts[1:]
            candidate_core = "_".join(parts)
            if candidate_core in stripped:
                continue
            stripped.add(candidate_core)
            hits = {
                entry_name for entry_name in registered
                if entry_name.endswith(f"_{candidate_core}")
            }
            if len(hits) == 1:
                return self._resolve_registered(hits.pop())

        # Typo-level fuzz: accept a single close match, or a top-1 that
        # clearly beats the runner-up (margin ≥ 0.15). A plain 0.85 cutoff
        # missed real matches like "deriv-get-candles" → "mcp_deriv_deriv-
        # get-candles" (ratio 0.842) whenever a suffix layer also missed.
        # 0.58 + margin: "deriv-atrr" (0.60) resolves; "market-snapshot"
        # (two servers, margin 0.02) correctly stays unresolved.
        close = difflib.get_close_matches(name, registered, n=2, cutoff=0.58)
        if close:
            top_score = difflib.SequenceMatcher(None, name, close[0]).ratio()
            runner_score = (
                difflib.SequenceMatcher(None, name, close[1]).ratio()
                if len(close) > 1
                else 0.0
            )
            if len(close) == 1 or top_score - runner_score >= 0.15:
                return self._resolve_registered(close[0])

        self._last_tool_suggestions = difflib.get_close_matches(
            name, registered, n=3, cutoff=0.55
        )
        return None

    def get_tools(self) -> List[Tool]:
        """Get all available tools list"""
        return [tool for toolkit in self.toolkits for tool in toolkit.get_tools()]

    async def invoke_tool(self, tool: Tool, tool_call: ToolCall) -> ToolMessage:
        """Invoke specified tool with retry mechanism."""
        retries = 0
        last_error = "Unknown error"
        while retries <= self.max_retries:
            try:
                result_msg: ToolMessage = await tool.ainvoke(tool_call)
                return result_msg
            except Exception as e:
                last_error = str(e)
                retries += 1
                if retries <= self.max_retries:
                    await asyncio.sleep(self.retry_interval)
                else:
                    logger.exception(f"Tool execution failed, {tool_call['name']}, {tool_call['args']}")
                    break

        return ToolMessage(tool_call_id=tool_call["id"], name=tool.name, content=last_error)

    def _stop_after_tool_result(
        self,
        function_name: str,
        tool_result: ToolMessage,
    ) -> Optional[str]:
        """Return a stop reason for a domain-specific terminal tool result.

        Most agents should continue after every tool. Specialized agents can
        override this hook for safety or market-state gates.
        """
        return None
    
    # Compact tool results in memory every this many tool-call rounds
    # within a single step to prevent "Payload Too Large" on large responses.
    _COMPACT_EVERY_N_ITERATIONS = 10

    async def execute(self, request: Union[str, list], format: Optional[str] = None) -> AsyncGenerator[BaseEvent, None]:
        format = format or self.format
        first_messages = [HumanMessage(content=request)]
        message: Optional[AIMessage] = None
        async for item in self.astream_ask_with_messages(first_messages, format):
            if isinstance(item, _StreamResult):
                message = item.message
            elif isinstance(item, ThinkingEvent):
                yield item
        for iteration in range(self.max_iterations):
            if not message.tool_calls:
                break
            tool_responses = []
            for tool_call in message.tool_calls:
                function_name = tool_call["name"]
                tool_call_id = tool_call["id"] = tool_call["id"] or str(uuid.uuid4())
                function_args = tool_call["args"]
                
                tool = self.get_tool(function_name)
                if tool:
                    if tool.name != function_name:
                        # Canonicalize the LLM's (possibly malformed) tool name
                        # so downstream narration/notification special-casing
                        # and the ToolMessage pairing stay consistent.
                        tool_call["name"] = tool.name
                        function_name = tool.name
                if not tool:
                    yield ErrorEvent(error=f"Unknown tool: {function_name}")
                    # Return a ToolMessage so the LLM knows the call failed and
                    # can adapt, rather than leaving a dangling tool_call in its
                    # conversation history which causes confused responses.
                    suggestions = list(getattr(self, "_last_tool_suggestions", None) or [])
                    hint = f" Closest available tools: {', '.join(suggestions)}." if suggestions else ""
                    tool_responses.append(
                        ToolMessage(
                            tool_call_id=tool_call_id,
                            name=function_name,
                            content=f"Error: tool '{function_name}' is not available.{hint} "
                                    f"Use only the tools listed in your system prompt.",
                        )
                    )
                    continue

                # Generate event before tool call
                yield ToolEvent(
                    status=ToolStatus.CALLING,
                    tool_call_id=tool_call_id,
                    tool_name=tool.toolkit.name,
                    function_name=function_name,
                    function_args=function_args
                )

                tool_result = await self.invoke_tool(tool, tool_call)

                # Generate event after tool call
                yield ToolEvent(
                    status=ToolStatus.CALLED,
                    tool_call_id=tool_call_id,
                    tool_name=tool.toolkit.name,
                    function_name=function_name,
                    function_args=function_args,
                    function_result=tool_result.artifact
                )

                stop_reason = self._stop_after_tool_result(function_name, tool_result)
                if stop_reason:
                    self._stop_after_tool_reason = stop_reason
                    return

                tool_responses.append(tool_result)

            # Periodically compact tool results mid-step to prevent
            # "Payload Too Large" errors on large tool responses.
            if (iteration + 1) % self._COMPACT_EVERY_N_ITERATIONS == 0:
                logger.debug(f"Mid-step compact at iteration {iteration + 1}")
                await self.compact_memory()

            message = None
            async for item in self.astream_ask_with_messages(tool_responses):
                if isinstance(item, _StreamResult):
                    message = item.message
                elif isinstance(item, ThinkingEvent):
                    yield item
        else:
            yield ErrorEvent(error="Maximum iteration count reached, failed to complete the task")
        
        yield MessageEvent(message=message.content)
    
    async def _ensure_memory(self):
        if not self.memory:
            self.memory = await self._repository.get_memory(self._agent_id, self.name)
    
    async def _add_to_memory(self, messages: List[Dict[str, Any]]) -> None:
        """Update memory and save to repository"""
        await self._ensure_memory()
        if self.memory.empty:
            self.memory.add_message(SystemMessage(content=self.system_prompt))
        self.memory.add_messages(messages)
        await self._repository.save_memory(self._agent_id, self.name, self.memory)
    
    async def _roll_back_memory(self) -> None:
        await self._ensure_memory()
        self.memory.roll_back()
        await self._repository.save_memory(self._agent_id, self.name, self.memory)

    async def astream_ask_with_messages(
        self, messages: List[Dict[str, Any]], format: Optional[str] = None
    ) -> AsyncGenerator[Union[ThinkingEvent, _StreamResult], None]:
        """Streaming version of ask_with_messages.

        Streams the model response token-by-token. Reasoning models emit
        chain-of-thought deltas in additional_kwargs["reasoning_content"];
        each delta is yielded as a transient ThinkingEvent(done=False) so the
        frontend can render a live collapsible Thinking block. When reasoning
        was emitted, a final ThinkingEvent(done=True) with the full text is
        yielded for persistence.

        The final item yielded is always a _StreamResult wrapping the complete
        AIMessage (with tool calls repaired through RobustJsonParser stages
        1-3, and stages 4-5 retries on top — same semantics as before).
        """
        await self._add_to_memory(messages)

        response_format = None
        if format:
            response_format = {"type": format}

        model = (
            self._current_model()
            .bind(response_format=response_format, tool_choice=self.tool_choice)
            .bind_tools(self.get_tools())
        )

        # Transient API errors that are safe to retry (5xx, network blips, rate
        # limits). openai.APIError (base class) is included on purpose: NVIDIA
        # NIM raises a BARE APIError mid-stream ("Service temporarily
        # overloaded") when the backend saturates — without catching it the
        # whole agent task used to crash on the first blip.
        _TRANSIENT_API_ERRORS = (
            openai.InternalServerError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.APIError,
        )
        # Permanent client errors — retrying is pointless, fail fast.
        _PERMANENT_API_ERRORS = (
            openai.BadRequestError,
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.NotFoundError,
            openai.UnprocessableEntityError,
            openai.ConflictError,
        )

        context = list(self.memory.get_messages())
        message: Optional[AIMessage] = None
        full_reasoning = ""
        for attempt in range(self.max_retries):
            try:
                chunks = []
                reasoning_parts: list = []
                async for chunk in model.astream(context):
                    chunks.append(chunk)
                    reasoning_delta = (getattr(chunk, "additional_kwargs", {}) or {}).get("reasoning_content")
                    if reasoning_delta:
                        reasoning_parts.append(reasoning_delta)
                        yield ThinkingEvent(content=reasoning_delta, done=False)

                message = self._merge_stream_chunks(chunks)
                full_reasoning = "".join(reasoning_parts)
                # Stage 1-3 repair for broken tool-call JSON (passes valid
                # messages through unchanged), then stage 4-5 retries below.
                message = await self._robust_parser.ainvoke(message)
                break
            except ToolCallParseError as e:
                if attempt == self.max_retries - 1:
                    raise
                logger.warning(
                    "Attempt %d/%d: tool call JSON repair failed, retrying model",
                    attempt + 1, self.max_retries,
                )
                if attempt == 0:
                    # Stage 4 (RetryOutputParser style): silent retry, same context.
                    pass
                else:
                    # Stage 5 (RetryWithErrorOutputParser style): add error feedback.
                    context = e.make_retry_context(context)
            except _TRANSIENT_API_ERRORS as e:
                if isinstance(e, _PERMANENT_API_ERRORS):
                    raise
                if attempt == self.max_retries - 1:
                    logger.error(
                        "LLM API error after %d attempts, giving up: %s",
                        self.max_retries, e,
                    )
                    raise
                wait = min(self.retry_interval * (2 ** attempt), 30.0)  # capped exp back-off
                logger.warning(
                    "Transient LLM API error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, self.max_retries, wait, type(e).__name__,
                )
                await asyncio.sleep(wait)

        # Persist the full reasoning for this call as a single event so the
        # collapsible Thinking block survives page refresh.
        if message is not None and full_reasoning:
            yield ThinkingEvent(content=full_reasoning, done=True)

        await self._add_to_memory([message])
        yield _StreamResult(message)

    @staticmethod
    def _merge_stream_chunks(chunks: list) -> AIMessage:
        """Merge streamed AIMessageChunk fragments into a single AIMessage.

        additional_kwargs are stripped before merging (reasoning_content is
        accumulated separately by the stream consumer) so conflicting string
        merges cannot occur; tool_call_chunks merge correctly via __add__.
        """
        merged = None
        for chunk in chunks:
            stripped = chunk.model_copy(deep=False)
            stripped.additional_kwargs = {}
            merged = stripped if merged is None else (merged + stripped)
        if merged is None:
            return AIMessage(content="")
        return AIMessage(
            content=merged.content if isinstance(merged.content, str) else str(merged.content),
            additional_kwargs={},
            tool_calls=list(merged.tool_calls or []),
            invalid_tool_calls=list(merged.invalid_tool_calls or []),
        )

    async def ask_with_messages(self, messages: List[Dict[str, Any]], format: Optional[str] = None) -> AIMessage:
        """Non-streaming convenience wrapper — drains the streaming ask and
        discards ThinkingEvents, returning only the final AIMessage."""
        final: Optional[_StreamResult] = None
        async for item in self.astream_ask_with_messages(messages, format):
            if isinstance(item, _StreamResult):
                final = item
        return final.message

    async def ask(self, request: Union[str, list], format: Optional[str] = None) -> AIMessage:
        return await self.ask_with_messages([
            HumanMessage(content=request)
        ], format)
    
    async def roll_back(self, message: Message):
        await self._ensure_memory()
        last_message = self.memory.get_last_message()
        if not last_message:
            return
        if last_message.type != "ai":
            return
        if not last_message.tool_calls:
            return
        tool_call = last_message.tool_calls[0]
        function_name = tool_call["name"]
        tool_call_id = tool_call["id"]
        if function_name == "message_ask_user":
            self.memory.add_message(ToolMessage(tool_call_id=tool_call_id, name=function_name, content=message))
        else:
            self.memory.roll_back()
        await self._repository.save_memory(self._agent_id, self.name, self.memory)
    
    async def compact_memory(self) -> None:
        await self._ensure_memory()
        self.memory.compact()
        await self._repository.save_memory(self._agent_id, self.name, self.memory)
        # Optionally persist conversation to disk for audit / debugging
        await self._save_conversation_if_configured()

    async def _save_conversation_if_configured(self) -> None:
        """Save full conversation messages to a JSONL file if CONVERSATION_SAVE_PATH is set."""
        try:
            settings = get_settings()
            if not settings.conversation_save_path:
                return
            os.makedirs(settings.conversation_save_path, exist_ok=True)
            ts = datetime.now(UTC).strftime("%Y%m%d")
            file_path = os.path.join(
                settings.conversation_save_path,
                f"agent_{self._agent_id}_{self.name}_{ts}.jsonl",
            )
            messages = list(self.memory.get_messages())
            with open(file_path, "w", encoding="utf-8") as f:
                for msg in messages:
                    try:
                        record = {
                            "type": msg.type,
                            "content": msg.content if isinstance(msg.content, str) else str(msg.content)[:500],
                        }
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("Could not save conversation: %s", e)
