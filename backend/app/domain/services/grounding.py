"""Grounding gate — mechanical verification of numbers against session evidence.

Port of the Vibe-Trading grounding philosophy, sized to this agent:
every price/percentage the final summary asserts must be traceable to a tool
result the run actually produced. The gate is deliberately mechanical:

1. EVIDENCE — every successful ToolMessage in the agent memory is ingested:
   JSON numeric leaves (with their key path) plus plain-text numbers.
2. CLAIMS — the summary is scanned for figure-shaped numbers (decimals and
   percentages; plain integers, dates and times are exempt). The model MAY
   declare roles in a ```figures``` block (observed / derived / proposed /
   cited / count); undeclared numbers are checked as observations.
3. VERDICTS — a claim passes if it matches some evidence value within a 0.5%
   tolerance band, is a `derived` with an evaluable arithmetic note over
   observed operands, is a `proposed` inside the observed price band, or is a
   `cited` with a visible source. Otherwise it is flagged with the nearest
   observed values so the model can correct it.
4. RELEASE LADDER — invalid drafts get ONE-style bounded correction rounds
   (max 2); a draft that still fails is released REDACTED: flagged figures
   are cut, a footnote names the count and the observed range. The gate never
   blocks forever and never crashes the run.

Fail-open behaviour: if the gate itself errors, the original text is released.
"""

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_TOLERANCE_REL = 0.005      # 0.5% agreement band
_TOLERANCE_ABS_FLOOR = 0.05  # helps rounded small values (percents, FX)

_ROLES = ("observed", "derived", "proposed", "cited", "count")

# Field-name fragments that mark a numeric evidence leaf as a percentage.
_PCT_KEY_RE = re.compile(r"pct|percent|ratio|rate|_rsi|rsi_|probability|confidence", re.IGNORECASE)

_FIGURES_BLOCK_RE = re.compile(
    r"```figures\s*\n(?P<body>.*?)```", re.DOTALL | re.IGNORECASE
)

# Number shapes: 4296.14 | 4,296.14 | 4.296,14 | 42.7% | 82345.67
# Maximal digit run with repeating [.,] groups — the ambiguity between
# thousands and decimal separators is resolved by _parse_num (all readings).
_NUM_TOKEN_RE = re.compile(
    r"(?P<num>-?\d+(?:[.,]\d+)*)"
    r"(?P<pct>\s?%)?"
)
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}|\b\d{2}:\d{2}(?::\d{2})?\b")
_INT_RE = re.compile(r"^-?\d+$")

_SOURCE_HINT_RE = re.compile(
    r"https?://|source|sumber|menurut|reported|report|tool|binance|deriv|tradingview|"
    r"sentiment|calendar|api|via|dari",
    re.IGNORECASE,
)


def _parse_num(raw: str) -> Optional[float]:
    """Parse a numeric token, resolving en/id thousands+decimal conventions."""
    readings = _parse_num_all(raw)
    return readings[0] if readings else None


def _parse_num_all(raw: str) -> List[float]:
    """ALL plausible readings of a numeric token.

    "4296.14" → [4296.14]; "4,296" → [4296.0, 4.296] (en thousands vs id
    decimal); "4.296" → [4.296, 4296.0]. Callers match ANY reading against
    evidence so a locale-slip never produces a false conflict.
    """
    s = raw.strip().replace(" ", "").replace("%", "")
    if not s:
        return []
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    candidates: List[str] = []
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):      # 4.296,14 → id
            candidates.append(s.replace(".", "").replace(",", "."))
        else:                                 # 4,296.14 → en
            candidates.append(s.replace(",", ""))
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) != 3:
            candidates.append(s.replace(",", "."))       # 42,7 → 42.7
        else:
            candidates.append(s.replace(",", ""))         # 4,296 → 4296
            if len(parts) == 2 and len(parts[1]) <= 4:
                candidates.append(parts[0] + "." + parts[1])  # or 4.296
    elif "." in s:
        parts = s.split(".")
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
            candidates.append(s)                    # 4.296 → 4.296
            candidates.append(s.replace(".", ""))   # or 4296 — ambiguous
        else:
            candidates.append(s)
    else:
        candidates.append(s)
    out: List[float] = []
    for cand in candidates:
        try:
            val = float(cand)
            if val not in out:
                out.append(-val if neg else val)
        except ValueError:
            continue
    return out


def _num_variants(values: List[float]) -> List[float]:
    return values


@dataclass
class EvidenceRecord:
    key: str
    value: float
    percent: bool
    tool: str


