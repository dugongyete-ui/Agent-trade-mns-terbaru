---
name: TradingView MCP tool audit
description: Results of full audit of all 29 tools in _TRADINGVIEW_ALLOWED; bugs found and fixed; known limitations.
---

## Rule
All 29 tools in `_TRADINGVIEW_ALLOWED` (backend/app/domain/services/tools/mcp.py) match actual function names in the `tradingview_mcp` package. Tool signatures use separate `symbol` + `exchange` params (not `BINANCE:BTCUSDT` combined form), though `multi_timeframe_analysis` also accepts the `EXCHANGE:SYMBOL` combined form.

## Bugs fixed
1. **`get_live_price` TypeError** — `live_market_service.py` called `get_price(symbol=symbol, exchange=exchange)` but `get_price()` only accepts `symbol`. Fixed by removing `exchange=exchange` kwarg. File: `.pythonlibs/lib/python3.12/site-packages/tradingview_mcp/core/services/live_market_service.py` line 92.

2. **`recognize_market_pattern`** requires `recent_candles: list` and `indicators: dict` as mandatory positional args. Agent cannot easily call this without raw OHLCV data. Not a blocker but complex to invoke.

## Known limitation: Yahoo Finance blocked in Replit
`get_live_price`, `get_multi_price`, `yahoo_price`, `market_snapshot` all call Yahoo Finance internally. Yahoo Finance HTTP requests return empty/None data in the Replit environment (network restriction). Tools return graceful error dicts — no crash. `get_global_market_overview` uses a different path and works.

**Why:** Yahoo Finance is blocked by Replit network policy. Not fixable from code.

## Working tools verified live
coin_analysis, multi_timeframe_analysis, combined_analysis, bollinger_scan, rating_filter, volume_confirmation_analysis, advanced_candle_pattern, consecutive_candles_scan, market_sentiment, financial_news, backtest_strategy, get_global_market_overview, market_snapshot (partial).
