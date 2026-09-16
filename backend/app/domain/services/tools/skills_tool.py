"""Skills toolkit — progressive-disclosure docs the agent can read AND write.

The agent can write its own skills: ``save_skill`` persists a reusable
playbook, ``patch_skill`` fixes one, ``load_skill`` reads the full document on
demand (the system prompt only carries name + description).

Safety model (same as Vibe-Trading): slug sanitisation, user writes confined
to ``backend/skills/user/``, bundled skills immutable (patching copies them to
the user dir first). No arbitrary file access.
"""

from typing import Any, Dict, List, Optional

from langchain.tools import tool
from pydantic import BaseModel, Field

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit
from app.domain.services.skills.store import (
    SkillStore,
    get_skill_store,
    sanitize_skill_name,
)

_OUTLINE_CHARS = 4_500


class _Section(BaseModel):
    title: str
    start: int
    end: int


def _split_sections(body: str) -> List[_Section]:
    """Split markdown body into heading-delimited sections."""
    marks: List[tuple] = []
    offset = 0
    for line in body.splitlines(keepends=True):
        if line.lstrip().startswith("#"):
            marks.append((offset, line.strip()))
        offset += len(line)
    if not marks:
        return [_Section(title="(whole document)", start=0, end=len(body))]
    sections: List[_Section] = []
    # Preamble before the first heading
    if marks[0][0] > 0:
        sections.append(_Section(title="(introduction)", start=0, end=marks[0][0]))
    for i, (pos, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        sections.append(_Section(title=title, start=pos, end=end))
    return sections


class SkillToolkit(BaseToolkit):
    """Read + author markdown skills (progressive disclosure)."""

    name: str = "skills"

    def __init__(self, store: Optional[SkillStore] = None, **kwargs: Any):
        super().__init__(**kwargs)
        self._store = store or get_skill_store()

    @tool(parse_docstring=True)
    async def load_skill(self, name: str, section: Optional[str] = None) -> ToolResult:
        """Load the full documentation of a named skill.

        Use this to learn a workflow or playbook before doing that kind of
        task. A skill that fits comes back complete. A long one comes back as
        an outline of its sections plus the opening text: call again with
        section="<copy a heading below verbatim>" to pull one section in full.

        Args:
            name: Skill name exactly as listed in the skills list, e.g. "backtest-methodology".
            section: Optional heading to load one section instead of the whole document.
        """
        content = self._store.get_content(name)
        if content is None:
            available = ", ".join(s.name for s in self._store.list_skills()) or "(none)"
            return ToolResult(
                success=False,
                message=f"Skill '{name}' not found. Available skills: {available}",
            )
        body = content.split("\n", 1)[1].rsplit("\n", 1)[0] if content.count("\n") >= 2 else content

        if section:
            sections = _split_sections(body)
            exact = [s for s in sections if s.title.strip().lower() == section.strip().lower()]
            if not exact:
                prefix = [s for s in sections if s.title.strip().lower().startswith(section.strip().lower())]
                if len(prefix) == 1:
                    exact = prefix
            if len(exact) != 1:
                listing = "\n".join(f"  - {s.title}" for s in sections)
                return ToolResult(
                    success=False,
                    message=(
                        f"Section '{section}' is ambiguous in skill '{name}'. "
                        f"Pick one verbatim:\n{listing}"
                    ) if len(exact) > 1 else
                    f"Section '{section}' not found in skill '{name}'. Sections:\n{listing}",
                )
            s0 = exact[0]
            return ToolResult(success=True, data={
                "skill": sanitize_skill_name(name),
                "mode": "section",
                "section": s0.title,
                "content": body[s0.start:s0.end].strip(),
            })

        if len(body) <= _OUTLINE_CHARS:
            return ToolResult(success=True, data={
                "skill": sanitize_skill_name(name),
                "mode": "document",
                "content": body,
            })

        sections = _split_sections(body)
        outline_lines = [
            f"  - {s.title} ({s.end - s.start} chars)" for s in sections
        ]
        opening = body[:1200]
        return ToolResult(success=True, data={
            "skill": sanitize_skill_name(name),
            "mode": "outline",
            "outline": "\n".join(outline_lines),
            "opening": opening,
            "hint": 'Call load_skill(name="' + name + '", section="<heading verbatim>") to read one section in full.',
        })

    @tool(parse_docstring=True)
    async def save_skill(self, name: str, content: str, description: str = "", category: str = "other") -> ToolResult:
        """Create or overwrite a reusable skill written by you (the agent).

        Persist a workflow that worked: concrete steps, decision rules, data
        quirks you discovered, tool-call recipes with real parameter patterns.
        Write it as markdown. A good skill is self-contained and specific —
        a future session with NO memory of this conversation must be able to
        follow it. Frontmatter is added automatically when missing.

        Args:
            name: Short slug for the skill, e.g. "xauusd-session-playbook".
            content: Full markdown body of the skill (the playbook itself).
            description: One-line description shown in the skills list (what it teaches + when to use it).
            category: Free tag, e.g. "strategy", "analysis", "workflow".
        """
        if not content or len(content.strip()) < 40:
            return ToolResult(success=False, message="Skill content too short to be a useful playbook (min ~40 chars).")
        text = content.strip()
        if description:
            if text.lstrip().startswith("---"):
                # Rewrite the description line in existing frontmatter if absent
                lines = text.splitlines()
                if not any(l.strip().lower().startswith("description:") for l in lines[:8]):
                    insert_at = next((i for i, l in enumerate(lines) if l.strip() == "---" and i > 0), None)
                    if insert_at is None:
                        lines.insert(1, f"description: {description}")
                        text = "\n".join(lines)
            else:
                text = f"---\nname: {sanitize_skill_name(name)}\ndescription: {description}\ncategory: {category or 'other'}\n---\n\n{text}"
        skill = self._store.save(name, text, category=category or "other")
        return ToolResult(success=True, data={
            "saved": skill.name,
            "user_written": True,
            "chars": len(skill.body),
            "description": skill.description,
            "note": "Skill available immediately via load_skill and in future sessions' skills list.",
        })

    @tool(parse_docstring=True)
    async def patch_skill(self, name: str, find: str, replace: str) -> ToolResult:
        """Fix an existing skill with a targeted find/replace (exactly one occurrence).

        Use when a skill's instructions turned out to be wrong or outdated.
        Bundled skills are copied to your user directory first, then patched —
        originals stay untouched.

        Args:
            name: Skill name to patch.
            find: Exact text to replace (must occur exactly once in the skill body).
            replace: Replacement text.
        """
        try:
            skill = self._store.patch(name, find, replace)
        except ValueError as exc:
            return ToolResult(success=False, message=str(exc))
        return ToolResult(success=True, data={
            "patched": skill.name,
            "chars": len(skill.body),
        })
