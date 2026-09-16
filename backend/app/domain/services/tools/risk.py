"""Portfolio risk toolkit — cross-asset risk analytics (HHI, VaR/ES, beta, correlations).

Ported concept from HKUDS/Vibe-Trading's portfolio_risk_xray: quantify
concentration, tail risk and diversification of a crypto basket with plain
arithmetic — no LLM involved in the computation.

Design goal (same philosophy as TechnicalToolkit): the AGENT decides which
assets, which weights, which lookback and which confidence level fit the
question. The tool exposes raw computation and never prescribes usage.
"""

import asyncio
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from langchain.tools import tool
from pydantic import BaseModel, Field

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.base import BaseToolkit
from app.domain.services.tools.technical import _VALID_INTERVALS, _fetch_klines


class RiskSpec(BaseModel):
    """One-asset entry with an optional weight — the agent composes the basket."""

    symbol: str = Field(description="Trading pair, e.g. 'BTCUSDT'.")
    weight: Optional[float] = Field(
        default=None,
        description=(
            "Portfolio weight as a decimal (0.5 = 50%). If omitted for any asset, "
            "all assets are weighted equally. If given for some, they are "
            "normalized against the total."
        ),
    )


def _annualize_factor(tf: str) -> float:
    bars_per_year = {
        "1m": 525_600, "3m": 175_200, "5m": 105_120, "15m": 35_040,
        "30m": 17_520, "1h": 8_760, "2h": 4_380, "4h": 2_190,
        "6h": 1_460, "8h": 1_095, "12h": 730, "1d": 365, "1w": 52.14,
    }
    return bars_per_year.get(tf, 365.0)


def _max_drawdown(curve: np.ndarray) -> float:
    running = np.maximum.accumulate(curve)
    return float((curve / running - 1.0).min())


def _historical_var_es(returns: np.ndarray, confidence: float) -> Dict[str, float]:
    """Historical VaR/ES + parametric (normal) VaR on per-bar returns, in %."""
    if len(returns) < 20:
        return {}
    alpha = 1.0 - confidence
    q = float(np.percentile(returns, alpha * 100.0))
    tail = returns[returns <= q]
    mu, sd = float(returns.mean()), float(returns.std(ddof=1))
    z = {0.90: 1.2816, 0.95: 1.6449, 0.975: 1.9600, 0.99: 2.3263, 0.999: 3.0902}
    zval = z.get(round(confidence, 3), 1.6449)
    return {
        "hist_var_pct": round(q * 100.0, 3),
        "hist_es_pct": round(float(tail.mean()) * 100.0, 3) if len(tail) else round(q * 100.0, 3),
        "param_var_pct": round((mu - zval * sd) * 100.0, 3),
    }


