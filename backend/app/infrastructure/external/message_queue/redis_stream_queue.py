import json
import re
import uuid
import asyncio
from typing import Any, AsyncGenerator, Optional, Tuple
import logging
from app.infrastructure.storage.redis import get_redis
from app.domain.external.message_queue import MessageQueue

logger = logging.getLogger(__name__)

# Redis stream ID format: <milliseconds>-<sequence>
_STREAM_ID_RE = re.compile(r'^\d+-\d+$')

def _is_valid_stream_id(stream_id: Any) -> bool:
    """Return True when stream_id is a valid Redis stream ID or special marker."""
    if stream_id is None:
        return False
    s = str(stream_id)
    return s in ("0", "$", "-", "+") or bool(_STREAM_ID_RE.match(s))


class RedisStreamQueue(MessageQueue):
    """Redis Stream implementation of message queue"""
    
    def __init__(self, stream_name: str):
        self._stream_name = stream_name
        self._redis = get_redis()
        self._lock_expire_seconds = 10
    
    async def _acquire_lock(self, lock_key: str, timeout_seconds: int = 5) -> Optional[str]:
        """Acquire distributed lock"""
        lock_value = str(uuid.uuid4())
        end_time = timeout_seconds
        
        while end_time > 0:
            result = await self._redis.client.set(
                lock_key,
                lock_value,
                nx=True,
                ex=self._lock_expire_seconds
            )
            if result:
                return lock_value
            await asyncio.sleep(0.1)
            end_time -= 0.1
        
        return None
    
    async def _release_lock(self, lock_key: str, lock_value: str) -> bool:
        """Release distributed lock"""
        release_script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        else
            return 0
        end
        """
        try:
            script = self._redis.client.register_script(release_script)
            result = await script(keys=[lock_key], args=[lock_value])
            return result == 1
        except Exception:
            return False
    
    async def put(self, message: Any) -> str:
        """Add a message to the stream"""
        logger.debug(f"Putting message into stream ({self._stream_name}): {message}")
        message_id = await self._redis.client.xadd(self._stream_name, {"data": message}, maxlen=1000, approximate=True)
        return message_id
    
    async def get(self, start_id: str = "0", block_ms: Optional[int] = None) -> Tuple[str, Any]:
        """Get a message from the stream.

        Silently resets an invalid start_id to "0" instead of letting Redis
        raise "Invalid stream ID specified as stream command argument".
        Also catches timeout/connection errors and returns (None, None) so
        the caller can retry on the next poll cycle.
        """
        logger.debug(f"Getting message from stream ({self._stream_name}): {start_id}")

        # Sanitise start_id — fall back to "0" for anything Redis would reject
        if not _is_valid_stream_id(start_id):
            logger.warning(
                "Invalid stream ID '%s' for stream %s — resetting to '0'",
                start_id, self._stream_name,
            )
            start_id = "0"

        try:
            messages = await self._redis.client.xread(
                {self._stream_name: start_id},
                count=1,
                block=block_ms
            )
        except Exception as exc:
            logger.warning(
                "Redis xread error on stream %s (start_id=%s): %s — will retry",
                self._stream_name, start_id, exc,
            )
            return None, None
            
        if not messages:
            return None, None
            
        stream_messages = messages[0][1]
        if not stream_messages:
            return None, None
            
        message_id, message_data = stream_messages[0]
        
        try:
            return message_id, message_data.get("data")
        except (KeyError, json.JSONDecodeError):
            return None, None
    
    async def get_range(self, start_id: str = "-", end_id: str = "+", count: int = 100) -> AsyncGenerator[Tuple[str, Any], None]:
        """Get messages within a specified range"""
        messages = await self._redis.client.xrange(self._stream_name, start_id, end_id, count=count)
        
        if not messages:
            return
            
        for message_id, message_data in messages:
            try:
                data = message_data.get("data")
                yield message_id, data
            except (KeyError, json.JSONDecodeError):
                continue
    
    async def get_latest_id(self) -> str:
        """Get the latest message ID"""
        messages = await self._redis.client.xrevrange(self._stream_name, "+", "-", count=1)
        if not messages:
            return "0"
        return messages[0][0]
    
    async def clear(self) -> None:
        """Clear all messages from the stream"""
        await self._redis.client.xtrim(self._stream_name, 0)
    
    async def is_empty(self) -> bool:
        """Check if the stream is empty"""
        return await self.size() == 0
    
    async def size(self) -> int:
        """Get the number of messages in the stream"""
        info = await self._redis.client.xlen(self._stream_name)
        return info

    async def delete_message(self, message_id: str) -> bool:
        """Delete a specific message from the stream"""
        try:
            await self._redis.client.xdel(self._stream_name, message_id)
            return True
        except Exception:
            return False

    async def pop(self) -> Tuple[str, Any]:
        """Get and remove the first message from the stream using distributed lock"""
        logger.debug(f"Popping message from stream ({self._stream_name})")
        lock_key = f"lock:{self._stream_name}:pop"
        
        lock_value = await self._acquire_lock(lock_key)
        if not lock_value:
            return None, None
        
        try:
            messages = await self._redis.client.xrange(self._stream_name, "-", "+", count=1)
            
            if not messages:
                return None, None
            
            message_id, message_data = messages[0]
            await self._redis.client.xdel(self._stream_name, message_id)
            
            try:
                return message_id, message_data.get("data")
            except (KeyError, json.JSONDecodeError):
                logger.exception(f"Error parsing message from stream ({self._stream_name}): {message_data}")
                return None, None
                
        finally:
            await self._release_lock(lock_key, lock_value)
