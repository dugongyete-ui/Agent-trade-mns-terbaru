"""Technical indicator toolkit — ATR, VWAP, EMA stack, RSI divergence.

Design goal: the AGENT decides everything. The tool exposes raw computation
capability with documented parameters; it never prescribes when or how to use
the indicators, and the system prompt is not touched. The agent picks symbol,
timeframe, indicator types and every parameter per call, adapting to whatever
market condition it faces.

Data source: shared market_data fetcher — Binance public OHLCV for crypto
pairs, Deriv API for gold/forex/synthetic symbols (XAUUSD, frxEURUSD, R_100,
...). The response always reports which source produced the candles.
"""

from typing import Any, Dict, List, Literal, Optional

import numpy as np
import pandas as pd
from langchain.tools import tool
from pydantic import BaseModel, Field

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit
from app.domain.services.tools.market_data import fetch_ohlcv

_VALID_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}


class IndicatorSpec(BaseModel):
    """One indicator the agent wants computed, with its own parameters."""

    type: Literal["atr", "vwap", "ema", "rsi_divergence"] = Field(
        description=(
            "Indicator to compute: "
            "'atr' = Average True Range (volatility, Wilder smoothing); "
            "'vwap' = Volume Weighted Average Price (anchored, resets per session); "
            "'ema' = Exponential Moving Average stack (one or many periods); "
            "'rsi_divergence' = RSI with pivot-based divergence scan against price."
        )
    )
    period: Optional[int] = Field(
        default=None,
        description=(
            "Single period. atr/rsi_divergence default 14; vwap ignores it. "
            "ema uses it as a shorthand when 'periods' list is not given."
        ),
    )
    periods: Optional[List[int]] = Field(
        default=None,
        description=(
            "Multiple periods at once — used by 'ema' for a stack "
            "(e.g. [20, 50, 200]). Ignored by other types."
        ),
    )
    anchor: Optional[Literal["session", "weekly", "monthly"]] = Field(
        default=None,
        description=(
            "vwap only: reset point of the cumulative window. "
            "'session' = since UTC midnight (default), 'weekly' = since Monday 00:00 UTC, "
            "'monthly' = since 1st of month 00:00 UTC."
        ),
    )
    lookback: Optional[int] = Field(
        default=None,
        description=(
            "rsi_divergence only: how many recent bars to scan for divergences. "
            "Default 60."
        ),
    )
    pivot_window: Optional[int] = Field(
        default=None,
        description=(
            "rsi_divergence only: bars required on each side to confirm a swing "
            "pivot. Higher = stricter/stronger pivots. Default 2."
        ),
    )


