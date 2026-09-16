---
name: Economic Calendar importance scale
description: TradingView calendar importance values and how they map to agent prompt parameters.
---

# TradingView Calendar — Importance Scale

**Rule:** TradingView importance is numeric: -1, 0, 1. There is no "Medium" distinct tier.

| TV value | Label | Icon | Examples |
|---|---|---|---|
| 1 | High | 🔴 | FOMC, BOJ, BOE, RBA, CPI, NFP, GDP |
| 0 | Low | 🟢 | Building permits, retail sales (minor), jobless claims |
| -1 | N/A | ⚪ | Holidays, some minor data |

**min_impact mapping in `_filter()` (server.py):**
- `"High"` → importance ≥ 1 (only 🔴 events)
- `"Medium"` → importance ≥ 0 (🟢 + 🔴, i.e. same as ForexFactory Medium+High)
- `"Low"` → importance ≥ -1 (all events)

**How to apply:** Agent prompts use `min_impact="High"` for Phase 0 scan — this correctly returns only the market-moving events (central bank decisions, CPI, NFP, GDP). No changes needed to prompt files when this scale is understood.
