"""Runtime thinking (reasoning) mode state.

A tiny process-wide flag that decides whether reasoning models should emit
chain-of-thought. Two sources control it:

1. ``THINKING_MODE`` env var (``on``/``off``) — the default at startup.
2. ``PUT /api/v1/config/thinking`` — runtime toggle from the web UI button.

Both agents (planner/executor) read this flag on EVERY LLM call, so flipping
it takes effect immediately for new calls without restarting the server.

When disabled, the request carries ``chat_template_kwargs: {"thinking":
false}`` (verified against NVIDIA NIM nemotron) so the model answers directly
and no ``reasoning_content`` is returned — the UI simply shows no Thinking
block and nothing can crash on its absence.
"""

import logging
import threading

logger = logging.getLogger(__name__)

_enabled: bool = True
_lock = threading.Lock()


def init_from_settings() -> None:
    """Seed the runtime flag from THINKING_MODE env/config (called at startup)."""
    global _enabled
    from app.core.config import get_settings
    try:
        _enabled = bool(get_settings().thinking_mode)
    except Exception:
        _enabled = True
    logger.info("Thinking mode initialized: %s", "on" if _enabled else "off")


def is_enabled() -> bool:
    return _enabled


def set_enabled(value: bool) -> bool:
    """Flip the runtime flag. Returns the new value."""
    global _enabled
    with _lock:
        _enabled = bool(value)
    logger.info("Thinking mode switched %s", "ON" if _enabled else "OFF")
    return _enabled
