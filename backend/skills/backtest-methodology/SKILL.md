---
name: backtest-methodology
description: How to interpret and stress-test run_backtest results honestly — robustness checks, regime dependence, overfitting traps, and what to always report.
category: analysis
---
# Backtest Interpretation Methodology

You ran `run_backtest` and got numbers. Before presenting them, apply these checks — a backtest is only as good as the honesty of its reading.

## Always report, in one compact table
- `total_return_pct`, `sharpe`, `max_drawdown_pct`, `trade_count`, `win_rate_pct`
- The buy & hold benchmark from the same run — a strategy that loses to buy & hold is not automatically wrong (risk-adjusted may still win), but the user must see it.
- The exact candle range (`range_utc`) and source. An undated backtest is an unusable backtest.

## Robustness checks (run mentally, mention the ones that matter)
1. **Top-trade dependency**: if `trade_count` is small, look at the trade list. If removing the best 1–2 trades flips the result from profit to loss, say so — the edge is one lucky swing, not a system.
2. **Sample size**: under ~30 trades, win-rate percentages are noise. Report but flag them.
3. **Regime dependence**: state which regime the candle window covers (trend up / down / range). A trend strategy backtested on a trending window tells you almost nothing about its range behavior. Prefer running the same spec on a second, opposite-regime window (one extra tool call, same params) before making claims.
4. **Fees realism**: default fee 0.1% + slippage 0.05% per side is fine for crypto takers; for high-frequency specs (hundreds of trades) on lower timeframes, costs dominate — say when the result is fee-driven.
5. **Next-bar-open honesty**: fills happen at next bar open by design; there is no look-ahead. Do not "improve" results by assuming better fills.

## Overfitting traps (name them when you see them)
- Trying many parameter sets in one call and presenting only the best = curve fitting. If you screen N specs, disclose that you screened and that the others performed worse.
- Parameters tuned to the exact window (e.g. Donchian 20 on a window where the single big move lasted 20 bars) are brittle — widen the parameter ±50% and check the sign of the edge flips or not when it matters.
- Short windows (< 200 candles) with daily+ timeframes: treat every metric as indicative only.

## Presentation rules
- Historical performance never proves future results — say it once, concretely, not as a disclaimer wall.
- Lead with the decision-relevant fact (does this idea have an edge at all?), not with the metric dump.
- If the run failed or returned no trades, say exactly that — never narrate an imagined equity curve.
