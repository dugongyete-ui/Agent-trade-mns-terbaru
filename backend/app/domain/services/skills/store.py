"""Skill store — bundled + user-authored markdown skills with YAML-ish frontmatter.

Port of the Vibe-Trading skills system, trimmed to what this agent needs:

- A skill is a directory holding ``SKILL.md`` (optional extra files allowed but
  only SKILL.md is loaded into context).
- Frontmatter: ``name``, ``description``, ``category`` (all optional — name
  falls back to the directory name).
- Progressive disclosure: the system prompt only ever carries
  ``name: description`` lines; the full body is pulled on demand via the
  ``load_skill`` tool.
- The agent can write its own skills: ``save_skill`` creates/updates user
  skills, ``patch_skill`` edits them. Bundled skills are immutable — patching
  one copies it into the user directory first, then edits the copy.

Layout:
    backend/skills/<name>/SKILL.md        bundled (read-only, shipped)
    backend/skills/user/<name>/SKILL.md   user/agent-authored (mutable)
"""

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[4]
BUNDLED_SKILLS_DIR = _BACKEND_ROOT / "skills"
USER_SKILLS_DIR = BUNDLED_SKILLS_DIR / "user"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_SLUG_RE = re.compile(r"[^a-z0-9\-_]+")
_MAX_NAME_LEN = 60


@dataclass
class Skill:
    name: str
    description: str = ""
    category: str = "other"
    body: str = ""
    dir_path: str = ""
    user_written: bool = False
    metadata: Dict[str, str] = field(default_factory=dict)


def parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """Split ``---\\nkey: value\\n---`` frontmatter from the markdown body."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if not key:
            continue
        meta[key] = value
    return meta, text[m.end():]


def sanitize_skill_name(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:_MAX_NAME_LEN].strip("-")
    return slug or "unnamed-skill"


class SkillStore:
    """Loads bundled skills, lets user skills override them by name."""

    def __init__(self, bundled_dir: Path = BUNDLED_SKILLS_DIR,
                 user_dir: Path = USER_SKILLS_DIR):
        self.bundled_dir = Path(bundled_dir)
        self.user_dir = Path(user_dir)
        self._cache: Dict[str, Skill] = {}

    # ── loading ──────────────────────────────────────────────────────────
    @staticmethod
    def _load_skill_dir(path: Path, user_written: bool) -> Optional[Skill]:
        md = path / "SKILL.md"
        if not md.is_file():
            return None
        try:
            text = md.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skill %s unreadable: %s", path.name, exc)
            return None
        meta, body = parse_frontmatter(text)
        name = meta.get("name") or path.name
        return Skill(
            name=name,
            description=meta.get("description", ""),
            category=meta.get("category", "other"),
            body=body.strip(),
            dir_path=str(path),
            user_written=user_written,
            metadata={k: v for k, v in meta.items()
                      if k not in ("name", "description", "category")},
        )

    def _load_all(self) -> Dict[str, Skill]:
        skills: Dict[str, Skill] = {}
        if self.bundled_dir.is_dir():
            for child in sorted(self.bundled_dir.iterdir()):
                if not child.is_dir() or child.name == "user":
                    continue
                skill = self._load_skill_dir(child, user_written=False)
                if skill:
                    skills[skill.name] = skill
        # User dir loaded LAST so user skills override bundled by name.
        self.user_dir.mkdir(parents=True, exist_ok=True)
        for child in sorted(self.user_dir.iterdir()):
            if not child.is_dir():
                continue
            skill = self._load_skill_dir(child, user_written=True)
            if skill:
                skills[skill.name] = skill
        return skills

    def list_skills(self, refresh: bool = True) -> List[Skill]:
        if refresh or not self._cache:
            self._cache = self._load_all()
        return list(self._cache.values())

    def get(self, name: str, refresh: bool = False) -> Optional[Skill]:
        if refresh or not self._cache:
            self._cache = self._load_all()
        return self._cache.get(name)

    def get_content(self, name: str) -> Optional[str]:
        skill = self.get(name, refresh=True)
        if not skill:
            # On-disk fallback so a save_skill moments ago is immediately
            # loadable even if the name slug drifted.
            slug = sanitize_skill_name(name)
            skill = self._load_skill_dir(self.user_dir / slug, user_written=True)
        if not skill:
            return None
        return f'<skill name="{skill.name}">\n{skill.body}\n</skill>'

    def descriptions(self) -> str:
        """One line per skill for the system prompt (progressive disclosure)."""
        skills = self.list_skills()
        if not skills:
            return "(no skills available yet)"
        lines = [f"  - {s.name}: {s.description or '(no description)'}" for s in skills]
        return "\n".join(lines)

    # ── writing ──────────────────────────────────────────────────────────
    @staticmethod
    def _ensure_frontmatter(content: str, slug: str, category: str) -> str:
        if content.lstrip().startswith("---"):
            return content
        return (
            f"---\nname: {slug}\ndescription: User-created skill\ncategory: {category}\n---\n\n{content}"
        )

    def _user_skill_dir(self, name: str) -> Path:
        slug = sanitize_skill_name(name)
        target = (self.user_dir / slug).resolve()
        user_root = self.user_dir.resolve()
        if target != user_root and user_root not in target.parents:
            raise ValueError("skill path escapes the user skills directory")
        return target

    def save(self, name: str, content: str, category: str = "other") -> Skill:
        slug = sanitize_skill_name(name)
        target = self._user_skill_dir(slug)
        target.mkdir(parents=True, exist_ok=True)
        text = self._ensure_frontmatter(content, slug, category or "other")
        (target / "SKILL.md").write_text(text, encoding="utf-8")
        self._cache.pop(slug, None)
        skill = self._load_skill_dir(target, user_written=True)
        assert skill is not None
        return skill

    def patch(self, name: str, find: str, replace: str) -> Skill:
        """Find/replace exactly ONE occurrence; bundled skills are copied to
        the user directory first — never modified in place."""
        skill = self.get(name, refresh=True)
        if not skill:
            raise ValueError(f"skill '{name}' not found")
        body = skill.body
        count = body.count(find)
        if count != 1:
            raise ValueError(
                f"patch expects exactly one occurrence of the find text, found {count}"
            )
        new_body = body.replace(find, replace, 1)
        if skill.user_written:
            return self.save(skill.name, new_body, category=skill.category)
        # Copy bundled → user, then patch the copy.
        slug = sanitize_skill_name(skill.name)
        target = self._user_skill_dir(slug)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(skill.dir_path, target)
        return self.save(skill.name, new_body, category=skill.category)


# Process-wide store (skills live on disk; cheap to share).
_store: Optional[SkillStore] = None


def get_skill_store() -> SkillStore:
    global _store
    if _store is None:
        _store = SkillStore()
    return _store


def build_skills_block() -> str:
    """System-prompt section listing available skills (name + description only)."""
    store = get_skill_store()
    try:
        skills = store.list_skills()
    except Exception as exc:  # noqa: BLE001
        logger.warning("skills block build failed: %s", exc)
        return ""
    if not skills:
        return ""
    lines = [
        "",
        "<skills>",
        "Reusable playbooks live as skills. This list is only name + description —",
        "call load_skill(name) to read the full document BEFORE doing that kind of",
        "task. After a workflow you built succeeds, persist it with save_skill so",
        "future sessions can reuse it; fix outdated ones with patch_skill.",
        "",
        "Available skills:",
        store.descriptions(),
        "</skills>",
        "",
    ]
    return "\n".join(lines)
