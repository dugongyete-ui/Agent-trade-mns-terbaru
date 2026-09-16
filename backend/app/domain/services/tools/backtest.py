"""Strategy backtest toolkit — vectorized event simulation on real OHLCV candles.

Ported concept from HKUDS/Vibe-Trading: research-grade backtest metrics
(Sharpe, Sortino, max drawdown, profit factor, exposure, buy & hold benchmark)
computed locally so the agent can validate any thesis with real numbers.

Design goal (same philosophy as TechnicalToolkit): the AGENT decides which
strategy family fits the current market regime and picks every parameter.
The tool exposes raw simulation capability with documented parameters and
never prescribes when or how to use it.

Execution model: signals are evaluated on bar close, positions are taken at
the NEXT bar open (no look-ahead bias), with fees + slippage on each side.
Long/flat only (spot style) — shorting is intentionally not supported.
"""

from typing import Any, Dict, List, Literal, Optional

import numpy as np
import pandas as pd
from langchain.tools import tool
from pydantic import BaseModel, Field

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit
from app.domain.services.tools.market_data import fetch_ohlcv
from app.domain.services.tools.technical import _VALID_INTERVALS, _ema, _rma


# Bars per year for annualization (crypto trades 24/7)
_BARS_PER_YEAR = {
    "1m": 525_600, "3m": 175_200, "5m": 105_120, "15m": 35_040,
    "30m": 17_520, "1h": 8_760, "2h": 4_380, "4h": 2_190,
    "6h": 1_460, "8h": 1_095, "12h": 730, "1d": 365,
    "3d": 121.67, "1w": 52.14, "1M": 12,
}

_MAX_CANDLES = 1000


class StrategySpec(BaseModel):
    """One strategy to backtest, with its own parameters.

    All parameters beyond `type` are optional — the agent picks whatever
    fits the asset, timeframe and market regime it is analyzing.
    """

    type: Literal[
        "sma_cross", "ema_cross", "rsi_reversion", "bollinger",
        "donchian", "macd", "supertrend", "momentum",
    ] = Field(
        description=(
            "Strategy family: "
            "'sma_cross' = long while fast SMA above slow SMA (trend following); "
            "'ema_cross' = same with EMAs, reacts faster; "
            "'rsi_reversion' = long after RSI dips below oversold, exit above overbought (mean reversion); "
            "'bollinger' = band system, choose breakout or mean_reversion mode; "
            "'donchian' = long on N-bar high breakout, exit on M-bar low breakdown (channel breakout); "
            "'macd' = long while MACD line above its signal line (momentum); "
            "'supertrend' = long while price above ATR-based supertrend line (trend following); "
            "'momentum' = long while N-bar rate of change is above a threshold (momentum)."
        )
    )
    fast: Optional[int] = Field(default=None, description="sma_cross/ema_cross/macd: fast period (default 10; macd 12).")
    slow: Optional[int] = Field(default=None, description="sma_cross/ema_cross/macd: slow period (default 30; macd 26).")
    signal: Optional[int] = Field(default=None, description="macd only: signal line period (default 9).")
    period: Optional[int] = Field(default=None, description="rsi_reversion/bollinger/supertrend/momentum: main period (default 14 / 20 / 10 / 20).")
    oversold: Optional[float] = Field(default=None, description="rsi_reversion: entry threshold (default 30).")
    overbought: Optional[float] = Field(default=None, description="rsi_reversion: exit threshold (default 70).")
    std: Optional[float] = Field(default=None, description="bollinger: standard deviation multiplier (default 2.0).")
    mode: Optional[Literal["breakout", "mean_reversion"]] = Field(
        default=None,
        description="bollinger only: 'breakout' = long when close crosses above upper band; "
                    "'mean_reversion' = long below lower band, exit at middle band. Default breakout.",
    )
    entry_lookback: Optional[int] = Field(default=None, description="donchian: bars for entry high (default 20).")
    exit_lookback: Optional[int] = Field(default=None, description="donchian: bars for exit low (default 10).")
    multiplier: Optional[float] = Field(default=None, description="supertrend: ATR multiplier (default 3.0).")
    threshold: Optional[float] = Field(default=None, description="momentum: minimum rate of change to stay long, as decimal (default 0.0).")


