import logging
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from app.domain.models.tool_result import ToolResult
from langchain.messages import AnyMessage

logger = logging.getLogger(__name__)

class Memory(BaseModel):
    """
    Memory class, defining the basic behavior of memory
    """
    messages: List[AnyMessage] = []

    def add_message(self, message: AnyMessage) -> None:
        """Add message to memory"""
        self.messages.append(message)
    
    def add_messages(self, messages: List[AnyMessage]) -> None:
        """Add messages to memory"""
        self.messages.extend(messages)

    def get_messages(self) -> List[AnyMessage]:
        """Get all message history"""
        return self.messages
    
    def get_last_message(self) -> Optional[AnyMessage]:
        """Get the last message"""
        if len(self.messages) > 0:  
            return self.messages[-1]
        return None
    
    def roll_back(self) -> None:
        """Roll back memory"""
        self.messages = self.messages[:-1]
    
    def compact(self) -> None:
        """Compact memory — three-pass cleanup to keep context size small:

        Pass 1 — Vision image_url base64 in HumanMessages:
            Vision images (chart uploads) are embedded as data-URI base64
            strings (~150-300 KB each) inside multimodal HumanMessage content
            lists. Once the LLM has processed them they are never needed again,
            but they accumulate across steps and inflate every subsequent API
            request. This pass strips all image_url entries from every
            HumanMessage, preserving only the text parts.

        Pass 2 — Truncate large MCP ToolMessage results:
            MCP/market-data tool results can be very large. Keep only the last
            _MAX_TOOL_RESULT_CHARS characters so accumulated step context does
            not flood subsequent steps and cause the model to skip tool calls.

        Pass 3 — Remove intermediate tool call/result pairs:
            After a step completes, all tool call dispatch messages (AIMessage
            with tool_calls) and their corresponding ToolMessage responses have
            already been summarised into step.result. Keeping them in memory
            inflates context for every subsequent step, causing the model to
            lose track of available tools and skip tool calls ("ghost success").
            Keeps: SystemMessage, HumanMessages, and AIMessages with real text.
            Removes: pure tool-dispatch AIMessages (empty content) and all
            ToolMessages.
        """
        # --- Pass 1: strip base64 image_url data from HumanMessages ---
        for i, message in enumerate(self.messages):
            if message.type != "human":
                continue
            if not isinstance(message.content, list):
                continue
            has_image = any(
                isinstance(part, dict) and part.get("type") == "image_url"
                for part in message.content
            )
            if not has_image:
                continue
            text_parts = [
                part for part in message.content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            if text_parts:
                message.content = (
                    text_parts[0]["text"] if len(text_parts) == 1 else text_parts
                )
            else:
                message.content = "(image removed)"
            logger.debug(f"Stripped vision image(s) from HumanMessage at index {i}")

        # --- Pass 2: truncate large MCP ToolMessage results ---
        _MAX_TOOL_RESULT_CHARS = 3000
        for i, message in enumerate(self.messages):
            if message.type != "tool":
                continue
            if not isinstance(message.content, str):
                continue
            if len(message.content) > _MAX_TOOL_RESULT_CHARS:
                message.content = message.content[-_MAX_TOOL_RESULT_CHARS:]
                logger.debug(
                    f"Truncated large tool result in memory: {message.name} at index {i} "
                    f"to last {_MAX_TOOL_RESULT_CHARS} chars"
                )

        # --- Pass 3: remove intermediate tool call/result pairs ---
        # After a step completes, all tool call dispatch messages (AIMessage
        # with tool_calls) and their corresponding ToolMessage responses have
        # already been summarised into step.result. Keeping them in memory
        # inflates context for every subsequent step, causing the model to
        # lose track of available tools and skip tool calls ("ghost success").
        # We keep: SystemMessage, HumanMessages, and AIMessages that contain
        # actual text content (narration / final step JSON).
        # We remove: pure tool-dispatch AIMessages (no text content) and all
        # ToolMessages.
        cleaned = []
        for msg in self.messages:
            # Always keep system and human messages
            if msg.type in ("system", "human"):
                cleaned.append(msg)
                continue
            # Remove tool response messages entirely
            if msg.type == "tool":
                continue
            # For AI messages: keep only those that have real text content.
            # Pure tool-call dispatch messages have empty or whitespace content.
            if msg.type == "ai":
                content_text = (
                    msg.content if isinstance(msg.content, str) else ""
                )
                if content_text.strip():
                    cleaned.append(msg)
                # else: pure tool-dispatch message — discard
                continue
            # Keep anything else (safety fallback)
            cleaned.append(msg)
        removed = len(self.messages) - len(cleaned)
        if removed > 0:
            logger.debug(f"Pass 3: removed {removed} intermediate tool call/result messages from memory")
        self.messages = cleaned

    @property
    def empty(self) -> bool:
        """Check if memory is empty"""
        return len(self.messages) == 0