class EvidenceLedger:
    """Everything the run's tools actually returned, numerically."""

    def __init__(self) -> None:
        self.records: List[EvidenceRecord] = []

    # ── ingest ───────────────────────────────────────────────────────────
    def ingest_tool_content(self, tool_name: str, content: Any) -> None:
        try:
            self._ingest_value(tool_name, "", content)
        except Exception as exc:  # noqa: BLE001 — evidence ingest must never crash the run
            logger.debug("evidence ingest issue (%s): %s", tool_name, exc)

    def _ingest_value(self, tool: str, key: str, value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                self._ingest_value(tool, f"{key}.{k}" if key else str(k), v)
        elif isinstance(value, list):
            for i, v in enumerate(value[:50]):
                self._ingest_value(tool, key, v)
        elif isinstance(value, bool) or value is None:
            return
        elif isinstance(value, (int, float)):
            self.records.append(EvidenceRecord(
                key=key or "value",
                value=float(value),
                percent=bool(_PCT_KEY_RE.search(key)),
                tool=tool,
            ))
        elif isinstance(value, str):
            # Plain-text tool content: harvest figure-shaped numbers so the
            # agent quoting its own tool output verbatim passes the gate.
            if len(value) > 20_000:
                value = value[:20_000]
            if _DATE_RE.search(value) and value.count("\n") > 3 and not _NUM_TOKEN_RE.search(value):
                return
            for m in _NUM_TOKEN_RE.finditer(value):
                if m.group("pct"):
                    val = _parse_num(m.group("num"))
                    if val is not None:
                        self.records.append(EvidenceRecord(key=f"{key or 'text'}%", value=val, percent=True, tool=tool))
                    continue
                start, end = m.span("num")
                before = value[max(0, start - 1):start]
                after = value[end:end + 1]
                if re.match(r"[\d.,]", before or "") or re.match(r"[\d.,]", after or ""):
                    continue  # part of a larger token
                val = _parse_num(m.group("num"))
                if val is not None and not _INT_RE.match(m.group("num").strip()):
                    self.records.append(EvidenceRecord(key=key or "text", value=val, percent=False, tool=tool))

    # ── queries ──────────────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self.records)

    def nearest(self, value: float, limit: int = 3) -> List[EvidenceRecord]:
        return sorted(
            self.records,
            key=lambda r: abs(r.value - value),
        )[:limit]

    def matches(self, value: float) -> bool:
        tol = max(_TOLERANCE_REL * abs(value), _TOLERANCE_ABS_FLOOR)
        return any(abs(r.value - value) <= tol for r in self.records)

    @property
    def price_band(self) -> Optional[Tuple[float, float]]:
        candidates = [
            r.value for r in self.records
            if not r.percent and abs(r.value) >= 1.0
        ]
        if not candidates:
            return None
        return min(candidates), max(candidates)


# ── claims & figures block ───────────────────────────────────────────────────

@dataclass
class Declaration:
    value: float
    percent: bool
    role: str
    note: str
    ref: str


@dataclass
class Claim:
    values: List[float]          # all locale readings; values[0] is primary
    percent: bool
    span: Tuple[int, int]
    raw: str
    declaration: Optional[Declaration] = None

    @property
    def value(self) -> float:
        return self.values[0]


@dataclass
class Issue:
    code: str
    value: float
    role: str
    message: str
    span: Tuple[int, int]


@dataclass
class ValidationResult:
    valid: bool
    issues: List[Issue] = field(default_factory=list)
    released_text: str = ""
    figures_present: bool = False
    claim_count: int = 0


def parse_figures_block(text: str) -> Tuple[List[Declaration], str, bool]:
    """Extract (and strip) the ```figures``` block. Returns (decls, clean_text, present)."""
    m = _FIGURES_BLOCK_RE.search(text)
    if not m:
        return [], text, False
    decls: List[Declaration] = []
    for line in m.group("body").splitlines():
        line = line.strip()
        if not line or line.startswith("|") or set(line) <= {"|", "-", " "}:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        value = _parse_num(parts[0])
        if value is None:
            continue
        role = parts[1].lower() if len(parts) > 1 else "observed"
        if role not in _ROLES:
            role = "observed"
        note = parts[2] if len(parts) > 2 else ""
        ref = parts[3] if len(parts) > 3 else ""
        decls.append(Declaration(
            value=value,
            percent="%" in parts[0],
            role=role,
            note=note,
            ref=ref,
        ))
    clean = (text[:m.start()] + text[m.end():]).strip()
    return decls, clean, True


def extract_claims(text: str, decls: List[Declaration]) -> List[Claim]:
    """Figure-shaped numbers in prose: decimals, percents, and currency-formatted
    thousands. Plain integers, dates and times are exempt (counts)."""
    claims: List[Claim] = []
    # declared values consumed by (value, percent) — declarations cover their
    # own occurrences; prose restatements still checked via evidence matching.
    for m in _NUM_TOKEN_RE.finditer(text):
        raw = m.group("num")
        if _INT_RE.match(raw.strip()):
            continue  # plain integer = count/date/year — never gated
        start, end = m.span()
        before = text[start - 1:start]
        if re.match(r"[\d.,%]", before or ""):
            continue
        # pure-integer runs (dates, times, years, counts) are already exempt;
        # decimals and percents are the gated shapes.
        value = _parse_num(raw)
        if value is None:
            continue
        percent = bool(m.group("pct"))
        readings = _parse_num_all(raw)
        decl = _attach_declaration(readings, percent, decls)
        claims.append(Claim(values=readings, percent=percent, span=(start, end), raw=raw, declaration=decl))
    return claims


def _attach_declaration(values: List[float], percent: bool, decls: List[Declaration]) -> Optional[Declaration]:
    for d in decls:
        for value in values:
            tol = max(_TOLERANCE_REL * abs(value), _TOLERANCE_ABS_FLOOR)
            if abs(d.value - value) <= tol and d.percent == percent:
                return d
    return None


# ── role checks ──────────────────────────────────────────────────────────────

def _eval_formula(note: str) -> Optional[float]:
    """Safely evaluate pure arithmetic (+ - * / ( )) found in a note string."""
    if not note:
        return None
    normalized = (
        note.replace("×", "*").replace("÷", "/").replace("−", "-")
        .replace(",", "").replace("%", "")
    )
    expr_match = re.search(r"[-+*/().\d\s]{3,}", normalized)
    if not expr_match:
        return None
    expr = expr_match.group(0).strip()
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = _eval(node.left), _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                raise ValueError("division by zero")
            return left / right
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return _eval(node.operand) * (1 if isinstance(node.op, ast.UAdd) else -1)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError("unsupported node")

    try:
        return _eval(tree)
    except Exception:  # noqa: BLE001
        return None


def _additive_terms(tree: ast.AST) -> List[float]:
    """Numeric constants used as ADDITIVE terms (added/subtracted) in a formula.

    Multiplicative factors (x1.02, /2) are exempt — a rounding buffer is not an
    unobserved data point; a term ADDED to an observed value is.
    """
    out: List[float] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            for child in (node.left, node.right):
                if isinstance(child, ast.Constant) and isinstance(child.value, (int, float)):
                    out.append(float(child.value))
    return out


def _check_claim(claim: Claim, ledger: EvidenceLedger) -> Optional[Issue]:
    d = claim.declaration
    role = d.role if d else "observed"
    value = claim.value

    def _any_reading(fn) -> bool:
        return any(fn(v) for v in claim.values)

    def issue(code: str, message: str) -> Issue:
        return Issue(code=code, value=value, role=role, message=message, span=claim.span)

    if role == "count":
        return None
    if role == "cited":
        if d and (d.note or d.ref) and _SOURCE_HINT_RE.search(d.note + " " + d.ref):
            return None
        return issue("citation_without_source",
                     "a cited figure needs a visible source (in the note and its sentence)")
    if role == "derived":
        if not d or not d.note:
            return issue("no_formula", "a derived figure needs the formula in its note")
        result = _eval_formula(d.note)
        if result is None:
            return issue("formula_not_evaluable",
                         "the note must contain plain arithmetic over observed values, e.g. (1.053 - 0.666) / 1.053")
        # every ADDITIVE term in the formula must exist in evidence
        try:
            tree = ast.parse(
                re.sub(r"[^\d+\-*/().\s]", "", d.note.replace("×", "*").replace("÷", "/").replace("−", "-").replace(",", "")),
                mode="eval",
            )
            unanchored = [t for t in _additive_terms(tree) if not ledger.matches(t)]
        except (SyntaxError, ValueError):
            unanchored = []
        if unanchored:
            return issue("formula_not_anchored",
                         f"terms added or subtracted in the formula must be observed values; unanchored: {unanchored[:3]}")
        tol = max(_TOLERANCE_REL * abs(value), _TOLERANCE_ABS_FLOOR, _TOLERANCE_REL * abs(result))
        if not _any_reading(lambda v: abs(result - v) <= tol):
            return issue("derivation_result_mismatch",
                         f"formula evaluates to {result:g}, prose says {value:g}")
        return None
    if role == "proposed":
        if _any_reading(ledger.matches):
            return None
        if d and _eval_formula(d.note) is not None:
            return None
        band = ledger.price_band
        if band and _any_reading(lambda v: band[0] * 0.98 <= v <= band[1] * 1.02):
            return None
        return issue("proposed_outside_observed",
                     f"proposed level is outside the observed range {band[0]:g}–{band[1]:g}" if band
                     else "no observed prices to anchor the proposed level")
    # observed (and undeclared claims treated as observed)
    if _any_reading(ledger.matches):
        return None
    near = ledger.nearest(value)
    near_txt = ", ".join(f"{r.value:g} ({r.key.strip('.')} via {r.tool})" for r in near)
    if len(ledger) == 0:
        return issue("numeric_claim_unavailable",
                     "this run retrieved no numeric tool data — fetch the data or remove the figure")
    return issue(
        "numeric_claim_conflict" if _plausible_price_like(claim, ledger) else "figure_unverified",
        f"no tool result in this session matches {value:g}; nearest observed: {near_txt}",
    )


def _plausible_price_like(claim: Claim, ledger: EvidenceLedger) -> bool:
    band = ledger.price_band
    if not band:
        return False
    lo, hi = band
    return any(lo * 0.5 <= v <= hi * 2.0 for v in claim.values)


# ── the gate ─────────────────────────────────────────────────────────────────

class GroundingGate:
    def __init__(self, ledger: EvidenceLedger):
        self.ledger = ledger

    def validate(self, text: str) -> ValidationResult:
        try:
            return self._validate(text)
        except Exception as exc:  # noqa: BLE001 — fail OPEN: never lose the answer
            logger.exception("grounding gate error (fail-open): %s", exc)
            return ValidationResult(valid=True, released_text=text)

    def _validate(self, text: str) -> ValidationResult:
        decls, clean_text, present = parse_figures_block(text)
        claims = extract_claims(clean_text, decls)
        issues: List[Issue] = []
        for claim in claims:
            if claim.declaration is None and present:
                # the model showed intent to declare but missed this one —
                # still check it as an observation; flag separately if unbacked.
                res = _check_claim(claim, self.ledger)
                if res is not None:
                    res.code = "figure_undeclared_" + res.code
                    issues.append(res)
                continue
            res = _check_claim(claim, self.ledger)
            if res is not None:
                issues.append(res)
        return ValidationResult(
            valid=not issues,
            issues=issues,
            released_text=clean_text,
            figures_present=present,
            claim_count=len(claims),
        )

    def correction_prompt(self, result: ValidationResult) -> str:
        lines = [
            "[GROUNDING GATE] Your summary was rejected because some numbers are not "
            "backed by this session's tool results. The draft was NOT delivered to the user.",
            "",
            "Flagged figures (value | role | problem):",
        ]
        for i in result.issues[:20]:
            lines.append(f"  - {i.value:g} | {i.role} | {i.message}")
        observed = self.ledger.price_band
        if observed:
            lines.append(f"Observed price range this run: {observed[0]:g} – {observed[1]:g}.")
        lines += [
            "",
            "Fix EVERY flagged figure in one of exactly three ways:",
            "  (1) DECLARE it with the role it really has, in a ```figures``` block at the end: "
            "value | role | note | ref  (roles: observed / derived / proposed / cited / count; "
            "derived requires the arithmetic in the note; cited requires the source)",
            "  (2) REWRITE it to a value this session's tools actually returned;",
            "  (3) REMOVE it from the answer.",
            "Restating a rejected value in another format counts as none of these and fails again.",
            "Re-emit the complete summary with the fixes applied.",
        ]
        return "\n".join(lines)

    def redact(self, text: str, result: ValidationResult) -> str:
        """Release the summary with unverifiable figures cut, plus a footnote."""
        released = text
        spans = sorted(
            [(i.span, f"{i.value:g}") for i in result.issues
             if i.code.split("_")[-1] in ("conflict", "unavailable", "unverified", "observed", "outside")],
            key=lambda x: x[0][0],
            reverse=True,
        )
        # Deduplicate overlapping spans
        last_start = None
        for (start, end), _val in spans:
            if last_start is not None and end > last_start:
                continue
            released = released[:start] + "…" + released[end:]
            last_start = start
        removed = len(result.issues)
        band = self.ledger.price_band
        footnote = (
            f"\n\n※ {removed} angka tidak dapat diverifikasi terhadap data sesi ini sehingga dihapus. "
            + (f"Rentang harga teramati: {band[0]:g}–{band[1]:g}. " if band else "")
            + "Minta saya hitung ulang level tersebut dari data live bila diperlukan."
        )
        return released.strip() + footnote