def _signal_series(df: pd.DataFrame, spec: StrategySpec) -> np.ndarray:
    """Return desired position (1/0) at each bar CLOSE (executed next open)."""
    close = df["close"]
    n = len(df)

    if spec.type in ("sma_cross", "ema_cross"):
        fast = int(spec.fast or 10)
        slow = int(spec.slow or 30)
        if fast >= slow:
            raise ValueError(f"fast ({fast}) must be < slow ({slow})")
        f = _ema(close, fast) if spec.type == "ema_cross" else close.rolling(fast).mean()
        s = _ema(close, slow) if spec.type == "ema_cross" else close.rolling(slow).mean()
        sig = (f > s).astype(float)
        sig[f.isna() | s.isna()] = 0.0
        return sig.to_numpy()

    if spec.type == "macd":
        fast = int(spec.fast or 12)
        slow = int(spec.slow or 26)
        sg = int(spec.signal or 9)
        macd_line = _ema(close, fast) - _ema(close, slow)
        signal_line = macd_line.ewm(span=sg, adjust=False, min_periods=sg).mean()
        sig = (macd_line > signal_line).astype(float)
        sig[signal_line.isna()] = 0.0
        return sig.to_numpy()

    if spec.type == "rsi_reversion":
        period = int(spec.period or 14)
        os_ = float(spec.oversold if spec.oversold is not None else 30.0)
        ob = float(spec.overbought if spec.overbought is not None else 70.0)
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)
        rsi = 100.0 - 100.0 / (1.0 + _rma(gain, period) / _rma(loss, period).replace(0.0, np.nan))
        out = np.zeros(n)
        state = 0.0
        for i in range(n):
            v = rsi.iloc[i]
            if not np.isnan(v):
                if state == 0.0 and v < os_:
                    state = 1.0
                elif state == 1.0 and v > ob:
                    state = 0.0
            out[i] = state
        return out

    if spec.type == "bollinger":
        period = int(spec.period or 20)
        mult = float(spec.std if spec.std is not None else 2.0)
        mode = spec.mode or "breakout"
        mid = close.rolling(period).mean()
        sd = close.rolling(period).std(ddof=0)
        upper = mid + mult * sd
        lower = mid - mult * sd
        out = np.zeros(n)
        state = 0.0
        for i in range(n):
            if np.isnan(mid.iloc[i]):
                out[i] = state
                continue
            c = float(close.iloc[i])
            if mode == "breakout":
                if state == 0.0 and c > float(upper.iloc[i]):
                    state = 1.0
                elif state == 1.0 and c < float(mid.iloc[i]):
                    state = 0.0
            else:  # mean_reversion
                if state == 0.0 and c < float(lower.iloc[i]):
                    state = 1.0
                elif state == 1.0 and c >= float(mid.iloc[i]):
                    state = 0.0
            out[i] = state
        return out

    if spec.type == "donchian":
        entry_lb = int(spec.entry_lookback or 20)
        exit_lb = int(spec.exit_lookback or 10)
        hh = df["high"].rolling(entry_lb).max().shift(1)
        ll = df["low"].rolling(exit_lb).min().shift(1)
        out = np.zeros(n)
        state = 0.0
        for i in range(n):
            if np.isnan(hh.iloc[i]) or np.isnan(ll.iloc[i]):
                out[i] = state
                continue
            c = float(close.iloc[i])
            if state == 0.0 and c > float(hh.iloc[i]):
                state = 1.0
            elif state == 1.0 and c < float(ll.iloc[i]):
                state = 0.0
            out[i] = state
        return out

    if spec.type == "supertrend":
        period = int(spec.period or 10)
        mult = float(spec.multiplier if spec.multiplier is not None else 3.0)
        prev_close = close.shift(1)
        tr = pd.concat(
            [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = _rma(tr, period)
        hl2 = (df["high"] + df["low"]) / 2.0
        ub = (hl2 + mult * atr).to_numpy()
        lb = (hl2 - mult * atr).to_numpy()
        c = close.to_numpy()
        trend = np.ones(n)          # 1 = uptrend (long), -1 = downtrend
        final_ub = ub.copy()
        final_lb = lb.copy()
        for i in range(1, n):
            if np.isnan(ub[i]) or np.isnan(lb[i]):
                trend[i] = trend[i - 1]
                continue
            final_ub[i] = ub[i] if (ub[i] < final_ub[i - 1] or c[i - 1] > final_ub[i - 1]) else final_ub[i - 1]
            final_lb[i] = lb[i] if (lb[i] > final_lb[i - 1] or c[i - 1] < final_lb[i - 1]) else final_lb[i - 1]
            if trend[i - 1] == 1.0:
                trend[i] = -1.0 if c[i] < final_lb[i] else 1.0
            else:
                trend[i] = 1.0 if c[i] > final_ub[i] else -1.0
        return (trend == 1.0).astype(float)

    if spec.type == "momentum":
        period = int(spec.period or 20)
        thr = float(spec.threshold if spec.threshold is not None else 0.0)
        roc = close / close.shift(period) - 1.0
        sig = (roc > thr).astype(float)
        sig[roc.isna()] = 0.0
        return sig.to_numpy()

    raise ValueError(f"Unknown strategy type: {spec.type}")


def _simulate(
    df: pd.DataFrame,
    desired: np.ndarray,
    initial_cash: float,
    fee_pct: float,
    slippage_pct: float,
) -> Dict[str, Any]:
    """Event simulation: next-bar-open execution, mark-to-market equity."""
    n = len(df)
    opens = df["open"].to_numpy()
    closes = df["close"].to_numpy()
    times = df["open_time"].to_numpy()

    cash = initial_cash
    qty = 0.0
    entry_px = 0.0
    entry_time = None
    trades: List[Dict[str, Any]] = []
    equity = np.zeros(n)
    in_pos = np.zeros(n, dtype=bool)

    for i in range(n):
        target = int(desired[i - 1]) if i > 0 else 0
        if target == 1 and qty == 0.0:
            px = opens[i] * (1.0 + slippage_pct)
            spend = cash / (1.0 + fee_pct)
            fee = spend * fee_pct
            qty = spend / px
            cash = 0.0
            entry_px = px
            entry_time = times[i]
        elif target == 0 and qty > 0.0:
            px = opens[i] * (1.0 - slippage_pct)
            gross = qty * px
            fee = gross * fee_pct
            cash = gross - fee
            trades.append({
                "entry_utc": str(pd.Timestamp(entry_time).isoformat()) if entry_time is not None else None,
                "entry_px": round(float(entry_px), 8),
                "exit_utc": str(pd.Timestamp(times[i]).isoformat()),
                "exit_px": round(float(px), 8),
                "pnl_pct": round((px - entry_px) / entry_px * 100.0, 3),
            })
            qty = 0.0
            entry_px = 0.0
            entry_time = None
        equity[i] = cash + qty * closes[i]
        in_pos[i] = qty > 0.0

    # Force-close an open position at the last close so metrics are complete
    if qty > 0.0:
        px = closes[-1] * (1.0 - slippage_pct)
        gross = qty * px
        cash = gross * (1.0 - fee_pct)
        trades.append({
            "entry_utc": str(pd.Timestamp(entry_time).isoformat()) if entry_time is not None else None,
            "entry_px": round(float(entry_px), 8),
            "exit_utc": str(pd.Timestamp(times[-1]).isoformat()),
            "exit_px": round(float(px), 8),
            "pnl_pct": round((px - entry_px) / entry_px * 100.0, 3),
            "closed_at_end": True,
        })
        qty = 0.0
        equity[-1] = cash

    return {
        "equity": equity,
        "in_pos": in_pos,
        "trades": trades,
        "final_equity": float(equity[-1]),
    }


def _metrics(
    sim: Dict[str, Any],
    df: pd.DataFrame,
    initial_cash: float,
    bars_per_year: float,
) -> Dict[str, Any]:
    eq = sim["equity"]
    n = len(eq)
    total_return = eq[-1] / initial_cash - 1.0

    # Annualized return from bar count
    years = n / bars_per_year
    annual = (max(eq[-1], 1e-12) / initial_cash) ** (1.0 / years) - 1.0 if years > 0 else 0.0

    # Per-bar returns of the equity curve
    rets = np.diff(eq) / eq[:-1]
    mu = float(rets.mean()) if n > 2 else 0.0
    sd = float(rets.std(ddof=1)) if n > 2 else 0.0
    sharpe = (mu / sd * np.sqrt(bars_per_year)) if sd > 0 else 0.0
    downside = rets[rets < 0]
    dsd = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = (mu / dsd * np.sqrt(bars_per_year)) if dsd > 0 else 0.0

    running_max = np.maximum.accumulate(eq)
    dd = eq / running_max - 1.0
    max_dd = float(dd.min())

    trades = sim["trades"]
    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    bh = float(df["close"].iloc[-1] / df["close"].iloc[0] - 1.0)

    return {
        "total_return_pct": round(total_return * 100.0, 2),
        "annualized_return_pct": round(annual * 100.0, 2) if np.isfinite(annual) else None,
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100.0, 1) if trades else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_trade_pct": round(float(np.mean(pnls)), 3) if pnls else None,
        "best_trade_pct": round(max(pnls), 2) if pnls else None,
        "worst_trade_pct": round(min(pnls), 2) if pnls else None,
        "exposure_pct": round(float(sim["in_pos"].mean()) * 100.0, 1),
        "buy_hold_return_pct": round(bh * 100.0, 2),
        "return_vs_buy_hold_pct": round((total_return - bh) * 100.0, 2),
        "final_equity": round(sim["final_equity"], 2),
    }


def _downsample_equity(
    times: np.ndarray,
    equity: np.ndarray,
    max_points: int = 120,
) -> List[Dict[str, Any]]:
    n = len(equity)
    if n <= max_points:
        idx = list(range(n))
    else:
        step = n / max_points
        idx = sorted({int(i * step) for i in range(max_points)} | {n - 1})
    return [
        {"t": int(pd.Timestamp(times[i]).value // 1_000_000), "e": round(float(equity[i]), 2)}
        for i in idx
    ]


def _describe(spec: StrategySpec) -> Dict[str, Any]:
    """Echo the agent's chosen parameters back for transparency."""
    params: Dict[str, Any] = {"type": spec.type}
    for field in ("fast", "slow", "signal", "period", "oversold", "overbought",
                  "std", "mode", "entry_lookback", "exit_lookback", "multiplier", "threshold"):
        val = getattr(spec, field)
        if val is not None:
            params[field] = val
    return params


class BacktestToolkit(BaseToolkit):
    """Event-driven backtest simulator on exchange OHLCV data.

    Pure capability: the agent picks the strategy family, every parameter,
    the asset and the timeframe. No strategy recommendation is embedded.
    """

    name: str = "backtest"

    @tool(parse_docstring=True)
    async def run_backtest(
        self,
        symbol: str,
        timeframe: str,
        strategies: List[StrategySpec],
        limit: int = 500,
        initial_cash: float = 10_000.0,
        fee_pct: float = 0.1,
        slippage_pct: float = 0.05,
    ) -> ToolResult:
        """Backtest trading strategies on real historical OHLCV candles and get research-grade metrics.

        Works for Binance crypto pairs (BTCUSDT, ...) AND Deriv markets
        (XAUUSD gold, frxEURUSD and other forex pairs, R_100 synthetics —
        pass the plain name, the source is resolved automatically and reported
        back as `source`). Simulates LONG/FLAT execution: signals evaluated on
        bar close, filled at next bar open, with fees and slippage. Returns per
        strategy: total/annualized return, Sharpe, Sortino, max drawdown, win
        rate, profit factor, trade list, exposure and a buy & hold benchmark,
        plus a downsampled equity curve for charting. You choose which strategy
        families and parameters to test — pass several specs in ONE call to
        compare them on the exact same candles.

        Args:
            symbol: Symbol, e.g. "BTCUSDT", "ETHUSDT", "XAUUSD", "frxGBPUSD", "R_100" (slashes/spaces removed automatically).
            timeframe: Candle interval — one of 1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w.
            strategies: List of strategy specs to simulate, each with its own parameters (see schema). Example: [{"type":"ema_cross","fast":20,"slow":50},{"type":"rsi_reversion","period":14,"oversold":30,"overbought":70}].
            limit: Number of candles to fetch (200-1000, default 500). More candles = more robust statistics.
            initial_cash: Starting equity in quote currency (default 10000).
            fee_pct: Per-side trading fee in percent (default 0.1 = Binance taker).
            slippage_pct: Per-side slippage in percent (default 0.05).
        """
        clean_symbol = symbol.replace("/", "").replace(" ", "").replace("-", "").upper()
        tf = timeframe.strip().lower()
        if tf not in _VALID_INTERVALS:
            return ToolResult(
                success=False,
                message=f"Invalid timeframe '{timeframe}'. Valid: {sorted(_VALID_INTERVALS)}",
            )
        if tf not in _BARS_PER_YEAR:
            return ToolResult(success=False, message=f"Timeframe '{tf}' cannot be annualized for backtesting.")
        limit = max(200, min(int(limit), _MAX_CANDLES))
        if not strategies:
            return ToolResult(success=False, message="No strategy specs provided.")
        fee = max(0.0, float(fee_pct)) / 100.0
        slip = max(0.0, float(slippage_pct)) / 100.0
        cash = max(1.0, float(initial_cash))

        # Coerce raw dicts (production wrapper does not pydantic-validate)
        specs: List[StrategySpec] = []
        spec_errors: List[Dict[str, Any]] = []
        for raw in strategies:
            if isinstance(raw, StrategySpec):
                specs.append(raw)
                continue
            try:
                specs.append(StrategySpec.model_validate(raw))
            except Exception as exc:  # noqa: BLE001 — invalid spec is a per-item error
                spec_errors.append({
                    "spec": str(getattr(raw, "get", lambda *_: raw)("type", raw))[:60],
                    "detail": str(exc)[:200],
                })

        try:
            df, source = await fetch_ohlcv(clean_symbol, tf, limit)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, message=f"Failed to fetch OHLCV data: {exc}")

        results: List[Dict[str, Any]] = []
        for spec in specs:
            try:
                sig = _signal_series(df, spec)
                sim = _simulate(df, sig, cash, fee, slip)
                metrics = _metrics(sim, df, cash, _BARS_PER_YEAR[tf])
                results.append({
                    "strategy": _describe(spec),
                    "metrics": metrics,
                    "equity_curve": _downsample_equity(df["open_time"].to_numpy(), sim["equity"]),
                    "trades": sim["trades"][-20:][::-1],
                    "trades_shown": min(20, len(sim["trades"])),
                })
            except Exception as exc:  # noqa: BLE001 — one bad spec must not kill the batch
                results.append({
                    "strategy": _describe(spec),
                    "error": str(exc)[:200],
                })

        ok_results = [r for r in results if "error" not in r]
        if not ok_results and not spec_errors:
            return ToolResult(success=False, message="No strategy could be simulated from the given specs.")

        best = max(
            ok_results,
            key=lambda r: r["metrics"]["total_return_pct"],
            default=None,
        )
        return ToolResult(success=True, data={
            "symbol": clean_symbol,
            "timeframe": tf,
            "source": source,
            "candles_fetched": int(len(df)),
            "range_utc": [
                str(df["open_time"].iloc[0].isoformat()),
                str(df["open_time"].iloc[-1].isoformat()),
            ],
            "execution_model": "next-bar-open, fees+slippage per side, long/flat only",
            "assumptions": {"initial_cash": cash, "fee_pct": fee_pct, "slippage_pct": slippage_pct},
            "best_by_total_return": best["strategy"]["type"] if best else None,
            "errors": spec_errors or None,
            "results": results,
        })
