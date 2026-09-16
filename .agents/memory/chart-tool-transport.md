---
name: Dynamic chart tool transport
description: Chart metadata travels separately from readable MCP tool output so the agent context stays prose-only.
---

The chart is derived from the exact candle/indicator tool call chosen by the agent. MCP results may carry a private structured chart payload alongside readable text; the LLM receives only the readable text, while the UI renders the structured payload in the existing tool panel.

**Why:** Parsing prices and indicator values back out of formatted prose is fragile and can show a chart that does not match the tool result.

**How to apply:** Add new chartable indicators by emitting aligned timestamp/value points from the tool that already fetched the candles; do not introduce a UI default timeframe or a second data-fetch path.