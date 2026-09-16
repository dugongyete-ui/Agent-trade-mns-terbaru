from typing import List, Optional, Union
from app.domain.services.tools.base import BaseToolkit
from app.domain.models.tool_result import ToolResult
from langchain.tools import tool


class MessageToolkit(BaseToolkit):
    """Message tool class, providing message sending functions for user interaction"""

    name: str = "message"
    
    def __init__(self):
        """Initialize message tool class"""
        super().__init__()

    @tool(parse_docstring=True)
    async def message_notify_user(
        self,
        text: str,
        attachments: Optional[Union[str, List[str]]] = None,
    ) -> ToolResult:
        """Send a message to user without requiring a response. Use for live narration, progress updates, and delivering results.

        Args:
            text: Message text to display to user
            attachments: (Optional) List of file paths to attach. Files are shown as download links.
        """
        return ToolResult(success=True, message="OK")
    
    @tool(parse_docstring=True)
    async def message_ask_user(
        self,
        text: str,
        attachments: Optional[Union[str, List[str]]] = None,
    ) -> ToolResult:
        """Ask user a question and wait for their response. Use only when you genuinely cannot proceed without user input — for example, when the trading symbol is completely ambiguous.

        Args:
            text: Question text to present to user
            attachments: (Optional) List of files to include alongside the question
        """
        return ToolResult(success=True)
