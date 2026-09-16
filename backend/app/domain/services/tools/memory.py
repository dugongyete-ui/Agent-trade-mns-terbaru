"""Cross-session persistent memory — the agent's self-improving knowledge base.

Ported concept from HKUDS/Vibe-Trading's PersistentMemory: the agent can save,
recall, reinforce and forget knowledge about the user and their projects across
sessions. Storage = MongoDB (Beanie MemoryDocument), scoped per user.

Design goal: capability only. The system prompt is not touched with usage
rules — the tool's own documentation tells the agent what memory is and when
saving makes sense. Recall ranking is plain arithmetic (keyword overlap ×
importance × recency), no LLM involved.
"""

import logging
import re
import uuid
from datetime import datetime, UTC
from typing import Any, Dict, List, Literal, Optional

from langchain.tools import tool
from pydantic import BaseModel, Field

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit

logger = logging.getLogger(__name__)

_VALID_KINDS = ("user", "feedback", "project", "reference", "lesson")

# Recall tuning
_RECALL_CANDIDATE_CAP = 300   # max memories fetched per recall
_RECALL_TOP_K = 5             # max memories returned by recall_memories()
_HALF_LIFE_DAYS = 30.0        # importance decay half-life (Ebbinghaus-style)


def _tokenize(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]{3,}", text.lower())]


