"""Shared OHLCV market-data fetcher — Binance (crypto) + Deriv (forex/gold/synthetics).

One entry point: ``fetch_ohlcv(symbol, interval, limit) -> (DataFrame, source)``.

Routing rules (the agent never needs to know which backend is used, the
response always reports the resolved source):
- Explicit Deriv shapes (``frxXAUUSD``, ``R_100``, ``1HZ100V``, ``stp*``...)
  go straight to Deriv.
- Crypto-quote suffixes (USDT/USDC/BTC/...) go to Binance first, Deriv fallback.
- Everything else (``XAUUSD``, ``EURUSD``, ``USDJPY``, ``XAGUSD``...) is tried
  on Deriv as a forex/metals symbol (auto ``frx`` prefix), Binance fallback.

Deriv candles carry NO volume — the volume column is filled with 0.0 and
callers that need volume (VWAP) must handle that case honestly.
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# Public Binance market-data hosts, tried in order. data-api.binance.vision is
# the official public data mirror and is not geo-restricted.
_KLINE_HOSTS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
]

# Deriv granularity accepts seconds; anything above 1d is aggregated from 1d.
_DERIV_GRANULARITY = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400,
}

_DERIV_PASSTHROUGH_RE = re.compile(
    r"^(frx|R_|RDK|1HZ|stp|JD|jd_|BO_|blwsk|brnsk|chg|otc|synthetic|WMOC|mrng|ev_)", re.IGNORECASE
)
_CRYPTO_SUFFIXES = (
    "USDT", "USDC", "FDUSD", "TUSD", "BUSD", "BNB", "BTC", "ETH", "TRX", "BRL",
)

_DERIV_WS_URL = "wss://ws.binaryws.com/websockets/v3?app_id=1089"


def resolve_deriv_symbol(symbol: str) -> str:
    """Map a generic symbol to a Deriv market code (case-preserving)."""
    s = symbol.strip()
    if _DERIV_PASSTHROUGH_RE.match(s):
        return s
    u = s.upper()
    # Plain 6-letter FX/metals names (XAUUSD, EURUSD, XAGUSD, USDJPY...).
    if re.fullmatch(r"[A-Z]{6}", u):
        return f"frx{u}"
    return s


def _looks_crypto(symbol: str) -> bool:
    s = symbol.strip().upper()
    return any(s.endswith(q) and not s.startswith("FRX") for q in _CRYPTO_SUFFIXES)


async def _fetch_binance(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """Fetch OHLCV candles from Binance public endpoints with host fallback."""
    params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
    last_err: Optional[str] = None
    async with httpx.AsyncClient(timeout=15.0) as client:
        for host in _KLINE_HOSTS:
            try:
                resp = await client.get(f"{host}/api/v3/klines", params=params)
                if resp.status_code != 200:
                    last_err = f"{host} -> HTTP {resp.status_code}: {resp.text[:160]}"
                    continue
                rows = resp.json()
                df = pd.DataFrame(rows, columns=[
                    "open_time", "open", "high", "low", "close", "volume",
                    "close_time", "quote_volume", "trades",
                    "taker_buy_base", "taker_buy_quote", "ignore",
                ])
                for col in ("open", "high", "low", "close", "volume"):
                    df[col] = df[col].astype(float)
                df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
                return df
            except Exception as exc:  # noqa: BLE001 — try next host
                last_err = f"{host} -> {exc}"
                continue
    raise RuntimeError(f"all Binance hosts failed for {symbol} {interval}: {last_err}")


async def _deriv_ws_request(payload: Dict[str, Any], timeout: float = 20.0) -> Dict[str, Any]:
    """One-shot Deriv websocket request (connect, send, await reply, close)."""
    import websockets  # local import: only needed when Deriv is the source

    async with websockets.connect(_DERIV_WS_URL, open_timeout=15, close_timeout=5) as ws:
        await ws.send(json.dumps({**payload, "req_id": 1}))
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError("Deriv websocket reply timed out")
            msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            data = json.loads(msg)
            if isinstance(data, dict) and data.get("req_id") == 1:
                return data


async def _fetch_deriv(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """Fetch OHLCV candles from the Deriv API (forex, metals, synthetics).

    Deriv only supports granularity up to 1d — larger intervals (3d/1w/1M) are
    aggregated from daily candles with pandas resample.
    """
    dsym = resolve_deriv_symbol(symbol)
    aggregate_rule = {"3d": "3D", "1w": "7D", "1M": "MS"}.get(interval)
    if aggregate_rule:
        # Deriv serves granularity up to 1d — pull dailies and aggregate.
        g = _DERIV_GRANULARITY["1d"]
        mult = {"3d": 3, "1w": 7, "1M": 31}[interval]
        count = max(2, min(int(limit) * mult + 7, 5000))
    else:
        g = _DERIV_GRANULARITY.get(interval)
        if g is None:
            raise ValueError(
                f"timeframe '{interval}' is not supported for Deriv symbols. "
                f"Use one of 1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w 1M."
            )
        count = max(2, min(int(limit), 5000))
    resp = await _deriv_ws_request({
        "ticks_history": dsym,
        "adjust_start_time": 1,
        "count": count,
        "end": "latest",
        "granularity": g,
        "style": "candles",
    })
    if "error" in resp:
        raise ValueError(f"Deriv error for {dsym}: {resp['error'].get('message', resp['error'])}")
    candles = resp.get("candles", [])
    if not candles:
        raise ValueError(f"Deriv returned no candles for {dsym} {interval}")

    df = pd.DataFrame(candles)
    df = df.rename(columns={"epoch": "open_time"})
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    # Deriv candle payloads carry no volume — mark it explicitly as absent.
    df["volume"] = 0.0
    df["open_time"] = pd.to_datetime(df["open_time"].astype(float), unit="s", utc=True)
    df = df.sort_values("open_time").reset_index(drop=True)

    if aggregate_rule:
        df = (
            df.set_index("open_time")
            .resample(aggregate_rule)
            .agg({"open": "first", "high": "max", "low": "min",
                  "close": "last", "volume": "sum"})
            .dropna(subset=["open", "close"])
            .reset_index()
        )
    return df


async def fetch_ohlcv(symbol: str, interval: str, limit: int) -> Tuple[pd.DataFrame, str]:
    """Fetch OHLCV candles choosing the best source for ``symbol``.

    Returns ``(dataframe, source_label)`` where source_label is
    ``"binance"`` or ``"deriv"`` (plus the resolved Deriv symbol when used).
    """
    s = symbol.strip().upper()

    def _is_deriv_shape(x: str) -> bool:
        return bool(_DERIV_PASSTHROUGH_RE.match(x))

    errors: list = []

    if _is_deriv_shape(s):
        try:
            return await _fetch_deriv(s, interval, limit), "deriv"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"deriv: {exc}")

    if _looks_crypto(s) or not _is_deriv_shape(s):
        crypto_first = _looks_crypto(s)
        attempts = (
            [("binance", lambda: _fetch_binance(s, interval, limit)),
             ("deriv", lambda: _fetch_deriv(s, interval, limit))]
            if crypto_first
            else [("deriv", lambda: _fetch_deriv(s, interval, limit)),
                  ("binance", lambda: _fetch_binance(s, interval, limit))]
        )
        for label, fn in attempts:
            try:
                df = await fn()
                if label == "deriv":
                    return df, f"deriv ({resolve_deriv_symbol(s)})"
                return df, label
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: {exc}")

    raise RuntimeError(
        f"no OHLCV source succeeded for {symbol} {interval}: " + " | ".join(errors)
    )
