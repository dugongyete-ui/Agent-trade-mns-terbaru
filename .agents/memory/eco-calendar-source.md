---
name: Economic Calendar MCP data source
description: Why TradingView is used instead of ForexFactory for the economic calendar MCP server.
---

# Economic Calendar — Data Source Decision

**Rule:** Always use TradingView Economic Calendar API, never ForexFactory JSON.

**Why:** ForexFactory (`nfs.faireconomy.media/ff_calendar_thisweek.json`) rate-limits at 429 after even 2-3 rapid successive requests from the same IP. This breaks testing and multi-user production scenarios. TradingView's calendar API (`economic-calendar.tradingview.com/events`) returns 370–538 events for a 3-week window reliably with no rate limiting observed.

**How to apply:** The MCP server at `mcp-servers/economic-calendar/server.py` uses TV API. If you ever need to change the data source, do NOT revert to ForexFactory without adding a server-level Redis/disk cache (not just in-process memory) shared across all processes.

**Cache:** The server uses dual-layer cache — memory (same process, instant) + disk at `/tmp/ecocal_cache.json` (survives process restarts). TTL = 60 minutes for both.

**TV API endpoint:**
```
GET https://economic-calendar.tradingview.com/events
  ?from=2026-06-15T00:00:00.000Z
  &to=2026-07-06T23:59:59.000Z
  &countries=US,EU,GB,JP,AU,NZ,CA,CH
Headers: Origin: https://www.tradingview.com
```
