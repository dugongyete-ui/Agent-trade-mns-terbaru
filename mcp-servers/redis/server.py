#!/usr/bin/env python3
"""
Redis MCP Server
Connects to Redis Cloud for the AI Dzeck project.
Supports password authentication — for monitoring sessions, cache, and task queues.
"""

import asyncio
import json
import os
from typing import Any

import redis as redis_lib
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
REDIS_DB = int(os.environ.get("REDIS_DB", 0))

_client = None


def get_client():
    global _client
    if _client is None:
        _client = redis_lib.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD if REDIS_PASSWORD else None,
            db=REDIS_DB,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        _client.ping()
    return _client


async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="redis-info",
            description="Get Redis server info — memory, clients, stats, uptime.",
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="redis-keys",
            description=(
                "List Redis keys matching a pattern. "
                "Use '*' for all keys, 'session:*' for sessions, 'task:*' for tasks, etc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Key pattern e.g. '*', 'session:*', 'cache:*'",
                        "default": "*"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max keys to show",
                        "default": 50
                    }
                }
            }
        ),
        Tool(
            name="redis-get",
            description="Get the value of a Redis key. Auto-detects type (string, hash, list, set).",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Redis key name"
                    }
                },
                "required": ["key"]
            }
        ),
        Tool(
            name="redis-delete",
            description="Delete one or more Redis keys.",
            inputSchema={
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of keys to delete"
                    }
                },
                "required": ["keys"]
            }
        ),
        Tool(
            name="redis-stats",
            description=(
                "Get overview stats — total keys, memory usage, connected clients, "
                "active sessions, task queues. Good for monitoring the project's Redis usage."
            ),
            inputSchema={"type": "object", "properties": {}}
        ),
        Tool(
            name="redis-flush-pattern",
            description=(
                "Delete all keys matching a pattern. "
                "Use with caution — e.g. 'cache:*' to clear all cache entries."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Pattern to match keys for deletion e.g. 'cache:*'"
                    }
                },
                "required": ["pattern"]
            }
        ),
    ]


