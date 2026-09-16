from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import TimeoutError, ConnectionError, BusyLoadingError
import logging
from app.core.config import get_settings

logger = logging.getLogger(__name__)

class RedisClient:
    def __init__(self):
        self._client: Redis | None = None
        self._settings = get_settings()
    
    async def initialize(self) -> None:
        """Initialize Redis connection."""
        if self._client is not None:
            return
            
        try:
            retry = Retry(ExponentialBackoff(cap=10, base=1), retries=3)
            self._client = Redis(
                host=self._settings.redis_host,
                port=self._settings.redis_port,
                db=self._settings.redis_db,
                password=self._settings.redis_password,
                decode_responses=True,
                socket_keepalive=True,
                socket_timeout=30,
                socket_connect_timeout=10,
                health_check_interval=20,
                retry=retry,
                retry_on_timeout=True,
                retry_on_error=[TimeoutError, ConnectionError, BusyLoadingError],
            )
            await self._client.ping()
            logger.info("Successfully connected to Redis")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {str(e)}")
            raise
    
    async def shutdown(self) -> None:
        """Shutdown Redis connection."""
        if self._client is not None:
            await self._client.close()
            self._client = None
            logger.info("Disconnected from Redis")
        get_redis.cache_clear()
    
    @property
    def client(self) -> Redis:
        """Return initialized Redis client"""
        if self._client is None:
            raise RuntimeError("Redis client not initialized. Call initialize() first.")
        return self._client

from functools import lru_cache

@lru_cache()
def get_redis() -> RedisClient:
    """Get the Redis client instance."""
    return RedisClient()
