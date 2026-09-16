from functools import lru_cache
from typing import Optional
import logging

from app.domain.external.search import SearchEngine
from app.core.config import get_settings

logger = logging.getLogger(__name__)

@lru_cache()
def get_search_engine() -> Optional[SearchEngine]:
    """Get search engine instance (Tavily)"""
    settings = get_settings()
    if settings.search_provider == "tavily":
        from app.infrastructure.external.search.tavily_search import TavilySearchEngine
        if settings.tavily_api_key:
            logger.info("Initializing Tavily Search Engine")
            return TavilySearchEngine(api_key=settings.tavily_api_key)
        else:
            logger.warning("Tavily Search Engine not initialized: missing TAVILY_API_KEY")
    else:
        logger.warning(f"Unsupported search provider: {settings.search_provider} — only 'tavily' is supported")

    return None
