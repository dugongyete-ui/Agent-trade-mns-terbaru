---
name: Backtest strategy valid names
description: Valid strategy name strings for backtest_strategy MCP tool.
---

## Rule
`backtest_strategy` tool in tradingview_mcp only accepts these strategy names (lowercase):
- `rsi`
- `bollinger`
- `macd`
- `ema_cross`
- `supertrend`
- `donchian`

Any other name returns an error: `Unknown strategy '...'. Choose: rsi, bollinger, macd, ema_cross, supertrend, donchian`.

Added to system prompt (`backend/app/domain/services/prompts/system.py`) in the `backtest_strategy` tool description so the agent knows valid names without trial-and-error.

**Why:** The tool validates the strategy param strictly — no fuzzy matching. Agent passing `macd_rsi` or other variants will get an error.
