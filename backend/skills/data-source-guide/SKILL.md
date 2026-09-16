---
name: data-source-guide
description: Which candle/price source each symbol type uses (Binance vs Deriv), timeframe support, known depth limits, and volume caveats.
category: data-source
---
# Data Source Guide

`technical_indicators` and `run_backtest` resolve the data source automatically and report it back as `source`. Know the routing so you can pick valid symbols and timeframes on the first try.

## Routing
- **Binance** — anything ending in a crypto quote: BTCUSDT, ETHUSDT, SOLUSDT, EURUSDT... Full OHLCV with real volume.
- **Deriv** — forex/metals/synthetics: XAUUSD (gold), XAGUSD (silver), EURUSD, GBPUSD, USDJPY (auto-prefixed to frx...), R_100 / R_75 / BO_... synthetics, or explicit frx... / R_ / 1HZ... codes.

## Timeframes
- Binance: 1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w 1M.
- Deriv: 1m–1d native; 3d / 1w / 1M are aggregated from daily candles (works, but the last aggregated candle is partial).

## Known limits & caveats
- **No volume from Deriv**: VWAP on forex/gold degrades to an unweighted typical-price mean and is flagged `volume_weighted: false` in the response — always pass that caveat on, or avoid VWAP there and use session range + ATR instead.
- **Deriv intraday history depth**: M5/M15 windows reach back a few days, not months. For deep history on gold/forex use 1h or 1d candles; for backtests on M5 keep expectations modest (a few hundred candles).
- **Crypto = 24/7**, forex/gold follow market sessions; if the forex session gate stopped the run, do not retry live-price tools — say the market is closed.
- `source` in the response is authoritative: cite it ("data via Deriv" / "Binance") when presenting numbers.