def _rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (Running Moving Average)."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return _rma(tr, period)


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _rma(gain, period)
    avg_loss = _rma(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(100.0).where(avg_loss.notna(), np.nan)


def _vwap(df: pd.DataFrame, anchor: str) -> Dict[str, Any]:
    """Anchored VWAP: cumulative since the anchor boundary (UTC).

    Sources without volume (Deriv) degrade to an unweighted typical-price
    mean per anchor window, flagged via ``volume_weighted: false`` — never
    presented as a true VWAP.
    """
    ts = pd.to_datetime(df["open_time"], utc=True)
    if anchor == "weekly":
        boundary = ts.dt.strftime("%G-W%V")
    elif anchor == "monthly":
        boundary = ts.dt.strftime("%Y-%m")
    else:  # session
        boundary = ts.dt.strftime("%Y-%m-%d")
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    has_volume = float(df["volume"].fillna(0.0).abs().sum()) > 0.0
    if has_volume:
        pv = typical * df["volume"]
        cum_pv = pv.groupby(boundary.values).cumsum()
        cum_vol = df["volume"].groupby(boundary.values).cumsum()
        vwap = (cum_pv / cum_vol.replace(0.0, np.nan)).ffill()
    else:
        vwap = typical.groupby(boundary.values).cumcount().add(1).pipe(
            lambda n: typical.groupby(boundary.values).cumsum() / n
        )
    close = float(df["close"].iloc[-1])
    last_vwap = float(vwap.iloc[-1])
    last_boundary = boundary.iloc[-1]
    basis_bars = int((boundary == last_boundary).sum())
    first_bar_of_window = int(np.flatnonzero((boundary == last_boundary).values)[0])
    start_iso = str(ts.iloc[first_bar_of_window].isoformat())
    return {
        "anchor": anchor,
        "value": round(last_vwap, 6),
        "volume_weighted": has_volume,
        "note": None if has_volume else "source carries no volume — unweighted typical-price mean, NOT a true VWAP",
        "close_vs_vwap_pct": round((close - last_vwap) / last_vwap * 100.0, 3) if last_vwap else None,
        "window_start_utc": start_iso,
        "window_bars": basis_bars,
    }


def _find_pivots(high: pd.Series, low: pd.Series, w: int):
    """Return (pivot_high_idx, pivot_low_idx) using a centered window."""
    n = len(high)
    ph: List[int] = []
    pl: List[int] = []
    for i in range(w, n - w):
        seg_h = high.iloc[i - w: i + w + 1]
        seg_l = low.iloc[i - w: i + w + 1]
        if high.iloc[i] >= seg_h.max():
            ph.append(i)
        if low.iloc[i] <= seg_l.min():
            pl.append(i)
    return ph, pl


def _rsi_divergence(
    df: pd.DataFrame,
    period: int,
    lookback: int,
    pivot_w: int,
) -> Dict[str, Any]:
    close = df["close"]
    rsi = _rsi(close, period)
    n = len(df)
    ph, pl = _find_pivots(df["high"], df["low"], pivot_w)
    scan_from = max(0, n - lookback)

    found: List[Dict[str, Any]] = []

    def _check(pivots: List[int], is_high_pivot: bool) -> None:
        pairs = list(zip(pivots, pivots[1:]))
        for a, b in pairs:
            if a < scan_from:
                continue
            pa, pb = float(df["low"].iloc[a]), float(df["low"].iloc[b])
            xa, xb = float(df["high"].iloc[a]), float(df["high"].iloc[b])
            ra, rb = float(rsi.iloc[a]), float(rsi.iloc[b])
            if np.isnan(ra) or np.isnan(rb):
                continue
            kind = None
            if not is_high_pivot:
                if pb < pa and rb > ra:
                    kind = "regular_bullish"
                elif pb > pa and rb < ra:
                    kind = "hidden_bullish"
            else:
                if xb > xa and rb < ra:
                    kind = "regular_bearish"
                elif xb < xa and rb > ra:
                    kind = "hidden_bearish"
            if kind:
                found.append({
                    "type": kind,
                    "bars_ago": n - 1 - b,
                    "price_from": round(pa if not is_high_pivot else xa, 6),
                    "price_to": round(pb if not is_high_pivot else xb, 6),
                    "rsi_from": round(ra, 2),
                    "rsi_to": round(rb, 2),
                })

    _check(pl, is_high_pivot=False)
    _check(ph, is_high_pivot=True)

    # Keep the most recent few, newest first
    found.sort(key=lambda d: d["bars_ago"])
    current_rsi = float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else None
    return {
        "period": period,
        "rsi_now": round(current_rsi, 2) if current_rsi is not None else None,
        "lookback_bars": lookback,
        "pivot_window": pivot_w,
        "divergences": found[:4],
        "divergence_count": len(found),
    }


# Backwards-compatible alias: historical callers fetched Binance-only candles
# via _fetch_klines. New code should use market_data.fetch_ohlcv, which routes
# between Binance and Deriv automatically.
from app.domain.services.tools.market_data import _fetch_binance as _fetch_klines  # noqa: E402,F401


def _round(v: float, ref: float) -> float:
    """Round a price relative to its magnitude for compact output."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    digits = 6 if ref < 1 else (4 if ref < 100 else 2)
    return round(float(v), digits)


class TechnicalToolkit(BaseToolkit):
    """Technical indicator computations on exchange OHLCV data.

    Pure capability: the agent chooses symbols, timeframes, indicator types
    and every parameter. No strategy, bias or usage rule is embedded here.
    """

    name: str = "technical"

    @tool(parse_docstring=True)
    async def technical_indicators(
        self,
        symbol: str,
        timeframe: str,
        indicators: List[IndicatorSpec],
        limit: int = 300,
    ) -> ToolResult:
        """Compute technical indicators (ATR, VWAP, EMA stack, RSI divergence) on real OHLCV candles.

        Works for Binance crypto pairs (BTCUSDT, ETHUSDT, ...) AND Deriv
        markets (XAUUSD gold, frxEURUSD and other forex pairs, R_100 and other
        synthetics — pass the plain name, the source is resolved automatically
        and reported back as `source`). Computes exactly the indicators
        requested in `indicators`, each with the parameters you choose.
        Results contain current values (plus compact context) — interpret them
        yourself according to the market situation.

        Args:
            symbol: Symbol, e.g. "BTCUSDT", "ETHUSDT", "XAUUSD", "frxEURUSD", "R_100" (slashes/spaces removed automatically).
            timeframe: Candle interval — one of 1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w.
            indicators: List of indicator specs to compute in one call; each spec has its own parameters (see schema). Example: [{"type":"atr","period":14},{"type":"vwap","anchor":"session"},{"type":"ema","periods":[20,50,200]},{"type":"rsi_divergence","period":14,"lookback":60}].
            limit: Number of candles to fetch (100-1000, default 300). Use >= 260 when requesting EMA 200 so the value has full warm-up.
        """
        clean_symbol = symbol.replace("/", "").replace(" ", "").replace("-", "").upper()
        tf = timeframe.strip().lower()
        if tf not in _VALID_INTERVALS:
            return ToolResult(
                success=False,
                message=f"Invalid timeframe '{timeframe}'. Valid: {sorted(_VALID_INTERVALS)}",
            )
        limit = max(100, min(int(limit), 1000))
        if not indicators:
            return ToolResult(success=False, message="No indicator specs provided.")

        # The production tool wrapper passes raw dicts (no pydantic validation),
        # so coerce specs explicitly and report invalid ones instead of failing.
        specs: List[IndicatorSpec] = []
        spec_errors: List[Dict[str, Any]] = []
        for raw in indicators:
            if isinstance(raw, IndicatorSpec):
                specs.append(raw)
                continue
            try:
                specs.append(IndicatorSpec.model_validate(raw))
            except Exception as exc:  # noqa: BLE001 — invalid spec is a per-item error
                spec_errors.append({
                    "spec": str(getattr(raw, "get", lambda *_: raw)("type", raw))[:60],
                    "detail": str(exc)[:200],
                })

        try:
            df, source = await fetch_ohlcv(clean_symbol, tf, limit)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, message=f"Failed to fetch OHLCV data: {exc}")

        close = float(df["close"].iloc[-1])
        out: Dict[str, List[Dict[str, Any]]] = {}
        if spec_errors:
            out["errors"] = spec_errors
        for spec in specs:
            try:
                if spec.type == "atr":
                    period = int(spec.period or 14)
                    atr = _atr(df, period)
                    val = float(atr.iloc[-1])
                    out.setdefault("atr", []).append({
                        "period": period,
                        "value": _round(val, close),
                        "atr_pct_of_price": round(val / close * 100.0, 3) if close else None,
                        "history_last5": [_round(v, close) for v in atr.iloc[-5:].tolist()],
                    })
                elif spec.type == "vwap":
                    anchor = spec.anchor or "session"
                    out.setdefault("vwap", []).append(_vwap(df, anchor))
                elif spec.type == "ema":
                    if spec.periods:
                        periods = [int(p) for p in spec.periods]
                    elif spec.period:
                        periods = [int(spec.period)]
                    else:
                        periods = [20]
                    periods = sorted(set(periods))
                    emas = {p: _ema(df["close"], p) for p in periods}
                    entry: Dict[str, Any] = {"periods": {}}
                    for p, s in emas.items():
                        entry["periods"][str(p)] = _round(float(s.iloc[-1]), close)
                    entry["close"] = _round(close, close)
                    entry["close_above"] = [
                        p for p, s in emas.items()
                        if not np.isnan(s.iloc[-1]) and close > float(s.iloc[-1])
                    ]
                    entry["close_below"] = [
                        p for p, s in emas.items()
                        if not np.isnan(s.iloc[-1]) and close < float(s.iloc[-1])
                    ]
                    ordered = sorted(
                        ((p, float(s.iloc[-1])) for p, s in emas.items() if not np.isnan(s.iloc[-1])),
                        key=lambda x: x[1], reverse=True,
                    )
                    entry["ema_order_high_to_low"] = [p for p, _ in ordered]
                    out.setdefault("ema", []).append(entry)
                elif spec.type == "rsi_divergence":
                    period = int(spec.period or 14)
                    lookback = int(spec.lookback or 60)
                    pivot_w = int(spec.pivot_window or 2)
                    res = _rsi_divergence(df, period, lookback, pivot_w)
                    # Compact price levels
                    for d in res["divergences"]:
                        d["price_from"] = _round(d["price_from"], close)
                        d["price_to"] = _round(d["price_to"], close)
                    out.setdefault("rsi_divergence", []).append(res)
            except Exception as exc:  # noqa: BLE001 — one bad spec must not kill the batch
                out.setdefault("errors", []).append(
                    {"spec": getattr(spec, "type", str(spec)), "detail": str(exc)[:200]}
                )

        if not out:
            return ToolResult(success=False, message="No indicator could be computed from the given specs.")

        return ToolResult(success=True, data={
            "symbol": clean_symbol,
            "timeframe": tf,
            "source": source,
            "candles_fetched": int(len(df)),
            "last_close": _round(close, close),
            "last_candle_close_utc": str(df["open_time"].iloc[-1].isoformat()),
            "indicators": out,
        })