async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        r = get_client()

        if name == "redis-info":
            info = r.info()
            lines = ["📊 Redis Server Info\n"]
            important = [
                ("redis_version", "Version"),
                ("uptime_in_seconds", "Uptime (s)"),
                ("connected_clients", "Connected clients"),
                ("used_memory_human", "Memory used"),
                ("maxmemory_human", "Memory max"),
                ("total_commands_processed", "Commands processed"),
                ("keyspace_hits", "Keyspace hits"),
                ("keyspace_misses", "Keyspace misses"),
            ]
            for key, label in important:
                if key in info:
                    lines.append(f"  {label:<25} {info[key]}")

            keyspace = {k: v for k, v in info.items() if k.startswith("db")}
            if keyspace:
                lines.append(f"\n  Keyspaces:")
                for db, stats in keyspace.items():
                    lines.append(f"    {db}: {stats}")

            text = "\n".join(lines)

        elif name == "redis-keys":
            pattern = arguments.get("pattern", "*")
            limit = min(arguments.get("limit", 50), 200)

            all_keys = list(r.scan_iter(pattern, count=200))
            total = len(all_keys)
            keys_shown = all_keys[:limit]

            lines = [f"🔑 Keys matching '{pattern}' — {total} found (showing {len(keys_shown)})\n"]
            for key in sorted(keys_shown):
                try:
                    ktype = r.type(key)
                    ttl = r.ttl(key)
                    ttl_str = f"TTL:{ttl}s" if ttl > 0 else ("no-exp" if ttl == -1 else "expired")
                    lines.append(f"  {ktype:<8} {ttl_str:<12} {key}")
                except Exception:
                    lines.append(f"  {'?':<8} {'?':<12} {key}")

            text = "\n".join(lines)

        elif name == "redis-get":
            key = arguments["key"]
            ktype = r.type(key)

            if ktype == "none":
                text = f"Key '{key}' does not exist"
            elif ktype == "string":
                value = r.get(key)
                ttl = r.ttl(key)
                try:
                    parsed = json.loads(value)
                    value_str = json.dumps(parsed, indent=2, ensure_ascii=False)
                except Exception:
                    value_str = value
                text = f"🔑 Key: {key}\nType: string | TTL: {ttl}s\n\nValue:\n{value_str}"
            elif ktype == "hash":
                value = r.hgetall(key)
                ttl = r.ttl(key)
                text = f"🔑 Key: {key}\nType: hash | TTL: {ttl}s | Fields: {len(value)}\n\n{json.dumps(value, indent=2, ensure_ascii=False)}"
            elif ktype == "list":
                length = r.llen(key)
                value = r.lrange(key, 0, 19)
                ttl = r.ttl(key)
                text = f"🔑 Key: {key}\nType: list | TTL: {ttl}s | Length: {length}\n\nFirst 20:\n{json.dumps(value, indent=2, ensure_ascii=False)}"
            elif ktype == "set":
                value = list(r.smembers(key))
                ttl = r.ttl(key)
                text = f"🔑 Key: {key}\nType: set | TTL: {ttl}s | Members: {len(value)}\n\n{json.dumps(sorted(value), indent=2, ensure_ascii=False)}"
            elif ktype == "zset":
                value = r.zrange(key, 0, 19, withscores=True)
                ttl = r.ttl(key)
                text = f"🔑 Key: {key}\nType: zset | TTL: {ttl}s\n\nTop 20:\n{json.dumps(value, indent=2, ensure_ascii=False)}"
            else:
                text = f"Key '{key}' type '{ktype}' not supported for display"

        elif name == "redis-delete":
            keys = arguments["keys"]
            deleted = r.delete(*keys)
            text = f"🗑️  Deleted {deleted}/{len(keys)} key(s):\n" + "\n".join(f"  - {k}" for k in keys)

        elif name == "redis-stats":
            info = r.info()
            total_keys = sum(
                db_info.get("keys", 0)
                for k, db_info in info.items()
                if k.startswith("db") and isinstance(db_info, dict)
            )

            session_keys = len(list(r.scan_iter("session:*", count=100)))
            task_keys = len(list(r.scan_iter("task:*", count=100)))
            cache_keys = len(list(r.scan_iter("cache:*", count=100)))

            lines = [
                "📊 Redis Stats — Project Overview\n",
                f"  Server         : {REDIS_HOST}:{REDIS_PORT}",
                f"  Total keys     : {total_keys}",
                f"  Memory used    : {info.get('used_memory_human', 'N/A')}",
                f"  Clients        : {info.get('connected_clients', 'N/A')}",
                f"  Uptime         : {info.get('uptime_in_seconds', 0) // 3600}h",
                f"",
                f"  Project keys:",
                f"    session:*    : {session_keys}",
                f"    task:*       : {task_keys}",
                f"    cache:*      : {cache_keys}",
            ]
            text = "\n".join(lines)

        elif name == "redis-flush-pattern":
            pattern = arguments["pattern"]
            keys = list(r.scan_iter(pattern, count=200))
            if not keys:
                text = f"No keys found matching '{pattern}'"
            else:
                deleted = r.delete(*keys)
                text = f"🗑️  Flushed {deleted} key(s) matching '{pattern}'"

        else:
            text = f"Unknown tool: {name}"

    except redis_lib.ConnectionError as e:
        text = f"Redis Connection Error: {e}\nHost: {REDIS_HOST}:{REDIS_PORT}"
    except Exception as e:
        text = f"Error: {type(e).__name__}: {e}"

    return [TextContent(type="text", text=text)]


async def _handle_list_tools(_context, _params):
    return ListToolsResult(tools=await list_tools())


async def _handle_call_tool(_context, params):
    return CallToolResult(content=await call_tool(params.name, params.arguments or {}))


app = Server("redis-mcp", on_list_tools=_handle_list_tools, on_call_tool=_handle_call_tool)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
