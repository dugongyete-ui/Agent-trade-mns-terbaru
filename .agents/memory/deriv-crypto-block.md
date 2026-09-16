---
name: Deriv MCP Crypto Block
description: Two-layer enforcement preventing crypto/exchange symbols from reaching Deriv WebSocket API.
---

## Rule
Deriv MCP has two independent layers blocking crypto and exchange-format symbols:

**Layer 1 — system.py routing rule (prompt-level):**
```
DERIV MCP → ONLY for Forex/Gold (frxEURUSD, frxXAUUSD, etc.)
DO NOT use Deriv MCP for BTC, ETH, or any crypto exchange pair
```

**Layer 2 — server.py call_tool() guard (code-level):**
`_is_blocked_symbol()` runs before any WebSocket call. Blocks:
- Symbols starting with `cry` or `crypto` (e.g. `cryBTCUSD` — Deriv's own synthetic crypto)
- Exchange-format keywords: `BTCUSDT`, `ETHUSDT`, `BINANCE:`, `NASDAQ:`, `NYSE:`, `SP:`, `KUCOIN:`, etc.
- Returns actionable error message directing the agent to TradingView MCP instead.

Also applies to `symbol_a`, `symbol_b` (correlation tool) and list items in `symbols` (multi-symbol calls).

**Why:**
The LLM occasionally ignores routing rules in the prompt. Without the server guard, it would call `deriv-rsi` with `BTCUSDT` — the Deriv WebSocket would return a symbol-not-found error with no useful message, and the agent would not know why or where to retry. With the guard, the server returns an explicit redirect. Before this fix, `cryBTCUSD` was also in the default symbol list, causing confusing failures.

**How to apply:**
- Adding a new Deriv tool: check whether its symbol argument is covered by `_SYMBOL_ARGS` in `call_tool()`. If the new tool uses a different arg name for symbols, add it to that tuple.
- Expanding the blocked list: add to `_BLOCKED_EXCHANGE_KEYWORDS` in server.py. Do NOT rely solely on the prompt — the code guard is the real enforcement.
