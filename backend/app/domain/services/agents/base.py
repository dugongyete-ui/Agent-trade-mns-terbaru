import logging
import asyncio
import json
import os
import re
import uuid
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
import openai


logger = logging.getLogger(__name__)
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
        if settings.max_tokens is not None:
            kwargs["max_tokens"] = settings.max_tokens
        if settings.extra_headers:
            kwargs["default_headers"] = settings.extra_headers
        if settings.api_base:
            verify = settings.ssl_verify
            kwargs["http_client"] = httpx.Client(verify=verify)
            kwargs["http_async_client"] = httpx.AsyncClient(verify=verify)
        self._model = init_chat_model(**kwargs)
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
    
    def get_tool(self, name: str) -> Optional[Tool]:
        """Get specified tool"""
        for toolkit in self.toolkits:
            tool = toolkit.get_tool(name)
            if tool:
                return tool
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
        message = await self.ask(request, format)
        for iteration in range(self.max_iterations):
            if not message.tool_calls:
                break
            tool_responses = []
            for tool_call in message.tool_calls:
                function_name = tool_call["name"]
                tool_call_id = tool_call["id"] = tool_call["id"] or str(uuid.uuid4())
                function_args = tool_call["args"]
                
                tool = self.get_tool(function_name)
                if not tool:
                    yield ErrorEvent(error=f"Unknown tool: {function_name}")
                    # Return a ToolMessage so the LLM knows the call failed and
                    # can adapt, rather than leaving a dangling tool_call in its
                    # conversation history which causes confused responses.
                    tool_responses.append(
                        ToolMessage(
                            tool_call_id=tool_call_id,
                            name=function_name,
                            content=f"Error: tool '{function_name}' is not available. "
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

            message = await self.ask_with_messages(tool_responses)
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

    async def ask_with_messages(self, messages: List[Dict[str, Any]], format: Optional[str] = None) -> AIMessage:
        await self._add_to_memory(messages)

        response_format = None
        if format:
            response_format = {"type": format}

        # Stage 1-3: model chain | RobustJsonParser repairs invalid tool call JSON.
        # Stages 4-5: outer retry loop handles cases that survive stages 1-3.
        chain = (
            self._model
            .bind(response_format=response_format, tool_choice=self.tool_choice)
            .bind_tools(self.get_tools())
            | RobustJsonParser.from_llm(self._model)
        )

        # Transient API errors that are safe to retry (5xx, network blips, rate limits).
        _TRANSIENT_API_ERRORS = (
            openai.InternalServerError,   # 500/502/503 from the provider
            openai.APIConnectionError,    # network-level failure
            openai.APITimeoutError,       # request timed out
            openai.RateLimitError,        # 429 – back off and retry
        )

        context = list(self.memory.get_messages())
        for attempt in range(self.max_retries):
            try:
                message: AIMessage = await chain.ainvoke(context)
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
                if attempt == self.max_retries - 1:
                    logger.error(
                        "LLM API error after %d attempts, giving up: %s",
                        self.max_retries, e,
                    )
                    raise
                wait = self.retry_interval * (2 ** attempt)  # exponential back-off
                logger.warning(
                    "Transient LLM API error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, self.max_retries, wait, type(e).__name__,
                )
                await asyncio.sleep(wait)
        logger.debug(f"Response from model: {message}")

        await self._add_to_memory([message])
        return message

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