class PortfolioRiskToolkit(BaseToolkit):
    """Cross-asset portfolio risk analytics on exchange OHLCV data.

    Pure capability: the agent picks the basket, weights, lookback and
    confidence level. No allocation advice is embedded here.
    """

    name: str = "portfolio_risk"

    @tool(parse_docstring=True)
    async def portfolio_risk(
        self,
        assets: List[RiskSpec],
        timeframe: str = "1d",
        lookback: int = 365,
        benchmark: Optional[str] = None,
        confidence: float = 0.95,
    ) -> ToolResult:
        """Compute portfolio risk metrics (HHI, VaR, ES, beta, correlations, diversification) for a basket of crypto assets.

        Fetches historical candles for every asset, aligns them on common
        dates, applies the weights you choose and returns: concentration
        (HHI + effective number of bets), annualized return/volatility,
        Sharpe, Sortino, max drawdown, Value-at-Risk and Expected Shortfall
        (historical + parametric) at your confidence level, the full
        correlation matrix, average pairwise correlation, diversification
        ratio, and beta/correlation vs a benchmark asset if you provide one.
        You choose every input — basket, weights, lookback, confidence.

        Args:
            assets: List of assets with optional weights, e.g. [{"symbol":"BTCUSDT","weight":0.5},{"symbol":"ETHUSDT","weight":0.3},{"symbol":"SOLUSDT"}]. Omitted weights default to equal.
            timeframe: Candle interval for the return series — one of 1h 4h 1d 1w recommended (default 1d).
            lookback: Number of most-recent candles used per asset (50-1000, default 365).
            benchmark: Optional symbol to compute portfolio beta and correlation against, e.g. "BTCUSDT". Must not be one of the basket assets.
            confidence: Confidence level for VaR/ES (0.90-0.999, default 0.95).
        """
        tf = timeframe.strip().lower()
        if tf not in _VALID_INTERVALS or tf in ("3d", "1M"):
            return ToolResult(success=False, message=f"Unsupported timeframe '{timeframe}' for risk analytics. Use: 1m..1w (no 3d/1M).")
        if not assets:
            return ToolResult(success=False, message="No assets provided.")

        # Production wrapper passes raw dicts (no pydantic validation) — coerce
        # explicitly and report invalid entries instead of failing the call.
        specs: List[RiskSpec] = []
        asset_errors: List[Dict[str, Any]] = []
        for raw in assets:
            if isinstance(raw, RiskSpec):
                specs.append(raw)
                continue
            try:
                specs.append(RiskSpec.model_validate(raw))
            except Exception as exc:  # noqa: BLE001 — invalid asset is a per-item error
                asset_errors.append({
                    "asset": str(raw)[:60],
                    "detail": str(exc)[:200],
                })
        assets = specs
        if not assets:
            return ToolResult(success=False, message=(
                "No valid assets after validation. Errors: "
                + "; ".join(e["detail"][:80] for e in asset_errors)
            ))
        lookback = max(50, min(int(lookback), 1000))
        confidence = min(0.999, max(0.90, float(confidence)))

        clean = [a.symbol.replace("/", "").replace(" ", "").replace("-", "").upper() for a in assets]
        if len(set(clean)) != len(clean):
            return ToolResult(success=False, message="Duplicate symbols in assets list.")
        bench_clean = None
        if benchmark:
            bench_clean = benchmark.replace("/", "").replace(" ", "").replace("-", "").upper()
            if bench_clean in clean:
                return ToolResult(success=False, message="Benchmark must differ from basket assets.")

        # Weights: equal if none given; otherwise omitted assets get the average
        # of the given ones, then the whole vector is normalized to sum 1.
        given = [a.weight for a in assets if a.weight is not None]
        if not given:
            raw = [1.0] * len(assets)
        else:
            avg = float(sum(given)) / len(given)
            raw = [float(a.weight) if a.weight is not None else avg for a in assets]
        weights = np.array(raw, dtype=float)
        if weights.sum() <= 0:
            return ToolResult(success=False, message="All weights are zero.")
        weights = weights / weights.sum()

        async def _load(sym: str) -> Optional[pd.DataFrame]:
            try:
                return await _fetch_klines(sym, tf, lookback)
            except Exception:  # noqa: BLE001 — missing asset reported, not fatal
                return None

        frames, bench_frame = await asyncio.gather(
            asyncio.gather(*(_load(s) for s in clean)),
            _load(bench_clean) if bench_clean else asyncio.sleep(0, result=None),
        )

        loaded: Dict[str, pd.DataFrame] = {}
        failed: List[str] = []
        for sym, frame in zip(clean, frames):
            if frame is None or len(frame) < 30:
                failed.append(sym)
            else:
                loaded[sym] = frame
        if not loaded:
            return ToolResult(success=False, message=f"Could not load data for any asset. Failed: {failed}")

        # Align closes on common UTC dates (inner join)
        close_series = {
            sym: pd.Series(
                frame["close"].to_numpy(),
                index=pd.to_datetime(frame["open_time"], utc=True).dt.floor("D"),
                name=sym,
            )
            for sym, frame in loaded.items()
        }
        prices = pd.concat(close_series.values(), axis=1, join="inner").dropna()
        if len(prices) < 30:
            return ToolResult(success=False, message=f"Only {len(prices)} common dates across assets — need >= 30. Try fewer assets or a shorter lookback.")

        # Re-normalize weights over the assets that actually loaded
        w_full = np.array([
            weights[clean.index(sym)] if sym in loaded else 0.0 for sym in prices.columns
        ])
        w_full = w_full / w_full.sum()

        rets = prices.pct_change().dropna()
        port_rets = rets.to_numpy() @ w_full
        af = _annualize_factor(tf)

        ann_return = float(np.mean(port_rets) * af)
        ann_vol = float(np.std(port_rets, ddof=1) * np.sqrt(af))
        downside = port_rets[port_rets < 0]
        dsd = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
        curve = np.cumprod(1.0 + port_rets)
        var_es = _historical_var_es(port_rets, confidence)

        corr = rets.corr()
        n_assets = len(rets.columns)
        if n_assets > 1:
            corr_vals = corr.to_numpy()
            iu = np.triu_indices(n_assets, k=1)
            avg_corr = float(np.mean(corr_vals[iu]))
            asset_vols = rets.std(ddof=1).to_numpy()
            div_ratio = float((w_full @ asset_vols) / np.std(port_rets, ddof=1)) if np.std(port_rets, ddof=1) > 0 else None
        else:
            avg_corr, div_ratio = None, None

        hhi = float(np.sum((w_full ** 2)))
        beta = None
        bench_corr = None
        if bench_clean and bench_frame is not None and len(bench_frame) >= 30:
            bser = pd.Series(
                bench_frame["close"].to_numpy(),
                index=pd.to_datetime(bench_frame["open_time"], utc=True).dt.floor("D"),
                name="bench",
            )
            joined = pd.concat([rets, bser.pct_change().rename("bench")], axis=1, join="inner").dropna()
            if len(joined) > 20 and float(joined["bench"].var()) > 0:
                port = joined.drop(columns=["bench"]).to_numpy() @ w_full
                bench_rets = joined["bench"].to_numpy()
                beta = round(float(np.cov(port, bench_rets)[0][1] / np.var(bench_rets)), 3)
                bench_corr = round(float(np.corrcoef(port, bench_rets)[0][1]), 3)

        asset_stats = []
        for i, sym in enumerate(prices.columns):
            r = rets[sym].to_numpy()
            asset_stats.append({
                "symbol": sym,
                "weight": round(float(w_full[i]), 4),
                "annual_vol_pct": round(float(np.std(r, ddof=1) * np.sqrt(af)) * 100.0, 2),
                "annual_return_pct": round(float(np.mean(r) * af) * 100.0, 2),
                "max_drawdown_pct": round(_max_drawdown(np.cumprod(1.0 + r)) * 100.0, 2),
            })

        return ToolResult(success=True, data={
            "timeframe": tf,
            "common_bars": int(len(rets)),
            "range_utc": [str(prices.index[0].isoformat()), str(prices.index[-1].isoformat())],
            "confidence": confidence,
            "failed_assets": failed or None,
            "errors": asset_errors or None,
            "concentration": {
                "hhi": round(hhi, 4),
                "effective_n_bets": round(1.0 / hhi, 2) if hhi > 0 else None,
                "note": "effective_n = 1/HHI: 1.0 = fully concentrated, equals asset count when weights are equal",
            },
            "portfolio": {
                "annual_return_pct": round(ann_return * 100.0, 2),
                "annual_volatility_pct": round(ann_vol * 100.0, 2),
                "sharpe": round(ann_return / ann_vol, 2) if ann_vol > 0 else None,
                "sortino": round(ann_return / (dsd * np.sqrt(af)), 2) if dsd > 0 else None,
                "max_drawdown_pct": round(_max_drawdown(curve) * 100.0, 2),
                "best_day_pct": round(float(port_rets.max()) * 100.0, 2),
                "worst_day_pct": round(float(port_rets.min()) * 100.0, 2),
                "positive_period_ratio_pct": round(float((port_rets > 0).mean()) * 100.0, 1),
            },
            "tail_risk": var_es or None,
            "diversification": {
                "avg_pairwise_correlation": round(avg_corr, 3) if avg_corr is not None else None,
                "diversification_ratio": round(div_ratio, 3) if div_ratio is not None else None,
                "correlation_matrix": {
                    "symbols": list(rets.columns),
                    "values": [[round(float(v), 2) for v in row] for row in corr.to_numpy()],
                },
            },
            "benchmark": {
                "symbol": bench_clean,
                "beta": beta,
                "correlation": bench_corr,
            } if bench_clean else None,
            "assets": asset_stats,
        })
