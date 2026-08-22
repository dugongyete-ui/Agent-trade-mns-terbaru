#!/usr/bin/env python3
"""
MongoDB MCP Server
Connects to MongoDB Atlas for the AI Dzeck project.
Allows AI agent to query users, sessions, agents, and other collections.
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
    from bson import ObjectId
except ImportError:
    raise ImportError("Install pymongo: pip install pymongo")

MONGODB_URI = os.environ.get("MONGODB_URI", "")

_client = None


def get_client():
    global _client
    if _client is None:
        if not MONGODB_URI:
            raise ValueError("MONGODB_URI environment variable is not set")
        _client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    return _client


def serialize(obj):
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize(i) for i in obj]
    return obj


async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="mongo-list-collections",
            description="List all collections in the MongoDB database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "database": {
                        "type": "string",
                        "description": "Database name (leave empty to use default from URI)"
                    }
                }
            }
        ),
        Tool(
            name="mongo-find",
            description=(
                "Query documents from a MongoDB collection. "
                "Supports filter, projection, sort, and limit. "
                "Collections in this project: users, sessions, agents, files, messages."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "collection": {
                        "type": "string",
                        "description": "Collection name e.g. users, sessions, agents"
                    },
                    "filter": {
                        "type": "object",
                        "description": "MongoDB filter query e.g. {\"status\": \"active\"}",
                        "default": {}
                    },
                    "projection": {
                        "type": "object",
                        "description": "Fields to include/exclude e.g. {\"password\": 0}",
                        "default": {}
                    },
                    "sort": {
                        "type": "object",
                        "description": "Sort order e.g. {\"created_at\": -1}",
                        "default": {}
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max documents to return",
                        "default": 10
                    },
                    "database": {
                        "type": "string",
                        "description": "Database name (optional)"
                    }
                },
                "required": ["collection"]
            }
        ),
        Tool(
            name="mongo-count",
            description="Count documents in a collection matching a filter.",
            inputSchema={
                "type": "object",
                "properties": {
                    "collection": {"type": "string"},
                    "filter": {"type": "object", "default": {}},
                    "database": {"type": "string"}
                },
                "required": ["collection"]
            }
        ),
        Tool(
            name="mongo-aggregate",
            description=(
                "Run a MongoDB aggregation pipeline. "
                "Use for complex analytics, grouping, and statistics."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "collection": {"type": "string"},
                    "pipeline": {
                        "type": "array",
                        "description": "Aggregation pipeline stages",
                        "items": {"type": "object"}
                    },
                    "database": {"type": "string"}
                },
                "required": ["collection", "pipeline"]
            }
        ),
        Tool(
            name="mongo-stats",
            description="Get database and collection statistics — sizes, document counts, indexes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "database": {"type": "string"}
                }
            }
        ),
    ]


def get_db(database: str = ""):
    client = get_client()
    if database:
        return client[database]
    db_name = MONGODB_URI.split("/")[-1].split("?")[0]
    if not db_name:
        db_name = "dzeck"
    return client[db_name]


async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        db = get_db(arguments.get("database", ""))

        if name == "mongo-list-collections":
            collections = db.list_collection_names()
            lines = [f"📂 Collections in '{db.name}' ({len(collections)} total)\n"]
            for col in sorted(collections):
                count = db[col].count_documents({})
                lines.append(f"  {col:<30} {count:>8} documents")
            text = "\n".join(lines)

        elif name == "mongo-find":
            col = db[arguments["collection"]]
            filter_q = arguments.get("filter", {})
            projection = arguments.get("projection", {}) or None
            sort_q = arguments.get("sort", {})
            limit = min(arguments.get("limit", 10), 100)

            cursor = col.find(filter_q, projection)
            if sort_q:
                cursor = cursor.sort(list(sort_q.items()))
            cursor = cursor.limit(limit)

            docs = [serialize(d) for d in cursor]
            text = (
                f"🔍 {arguments['collection']} — {len(docs)} document(s)\n\n"
                + json.dumps(docs, indent=2, ensure_ascii=False)
            )

        elif name == "mongo-count":
            col = db[arguments["collection"]]
            count = col.count_documents(arguments.get("filter", {}))
            text = f"📊 {arguments['collection']}: {count} document(s) match the filter"

        elif name == "mongo-aggregate":
            col = db[arguments["collection"]]
            results = [serialize(d) for d in col.aggregate(arguments["pipeline"])]
            text = (
                f"📊 Aggregation on '{arguments['collection']}' — {len(results)} result(s)\n\n"
                + json.dumps(results, indent=2, ensure_ascii=False)
            )

        elif name == "mongo-stats":
            lines = [f"📊 MongoDB Stats — '{db.name}'\n"]
            for col_name in sorted(db.list_collection_names()):
                col = db[col_name]
                count = col.count_documents({})
                indexes = len(list(col.list_indexes()))
                lines.append(f"  {col_name:<30} docs: {count:>8}  indexes: {indexes}")
            text = "\n".join(lines)

        else:
            text = f"Unknown tool: {name}"

    except PyMongoError as e:
        text = f"MongoDB Error: {e}"
    except Exception as e:
        text = f"Error: {type(e).__name__}: {e}"

    return [TextContent(type="text", text=text)]


async def _handle_list_tools(_context, _params):
    return ListToolsResult(tools=await list_tools())


async def _handle_call_tool(_context, params):
    return CallToolResult(content=await call_tool(params.name, params.arguments or {}))


app = Server("mongodb-mcp", on_list_tools=_handle_list_tools, on_call_tool=_handle_call_tool)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
