---
name: Sentiment MCP symbol normalizer
description: _normalize_binance_symbol in mcp-servers/sentiment/server.py — bugs fixed, symbols added.
---

## Rule
`_normalize_binance_symbol()` in `mcp-servers/sentiment/server.py` normalizes any symbol variant to Binance Futures format. It strips exchange prefixes (e.g. `BINANCE:BTCUSDT` → `BTCUSDT`) via hardcoded checks then a generic `:` split fallback.

## Bugs fixed
1. **Duplicate SOLUSDT check** — `if "SOLUSDT" in s or "SOLUSDT" in s:` was a typo (same condition twice). Fixed to `if "SOLUSDT" in s:`.
2. **MATICUSDT deprecated** — Binance Futures renamed MATIC to POL. `MATICUSDT` returns `[]` from the L/S API. Added: `if "POLUSDT" in s or "MATICUSDT" in s: return "POLUSDT"`.
3. **Missing symbols** — Added explicit handling for `AVAXUSDT` and `DOTUSDT` (both confirmed active on Binance Futures L/S API).

## Current hardcoded symbols (as of fix)
BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, LINKUSDT, AVAXUSDT, DOTUSDT, POLUSDT (also catches MATICUSDT).

**Why:** Binance Futures L/S ratio API only supports specific pairs; the normalizer ensures agent-supplied symbols (in any format) map to valid Binance Futures tickers.
