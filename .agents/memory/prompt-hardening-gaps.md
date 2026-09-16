---
name: Prompt Hardening — 9 Behavioral Gaps
description: Nine behavioral improvements added to execution.py and system.py to make the AI more human-like, thorough, and consistent.
---

## What was added and where

### execution.py — EXECUTION_SYSTEM_PROMPT

**Gap #1 — Calibrated notification depth**
Replaced "keep to ONE sentence" with tiered rule:
- Routine result → 1 sentence
- Significant/unexpected → 2-3 sentences
- Contradictory signal → always explain (what, why, how thinking about it)

**Gap #2 — Devil's advocate (mandatory before any BUY/SELL)**
Agent must name the single strongest counter-argument to the trade and explain why it is proceeding despite it. If it cannot, analysis is insufficient.

**Gap #3 + #9 — Pre-signal mandatory checks (session + calendar)**
Before finalizing BUY or SELL, two checks must be complete (from any step):
1. Session quality — which session is active, is liquidity adequate
2. Economic calendar — any high-impact news in next 4 hours for instruments involved

### execution.py — EXECUTION_PROMPT

**Gap #5 — Cross-step memory reference**
Agent explicitly told to reference earlier step findings when relevant. Treats each step as part of a continuous analysis, not isolated work.

### execution.py — SUMMARIZE_STREAM_PROMPT and SUMMARIZE_PROMPT (both)

**Gap #4 — Coherence reconciliation (internal check before writing)**
Before writing the final output, agent internally checks: do all step findings tell a consistent story? Contradictions must be resolved in the output, not ignored.

**Gap #6 — Conviction level (mandatory in every trading output)**
Every BUY/SELL must include: HIGH / MEDIUM / LOW with specific reason.
- HIGH = multiple independent signals (structure, momentum, volatility, session, levels) align, no material contradiction
- MEDIUM = most agree, one or two ambiguous
- LOW = setup exists but real uncertainty remains

**Gap #8 — Invalidation conditions (mandatory in every trading output)**
Every BUY/SELL must name 1-2 specific, observable conditions that signal the setup has failed. Not vague language — a price level, candle close pattern, or event.

### system.py — tool_routing

**Gap #7 — Sentiment tools mandatory for crypto**
Added explicit rule: for any crypto analysis (BTC, ETH, SOL, etc.), these three tools are NOT optional:
- `sentiment-ls-ratio`
- `sentiment-open-interest`
- `sentiment-fear-greed`

### system.py — decision_format

**Gap #6 + #8 mirrored here**
`decision_format` updated to require conviction level and invalidation conditions in the same terms as SUMMARIZE prompts, for consistency when agent delivers decisions outside the step loop.

## No-hardcode compliance
All 9 additions are behavioral guidance (HOW to think, not WHAT to say). None prescribe market content, specific price values, fixed ATR multipliers, or copy-paste responses. Verified clean against `.agents/skills/no-hardcode/SKILL.md`.

**Why:**
Before these additions, the agent could deliver a BUY signal with no session check, no calendar check, no conviction rating, no invalidation condition, and no confrontation of the opposing case. The analysis was technically correct but incomplete in ways that matter for real trading decisions.