def _as_aware(dt: datetime) -> datetime:
    """Mongo returns naive UTC datetimes — make comparisons timezone-safe."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def save_memory(
    user_id: str,
    content: str,
    kind: str = "reference",
    tags: Optional[List[str]] = None,
    importance: float = 0.5,
) -> Dict[str, Any]:
    """Insert or update a memory (content-hash dedup per user)."""
    from app.infrastructure.models.documents import MemoryDocument

    now = datetime.now(UTC)
    importance = min(1.0, max(0.0, float(importance)))
    kind = kind if kind in _VALID_KINDS else "reference"
    tags = [t.strip().lower()[:32] for t in (tags or []) if t and t.strip()][:8]

    existing = await MemoryDocument.find_one(
        MemoryDocument.user_id == user_id,
        MemoryDocument.content == content,
    )
    if existing:
        existing.updated_at = now
        existing.importance = max(existing.importance, importance)
        existing.access_count += 1
        if tags:
            merged = set(existing.tags or []) | set(tags)
            existing.tags = sorted(merged)[:8]
        await existing.save()
        return {"memory_id": existing.memory_id, "action": "updated", "duplicates_merged": 1}

    doc = MemoryDocument(
        memory_id=uuid.uuid4().hex[:16],
        user_id=user_id,
        kind=kind,
        content=content[:2000],
        tags=tags,
        importance=importance,
        created_at=now,
        updated_at=now,
        access_count=0,
        last_accessed_at=None,
    )
    await doc.insert()
    return {"memory_id": doc.memory_id, "action": "created"}


async def recall_memories(
    user_id: str,
    query: str,
    limit: int = _RECALL_TOP_K,
) -> List[Dict[str, Any]]:
    """Keyword-overlap × importance × recency ranking (plain arithmetic)."""
    from app.infrastructure.models.documents import MemoryDocument

    limit = max(1, min(int(limit), 10))
    docs = (
        await MemoryDocument.find(MemoryDocument.user_id == user_id)
        .sort(-MemoryDocument.updated_at)
        .limit(_RECALL_CANDIDATE_CAP)
        .to_list()
    )
    if not docs:
        return []

    now = datetime.now(UTC)
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        q_tokens = set()

    scored: List[tuple] = []
    for d in docs:
        d_tokens = set(_tokenize(d.content)) | set(d.tags or [])
        overlap = len(q_tokens & d_tokens) if q_tokens else 0
        # Coverage of query tokens matters more than raw count
        coverage = (overlap / len(q_tokens)) if q_tokens else 0.0
        # Recency boost: full weight at 0 days, halves every HALF_LIFE_DAYS
        age_days = max(0.0, (now - _as_aware(d.updated_at)).total_seconds() / 86400.0)
        recency = 0.5 ** (age_days / _HALF_LIFE_DAYS)
        score = coverage * (0.5 + d.importance) + 0.15 * recency + 0.02 * min(d.access_count, 10) / 10.0
        if score <= 0.01:
            continue
        scored.append((score, d))

    scored.sort(key=lambda t: t[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for score, d in scored[:limit]:
        d.last_accessed_at = now
        d.access_count += 1
        await d.save()
        out.append({
            "memory_id": d.memory_id,
            "kind": d.kind,
            "content": d.content,
            "tags": d.tags,
            "importance": round(d.importance, 2),
            "updated_at": d.updated_at.isoformat(),
            "score": round(score, 3),
        })
    return out


async def forget_memory(user_id: str, memory_id: str) -> bool:
    from app.infrastructure.models.documents import MemoryDocument
    doc = await MemoryDocument.find_one(
        MemoryDocument.user_id == user_id,
        MemoryDocument.memory_id == memory_id,
    )
    if not doc:
        return False
    await doc.delete()
    return True


async def list_memories(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    from app.infrastructure.models.documents import MemoryDocument
    docs = (
        await MemoryDocument.find(MemoryDocument.user_id == user_id)
        .sort(-MemoryDocument.updated_at)
        .limit(max(1, min(limit, 50)))
        .to_list()
    )
    return [{
        "memory_id": d.memory_id,
        "kind": d.kind,
        "content": d.content,
        "tags": d.tags,
        "importance": round(d.importance, 2),
        "access_count": d.access_count,
        "updated_at": d.updated_at.isoformat(),
    } for d in docs]


async def build_memory_recall_block(user_id: Optional[str], message: str) -> Optional[str]:
    """Build the internal recalled-memories block injected before each run."""
    if not user_id or not message or not message.strip():
        return None
    try:
        recalled = await recall_memories(user_id, message, limit=3)
    except Exception as exc:  # noqa: BLE001 — memory must never break a chat
        logger.warning(f"Memory recall failed (non-fatal): {exc}")
        return None
    if not recalled:
        return None
    lines = [
        f"{i+1}. ({m['kind']}) {m['content'][:400]}"
        for i, m in enumerate(recalled)
    ]
    return (
        "[INTERNAL MEMORY CONTEXT — prior knowledge about this user from previous "
        "sessions; use silently if relevant, never mention this block]:\n" + "\n".join(lines)
    )


class RememberArgs(BaseModel):
    """Args schema documented for the agent (kept in one place)."""

    action: Literal["save", "recall", "forget", "list"] = Field(
        description=(
            "save = store a new memory (or reinforce an identical one); "
            "recall = search memories relevant to a query; "
            "forget = delete one memory by id; "
            "list = show the most recently updated memories."
        )
    )
    content: Optional[str] = Field(
        default=None,
        description=(
            "save: the memory itself — a concise, self-contained statement of durable "
            "knowledge (preference, feedback, project fact, lesson learned). "
            "One fact per memory; no conversational filler."
        ),
    )
    kind: Optional[Literal["user", "feedback", "project", "reference", "lesson"]] = Field(
        default=None,
        description=(
            "save: memory category — "
            "'user' = who the user is / preferences (risk style, assets, language); "
            "'feedback' = corrections or preferences about how you should behave; "
            "'project' = durable facts about the user's trading projects/portfolios; "
            "'reference' = useful external facts (exchanges, symbols, tools that worked); "
            "'lesson' = what worked or failed in analysis, for future runs."
        ),
    )
    tags: Optional[List[str]] = Field(
        default=None,
        description="save: up to 8 short lowercase keywords that help future recall (e.g. ['risk','scalping']).",
    )
    importance: Optional[float] = Field(
        default=None,
        description=(
            "save: 0.0-1.0. 0.9+ = explicit user request or strong preference; "
            "0.5-0.8 = useful context; below 0.4 = marginal. Default 0.5."
        ),
    )
    query: Optional[str] = Field(
        default=None,
        description="recall: natural-language search phrase (keywords from it are matched).",
    )
    memory_id: Optional[str] = Field(
        default=None,
        description="forget: the id of the memory to delete (from recall/list output).",
    )
    limit: Optional[int] = Field(
        default=None,
        description="recall/list: max entries returned (default 10, max 50).",
    )


class MemoryToolkit(BaseToolkit):
    """Persistent cross-session memory for the agent (per-user, MongoDB-backed).

    Capability only: the agent decides what is worth remembering and when
    to recall it. No usage rules are injected into any prompt.
    """

    name: str = "memory"
    session_repository: Any = None
    session_id: Optional[str] = None

    def __init__(self, session_repository: Any = None, session_id: Optional[str] = None):
        super().__init__()
        self.session_repository = session_repository
        self.session_id = session_id

    async def _resolve_user_id(self) -> Optional[str]:
        if not self.session_repository or not self.session_id:
            return None
        try:
            session = await self.session_repository.find_by_id(self.session_id)
        except Exception:  # noqa: BLE001
            return None
        return getattr(session, "user_id", None) if session else None

    @tool(parse_docstring=True)
    async def remember(
        self,
        action: str,
        content: Optional[str] = None,
        kind: Optional[str] = None,
        tags: Optional[List[str]] = None,
        importance: Optional[float] = None,
        query: Optional[str] = None,
        memory_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> ToolResult:
        """Persist and recall knowledge about the user across sessions (long-term memory).

        save: store one durable, self-contained fact about the user, their
        preferences, projects or a lesson learned — future sessions will
        automatically recall relevant memories when related topics come up.
        Saving the same content twice merges (reinforces) instead of
        duplicating. recall: search by a query phrase and get the most
        relevant memories with ids. list: browse recent memories.
        forget: delete a memory by id (e.g. when the user says something no
        longer applies).

        Args:
            action: One of save, recall, forget, list.
            content: save — the memory text (one concise self-contained fact, max ~2000 chars).
            kind: save — one of user, feedback, project, reference, lesson.
            tags: save — up to 8 short lowercase keywords for future recall.
            importance: save — 0.0 to 1.0 (higher = more durable/central; user-stated preferences deserve 0.9+).
            query: recall — search phrase.
            memory_id: forget — id of the memory to delete.
            limit: recall/list — max entries (default 10, max 50).
        """
        user_id = await self._resolve_user_id()
        if not user_id:
            return ToolResult(success=False, message="Memory requires a valid session context.")

        act = (action or "").strip().lower()
        if act == "save":
            if not content or not content.strip():
                return ToolResult(success=False, message="save requires non-empty 'content'.")
            res = await save_memory(
                user_id=user_id,
                content=content.strip(),
                kind=(kind or "reference"),
                tags=tags,
                importance=0.5 if importance is None else float(importance),
            )
            total = await _count_user_memories(user_id)
            return ToolResult(success=True, data={
                "result": res,
                "user_memory_count": total,
                "note": "Saved. Relevant memories are auto-recalled in future sessions on related topics.",
            })

        if act == "recall":
            if not query or not query.strip():
                return ToolResult(success=False, message="recall requires a 'query'.")
            found = await recall_memories(user_id, query, limit or 10)
            return ToolResult(success=True, data={"query": query, "matches": found, "count": len(found)})

        if act == "forget":
            if not memory_id:
                return ToolResult(success=False, message="forget requires 'memory_id'.")
            ok = await forget_memory(user_id, memory_id)
            return ToolResult(
                success=ok,
                message="Memory deleted." if ok else f"No memory with id '{memory_id}' for this user.",
            )

        if act == "list":
            items = await list_memories(user_id, limit or 20)
            return ToolResult(success=True, data={"memories": items, "count": len(items)})

        return ToolResult(success=False, message=f"Unknown action '{action}'. Valid: save, recall, forget, list.")


async def _count_user_memories(user_id: str) -> int:
    from app.infrastructure.models.documents import MemoryDocument
    try:
        return int(max(0, await MemoryDocument.find(MemoryDocument.user_id == user_id).count()))
    except Exception:  # noqa: BLE001
        return 0
