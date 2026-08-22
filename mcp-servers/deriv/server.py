#!/usr/bin/env python3
"""
Deriv WebSocket MCP Server — Professional Grade
Real-time & historical market data from Deriv (Binary.com) + built-in
technical indicators:
RSI, MACD, Bollinger Bands, EMA/SMA, ATR, Stochastic, ADX,
Market Structure (HH/HL/LH/LL), Support/Resistance, Divergence detection,
Smart SL snap to swing, multi-timeframe confluence analysis.
Fibonacci Retracement/Extension, Pivot Points (Daily/Weekly),
Ichimoku Cloud, Parabolic SAR, Supertrend, Keltner Channel,
Donchian Channel, CCI, Williams %R, Heikin Ashi.
"""

import asyncio
import json
import math
from datetime import datetime
from typing import Any

import websockets
import websockets.connection as _ws_conn
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

DERIV_WS_URL = "wss://ws.binaryws.com/websockets/v3?app_id=1089"

GRAN_LABEL = {
    60:"1m", 120:"2m", 180:"3m", 300:"5m", 600:"10m",
    900:"15m", 1800:"30m", 3600:"1h", 7200:"2h",
    14400:"4h", 28800:"8h", 86400:"1D"
}

# Internal transport marker used to carry chart data alongside the
# human-readable tool result. The LLM still receives the readable text.
CHART_MARKER = "__DZECK_CHART__"



# ── Persistent Deriv WebSocket Connection ─────────────────────────────────────

class DerivConnection:
    """Persistent warm WebSocket connection to the Deriv API.

    A single connection is kept alive for the lifetime of the MCP server
    process.  All tool calls share it, eliminating per-call handshake
    overhead (~500 ms–1 s per open/close cycle).

    Features:
    - req_id multiplexing — concurrent requests are matched by req_id
    - Background reader — routes incoming messages to the correct Future
    - Keepalive ping every 25 s — prevents Deriv idle-timeout (~60 s)
    - Auto-reconnect — transparent reconnection on any connection failure
    """

    _PING_INTERVAL = 25          # seconds between keepalive pings
    _CONNECT_TIMEOUT = 12        # seconds to wait for initial handshake
    _RECV_TIMEOUT = 20           # seconds to wait for a response
    _MAX_RECONNECT_DELAY = 30    # cap on exponential back-off

    def __init__(self, url: str):
        self._url = url
        self._ws = None
        self._pending: dict[int, asyncio.Future] = {}
        self._req_id = 0
        self._lock = asyncio.Lock()          # serialises connect/reconnect
        self._reader_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._reconnect_delay = 1

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _connect(self) -> None:
        """Open a fresh WebSocket and start background tasks."""
        self._ws = await asyncio.wait_for(
            websockets.connect(
                self._url,
                open_timeout=self._CONNECT_TIMEOUT,
                close_timeout=5,
                ping_interval=None,    # we handle keepalive ourselves
            ),
            timeout=self._CONNECT_TIMEOUT,
        )
        self._reconnect_delay = 1          # reset back-off on success
        self._reader_task = asyncio.create_task(self._reader(), name="deriv-reader")
        self._keepalive_task = asyncio.create_task(self._keepalive(), name="deriv-keepalive")

    async def _disconnect(self) -> None:
        """Cancel background tasks and close the socket."""
        for task in (self._reader_task, self._keepalive_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._reader_task = None
        self._keepalive_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _reader(self) -> None:
        """Background task — read all incoming messages and dispatch by req_id."""
        try:
            async for raw in self._ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                req_id = data.get("req_id")
                if req_id is not None and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        fut.set_result(data)
        except Exception as exc:
            # Connection dropped — fail all pending requests so callers can retry
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(exc)
            self._pending.clear()
            # Schedule reconnect (don't await here — we're inside the reader task)
            asyncio.create_task(self._reconnect(), name="deriv-reconnect")

    async def _keepalive(self) -> None:
        """Background task — send a ping every _PING_INTERVAL seconds."""
        try:
            while True:
                await asyncio.sleep(self._PING_INTERVAL)
                if self._ws:
                    try:
                        await self._ws.send(json.dumps({"ping": 1}))
                    except Exception:
                        break   # reader will trigger reconnect
        except asyncio.CancelledError:
            pass

    async def _reconnect(self) -> None:
        """Reconnect with exponential back-off (called from the reader task)."""
        async with self._lock:
            await self._disconnect()
            while True:
                try:
                    await self._connect()
                    return
                except Exception:
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2, self._MAX_RECONNECT_DELAY
                    )

    async def _ensure_connected(self) -> None:
        """Lazily connect on first use; reconnect if the socket is gone."""
        if self._ws is not None and self._ws.state == _ws_conn.State.OPEN:
            return
        async with self._lock:
            if self._ws is not None and self._ws.state == _ws_conn.State.OPEN:
                return
            await self._disconnect()
            await self._connect()

    # ── Public API ────────────────────────────────────────────────────────────

    async def request(self, payload: dict, retries: int = 2) -> dict:
        """Send a request and return the response.

        Uses the persistent connection.  Falls back to a new connection on
        failure (up to *retries* times) to handle transient drops.
        """
        last_exc: Exception = RuntimeError("DerivConnection.request: no attempts made")
        for attempt in range(retries + 1):
            try:
                await self._ensure_connected()

                self._req_id += 1
                req_id = self._req_id
                payload = dict(payload, req_id=req_id)

                loop = asyncio.get_event_loop()
                fut: asyncio.Future = loop.create_future()
                self._pending[req_id] = fut

                await self._ws.send(json.dumps(payload))
                data = await asyncio.wait_for(
                    asyncio.shield(fut), timeout=self._RECV_TIMEOUT
                )
                return data

            except Exception as exc:
                last_exc = exc
                self._pending.pop(self._req_id, None)
                # Force reconnect before the next attempt
                async with self._lock:
                    await self._disconnect()
                if attempt < retries:
                    await asyncio.sleep(2 ** attempt)
                continue

        raise last_exc


# Process-level singleton — one warm connection for the whole server lifetime
_deriv = DerivConnection(DERIV_WS_URL)


async def deriv_request(payload: dict, _retries: int = 2) -> dict:
    """Send a request to Deriv via the persistent warm connection.

    Drop-in replacement for the old per-call open/close approach.
    Error responses from Deriv are returned as-is (dict with "error" key)
    so callers can handle them the same way as before.
    """
    data = await _deriv.request(payload, retries=_retries)
    return data


async def fetch_candles(symbol: str, granularity: int, count: int) -> list[dict]:
    """Fetch OHLCV candles from Deriv and return list of dicts."""
    resp = await deriv_request({
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count": count,
        "end": "latest",
        "granularity": granularity,
        "style": "candles"
    })
    if "error" in resp:
        raise ValueError(resp["error"].get("message", str(resp["error"])))
    return resp.get("candles", [])


# ── Technical Indicator Math (pure Python) ────────────────────────────────────

def _ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average (EMA) using multiplier k=2/(period+1)."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _sma(values: list[float], period: int) -> list[float]:
    """Simple Moving Average."""
    return [sum(values[i:i+period]) / period for i in range(len(values) - period + 1)]


def _rsi_series_incremental(closes: list[float], period: int) -> list[float]:
    """
    Compute full RSI series in O(n) using a single Wilder's smoothing pass.
    Returns a list of RSI values aligned with closes[period:] (same length).
    Replaces the O(n²) loop that called calc_rsi(closes[:end]) for each end.
    """
    if len(closes) < period + 1:
        return []
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _val(g: float, l: float) -> float:
        if l == 0:
            return 100.0
        return round(100 - 100 / (1 + g / l), 2)

    result = [_val(avg_gain, avg_loss)]
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result.append(_val(avg_gain, avg_loss))
    return result


def _macd_histogram_series(closes: list[float],
                            fast: int = 12, slow: int = 26, signal: int = 9
                            ) -> tuple[list[float], list[float]]:
    """
    Compute full MACD histogram series + aligned close prices in O(n) — one pass.
    Returns (hist_series, price_series) with identical lengths, aligned by index.
    Replaces the O(n²) loop that called calc_macd(closes[:end]) for each end.
    """
    if len(closes) < slow + signal:
        return [], []
    ema_fast  = _ema(closes, fast)
    ema_slow  = _ema(closes, slow)
    offset    = slow - fast
    macd_line = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
    if len(macd_line) < signal:
        return [], []
    sig_line    = _ema(macd_line, signal)
    hist_series = [macd_line[i + signal - 1] - sig_line[i] for i in range(len(sig_line))]
    # hist_series[i] aligns with closes index (slow-1) + (signal-1) + i
    price_start  = (slow - 1) + (signal - 1)
    price_series = closes[price_start: price_start + len(hist_series)]
    return hist_series, price_series


def _chart_payload(
    symbol: str,
    granularity: int,
    candles: list[dict],
    overlays: list[dict] | None = None,
    panels: list[dict] | None = None,
) -> dict:
    """Build chart data from the same candles used by the MCP tool."""
    return {
        "symbol": symbol,
        "granularity": granularity,
        "timeframe": GRAN_LABEL.get(granularity, f"{granularity}s"),
        "candles": [
            {
                "time": int(c["epoch"]),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            }
            for c in candles
            if all(key in c for key in ("epoch", "open", "high", "low", "close"))
        ],
        "overlays": overlays or [],
        "panels": panels or [],
    }


def _series_points(candles: list[dict], values: list[float], start_index: int) -> list[dict]:
    """Align a calculated series with candle timestamps."""
    points = []
    for index, value in enumerate(values):
        candle_index = start_index + index
        if candle_index >= len(candles) or value is None:
            continue
        points.append({
            "time": int(candles[candle_index]["epoch"]),
            "value": float(value),
        })
    return points


def _chart_text(text: str, chart: dict) -> str:
    """Keep readable output and append a machine-readable chart payload."""
    return f"{text}\n{CHART_MARKER}{json.dumps(chart, separators=(',', ':'))}"


def _bbands_series(
    closes: list[float], period: int, std_mult: float
) -> tuple[list[float], list[float], list[float]]:
    """Return aligned upper, middle and lower Bollinger Band series."""
    upper, middle, lower = [], [], []
    for end in range(period, len(closes) + 1):
        window = closes[end - period:end]
        mean = sum(window) / period
        variance = sum((value - mean) ** 2 for value in window) / period
        deviation = math.sqrt(variance)
        middle.append(mean)
        upper.append(mean + std_mult * deviation)
        lower.append(mean - std_mult * deviation)
    return upper, middle, lower


def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI. Returns latest RSI value."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calc_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
              ) -> tuple[float, float, float] | None:
    """MACD line, signal line, histogram. Returns (macd, signal, histogram)."""
    if len(closes) < slow + signal:
        return None
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    # Align: ema_fast starts at index fast-1, ema_slow at slow-1
    offset = slow - fast
    macd_line = [ema_fast[i + offset] - ema_slow[i] for i in range(len(ema_slow))]
    if len(macd_line) < signal:
        return None
    signal_line = _ema(macd_line, signal)
    hist = macd_line[-1] - signal_line[-1]
    return round(macd_line[-1], 5), round(signal_line[-1], 5), round(hist, 5)


def calc_bbands(closes: list[float], period: int = 20, std_mult: float = 2.0
                ) -> tuple[float, float, float] | None:
    """Bollinger Bands. Returns (upper, middle, lower)."""
    if len(closes) < period:
        return None
    recent = closes[-period:]
    mid = sum(recent) / period
    variance = sum((x - mid) ** 2 for x in recent) / period
    std = math.sqrt(variance)
    return round(mid + std_mult * std, 5), round(mid, 5), round(mid - std_mult * std, 5)


def calc_ema_series(closes: list[float], period: int) -> float | None:
    """Latest EMA value for given period."""
    result = _ema(closes, period)
    return round(result[-1], 5) if result else None


def calc_sma_series(closes: list[float], period: int) -> float | None:
    """Latest SMA value for given period."""
    result = _sma(closes, period)
    return round(result[-1], 5) if result else None


def calc_atr(highs: list[float], lows: list[float], closes: list[float],
             period: int = 14) -> float | None:
    """Wilder's ATR. Returns latest ATR value."""
    if len(closes) < period + 1:
        return None
    tr_list = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        tr_list.append(tr)

    atr = sum(tr_list[:period]) / period
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
    return round(atr, 5)


def calc_stoch(highs: list[float], lows: list[float], closes: list[float],
               k_period: int = 14, d_period: int = 3) -> tuple[float, float] | None:
    """Stochastic %K and %D. Returns (k, d)."""
    if len(closes) < k_period + d_period:
        return None
    k_values = []
    for i in range(k_period - 1, len(closes)):
        lo = min(lows[i - k_period + 1: i + 1])
        hi = max(highs[i - k_period + 1: i + 1])
        if hi == lo:
            k_values.append(50.0)
        else:
            k_values.append(100 * (closes[i] - lo) / (hi - lo))

    d = sum(k_values[-d_period:]) / d_period
    return round(k_values[-1], 2), round(d, 2)


def candles_to_ohlcv(candles: list[dict]) -> tuple[list, list, list, list]:
    """Extract float arrays from Deriv candle dicts."""
    opens  = [float(c["open"])  for c in candles]
    highs  = [float(c["high"])  for c in candles]
    lows   = [float(c["low"])   for c in candles]
    closes = [float(c["close"]) for c in candles]
    return opens, highs, lows, closes


# ── Professional Indicator Functions ─────────────────────────────────────────

def calc_adx(highs: list[float], lows: list[float], closes: list[float],
             period: int = 14) -> dict | None:
    """
    ADX (Average Directional Index) — Wilder's method.
    Returns dict: {adx, plus_di, minus_di, trending, strength}
    ADX > 25 = trending market, ADX < 20 = ranging/sideways.
    +DI > -DI = bullish trend, -DI > +DI = bearish trend.
    """
    if len(closes) < period * 2 + 1:
        return None

    plus_dm_list, minus_dm_list, tr_list = [], [], []
    for i in range(1, len(closes)):
        h_diff = highs[i] - highs[i - 1]
        l_diff = lows[i - 1] - lows[i]
        plus_dm_list.append(h_diff if h_diff > l_diff and h_diff > 0 else 0.0)
        minus_dm_list.append(l_diff if l_diff > h_diff and l_diff > 0 else 0.0)
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_list.append(tr)

    def _wilder_smooth(values, p):
        result = [sum(values[:p])]
        for v in values[p:]:
            result.append(result[-1] - result[-1] / p + v)
        return result

    atr_s    = _wilder_smooth(tr_list, period)
    plus_s   = _wilder_smooth(plus_dm_list, period)
    minus_s  = _wilder_smooth(minus_dm_list, period)

    dx_list = []
    for a, p, m in zip(atr_s, plus_s, minus_s):
        plus_di  = 100 * p / a if a else 0
        minus_di = 100 * m / a if a else 0
        di_sum = plus_di + minus_di
        dx_list.append(100 * abs(plus_di - minus_di) / di_sum if di_sum else 0)

    adx = sum(dx_list[-period:]) / period
    last_atr   = atr_s[-1]
    plus_di    = round(100 * plus_s[-1] / last_atr, 2) if last_atr else 0
    minus_di   = round(100 * minus_s[-1] / last_atr, 2) if last_atr else 0
    adx        = round(adx, 2)

    if adx >= 40:    strength = "Sangat Kuat"
    elif adx >= 25:  strength = "Trending"
    elif adx >= 15:  strength = "Lemah"
    else:            strength = "Sideways/Ranging"

    return {
        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "trending": adx >= 20,
        "strength": strength,
        "di_bias": "bullish" if plus_di > minus_di else "bearish"
    }


def calc_swing_points(highs: list[float], lows: list[float],
                      lookback: int = 5) -> dict:
    """
    Detect swing high/low points using a rolling window.
    Returns recent swing points for market structure & S/R analysis.
    """
    n = len(highs)
    swing_highs = []  # (index, price)
    swing_lows  = []  # (index, price)

    for i in range(lookback, n - lookback):
        window_h = highs[i - lookback: i + lookback + 1]
        window_l = lows[i - lookback:  i + lookback + 1]
        if highs[i] == max(window_h):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(window_l):
            swing_lows.append((i, lows[i]))

    return {"highs": swing_highs[-8:], "lows": swing_lows[-8:]}


def calc_market_structure(swing_highs: list, swing_lows: list) -> dict:
    """
    Determine market structure from swing points.
    BOS = Break of Structure, CHoCH = Change of Character.
    Returns: structure (bullish/bearish/ranging), last 3 HH/HL or LH/LL.
    """
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return {"structure": "insufficient_data", "label": "Data tidak cukup", "detail": []}

    sh_prices = [p for _, p in swing_highs]
    sl_prices = [p for _, p in swing_lows]

    hh = sh_prices[-1] > sh_prices[-2]  # Higher High
    hl = sl_prices[-1] > sl_prices[-2]  # Higher Low
    lh = sh_prices[-1] < sh_prices[-2]  # Lower High
    ll = sl_prices[-1] < sl_prices[-2]  # Lower Low

    details = []
    if hh: details.append("HH ✅")
    if hl: details.append("HL ✅")
    if lh: details.append("LH ⚠️")
    if ll: details.append("LL ⚠️")

    if hh and hl:
        structure = "bullish"
        label = "🟢 BULLISH STRUCTURE — Higher Highs & Higher Lows"
    elif lh and ll:
        structure = "bearish"
        label = "🔴 BEARISH STRUCTURE — Lower Highs & Lower Lows"
    elif hh and ll:
        structure = "ranging"
        label = "⚖️ RANGING — Expanding range (HH + LL)"
    elif lh and hl:
        structure = "ranging"
        label = "⚖️ RANGING — Contracting (LH + HL)"
    else:
        structure = "ranging"
        label = "⚖️ MIXED — No clear structure"

    return {"structure": structure, "label": label, "detail": details,
            "last_swing_high": sh_prices[-1], "last_swing_low": sl_prices[-1],
            "prev_swing_high": sh_prices[-2], "prev_swing_low": sl_prices[-2]}


def calc_support_resistance(swing_highs: list, swing_lows: list,
                             current_price: float, tolerance: float = 0.002) -> dict:
    """
    Auto-detect key S/R levels from swing points.
    Clusters nearby levels (within tolerance %) into single zone.
    Returns nearest support below and resistance above current price.
    """
    all_levels = [p for _, p in swing_highs] + [p for _, p in swing_lows]
    if not all_levels:
        return {"support": None, "resistance": None, "zones": []}

    # Cluster nearby levels
    all_levels.sort()
    clusters = []
    group = [all_levels[0]]
    for lvl in all_levels[1:]:
        if abs(lvl - group[-1]) / group[-1] <= tolerance:
            group.append(lvl)
        else:
            clusters.append(round(sum(group) / len(group), 5))
            group = [lvl]
    clusters.append(round(sum(group) / len(group), 5))

    supports    = sorted([c for c in clusters if c < current_price], reverse=True)
    resistances = sorted([c for c in clusters if c > current_price])

    return {
        "support":    supports[0]    if supports    else None,
        "resistance": resistances[0] if resistances else None,
        "support2":   supports[1]    if len(supports) > 1    else None,
        "resistance2":resistances[1] if len(resistances) > 1 else None,
        "all_zones":  clusters
    }


def calc_rsi_divergence(highs: list[float], lows: list[float],
                        closes: list[float], period: int = 14) -> dict:
    """
    Detect RSI divergence (bullish / bearish).
    Bullish divergence: price makes lower low, RSI makes higher low → reversal up.
    Bearish divergence: price makes higher high, RSI makes lower high → reversal down.
    Uses _rsi_series_incremental for O(n) performance instead of O(n²).
    """
    if len(closes) < period + 20:
        return {"bullish": False, "bearish": False, "type": "none", "detail": ""}

    window     = min(len(closes), 80)
    sub        = closes[-window:]
    # O(n) — single Wilder smoothing pass over `sub`
    rsi_series = _rsi_series_incremental(sub, period)
    # rsi_series[i] aligns with sub[period + i], i.e. sub[period:]
    price_sub  = sub[period:]

    if len(rsi_series) < 10:
        return {"bullish": False, "bearish": False, "type": "none", "detail": ""}

    n    = len(rsi_series)   # == len(price_sub)
    look = min(30, n - 5)

    p_lows  = [(i, price_sub[i]) for i in range(n - look, n - 3)
               if price_sub[i] < price_sub[i - 1] and price_sub[i] < price_sub[i + 1]]
    p_highs = [(i, price_sub[i]) for i in range(n - look, n - 3)
               if price_sub[i] > price_sub[i - 1] and price_sub[i] > price_sub[i + 1]]

    bullish_div = False
    bearish_div = False
    detail      = ""

    if len(p_lows) >= 2:
        i1, p1 = p_lows[-2]
        i2, p2 = p_lows[-1]
        r1 = rsi_series[i1] if i1 < n else None
        r2 = rsi_series[i2] if i2 < n else None
        if r1 and r2 and p2 < p1 and r2 > r1:
            bullish_div = True
            detail = f"Harga LL ({p1:.3f}→{p2:.3f}), RSI HL ({r1:.1f}→{r2:.1f})"

    if len(p_highs) >= 2:
        i1, p1 = p_highs[-2]
        i2, p2 = p_highs[-1]
        r1 = rsi_series[i1] if i1 < n else None
        r2 = rsi_series[i2] if i2 < n else None
        if r1 and r2 and p2 > p1 and r2 < r1:
            bearish_div = True
            detail = f"Harga HH ({p1:.3f}→{p2:.3f}), RSI LH ({r1:.1f}→{r2:.1f})"

    div_type = "bullish" if bullish_div else ("bearish" if bearish_div else "none")
    return {"bullish": bullish_div, "bearish": bearish_div, "type": div_type, "detail": detail}


def calc_macd_divergence(highs: list[float], lows: list[float],
                         closes: list[float]) -> dict:
    """
    Detect MACD histogram divergence.
    Bullish: price LL + histogram HL → momentum diverging up.
    Bearish: price HH + histogram LH → momentum diverging down.
    Uses _macd_histogram_series for O(n) performance instead of O(n²).
    """
    if len(closes) < 60:
        return {"bullish": False, "bearish": False, "type": "none", "detail": ""}

    # O(n) — single EMA pass, no per-candle calc_macd loop
    hist_series, price_series = _macd_histogram_series(closes, 12, 26, 9)

    if len(hist_series) < 10:
        return {"bullish": False, "bearish": False, "type": "none", "detail": ""}

    n    = len(hist_series)
    look = min(20, n - 3)

    p_lows  = [(i, price_series[i]) for i in range(n - look, n - 2)
               if price_series[i] < price_series[i - 1] and price_series[i] < price_series[i + 1]]
    p_highs = [(i, price_series[i]) for i in range(n - look, n - 2)
               if price_series[i] > price_series[i - 1] and price_series[i] > price_series[i + 1]]

    bullish_div = False
    bearish_div = False
    detail      = ""

    if len(p_lows) >= 2:
        i1, p1 = p_lows[-2]; i2, p2 = p_lows[-1]
        h1 = hist_series[i1]; h2 = hist_series[i2]
        if p2 < p1 and h2 > h1:
            bullish_div = True
            detail = f"Harga LL, MACD Hist naik ({h1:.4f}→{h2:.4f})"

    if len(p_highs) >= 2:
        i1, p1 = p_highs[-2]; i2, p2 = p_highs[-1]
        h1 = hist_series[i1]; h2 = hist_series[i2]
        if p2 > p1 and h2 < h1:
            bearish_div = True
            detail = f"Harga HH, MACD Hist turun ({h1:.4f}→{h2:.4f})"

    div_type = "bullish" if bullish_div else ("bearish" if bearish_div else "none")
    return {"bullish": bullish_div, "bearish": bearish_div, "type": div_type, "detail": detail}


def smart_sl_snap(bias: str, current_price: float,
                  swing_highs: list, swing_lows: list,
                  atr: float, max_atr_mult: float = 3.0) -> float:
    """
    Smart SL: snap to nearest swing point instead of fixed ATR multiple.
    - BUY: SL = nearest swing low below entry (max 3× ATR away)
    - SELL: SL = nearest swing high above entry (max 3× ATR away)
    Falls back to 1.5× ATR if no suitable swing found.
    """
    fallback_sl = (current_price - atr * 1.5) if bias == "BUY" else (current_price + atr * 1.5)
    max_dist = atr * max_atr_mult

    if bias == "BUY":
        candidates = sorted(
            [p for _, p in swing_lows if p < current_price and (current_price - p) <= max_dist],
            reverse=True
        )
        if candidates:
            # Add small buffer below swing low
            sl = round(candidates[0] - atr * 0.2, 5)
            return sl
    else:
        candidates = sorted(
            [p for _, p in swing_highs if p > current_price and (p - current_price) <= max_dist]
        )
        if candidates:
            sl = round(candidates[0] + atr * 0.2, 5)
            return sl

    return round(fallback_sl, 5)


def detect_candlestick_patterns(opens: list[float], highs: list[float],
                                 lows: list[float], closes: list[float]) -> list[str]:
    """
    Detect common candlestick patterns on the last 2 candles.
    Returns list of pattern names found. Empty list = no pattern.
    """
    if len(closes) < 3:
        return []

    patterns = []

    # Last candle values
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body     = abs(c - o)
    candle_range = h - l
    upper_wick   = h - max(o, c)
    lower_wick   = min(o, c) - l
    bull_candle  = c > o

    if candle_range == 0:
        return []

    body_ratio  = body / candle_range
    upper_ratio = upper_wick / candle_range
    lower_ratio = lower_wick / candle_range

    # Doji — body very small vs range
    if body_ratio < 0.1:
        patterns.append("⚖️ DOJI — ketidakpastian, tunggu konfirmasi")

    # Pin Bar / Hammer / Shooting Star
    elif lower_ratio >= 0.55 and body_ratio < 0.35:
        if bull_candle or (not bull_candle and lower_ratio > 0.6):
            patterns.append("🔨 HAMMER / PIN BAR BULLISH — ekor bawah panjang, potensi reversal naik")
    elif upper_ratio >= 0.55 and body_ratio < 0.35:
        patterns.append("⭐ SHOOTING STAR / PIN BAR BEARISH — ekor atas panjang, potensi reversal turun")

    # Marubozu — strong body, tiny wicks
    elif body_ratio >= 0.85:
        if bull_candle:
            patterns.append("🟢 MARUBOZU BULLISH — momentum beli sangat kuat")
        else:
            patterns.append("🔴 MARUBOZU BEARISH — momentum jual sangat kuat")

    # Engulfing — requires 2 candles
    if len(closes) >= 2:
        po, ph, pl, pc = opens[-2], highs[-2], lows[-2], closes[-2]
        prev_bull = pc > po
        # Bullish engulfing: prev bearish, current bullish engulfs it
        if not prev_bull and bull_candle and o <= pc and c >= po:
            patterns.append("✅ BULLISH ENGULFING — candle beli menelan candle jual sebelumnya")
        # Bearish engulfing: prev bullish, current bearish engulfs it
        elif prev_bull and not bull_candle and o >= pc and c <= po:
            patterns.append("⚠️ BEARISH ENGULFING — candle jual menelan candle beli sebelumnya")

    return patterns if patterns else ["— Tidak ada pola candlestick kuat saat ini"]


def classify_market_regime(d1_data: dict, h4_data: dict, h1_data: dict,
                            current_price: float) -> dict:
    """
    Classify current market regime from multi-TF analysis.
    Returns: regime type, recommended strategy, what to look for, what invalidates it.

    Regimes:
    - STRONG_TREND_BULL  : ADX>35 D1 + bullish structure + EMA stack aligned
    - STRONG_TREND_BEAR  : ADX>35 D1 + bearish structure + EMA stack aligned
    - PULLBACK_BUY       : D1 bull trend, H4/H1 pulling back to support zone
    - PULLBACK_SELL      : D1 bear trend, H4/H1 bouncing up to resistance zone
    - REVERSAL_BUY       : Bearish trend but divergence + oversold + near strong support
    - REVERSAL_SELL      : Bullish trend but divergence + overbought + near strong resistance
    - RANGING            : ADX<20 on D1+H4, price between S/R
    - BREAKOUT_WATCH     : BB squeeze, ADX rising, near key level
    """
    d1_adx  = d1_data.get("adx") or {}
    h4_adx  = h4_data.get("adx") or {}
    h1_adx  = h1_data.get("adx") or {}

    d1_adx_val  = d1_adx.get("adx", 0)
    d1_trending = d1_adx.get("trending", False)
    d1_di_bias  = d1_adx.get("di_bias", "neutral")
    d1_struct   = d1_data.get("market_structure", {}).get("structure", "ranging")
    h4_struct   = h4_data.get("market_structure", {}).get("structure", "ranging")

    d1_rsi  = d1_data.get("rsi") or 50
    h1_rsi  = h1_data.get("rsi") or 50
    d1_ema9  = d1_data.get("ema9")
    d1_ema21 = d1_data.get("ema21")
    d1_ema50 = d1_data.get("ema50")
    d1_ema200 = d1_data.get("ema200")

    h1_sr   = h1_data.get("sr", {})
    h4_sr   = h4_data.get("sr", {})
    support    = h1_sr.get("support")
    resistance = h1_sr.get("resistance")

    any_bull_div = any(
        tf.get("rsi_div", {}).get("bullish") or tf.get("macd_div", {}).get("bullish")
        for tf in [d1_data, h4_data, h1_data]
    )
    any_bear_div = any(
        tf.get("rsi_div", {}).get("bearish") or tf.get("macd_div", {}).get("bearish")
        for tf in [d1_data, h4_data, h1_data]
    )

    ema_bull_stack = d1_ema9 and d1_ema21 and d1_ema50 and d1_ema9 > d1_ema21 > d1_ema50
    ema_bear_stack = d1_ema9 and d1_ema21 and d1_ema50 and d1_ema9 < d1_ema21 < d1_ema50
    above_ema200   = d1_ema200 and current_price > d1_ema200

    near_support    = support    and abs(current_price - support)    / current_price < 0.004
    near_resistance = resistance and abs(current_price - resistance) / current_price < 0.004

    # ── Classify ──────────────────────────────────────────────────────────────

    # 1. RANGING — D1 sideways, ADX weak
    if d1_adx_val < 20 and not d1_trending:
        if h4_adx.get("adx", 0) < 22:
            # Check for BB squeeze potential (handled by caller)
            regime     = "RANGING"
            icon       = "⚖️"
            name       = "RANGING MARKET"
            strategy   = "Mean Reversion — Buy near Support, Sell near Resistance"
            approach   = (
                "Pasar sedang sideways. JANGAN kejar breakout, tunggu harga menyentuh tepi range.\n"
                "• BUY bila harga mendekati Support + RSI < 40 + Stoch oversold\n"
                "• SELL bila harga mendekati Resistance + RSI > 60 + Stoch overbought\n"
                "• SL di luar range, TP di tengah atau sisi berlawanan range"
            )
            watch_for  = "RSI extreme di S/R + candlestick reversal (pin bar, engulfing)"
            invalidate = "ADX naik di atas 25 = range breakout sedang berlaku"
            return {"regime": regime, "icon": icon, "name": name,
                    "strategy": strategy, "approach": approach,
                    "watch_for": watch_for, "invalidate": invalidate}

    # 2. STRONG TREND BULL — ADX kuat, semua TF align bullish
    if d1_adx_val >= 35 and d1_di_bias == "bullish" and d1_struct == "bullish" and ema_bull_stack:
        regime   = "STRONG_TREND_BULL"
        icon     = "🚀"
        name     = "STRONG BULLISH TREND"
        strategy = "Trend Following — Entry on pullback ke EMA, jangan melawan tren"
        approach = (
            "Tren kuat ke atas. JANGAN SELL di trending market yang kuat.\n"
            "• Tunggu pullback ke EMA21 atau EMA50 (jika H4/H1 dip)\n"
            "• Konfirmasi: H1 RSI bounce dari 40-50 zone + candle bullish\n"
            "• SL di bawah EMA50 atau swing low H4\n"
            "• TP 1: swing high terakhir, TP 2: extended move (2-3× ATR)"
        )
        watch_for  = "Pullback H4 ke EMA21/EMA50 + H1 reversal candle"
        invalidate = f"Harga close di bawah EMA50 D1 ({d1_ema50}) = tren melemah"
        return {"regime": regime, "icon": icon, "name": name,
                "strategy": strategy, "approach": approach,
                "watch_for": watch_for, "invalidate": invalidate}

    # 3. STRONG TREND BEAR — ADX kuat, semua TF align bearish
    if d1_adx_val >= 35 and d1_di_bias == "bearish" and d1_struct == "bearish" and ema_bear_stack:
        regime   = "STRONG_TREND_BEAR"
        icon     = "📉"
        name     = "STRONG BEARISH TREND"
        strategy = "Trend Following — Entry on bounce ke EMA, jangan melawan tren"
        approach = (
            "Tren kuat ke bawah. JANGAN BUY kecuali ada reversal signal kuat.\n"
            "• Tunggu bounce ke EMA21 atau EMA50 (H4/H1 retest resistance)\n"
            "• Konfirmasi: H1 RSI bounce dari 50-60 zone + bearish candle\n"
            "• SL di atas EMA50 atau swing high H4\n"
            "• TP 1: swing low terakhir, TP 2: extended move (2-3× ATR)"
        )
        watch_for  = "Bounce H4 ke EMA21/EMA50 + H1 bearish reversal candle"
        invalidate = f"Harga close di atas EMA50 D1 ({d1_ema50}) = tren membalik"
        return {"regime": regime, "icon": icon, "name": name,
                "strategy": strategy, "approach": approach,
                "watch_for": watch_for, "invalidate": invalidate}

    # 4. REVERSAL BUY — trend bearish tapi divergence bullish + oversold
    if d1_struct == "bearish" and any_bull_div and h1_rsi < 40:
        regime   = "REVERSAL_BUY"
        icon     = "🔄"
        name     = "POTENTIAL BULLISH REVERSAL"
        sl_hint  = f"di bawah support {support}" if support else "di bawah swing low H1"
        approach = (
            "Tren turun tapi momentum melemah — divergence bullish terdeteksi.\n"
            "• Ini setup KONTRA-TREND — risiko lebih tinggi, konfirmasi wajib\n"
            "• Tunggu candlestick bullish (pin bar, engulfing) di level support\n"
            "• Entry kecil dulu (50% normal size), tambah bila dikonfirmasi\n"
            f"• SL {sl_hint}\n"
            "• TP 1: resistance terdekat, TP 2: EMA21/EMA50"
        )
        watch_for  = "Pin bar / bullish engulfing di support + volume spike"
        invalidate = f"Harga tutup di bawah support {support} = reversal gagal" if support else "Harga buat low baru = reversal gagal"
        return {"regime": regime, "icon": icon, "name": name,
                "strategy": "Counter-Trend Reversal — High Risk/Reward",
                "approach": approach, "watch_for": watch_for, "invalidate": invalidate}

    # 5. REVERSAL SELL — trend bullish tapi divergence bearish + overbought
    if d1_struct == "bullish" and any_bear_div and h1_rsi > 60:
        regime   = "REVERSAL_SELL"
        icon     = "🔄"
        name     = "POTENTIAL BEARISH REVERSAL"
        res_hint = f"di atas resistance {resistance}" if resistance else "di atas swing high H1"
        approach = (
            "Tren naik tapi momentum melemah — divergence bearish terdeteksi.\n"
            "• Ini setup KONTRA-TREND — risiko lebih tinggi, konfirmasi wajib\n"
            "• Tunggu candlestick bearish (pin bar, engulfing) di level resistance\n"
            "• Entry kecil dulu (50% normal size)\n"
            f"• SL {res_hint}\n"
            "• TP 1: support terdekat, TP 2: EMA21/EMA50"
        )
        watch_for  = "Shooting star / bearish engulfing di resistance + RSI divergence"
        invalidate = f"Harga close di atas resistance {resistance} = reversal gagal" if resistance else "Harga buat high baru = reversal gagal"
        return {"regime": regime, "icon": icon, "name": name,
                "strategy": "Counter-Trend Reversal — High Risk/Reward",
                "approach": approach, "watch_for": watch_for, "invalidate": invalidate}

    # 6. PULLBACK BUY — D1 bull trend, H1/H4 pullback zone
    if d1_struct == "bullish" and d1_trending and h1_rsi < 55:
        regime   = "PULLBACK_BUY"
        icon     = "📈"
        name     = "PULLBACK IN BULL TREND"
        ema_hint = f"EMA21={d1_ema21}" if d1_ema21 else "EMA zona"
        approach = (
            "Tren utama naik. Harga sedang pullback — ini peluang entry searah tren.\n"
            f"• Tunggu harga test {ema_hint} atau support {support or 'terdekat'}\n"
            "• Konfirmasi H1: RSI bounce 40-50 + candlestick bullish\n"
            "• SL di bawah swing low H1 atau EMA50\n"
            "• TP di swing high D1 terbaru atau resistance"
        )
        watch_for  = f"H1 RSI masuk zona 40-50 + pin bar bullish di support/EMA"
        invalidate = f"H4 close di bawah EMA50 = pullback jadi reversal"
        return {"regime": regime, "icon": icon, "name": name,
                "strategy": "Trend Pullback — Buy the Dip (Lower Risk)",
                "approach": approach, "watch_for": watch_for, "invalidate": invalidate}

    # 7. PULLBACK SELL — D1 bear trend, H1/H4 bounce zone
    if d1_struct == "bearish" and d1_trending and h1_rsi > 45:
        regime   = "PULLBACK_SELL"
        icon     = "📉"
        name     = "PULLBACK IN BEAR TREND"
        ema_hint = f"EMA21={d1_ema21}" if d1_ema21 else "EMA zona"
        approach = (
            "Tren utama turun. Harga sedang bounce — ini peluang entry searah tren.\n"
            f"• Tunggu harga test {ema_hint} atau resistance {resistance or 'terdekat'}\n"
            "• Konfirmasi H1: RSI masuk 50-60 + candlestick bearish\n"
            "• SL di atas swing high H1 atau EMA50\n"
            "• TP di swing low D1 terbaru atau support"
        )
        watch_for  = f"H1 RSI masuk zona 50-60 + pin bar bearish di resistance/EMA"
        invalidate = f"H4 close di atas EMA50 = bounce jadi reversal"
        return {"regime": regime, "icon": icon, "name": name,
                "strategy": "Trend Pullback — Sell the Rally (Lower Risk)",
                "approach": approach, "watch_for": watch_for, "invalidate": invalidate}

    # 8. Default — transitional/unclear
    return {
        "regime":    "TRANSITIONAL",
        "icon":      "🌫️",
        "name":      "TRANSITIONAL / UNCLEAR",
        "strategy":  "Wait & Observe — belum ada setup yang jelas",
        "approach":  (
            "Kondisi market belum jelas. Ini bukan waktu yang baik untuk masuk.\n"
            "• Tunggu ADX naik di atas 20 (trending) ATAU harga menyentuh S/R yang kuat\n"
            "• Jangan FOMO — setup yang bagus akan datang\n"
            "• Gunakan waktu ini untuk mark level S/R di chart"
        ),
        "watch_for":  "ADX > 20 + structure breakout + volume konfirmasi",
        "invalidate": "N/A — tunggu dulu sebelum ambil posisi"
    }


def grade_setup_quality(regime: str, confluence_pct: float, has_pattern: bool,
                         has_divergence: bool, near_sr: bool,
                         d1_trending: bool, tf_alignment: int) -> dict:
    """
    Grade setup quality A/B/C/D.
    A = Semua faktor aligned, masuk dengan normal size
    B = Kebanyakan aligned, masuk dengan 75% size
    C = Setup lemah, masuk dengan 50% size atau skip
    D = Jangan masuk
    """
    score = 0

    # Confluence score
    conf_dist = abs(confluence_pct - 50)
    if conf_dist >= 30:   score += 3
    elif conf_dist >= 20: score += 2
    elif conf_dist >= 10: score += 1

    # Regime quality
    if regime in ("STRONG_TREND_BULL", "STRONG_TREND_BEAR"): score += 3
    elif regime in ("PULLBACK_BUY", "PULLBACK_SELL"):         score += 3
    elif regime in ("REVERSAL_BUY", "REVERSAL_SELL"):         score += 1
    elif regime == "RANGING":                                  score += 2
    else:                                                      score += 0

    # Candlestick pattern at key level
    if has_pattern and near_sr: score += 2
    elif has_pattern:           score += 1

    # Divergence adds conviction for reversals
    if has_divergence: score += 2

    # D1 trending = higher quality
    if d1_trending: score += 1

    # TF alignment (how many TFs agree)
    if tf_alignment >= 2: score += 2
    elif tf_alignment == 1: score += 1

    if score >= 11:
        grade = "A"; label = "🏆 Setup Grade A — Entry dengan full size (risiko normal)"; size_note = "Full position size"
    elif score >= 8:
        grade = "B"; label = "✅ Setup Grade B — Entry dengan 75% size"; size_note = "75% position size"
    elif score >= 5:
        grade = "C"; label = "⚠️ Setup Grade C — Entry dengan 50% size atau skip"; size_note = "50% position size atau skip"
    else:
        grade = "D"; label = "❌ Setup Grade D — SKIP, tunggu setup lebih baik"; size_note = "Jangan masuk"

    return {"grade": grade, "label": label, "score": score, "size_note": size_note}


# ── New Indicator Math ────────────────────────────────────────────────────────

def calc_fibonacci(swing_high: float, swing_low: float, trend: str = "up") -> dict:
    """Fibonacci retracement and extension levels from swing high/low."""
    diff = swing_high - swing_low
    retrace_pcts = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    ext_pcts      = [1.272, 1.618, 2.0, 2.618]
    if trend == "up":
        retracements = {f"{r*100:.1f}%": round(swing_high - diff * r, 5) for r in retrace_pcts}
        extensions   = {f"{e*100:.1f}%": round(swing_high + diff * (e - 1.0), 5) for e in ext_pcts}
    else:
        retracements = {f"{r*100:.1f}%": round(swing_low + diff * r, 5) for r in retrace_pcts}
        extensions   = {f"{e*100:.1f}%": round(swing_low - diff * (e - 1.0), 5) for e in ext_pcts}
    return {"retracements": retracements, "extensions": extensions}


def calc_pivot_points(high: float, low: float, close: float) -> dict:
    """Classic pivot points (daily or weekly) from previous period H/L/C."""
    pp = (high + low + close) / 3.0
    r1 = 2 * pp - low;  s1 = 2 * pp - high
    r2 = pp + (high - low); s2 = pp - (high - low)
    r3 = high + 2 * (pp - low); s3 = low - 2 * (high - pp)
    return {
        "pp": round(pp, 5),
        "r1": round(r1, 5), "r2": round(r2, 5), "r3": round(r3, 5),
        "s1": round(s1, 5), "s2": round(s2, 5), "s3": round(s3, 5),
    }


def calc_ichimoku(highs: list[float], lows: list[float], closes: list[float],
                  tenkan: int = 9, kijun: int = 26, senkou_b_period: int = 52) -> dict | None:
    """Ichimoku Kinko Hyo: Tenkan, Kijun, Senkou A/B, Chikou."""
    if len(closes) < senkou_b_period:
        return None
    def mid(h, l): return (max(h) + min(l)) / 2.0
    tenkan_val   = mid(highs[-tenkan:], lows[-tenkan:])
    kijun_val    = mid(highs[-kijun:], lows[-kijun:])
    senkou_a     = (tenkan_val + kijun_val) / 2.0
    senkou_b_val = mid(highs[-senkou_b_period:], lows[-senkou_b_period:])
    chikou       = closes[-1]
    price        = closes[-1]
    cloud_top    = max(senkou_a, senkou_b_val)
    cloud_bot    = min(senkou_a, senkou_b_val)
    if price > cloud_top:
        cloud_signal = "bullish"; cloud_desc = "Price ABOVE cloud — bullish ✅"
    elif price < cloud_bot:
        cloud_signal = "bearish"; cloud_desc = "Price BELOW cloud — bearish ⚠️"
    else:
        cloud_signal = "neutral"; cloud_desc = "Price INSIDE cloud — indecision ⚖️"
    tk_cross = "bullish" if tenkan_val > kijun_val else "bearish"
    return {
        "tenkan": round(tenkan_val, 5), "kijun": round(kijun_val, 5),
        "senkou_a": round(senkou_a, 5), "senkou_b": round(senkou_b_val, 5),
        "chikou": round(chikou, 5), "cloud_top": round(cloud_top, 5),
        "cloud_bot": round(cloud_bot, 5), "cloud_signal": cloud_signal,
        "cloud_desc": cloud_desc, "tk_cross": tk_cross,
    }


def calc_parabolic_sar(highs: list[float], lows: list[float],
                       af_start: float = 0.02, af_step: float = 0.02,
                       af_max: float = 0.2) -> tuple[float, str] | None:
    """Parabolic SAR. Returns (sar_value, trend: 'up'|'down')."""
    if len(highs) < 10:
        return None
    is_up = highs[1] > highs[0]
    af = af_start
    ep = highs[0] if is_up else lows[0]
    sar = lows[0] if is_up else highs[0]
    for i in range(2, len(highs)):
        sar = sar + af * (ep - sar)
        if is_up:
            sar = min(sar, lows[i-1], lows[i-2])
            if lows[i] < sar:
                is_up = False; sar = ep; ep = lows[i]; af = af_start
            else:
                if highs[i] > ep:
                    ep = highs[i]; af = min(af + af_step, af_max)
        else:
            sar = max(sar, highs[i-1], highs[i-2])
            if highs[i] > sar:
                is_up = True; sar = ep; ep = highs[i]; af = af_start
            else:
                if lows[i] < ep:
                    ep = lows[i]; af = min(af + af_step, af_max)
    return (round(sar, 5), "up" if is_up else "down")


def calc_supertrend(highs: list[float], lows: list[float], closes: list[float],
                    period: int = 10, multiplier: float = 3.0) -> tuple[float, str] | None:
    """Supertrend. Returns (value, direction: 'up'|'down')."""
    if len(closes) < period + 2:
        return None
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    if len(trs) < period:
        return None
    atr_v = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr_v = (atr_v * (period - 1) + tr) / period

    n = min(len(closes), period * 8)
    h, l, c = highs[-n:], lows[-n:], closes[-n:]
    trs2 = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(1, len(c))]
    atr2 = sum(trs2[:period]) / period
    for tr in trs2[period:]:
        atr2 = (atr2 * (period-1) + tr) / period

    bu = [(h[i]+l[i])/2 + multiplier*atr2 for i in range(len(c))]
    bl = [(h[i]+l[i])/2 - multiplier*atr2 for i in range(len(c))]
    fu = list(bu); fl = list(bl)
    for i in range(1, len(c)):
        fu[i] = bu[i] if (bu[i] < fu[i-1] or c[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = bl[i] if (bl[i] > fl[i-1] or c[i-1] < fl[i-1]) else fl[i-1]

    st = [0.0] * len(c); dirs = ["up"] * len(c)
    for i in range(1, len(c)):
        if st[i-1] == fu[i-1]:
            st[i]   = fl[i] if c[i] > fu[i] else fu[i]
            dirs[i] = "up"  if c[i] > fu[i] else "down"
        else:
            st[i]   = fu[i] if c[i] < fl[i] else fl[i]
            dirs[i] = "down" if c[i] < fl[i] else "up"
    return (round(st[-1], 5), dirs[-1])


def calc_keltner(highs: list[float], lows: list[float], closes: list[float],
                 ema_period: int = 20, atr_period: int = 10,
                 multiplier: float = 2.0) -> dict | None:
    """Keltner Channel: EMA ± multiplier × ATR."""
    if len(closes) < max(ema_period, atr_period) + 2:
        return None
    k = 2.0 / (ema_period + 1)
    ema = sum(closes[:ema_period]) / ema_period
    for cv in closes[ema_period:]:
        ema = cv * k + ema * (1 - k)
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    atr_v = sum(trs[:atr_period]) / atr_period
    for tr in trs[atr_period:]:
        atr_v = (atr_v * (atr_period-1) + tr) / atr_period
    return {
        "middle": round(ema, 5),
        "upper":  round(ema + multiplier * atr_v, 5),
        "lower":  round(ema - multiplier * atr_v, 5),
        "atr":    round(atr_v, 5),
    }


def calc_donchian(highs: list[float], lows: list[float], period: int = 20) -> dict | None:
    """Donchian Channel: highest high / lowest low over N periods."""
    if len(highs) < period:
        return None
    upper  = max(highs[-period:])
    lower  = min(lows[-period:])
    return {"upper": round(upper, 5), "middle": round((upper+lower)/2, 5), "lower": round(lower, 5)}


def calc_cci(highs: list[float], lows: list[float], closes: list[float],
             period: int = 20) -> float | None:
    """CCI = (TP - SMA_TP) / (0.015 × Mean Absolute Deviation)."""
    if len(closes) < period:
        return None
    tp = [(highs[i]+lows[i]+closes[i])/3.0 for i in range(len(closes))]
    tp_w = tp[-period:]
    sma_tp = sum(tp_w) / period
    mad = sum(abs(x - sma_tp) for x in tp_w) / period
    if mad == 0:
        return 0.0
    return round((tp[-1] - sma_tp) / (0.015 * mad), 2)


def calc_williams_r(highs: list[float], lows: list[float], closes: list[float],
                    period: int = 14) -> float | None:
    """Williams %R = (HH - Close) / (HH - LL) × -100."""
    if len(closes) < period:
        return None
    hh = max(highs[-period:]); ll = min(lows[-period:])
    if hh == ll:
        return -50.0
    return round((hh - closes[-1]) / (hh - ll) * -100, 2)


def calc_heikin_ashi(opens: list[float], highs: list[float], lows: list[float],
                     closes: list[float], analyze_last: int = 10) -> dict | None:
    """Convert OHLC to Heikin Ashi and analyze trend."""
    if len(closes) < 2:
        return None
    ha = []
    for i in range(len(closes)):
        ha_c = (opens[i]+highs[i]+lows[i]+closes[i]) / 4.0
        ha_o = (opens[0]+closes[0])/2.0 if i == 0 else (ha[i-1]["open"]+ha[i-1]["close"])/2.0
        ha_h = max(highs[i], ha_o, ha_c)
        ha_l = min(lows[i],  ha_o, ha_c)
        ha.append({"open": round(ha_o,5), "high": round(ha_h,5),
                   "low":  round(ha_l,5), "close": round(ha_c,5)})
    recent = ha[-analyze_last:]
    bull_n = sum(1 for c in recent if c["close"] > c["open"])
    bear_n = sum(1 for c in recent if c["close"] < c["open"])
    last5  = ha[-5:]
    # Strong bull: bullish HA candles with no lower shadow
    strong_bull = sum(1 for c in last5
                      if c["close"] > c["open"]
                      and abs(c["low"] - min(c["open"], c["close"])) < 0.001 * c["close"])
    strong_bear = sum(1 for c in last5
                      if c["close"] < c["open"]
                      and abs(c["high"] - max(c["open"], c["close"])) < 0.001 * c["close"])
    trend = "BULLISH" if bull_n > bear_n else ("BEARISH" if bear_n > bull_n else "NEUTRAL")
    return {
        "last_candle": ha[-1], "recent_5": last5, "trend": trend,
        "bullish_candles": bull_n, "bearish_candles": bear_n,
        "strong_bull": strong_bull >= 3, "strong_bear": strong_bear >= 3,
    }


# ── Advanced Analysis Helpers (Tier 1-3) ─────────────────────────────────────

def calc_volume_profile(candles: list[dict], bins: int = 20) -> dict:
    """Volume Profile / VPVR — POC, VAH, VAL, HVN, LVN using range as volume proxy."""
    if not candles:
        return {}
    highs = [float(c["high"]) for c in candles]
    lows  = [float(c["low"])  for c in candles]
    price_min = min(lows)
    price_max = max(highs)
    if price_max == price_min:
        return {}
    bin_size     = (price_max - price_min) / bins
    volume_nodes = [0.0] * bins
    for c in candles:
        h, l = float(c["high"]), float(c["low"])
        rng  = h - l or bin_size * 0.1
        lo_b = max(0,       int((l - price_min) / bin_size))
        hi_b = min(bins-1,  int((h - price_min) / bin_size))
        n_b  = hi_b - lo_b + 1
        for b in range(lo_b, hi_b + 1):
            volume_nodes[b] += rng / n_b
    poc_bin   = volume_nodes.index(max(volume_nodes))
    poc_price = price_min + (poc_bin + 0.5) * bin_size
    total_vol = sum(volume_nodes)
    target    = total_vol * 0.70
    vah_b, val_b, va_vol = poc_bin, poc_bin, volume_nodes[poc_bin]
    while va_vol < target:
        above = volume_nodes[vah_b + 1] if vah_b + 1 < bins else 0
        below = volume_nodes[val_b - 1] if val_b - 1 >= 0  else 0
        if above >= below and vah_b + 1 < bins:
            vah_b += 1; va_vol += above
        elif val_b - 1 >= 0:
            val_b -= 1; va_vol += below
        else:
            break
    avg_vol  = total_vol / bins
    hvn = [round(price_min + (i+0.5)*bin_size, 5) for i, v in enumerate(volume_nodes) if v > avg_vol * 1.5]
    lvn = [round(price_min + (i+0.5)*bin_size, 5) for i, v in enumerate(volume_nodes) if 0 < v < avg_vol * 0.5]
    return {
        "poc": round(poc_price, 5),
        "vah": round(price_min + (vah_b + 1) * bin_size, 5),
        "val": round(price_min + val_b * bin_size, 5),
        "price_min": round(price_min, 5), "price_max": round(price_max, 5),
        "hvn_nodes": hvn[-5:], "lvn_nodes": lvn[-5:],
        "total_candles": len(candles),
    }


def detect_fvg(candles: list[dict]) -> list[dict]:
    """Fair Value Gap (FVG/Imbalance) detector — bullish & bearish."""
    fvgs = []
    for i in range(1, len(candles) - 1):
        prev_h = float(candles[i-1]["high"]); prev_l = float(candles[i-1]["low"])
        next_h = float(candles[i+1]["high"]); next_l = float(candles[i+1]["low"])
        epoch  = candles[i]["epoch"]
        if next_l > prev_h:
            gap  = next_l - prev_h
            mid  = (prev_h + next_l) / 2
            filled = any(float(candles[j]["low"]) <= prev_h + gap * 0.5
                         for j in range(i+2, len(candles)))
            fvgs.append({"type":"bullish","top":round(next_l,5),"bottom":round(prev_h,5),
                         "mid":round(mid,5),"size":round(gap,5),"epoch":epoch,"filled":filled,"idx":i})
        elif next_h < prev_l:
            gap  = prev_l - next_h
            mid  = (prev_l + next_h) / 2
            filled = any(float(candles[j]["high"]) >= prev_l - gap * 0.5
                         for j in range(i+2, len(candles)))
            fvgs.append({"type":"bearish","top":round(prev_l,5),"bottom":round(next_h,5),
                         "mid":round(mid,5),"size":round(gap,5),"epoch":epoch,"filled":filled,"idx":i})
    return sorted(fvgs, key=lambda x: x["idx"], reverse=True)[:15]


def detect_order_blocks(candles: list[dict], lookback: int = 5) -> list[dict]:
    """Order Block detector (ICT/SMC) — last opposing candle before strong impulse."""
    if len(candles) < lookback + 3:
        return []
    closes = [float(c["close"]) for c in candles]
    opens  = [float(c["open"])  for c in candles]
    highs  = [float(c["high"])  for c in candles]
    lows   = [float(c["low"])   for c in candles]
    bodies = [abs(closes[i]-opens[i]) for i in range(len(candles))]
    avg_body = sum(bodies)/len(bodies) if bodies else 1
    current  = closes[-1]
    obs = []
    for i in range(1, len(candles) - lookback):
        for j in range(i+1, min(i+lookback+1, len(candles))):
            body_j = closes[j] - opens[j]
            # Bullish OB: bearish candle → strong bullish impulse
            if closes[i] < opens[i] and body_j > avg_body*1.5 and closes[j] > highs[i]:
                mitigated = any(closes[k] < float(candles[i]["low"])
                                for k in range(j+1, len(candles)))
                obs.append({"type":"bullish","high":round(highs[i],5),"low":round(lows[i],5),
                            "mid":round((highs[i]+lows[i])/2,5),"epoch":candles[i]["epoch"],
                            "mitigated":mitigated,"distance_pct":round((current-highs[i])/highs[i]*100,3),"idx":i})
                break
            # Bearish OB: bullish candle → strong bearish impulse
            body_j_bear = opens[j] - closes[j]
            if closes[i] > opens[i] and body_j_bear > avg_body*1.5 and closes[j] < lows[i]:
                mitigated = any(closes[k] > float(candles[i]["high"])
                                for k in range(j+1, len(candles)))
                obs.append({"type":"bearish","high":round(highs[i],5),"low":round(lows[i],5),
                            "mid":round((highs[i]+lows[i])/2,5),"epoch":candles[i]["epoch"],
                            "mitigated":mitigated,"distance_pct":round((lows[i]-current)/lows[i]*100,3),"idx":i})
                break
    return sorted(obs, key=lambda x: x["idx"], reverse=True)[:10]


def detect_swing_structure(candles: list[dict], lookback: int = 3) -> dict:
    """Market structure: HH/HL/LH/LL, BOS (Break of Structure), CHoCH (Change of Character)."""
    if len(candles) < lookback * 2 + 1:
        return {}
    highs  = [float(c["high"]) for c in candles]
    lows   = [float(c["low"])  for c in candles]
    epochs = [c["epoch"]       for c in candles]
    swing_highs, swing_lows = [], []
    for i in range(lookback, len(candles) - lookback):
        if highs[i] == max(highs[i-lookback:i+lookback+1]):
            swing_highs.append({"price": round(highs[i],5), "epoch": epochs[i], "idx": i})
        if lows[i]  == min(lows[i-lookback:i+lookback+1]):
            swing_lows.append({"price":  round(lows[i],5),  "epoch": epochs[i], "idx": i})
    structure = "neutral"; bos = None; choch = None
    last_hh = prev_hh = last_hl = prev_hl = None
    if len(swing_highs) >= 2:
        last_hh, prev_hh = swing_highs[-1]["price"], swing_highs[-2]["price"]
    if len(swing_lows)  >= 2:
        last_hl, prev_hl = swing_lows[-1]["price"],  swing_lows[-2]["price"]
    if last_hh and prev_hh and last_hl and prev_hl:
        bull = last_hh > prev_hh and last_hl > prev_hl
        bear = last_hh < prev_hh and last_hl < prev_hl
        structure = "bullish" if bull else ("bearish" if bear else "neutral")
        if bull:
            bos = {"direction":"bullish","level":round(prev_hh,5),"broken_at":round(last_hh,5)}
        elif bear:
            bos = {"direction":"bearish","level":round(prev_hl,5),"broken_at":round(last_hl,5)}
        if last_hh < prev_hh and last_hl > prev_hl:
            choch = {"type":"potential_bearish_flip","level":round(prev_hl,5)}
        elif last_hh > prev_hh and last_hl < prev_hl:
            choch = {"type":"potential_bullish_flip","level":round(prev_hh,5)}
    return {
        "structure": structure, "swing_highs": swing_highs[-5:], "swing_lows": swing_lows[-5:],
        "last_hh": last_hh, "last_hl": last_hl, "prev_hh": prev_hh, "prev_hl": prev_hl,
        "bos": bos, "choch": choch, "current_price": round(float(candles[-1]["close"]),5),
    }


def detect_liquidity_sweeps(candles: list[dict], lookback: int = 20) -> list[dict]:
    """Liquidity sweep / stop hunt detector — price sweeps prior swing then reverses."""
    if len(candles) < lookback + 3:
        return []
    highs  = [float(c["high"])  for c in candles]
    lows   = [float(c["low"])   for c in candles]
    closes = [float(c["close"]) for c in candles]
    epochs = [c["epoch"]        for c in candles]
    sweeps = []
    for i in range(lookback, len(candles) - 1):
        prior_high = max(highs[i-lookback:i])
        prior_low  = min(lows[i-lookback:i])
        h, l, c_ = highs[i], lows[i], closes[i]
        if h > prior_high and c_ < prior_high:
            sweep = h - prior_high; rev = prior_high - c_
            if rev > sweep * 0.5:
                sweeps.append({"type":"bearish_sweep","swept_level":round(prior_high,5),
                               "wick_high":round(h,5),"close":round(c_,5),
                               "sweep_size":round(sweep,5),"epoch":epochs[i],"idx":i,
                               "signal":"sell — stop hunt above resistance complete"})
        elif l < prior_low and c_ > prior_low:
            sweep = prior_low - l; rev = c_ - prior_low
            if rev > sweep * 0.5:
                sweeps.append({"type":"bullish_sweep","swept_level":round(prior_low,5),
                               "wick_low":round(l,5),"close":round(c_,5),
                               "sweep_size":round(sweep,5),"epoch":epochs[i],"idx":i,
                               "signal":"buy — stop hunt below support complete"})
    return sorted(sweeps, key=lambda x: x["idx"], reverse=True)[:10]


def calc_session_ohlc(candles: list[dict], granularity: int) -> dict:
    """Session OHLC — Asia (00-08 UTC), London (07-16 UTC), New York (13-22 UTC)."""
    if granularity > 3600:
        return {}
    sess_def = {"asia": (0,8), "london": (7,16), "new_york": (13,22)}
    buckets: dict = {s: [] for s in sess_def}
    for c in candles:
        h = datetime.utcfromtimestamp(c["epoch"]).hour
        for s, (st, en) in sess_def.items():
            if st <= h < en:
                buckets[s].append(c)
    result = {}
    for s, sc in buckets.items():
        if not sc:
            result[s] = None; continue
        result[s] = {"high":  round(max(float(c["high"]) for c in sc),5),
                     "low":   round(min(float(c["low"])  for c in sc),5),
                     "open":  round(float(sc[0]["open"]),5),
                     "close": round(float(sc[-1]["close"]),5),
                     "candle_count": len(sc)}
    return result


def calc_prev_levels(daily_candles: list[dict]) -> dict:
    """PDH/PDL/PDC, PWH/PWL, PMH/PML from daily candle history."""
    result = {}
    if len(daily_candles) >= 2:
        pd_ = daily_candles[-2]
        result["prev_day"] = {"high":round(float(pd_["high"]),5),"low":round(float(pd_["low"]),5),
                              "open":round(float(pd_["open"]),5),"close":round(float(pd_["close"]),5),
                              "date":datetime.utcfromtimestamp(pd_["epoch"]).strftime("%Y-%m-%d")}
    if daily_candles:
        cd = daily_candles[-1]
        result["current_day"] = {"high":round(float(cd["high"]),5),"low":round(float(cd["low"]),5),
                                 "open":round(float(cd["open"]),5),"close":round(float(cd["close"]),5),
                                 "date":datetime.utcfromtimestamp(cd["epoch"]).strftime("%Y-%m-%d")}
    if len(daily_candles) >= 7:
        today_iso = datetime.utcnow().isocalendar()
        pw_h, pw_l = [], []
        for dc in daily_candles:
            iso = datetime.utcfromtimestamp(dc["epoch"]).isocalendar()
            prev_week = (today_iso[1] - 1) or 52
            if iso[1] == prev_week:
                pw_h.append(float(dc["high"])); pw_l.append(float(dc["low"]))
        if pw_h:
            result["prev_week"] = {"high":round(max(pw_h),5),"low":round(min(pw_l),5)}
    if len(daily_candles) >= 20:
        now = datetime.utcnow()
        pm_h, pm_l = [], []
        for dc in daily_candles:
            dt = datetime.utcfromtimestamp(dc["epoch"])
            prev_m = now.month - 1 or 12
            prev_y = now.year if now.month > 1 else now.year - 1
            if dt.month == prev_m and dt.year == prev_y:
                pm_h.append(float(dc["high"])); pm_l.append(float(dc["low"]))
        if pm_h:
            result["prev_month"] = {"high":round(max(pm_h),5),"low":round(min(pm_l),5)}
    return result


def calc_seasonality(daily_candles: list[dict]) -> dict:
    """Monthly seasonality: avg return, win rate, and bias per calendar month."""
    if len(daily_candles) < 30:
        return {}
    from collections import defaultdict
    monthly: dict = defaultdict(list)
    for i in range(1, len(daily_candles)):
        dt = datetime.utcfromtimestamp(daily_candles[i]["epoch"])
        ret = (float(daily_candles[i]["close"]) - float(daily_candles[i-1]["close"])) \
              / float(daily_candles[i-1]["close"]) * 100
        monthly[dt.month].append(ret)
    mn = {1:"Jan",2:"Feb",3:"Mar",4:"Apr",5:"May",6:"Jun",
          7:"Jul",8:"Aug",9:"Sep",10:"Oct",11:"Nov",12:"Dec"}
    stats = {}
    for m, rets in monthly.items():
        if len(rets) < 5: continue
        avg = sum(rets)/len(rets)
        wr  = sum(1 for r in rets if r > 0)/len(rets)*100
        stats[m] = {"name":mn[m],"avg_ret":round(avg,3),"win_rate":round(wr,1),
                    "samples":len(rets),"bias":"bullish" if avg>0.1 else ("bearish" if avg<-0.1 else "neutral")}
    if not stats: return {}
    now_m = datetime.utcnow().month
    nxt_m = (now_m % 12) + 1
    return {"current_month": stats.get(now_m), "next_month": stats.get(nxt_m),
            "best_month":  stats.get(max(stats, key=lambda m: stats[m]["avg_ret"])),
            "worst_month": stats.get(min(stats, key=lambda m: stats[m]["avg_ret"])),
            "all_months":  stats}


def calc_correlation(candles_a: list[dict], candles_b: list[dict], period: int = 20) -> dict:
    """Pearson correlation between two Deriv price series aligned by epoch."""
    if not candles_a or not candles_b:
        return {}
    map_a = {c["epoch"]: float(c["close"]) for c in candles_a}
    map_b = {c["epoch"]: float(c["close"]) for c in candles_b}
    common = sorted(set(map_a) & set(map_b))
    if len(common) < period:
        return {"correlation": None, "note": f"Only {len(common)} common candles (need {period})"}
    ca = [map_a[e] for e in common[-period:]]
    cb = [map_b[e] for e in common[-period:]]
    n  = len(ca)
    ma = sum(ca)/n; mb = sum(cb)/n
    cov  = sum((ca[i]-ma)*(cb[i]-mb) for i in range(n))
    sa   = math.sqrt(sum((x-ma)**2 for x in ca))
    sb   = math.sqrt(sum((x-mb)**2 for x in cb))
    corr = cov/(sa*sb) if sa and sb else None
    rets_a = [(ca[i]-ca[i-1])/ca[i-1] for i in range(1,n)]
    rets_b = [(cb[i]-cb[i-1])/cb[i-1] for i in range(1,n)]
    mra=sum(rets_a)/len(rets_a); mrb=sum(rets_b)/len(rets_b)
    cov_r = sum((rets_a[i]-mra)*(rets_b[i]-mrb) for i in range(len(rets_a)))
    sra=math.sqrt(sum((x-mra)**2 for x in rets_a)); srb=math.sqrt(sum((x-mrb)**2 for x in rets_b))
    ret_corr = cov_r/(sra*srb) if sra and srb else None
    if corr is None: return {"correlation": None}
    label = ("strong positive" if corr>0.7 else "moderate positive" if corr>0.4 else
             "weak positive" if corr>0.2 else "uncorrelated" if corr>-0.2 else
             "weak negative" if corr>-0.4 else "moderate negative" if corr>-0.7 else "strong negative")
    return {"price_correlation":round(corr,4),"return_correlation":round(ret_corr,4) if ret_corr else None,
            "label":label,"period":period,"common_candles":len(common)}



# ── Additional Math Functions ─────────────────────────────────────────────────

def calc_wma(values: list[float], period: int) -> list[float]:
    """Weighted Moving Average — linearly weighted, recent bars have more weight."""
    result = []
    weights = list(range(1, period + 1))
    wsum = sum(weights)
    for i in range(period - 1, len(values)):
        val = sum(values[i - period + 1 + j] * weights[j] for j in range(period)) / wsum
        result.append(val)
    return result


def calc_hma(closes: list[float], period: int = 20) -> float | None:
    """Hull Moving Average — dramatically reduces lag while maintaining smoothness.
    Formula: HMA = WMA(2*WMA(n/2) - WMA(n), sqrt(n))"""
    if len(closes) < period * 2:
        return None
    half = max(int(period / 2), 1)
    sqrt_p = max(int(period ** 0.5), 1)
    wma_half = calc_wma(closes, half)
    wma_full = calc_wma(closes, period)
    min_len = min(len(wma_half), len(wma_full))
    if min_len < sqrt_p:
        return None
    diff = [2 * wma_half[-min_len + i] - wma_full[-min_len + i] for i in range(min_len)]
    hma_series = calc_wma(diff, sqrt_p)
    return round(hma_series[-1], 5) if hma_series else None


def calc_stoch_rsi(closes: list[float], rsi_period: int = 14,
                   stoch_period: int = 14, k_smooth: int = 3, d_smooth: int = 3
                   ) -> dict | None:
    """Stochastic RSI — applies Stochastic formula on RSI values.
    More sensitive than plain RSI. Values 0–100.
    K > 80 = overbought, K < 20 = oversold."""
    need = rsi_period + stoch_period + k_smooth + d_smooth + 10
    if len(closes) < need:
        return None
    rsi_series = _rsi_series_incremental(closes, rsi_period)
    if len(rsi_series) < stoch_period + k_smooth + d_smooth:
        return None
    # Stochastic on RSI
    k_raw = []
    for i in range(stoch_period - 1, len(rsi_series)):
        window = rsi_series[i - stoch_period + 1: i + 1]
        lo, hi = min(window), max(window)
        k_raw.append(100 * (rsi_series[i] - lo) / (hi - lo) if hi != lo else 50.0)
    # Smooth K
    def _sma_s(vals, p):
        return [sum(vals[i-p+1:i+1])/p for i in range(p-1, len(vals))]
    k_smooth_series = _sma_s(k_raw, k_smooth)
    d_smooth_series = _sma_s(k_smooth_series, d_smooth)
    if not k_smooth_series or not d_smooth_series:
        return None
    k = round(k_smooth_series[-1], 2)
    d = round(d_smooth_series[-1], 2)
    if k >= 80:
        signal = "🔴 Overbought"
    elif k <= 20:
        signal = "🟢 Oversold"
    elif k > d:
        signal = "📈 Bullish momentum"
    else:
        signal = "📉 Bearish momentum"
    return {"k": k, "d": d, "signal": signal,
            "bullish_cross": k > d and k_smooth_series[-2] <= d_smooth_series[-2] if len(k_smooth_series) > 1 and len(d_smooth_series) > 1 else False}


def calc_roc(closes: list[float], period: int = 14) -> float | None:
    """Rate of Change — percentage change over N periods.
    Positive = upward momentum, Negative = downward momentum.
    ROC > 0 and rising = accelerating bullish momentum."""
    if len(closes) <= period:
        return None
    prev = closes[-1 - period]
    if prev == 0:
        return None
    return round((closes[-1] - prev) / prev * 100, 4)


def calc_awesome_oscillator(highs: list[float], lows: list[float]) -> dict | None:
    """Bill Williams Awesome Oscillator = SMA(5, midpoint) - SMA(34, midpoint).
    Positive histogram = bullish momentum. Negative = bearish.
    Zero-line cross = potential reversal."""
    if len(highs) < 34:
        return None
    midpoints = [(h + l) / 2 for h, l in zip(highs, lows)]
    sma5_series = _sma(midpoints, 5)
    sma34_series = _sma(midpoints, 34)
    min_len = min(len(sma5_series), len(sma34_series))
    if min_len < 2:
        return None
    ao_now = round(sma5_series[-1] - sma34_series[-1], 5)
    ao_prev = round(sma5_series[-2] - sma34_series[-2], 5)
    hist_change = "rising" if ao_now > ao_prev else "falling"
    if ao_now > 0:
        momentum = "🟢 Bullish" if hist_change == "rising" else "🟡 Bullish (fading)"
    else:
        momentum = "🔴 Bearish" if hist_change == "falling" else "🟡 Bearish (fading)"
    zero_cross = (ao_now > 0) != (ao_prev > 0)
    return {"ao": ao_now, "prev": ao_prev, "histogram": hist_change,
            "momentum": momentum, "zero_line_cross": zero_cross}


def calc_choppiness(highs: list[float], lows: list[float], closes: list[float],
                    period: int = 14) -> float | None:
    """Choppiness Index — 100 * log10(sum(ATR1,n) / (HighN - LowN)) / log10(n).
    Range: 38.2 to 100.
    > 61.8 = sideways/choppy market (avoid trend strategies).
    < 38.2 = trending market (trend strategies work well)."""
    if len(closes) < period + 1:
        return None
    # True ranges for each bar
    tr_sum = 0.0
    for i in range(len(closes) - period, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i] - closes[i-1]))
        tr_sum += tr
    period_high = max(highs[-period:])
    period_low = min(lows[-period:])
    range_hl = period_high - period_low
    if range_hl == 0 or tr_sum == 0:
        return None
    import math as _math
    ci = 100 * _math.log10(tr_sum / range_hl) / _math.log10(period)
    return round(ci, 2)


def calc_squeeze_momentum(highs: list[float], lows: list[float], closes: list[float],
                          bb_period: int = 20, bb_mult: float = 2.0,
                          kc_period: int = 20, kc_mult: float = 1.5) -> dict | None:
    """LazyBear Squeeze Momentum Indicator.
    Squeeze ON: BB inside KC (low volatility, coiled spring).
    Squeeze OFF: BB breaks out of KC (momentum is firing).
    Momentum histogram shows direction of expected move."""
    if len(closes) < max(bb_period, kc_period) + 5:
        return None
    # Bollinger Bands
    bb = calc_bbands(closes, bb_period, bb_mult)
    if bb is None:
        return None
    # Keltner Channel
    kc = calc_keltner(highs, lows, closes, kc_period, kc_period, kc_mult)
    if kc is None:
        return None
    # Squeeze: BB inside KC?
    squeeze_on = bb["upper"] < kc["upper"] and bb["lower"] > kc["lower"]
    squeeze_off = bb["upper"] > kc["upper"]  # BB just broke out
    # Momentum: linear regression of delta (close - midpoint of highest/lowest)
    period = bb_period
    highest_h = max(highs[-period:])
    lowest_l = min(lows[-period:])
    midpoint = (highest_h + lowest_l) / 2
    mid_ma = sum(closes[-period:]) / period
    delta = closes[-1] - (midpoint + mid_ma) / 2
    # Simple momentum direction
    prev_midpoint = (max(highs[-period-1:-1]) + min(lows[-period-1:-1])) / 2 if len(highs) > period+1 else midpoint
    prev_mid_ma = sum(closes[-period-1:-1]) / period if len(closes) > period+1 else mid_ma
    prev_delta = closes[-2] - (prev_midpoint + prev_mid_ma) / 2 if len(closes) > 1 else delta
    direction = "🟢 Bullish" if delta > 0 else "🔴 Bearish"
    accelerating = abs(delta) > abs(prev_delta)
    return {
        "squeeze_on": squeeze_on,
        "squeeze_off": squeeze_off,
        "momentum": round(delta, 5),
        "momentum_prev": round(prev_delta, 5),
        "direction": direction,
        "accelerating": accelerating,
        "bb_upper": round(bb["upper"], 5),
        "bb_lower": round(bb["lower"], 5),
        "kc_upper": round(kc["upper"], 5),
        "kc_lower": round(kc["lower"], 5),
    }


def calc_linear_regression(closes: list[float], period: int = 50,
                            channel_std: float = 2.0) -> dict | None:
    """Linear Regression Channel — least-squares fit over N bars.
    Returns slope, intercept, upper/lower channel bands (±std deviation).
    Price above midline = bullish bias. Price at upper band = extended high."""
    if len(closes) < period:
        return None
    import math as _math
    y = closes[-period:]
    n = len(y)
    x = list(range(n))
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    den = sum((x[i] - mx) ** 2 for i in range(n))
    if den == 0:
        return None
    slope = num / den
    intercept = my - slope * mx
    predicted = [intercept + slope * xi for xi in x]
    residuals = [y[i] - predicted[i] for i in range(n)]
    std = _math.sqrt(sum(r**2 for r in residuals) / n)
    midline = round(predicted[-1], 5)
    upper = round(predicted[-1] + channel_std * std, 5)
    lower = round(predicted[-1] - channel_std * std, 5)
    slope_pct = round(slope / y[0] * 100, 4) if y[0] else 0
    current = closes[-1]
    if current > upper:
        position = "🔴 Above upper channel — significantly overextended"
    elif current > midline:
        position = "📈 Above midline — bullish bias"
    elif current > lower:
        position = "📉 Below midline — bearish bias"
    else:
        position = "🟢 Below lower channel — significantly underextended"
    return {"midline": midline, "upper": upper, "lower": lower,
            "slope": round(slope, 6), "slope_pct_per_bar": slope_pct,
            "std_dev": round(std, 5), "current_price": round(current, 5),
            "position": position,
            "trend_direction": "up" if slope > 0 else "down"}


def calc_zigzag(highs: list[float], lows: list[float], closes: list[float],
                threshold_pct: float = 1.0) -> list[dict]:
    """ZigZag — identifies significant swing pivots by filtering moves < threshold_pct%.
    Returns list of recent pivot points (type, index, price).
    Useful for manually identifying swing structure without arbitrary lookback."""
    if len(closes) < 10:
        return []
    pivots = []
    direction = None  # "up" or "down"
    last_pivot_idx = 0
    last_pivot_price = closes[0]
    for i in range(1, len(closes)):
        if direction is None:
            if highs[i] / last_pivot_price - 1 >= threshold_pct / 100:
                direction = "up"
                last_pivot_idx = i
                last_pivot_price = highs[i]
            elif 1 - lows[i] / last_pivot_price >= threshold_pct / 100:
                direction = "down"
                last_pivot_idx = i
                last_pivot_price = lows[i]
        elif direction == "up":
            if highs[i] > last_pivot_price:
                last_pivot_idx = i
                last_pivot_price = highs[i]
            elif 1 - lows[i] / last_pivot_price >= threshold_pct / 100:
                pivots.append({"type": "high", "index": last_pivot_idx, "price": round(last_pivot_price, 5)})
                direction = "down"
                last_pivot_idx = i
                last_pivot_price = lows[i]
        elif direction == "down":
            if lows[i] < last_pivot_price:
                last_pivot_idx = i
                last_pivot_price = lows[i]
            elif highs[i] / last_pivot_price - 1 >= threshold_pct / 100:
                pivots.append({"type": "low", "index": last_pivot_idx, "price": round(last_pivot_price, 5)})
                direction = "up"
                last_pivot_idx = i
                last_pivot_price = highs[i]
    if direction:
        ptype = "high" if direction == "up" else "low"
        pivots.append({"type": ptype, "index": last_pivot_idx, "price": round(last_pivot_price, 5)})
    return pivots[-10:]  # Return last 10 pivots


# ── Tool Definitions ──────────────────────────────────────────────────────────

async def list_tools() -> list[Tool]:
    return [
        # ── Market Data ───────────────────────────────────────────────────────
        Tool(
            name="deriv-get-price",
            description=(
                "Get the current real-time price (tick) for a Deriv platform instrument. "
                "Use ONLY for Deriv symbols — XAUUSD (Gold), forex pairs, Deriv commodities. "
                "Common symbols: frxXAUUSD (Gold/USD), frxEURUSD, frxGBPUSD, "
                "frxUSDJPY, frxXAGUSD (Silver/USD), R_100. "
                "NOT for BTC/ETH/crypto — use TradingView MCP for those."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Deriv symbol e.g. frxXAUUSD (Gold), frxEURUSD, frxGBPUSD",
                        "default": "frxXAUUSD"
                    }
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-get-candles",
            description=(
                "Get OHLC candlestick history for a Deriv platform instrument. "
                "Use ONLY for Deriv symbols: XAUUSD, forex pairs, Deriv commodities. "
                "NOT for BTC/ETH/crypto exchange data — use TradingView MCP for those."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds: 60=1m, 300=5m, 900=15m, 3600=1h, 86400=1D",
                        "enum": [60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "count": {"type": "integer", "description": "Number of candles (max 5000)", "default": 50}
                },
                "required": ["symbol", "granularity"]
            }
        ),
        Tool(
            name="deriv-get-tick-history",
            description="Get recent tick (price) history for a Deriv instrument.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "count": {"type": "integer", "description": "Number of ticks (max 5000)", "default": 100}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-active-symbols",
            description="List tradable symbols on Deriv. Filter by market: forex, commodities, indices. Use ONLY for Forex/Gold/Silver instruments — NOT for crypto.",
            inputSchema={
                "type": "object",
                "properties": {
                    "market": {"type": "string", "description": "Market filter e.g. forex, commodities, indices", "default": "forex"}
                }
            }
        ),
        Tool(
            name="deriv-market-snapshot",
            description=(
                "Get current prices for multiple Deriv platform instruments at once. "
                "Use ONLY for Deriv symbols: XAUUSD, XAGUSD, major forex pairs. "
                "NOT for BTC/ETH/crypto exchanges — use TradingView MCP for those."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of Deriv symbols e.g. frxXAUUSD, frxEURUSD, frxGBPUSD",
                        "default": ["frxXAUUSD", "frxXAGUSD", "frxEURUSD", "frxGBPUSD", "frxUSDJPY"]
                    }
                }
            }
        ),
        Tool(
            name="deriv-pip-size",
            description="Get pip/decimal precision and instrument info for a Deriv symbol.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"}
                },
                "required": ["symbol"]
            }
        ),

        # ── Technical Indicators ──────────────────────────────────────────────
        Tool(
            name="deriv-rsi",
            description=(
                "Calculate RSI (Relative Strength Index) from Deriv candle data. "
                "RSI > 70 = overbought (potential sell), RSI < 30 = oversold (potential buy). "
                "Uses Wilder's smoothing method (same as TradingView)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds: 60=1m, 900=15m, 3600=1h, 14400=4h, 86400=1D",
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "period": {"type": "integer", "description": "RSI period (default 14)", "default": 14},
                    "count": {"type": "integer", "description": "Candles to fetch (default 100)", "default": 100}
                },
                "required": ["symbol", "granularity"]
            }
        ),
        Tool(
            name="deriv-macd",
            description=(
                "Calculate MACD (Moving Average Convergence Divergence) from Deriv candle data. "
                "MACD line crosses above signal = bullish. Below = bearish. "
                "Histogram positive and growing = momentum strengthening. "
                "Uses same 12/26/9 defaults as TradingView."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds",
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "fast": {"type": "integer", "description": "Fast EMA period (default 12)", "default": 12},
                    "slow": {"type": "integer", "description": "Slow EMA period (default 26)", "default": 26},
                    "signal": {"type": "integer", "description": "Signal EMA period (default 9)", "default": 9},
                    "count": {"type": "integer", "description": "Candles to fetch (default 150)", "default": 150}
                },
                "required": ["symbol", "granularity"]
            }
        ),
        Tool(
            name="deriv-bbands",
            description=(
                "Calculate Bollinger Bands from Deriv candle data. "
                "Price near upper band = overbought zone. Near lower band = oversold zone. "
                "Squeeze (bands narrow) = breakout incoming. "
                "Uses SMA(20) ± 2 std dev same as TradingView default."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds",
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "period": {"type": "integer", "description": "BB period (default 20)", "default": 20},
                    "std_mult": {"type": "number", "description": "Std deviation multiplier (default 2.0)", "default": 2.0},
                    "count": {"type": "integer", "description": "Candles to fetch (default 100)", "default": 100}
                },
                "required": ["symbol", "granularity"]
            }
        ),
        Tool(
            name="deriv-ema",
            description=(
                "Calculate EMA (Exponential Moving Average) for multiple periods from Deriv data. "
                "EMA9 > EMA21 > EMA50 = strong uptrend. "
                "Price crossing EMA = trend signal. "
                "Common periods: 9, 21, 50, 100, 200."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds",
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "periods": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "EMA periods to calculate e.g. [9, 21, 50, 200]",
                        "default": [9, 21, 50, 100, 200]
                    },
                    "count": {"type": "integer", "description": "Candles to fetch (default 250)", "default": 250}
                },
                "required": ["symbol", "granularity"]
            }
        ),
        Tool(
            name="deriv-atr",
            description=(
                "Calculate ATR (Average True Range) from Deriv candle data. "
                "ATR measures volatility — useful for setting Stop Loss and Take Profit. "
                "SL = entry ± (ATR × multiplier), common multiplier: 1.5–2.0. "
                "High ATR = volatile market, use wider SL."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "period": {"type": "integer", "description": "ATR period (default 14)", "default": 14},
                    "count": {"type": "integer", "description": "Candles to fetch (default 100)", "default": 100}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-stoch",
            description=(
                "Calculate Stochastic Oscillator (%K and %D) from Deriv candle data. "
                "Both lines above 80 = overbought. Both below 20 = oversold. "
                "%K crossing above %D = buy signal. Below = sell signal."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "k_period": {"type": "integer", "description": "%K period (default 14)", "default": 14},
                    "d_period": {"type": "integer", "description": "%D smoothing period (default 3)", "default": 3},
                    "count": {"type": "integer", "description": "Candles to fetch (default 100)", "default": 100}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-technical-analysis",
            description=(
                "Full technical analysis for a Deriv instrument — all indicators in one call. "
                "Returns RSI, MACD, Bollinger Bands, EMA (9/21/50/200), ATR, Stochastic. "
                "Includes signal summary: trend direction, momentum, overbought/oversold, "
                "volatility, and suggested SL/TP based on ATR. "
                "Best tool for trading entry recommendation on XAUUSD."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle timeframe: 900=15m, 3600=1h, 14400=4h, 86400=1D",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "count": {"type": "integer", "description": "Candles to fetch (default 300 for accuracy)", "default": 300}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-smart-analysis",
            description=(
                "🏆 PROFESSIONAL-GRADE multi-timeframe trading analysis for Deriv platform instruments. "
                "Use ONLY for XAUUSD (Gold), forex pairs (frxEURUSD, frxGBPUSD, etc.), and Deriv commodities. "
                "NOT for BTC/ETH/crypto exchanges — use TradingView MCP coin_analysis for those. "
                "Automatically analyzes D1 (trend) → H4 (setup) → H1 (entry timing) in one call. "
                "Returns: trend direction, setup quality, entry zone, Stop Loss, Take Profit 1 & 2, "
                "confluence score, confidence level, and plain-language explanation. "
                "ALWAYS use this tool first for XAUUSD or forex analysis."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Deriv symbol e.g. frxXAUUSD (Gold), frxEURUSD, frxGBPUSD, frxXAGUSD",
                        "default": "frxXAUUSD"
                    }
                },
                "required": ["symbol"]
            }
        ),

        # ── Advanced Indicators ───────────────────────────────────────────────
        Tool(
            name="deriv-fibonacci",
            description=(
                "Calculate Fibonacci retracement and extension levels from recent swing high/low on Deriv data. "
                "Retracement levels: 0%, 23.6%, 38.2%, 50%, 61.8%, 78.6%, 100%. "
                "Extension levels: 127.2%, 161.8%, 200%, 261.8%. "
                "Use after identifying a significant price move to find pullback support (uptrend) "
                "or bounce resistance (downtrend). Agent should choose period based on regime."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds: 3600=1h, 14400=4h, 86400=1D",
                        "default": 14400,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "swing_lookback": {"type": "integer", "description": "Candles to look back for swing high/low (default 50)", "default": 50},
                    "count": {"type": "integer", "description": "Candles to fetch (default 200)", "default": 200}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-pivot-points",
            description=(
                "Calculate classic Pivot Points (PP, R1/R2/R3, S1/S2/S3) from Deriv data. "
                "Pivot points are used by institutions and market makers as key reference levels. "
                "Daily pivots = previous day's H/L/C. Weekly pivots = previous week's H/L/C. "
                "Price above PP = bullish bias. Price below PP = bearish bias."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "period": {
                        "type": "string",
                        "description": "Pivot period: 'daily' (uses previous day H/L/C) or 'weekly' (uses previous week H/L/C)",
                        "default": "daily",
                        "enum": ["daily", "weekly"]
                    }
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-ichimoku",
            description=(
                "Calculate Ichimoku Cloud (Kinko Hyo) for a Deriv instrument. "
                "Returns Tenkan-sen (9), Kijun-sen (26), Senkou Span A/B, Chikou Span. "
                "Price above cloud = bullish. Price below cloud = bearish. Price inside cloud = indecision. "
                "Tenkan > Kijun = bullish cross. "
                "Excellent for XAUUSD and JPY pairs — agent can adjust periods autonomously."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds",
                        "default": 14400,
                        "enum": [900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "tenkan": {"type": "integer", "description": "Tenkan-sen period (default 9, use 7 for faster markets)", "default": 9},
                    "kijun": {"type": "integer", "description": "Kijun-sen period (default 26, use 22 for faster markets)", "default": 26},
                    "senkou_b": {"type": "integer", "description": "Senkou Span B period (default 52)", "default": 52},
                    "count": {"type": "integer", "description": "Candles to fetch (default 300)", "default": 300}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-parabolic-sar",
            description=(
                "Calculate Parabolic SAR (Stop and Reverse) for a Deriv instrument. "
                "SAR below price = uptrend (hold long / buy). SAR above price = downtrend (hold short / sell). "
                "When SAR flips, it signals a trend reversal. "
                "Excellent trailing stop tool — agent can adjust acceleration factor for volatility."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "af_start": {"type": "number", "description": "Initial acceleration factor (default 0.02, higher = more sensitive)", "default": 0.02},
                    "af_step": {"type": "number", "description": "AF increment step (default 0.02)", "default": 0.02},
                    "af_max": {"type": "number", "description": "Maximum AF cap (default 0.20)", "default": 0.2},
                    "count": {"type": "integer", "description": "Candles to fetch (default 200)", "default": 200}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-supertrend",
            description=(
                "Calculate Supertrend indicator for a Deriv instrument. "
                "ATR-based dynamic support/resistance that flips direction with trend. "
                "Direction 'up' = bullish (price above supertrend line). "
                "Direction 'down' = bearish (price below supertrend line). "
                "Agent can tune period and multiplier: higher multiplier = less noise, fewer signals."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "period": {"type": "integer", "description": "ATR period (default 10; use 7 for scalping, 14 for swing)", "default": 10},
                    "multiplier": {"type": "number", "description": "ATR multiplier (default 3.0; use 2.0 for tight, 4.0 for wide)", "default": 3.0},
                    "count": {"type": "integer", "description": "Candles to fetch (default 200)", "default": 200}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-keltner",
            description=(
                "Calculate Keltner Channel for a Deriv instrument. "
                "EMA ± (multiplier × ATR). More stable than Bollinger Bands in volatile markets. "
                "Price above upper band = strong bullish momentum. Below lower band = strong bearish. "
                "Combined with Bollinger Bands: if BB is inside Keltner = squeeze (breakout incoming). "
                "Agent can adjust EMA period, ATR period, and multiplier."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "ema_period": {"type": "integer", "description": "EMA period (default 20)", "default": 20},
                    "atr_period": {"type": "integer", "description": "ATR period (default 10)", "default": 10},
                    "multiplier": {"type": "number", "description": "ATR multiplier (default 2.0)", "default": 2.0},
                    "count": {"type": "integer", "description": "Candles to fetch (default 200)", "default": 200}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-donchian",
            description=(
                "Calculate Donchian Channel (highest high / lowest low over N periods) for a Deriv instrument. "
                "Breakout above upper band = strong bullish signal. Below lower = strong bearish. "
                "Basis of the classic Turtle Trading System. "
                "Agent can tune period: 20 for medium-term, 55 for long-term breakouts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "period": {"type": "integer", "description": "Lookback period (default 20; use 55 for long-term breakouts)", "default": 20},
                    "count": {"type": "integer", "description": "Candles to fetch (default 150)", "default": 150}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-cci",
            description=(
                "Calculate CCI (Commodity Channel Index) for a Deriv instrument. "
                "Originally designed for commodities — excellent for XAUUSD and Silver. "
                "CCI > +100 = overbought / strong bullish momentum. "
                "CCI < -100 = oversold / strong bearish momentum. "
                "Zero-line cross = trend change signal. "
                "Agent can adjust period: 14 for short-term, 20 for medium-term."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "period": {"type": "integer", "description": "CCI period (default 20; use 14 for faster signals)", "default": 20},
                    "count": {"type": "integer", "description": "Candles to fetch (default 150)", "default": 150}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-williams-r",
            description=(
                "Calculate Williams %R for a Deriv instrument. "
                "%R near 0 = overbought (potential sell). %R near -100 = oversold (potential buy). "
                "Faster and more sensitive than Stochastic — preferred for scalping and intraday. "
                "Agent can tune period: 14 for standard, 5-9 for faster response."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "period": {"type": "integer", "description": "Williams %R period (default 14; use 7-9 for scalping)", "default": 14},
                    "count": {"type": "integer", "description": "Candles to fetch (default 100)", "default": 100}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-heikin-ashi",
            description=(
                "Calculate Heikin Ashi candles for a Deriv instrument and analyze trend. "
                "Heikin Ashi smooths out noise — consecutive bullish HA candles with no lower shadow = strong uptrend. "
                "Consecutive bearish HA candles with no upper shadow = strong downtrend. "
                "Doji-like HA candle = trend exhaustion / potential reversal. "
                "Use to filter false signals from other indicators."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size in seconds",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "analyze_last": {"type": "integer", "description": "Number of recent HA candles to analyze for trend (default 10)", "default": 10},
                    "count": {"type": "integer", "description": "Candles to fetch (default 150)", "default": 150}
                },
                "required": ["symbol"]
            }
        ),

        # ── Tier 1: Institutional Structure ───────────────────────────────────
        Tool(
            name="deriv-volume-profile",
            description=(
                "Volume Profile / VPVR for a Deriv instrument. "
                "Identifies POC (Point of Control — price with most activity), "
                "VAH (Value Area High), VAL (Value Area Low), HVN (High Volume Nodes = strong support/resistance), "
                "and LVN (Low Volume Nodes = fast-move zones). "
                "POC acts as a magnet — price tends to gravitate toward it. "
                "Use to find institutional price levels that simple S/R misses."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 3600=1h, 14400=4h, 86400=1D",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "count": {"type": "integer", "description": "Candles to build profile from (default 200)", "default": 200},
                    "bins":  {"type": "integer", "description": "Price bins for distribution (default 20, max 50)", "default": 20}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-fvg",
            description=(
                "Fair Value Gap (FVG) / Imbalance detector for a Deriv instrument. "
                "Bullish FVG: 3-candle pattern where candle[i+1].low > candle[i-1].high — "
                "price moved up leaving an unfilled gap, likely to be revisited. "
                "Bearish FVG: candle[i+1].high < candle[i-1].low — downside imbalance. "
                "Unfilled FVGs are strong magnet zones for price. "
                "Core to ICT/SMC methodology — use alongside order blocks for high-probability entries."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h, 86400=1D",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "count": {"type": "integer", "description": "Candles to scan (default 150)", "default": 150}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-order-blocks",
            description=(
                "Order Block detector (ICT/SMC methodology) for a Deriv instrument. "
                "A bullish OB = last bearish candle before a strong bullish impulse that breaks prior structure. "
                "A bearish OB = last bullish candle before a strong bearish impulse. "
                "Order blocks represent institutional accumulation/distribution zones. "
                "Price frequently returns to mitigate (retest) unmitigated OBs. "
                "Combine with FVG for confluence — OB + FVG overlap = highest-probability entry zone."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h, 86400=1D",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "count":   {"type": "integer", "description": "Candles to scan (default 150)", "default": 150},
                    "lookback": {"type": "integer", "description": "Max candles after OB to find impulse (default 5)", "default": 5}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-swing-structure",
            description=(
                "Market structure analysis: automated HH/HL/LH/LL detection, "
                "Break of Structure (BOS), and Change of Character (CHoCH) for a Deriv instrument. "
                "BOS = trend continuation confirmed. CHoCH = early warning of trend flip. "
                "Use to determine macro bias before selecting entry direction. "
                "Multi-timeframe: call on D1 for bias, H4 for structure, H1 for entry."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 3600=1h, 14400=4h, 86400=1D",
                        "default": 14400,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "count":   {"type": "integer", "description": "Candles to scan (default 100)", "default": 100},
                    "lookback": {"type": "integer", "description": "Swing detection sensitivity — candles each side (default 3)", "default": 3}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-liquidity-sweep",
            description=(
                "Liquidity sweep / stop hunt detector for a Deriv instrument. "
                "Detects candles that wick above a prior swing high (or below a prior swing low) "
                "then close back inside — classic institutional stop-hunt pattern. "
                "A bearish sweep above resistance = sell signal (stops cleared, reversal likely). "
                "A bullish sweep below support = buy signal (stops cleared, reversal likely). "
                "Most high-probability entries occur RIGHT AFTER a confirmed sweep."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "count":   {"type": "integer", "description": "Candles to scan (default 100)", "default": 100},
                    "lookback": {"type": "integer", "description": "Prior candles to define swing level (default 20)", "default": 20}
                },
                "required": ["symbol"]
            }
        ),

        # ── Tier 3: Precision Timing & Context ────────────────────────────────
        Tool(
            name="deriv-session-levels",
            description=(
                "Session OHLC levels for a Deriv instrument: "
                "Asia (00:00-08:00 UTC), London (07:00-16:00 UTC), New York (13:00-22:00 UTC). "
                "Session High/Low are key liquidity zones — price frequently sweeps them. "
                "Asia range = accumulation zone. London breakout = directional bias for the day. "
                "NY often retests London levels or extends the move. "
                "Use session open price as intraday bias anchor."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size (max 3600=1h for session analysis)",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600]
                    },
                    "count": {"type": "integer", "description": "Candles to fetch (default 48 = 2 days at 1h)", "default": 48}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-prev-levels",
            description=(
                "Previous day/week/month OHLC levels (PDH, PDL, PDC, PWH, PWL, PMH, PML) "
                "for a Deriv instrument. "
                "These are the most-watched institutional reference levels. "
                "PDH/PDL = most likely intraday targets for stop runs. "
                "PWH/PWL = key weekly liquidity zones. "
                "Price breaking above PDH = bullish continuation signal. "
                "Price failing at PDH = potential rejection/reversal. "
                "Essential for any intraday or swing trade."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "count":  {"type": "integer", "description": "Daily candles to fetch (default 60)", "default": 60}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-seasonality",
            description=(
                "Monthly seasonality analysis for a Deriv instrument based on historical data. "
                "Shows average return and win rate per calendar month. "
                "Identifies historically bullish months (e.g. Gold: Aug-Oct often strong) "
                "and bearish months. "
                "Use to confirm or question the macro bias — trading with seasonality improves edge. "
                "Best with 500+ daily candles for statistical significance."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "count":  {"type": "integer", "description": "Daily candles (default 750 ≈ 3 years)", "default": 750}
                },
                "required": ["symbol"]
            }
        ),

        # ── New Indicators ─────────────────────────────────────────────────────
        Tool(
            name="deriv-adx",
            description=(
                "ADX (Average Directional Index) standalone — measures TREND STRENGTH, not direction. "
                "ADX > 25 = trending market (trend-following strategies work). "
                "ADX < 20 = ranging/sideways (oscillators more reliable). "
                "+DI > -DI = bullish trend. -DI > +DI = bearish trend. "
                "ADX rising + +DI rising = accelerating bullish trend. "
                "Unlike deriv-technical-analysis, this gives you ADX in isolation with full detail."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h, 86400=1D",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "period": {"type": "integer", "description": "ADX period (default 14)", "default": 14},
                    "count":  {"type": "integer", "description": "Candles to fetch (default 100)", "default": 100}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-sma",
            description=(
                "Simple Moving Average (SMA) for one or multiple periods. "
                "Slower than EMA but less noise — better for identifying pure price average. "
                "SMA200 = key long-term support/resistance level. "
                "SMA50 cross above SMA200 = Golden Cross (bullish). Below = Death Cross (bearish). "
                "Price above SMA = bullish bias. Below = bearish."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h, 86400=1D",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "periods": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of SMA periods e.g. [20, 50, 100, 200]",
                        "default": [20, 50, 200]
                    },
                    "count": {"type": "integer", "description": "Candles to fetch (default 250)", "default": 250}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-hma",
            description=(
                "Hull Moving Average (HMA) — nearly eliminates lag from traditional MAs while remaining smooth. "
                "Responds to price changes much faster than EMA or SMA. "
                "HMA rising = bullish momentum. HMA falling = bearish momentum. "
                "Crossover with price = early trend signal. "
                "Best for: intraday momentum confirmation, replacing slow EMA in fast markets."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "period": {"type": "integer", "description": "HMA period (default 20)", "default": 20},
                    "count":  {"type": "integer", "description": "Candles to fetch (default 150)", "default": 150}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-stochrsi",
            description=(
                "Stochastic RSI — applies Stochastic formula on RSI values for higher sensitivity. "
                "More responsive than plain RSI, better for detecting short-term reversals. "
                "K > 80 = overbought zone. K < 20 = oversold zone. "
                "K crossing D from below = bullish signal. K crossing D from above = bearish. "
                "Best for: fast markets, scalping confirmation, ranging market entries."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "rsi_period":   {"type": "integer", "description": "RSI period (default 14)", "default": 14},
                    "stoch_period": {"type": "integer", "description": "Stochastic period on RSI values (default 14)", "default": 14},
                    "k_smooth":     {"type": "integer", "description": "K smoothing period (default 3)", "default": 3},
                    "d_smooth":     {"type": "integer", "description": "D smoothing period (default 3)", "default": 3},
                    "count":        {"type": "integer", "description": "Candles to fetch (default 150)", "default": 150}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-roc",
            description=(
                "Rate of Change (ROC / Momentum) — percentage price change over N periods. "
                "Positive ROC = upward momentum. Negative ROC = downward momentum. "
                "ROC crossing zero = potential trend change. "
                "ROC rising while positive = accelerating bullish momentum. "
                "Divergence: price makes new high but ROC doesn't = weakening momentum."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h, 86400=1D",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "period": {"type": "integer", "description": "ROC lookback period (default 14)", "default": 14},
                    "count":  {"type": "integer", "description": "Candles to fetch (default 100)", "default": 100}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-awesome-oscillator",
            description=(
                "Bill Williams Awesome Oscillator — difference between 5-period and 34-period midpoint SMAs. "
                "Above zero = bullish momentum territory. Below zero = bearish. "
                "Zero-line cross = potential trend reversal signal. "
                "Rising histogram = accelerating momentum. Falling = fading. "
                "Twin Peaks: two consecutive peaks on same side of zero = reversal signal. "
                "Combine with Ichimoku or MACD for confirmation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "count": {"type": "integer", "description": "Candles to fetch (default 100)", "default": 100}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-choppiness",
            description=(
                "Choppiness Index — measures whether the market is trending or sideways. "
                "Scale: 38.2 (max trend) to 100 (max chop). "
                "CI < 38.2 = strong directional trend (use trend-following strategies). "
                "CI > 61.8 = sideways/choppy (use mean-reversion, avoid breakout trades). "
                "CI between 38.2–61.8 = transitional — watch for breakout. "
                "ESSENTIAL first step: know if the market is trending before picking a strategy."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h, 86400=1D",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "period": {"type": "integer", "description": "Choppiness period (default 14)", "default": 14},
                    "count":  {"type": "integer", "description": "Candles to fetch (default 100)", "default": 100}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-squeeze",
            description=(
                "Squeeze Momentum Indicator (LazyBear) — detects volatility compression (Bollinger inside Keltner). "
                "Squeeze ON = BB inside KC = low volatility, coiled spring — expect explosive move soon. "
                "Squeeze OFF = BB breaks outside KC = momentum is FIRING — trade in histogram direction. "
                "Histogram positive & rising = bullish breakout. Negative & falling = bearish breakdown. "
                "Most powerful setup: squeeze_on → squeeze_off with momentum in one direction."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "bb_period":  {"type": "integer", "description": "Bollinger period (default 20)", "default": 20},
                    "bb_mult":    {"type": "number",  "description": "BB std multiplier (default 2.0)", "default": 2.0},
                    "kc_period":  {"type": "integer", "description": "Keltner period (default 20)", "default": 20},
                    "kc_mult":    {"type": "number",  "description": "KC ATR multiplier (default 1.5)", "default": 1.5},
                    "count":      {"type": "integer", "description": "Candles to fetch (default 100)", "default": 100}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-linreg",
            description=(
                "Linear Regression Channel — statistical least-squares trend line with standard deviation bands. "
                "Midline = the 'fair value' trend line based on N bars of price history. "
                "Upper band = price extended high (statistical overbought). "
                "Lower band = price extended low (statistical oversold). "
                "Slope direction shows the statistical trend. Steep positive slope = strong uptrend. "
                "Best for: identifying when price is overextended for mean-reversion, or when trend is intact."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h, 86400=1D",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "period":      {"type": "integer", "description": "Regression lookback bars (default 50)", "default": 50},
                    "channel_std": {"type": "number",  "description": "Channel width in standard deviations (default 2.0)", "default": 2.0},
                    "count":       {"type": "integer", "description": "Candles to fetch (default 200)", "default": 200}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-divergence",
            description=(
                "Divergence scanner — detects RSI and MACD divergence vs price action. "
                "Bullish RSI divergence: price makes lower low, RSI makes higher low → reversal up. "
                "Bearish RSI divergence: price makes higher high, RSI makes lower high → reversal down. "
                "MACD histogram divergence: price LL + histogram HL = bullish (momentum recovering). "
                "Divergence is one of the highest-probability reversal signals available. "
                "Combine with key S/R level, FVG, or order block for maximum confluence."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "rsi_period": {"type": "integer", "description": "RSI period (default 14)", "default": 14},
                    "count":      {"type": "integer", "description": "Candles to fetch (default 150)", "default": 150}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-zigzag",
            description=(
                "ZigZag indicator — filters out minor price noise and identifies significant swing pivots. "
                "Returns the last 10 significant highs and lows based on a minimum movement threshold. "
                "Use to quickly identify the most recent meaningful swing structure. "
                "Threshold 1.0% = only show moves > 1% of price. Increase for cleaner higher-TF pivots. "
                "Best for: identifying key S/R levels from recent price action, confirming structure breaks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 900=15m, 3600=1h, 14400=4h, 86400=1D",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "threshold_pct": {"type": "number", "description": "Minimum swing size in % (default 1.0)", "default": 1.0},
                    "count":         {"type": "integer", "description": "Candles to fetch (default 200)", "default": 200}
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="deriv-multitf",
            description=(
                "Multi-timeframe confluence tool — fetches RSI, MACD histogram, EMA trend, and ADX "
                "across three timeframes (D1, H4, H1) in a single call. "
                "Use to instantly see if all timeframes agree on direction. "
                "Aligned bullish (all TFs: RSI>50, MACD>0, price>EMA) = high-conviction long. "
                "Conflicting timeframes = wait for alignment. "
                "Saves multiple sequential tool calls when you need the full multi-TF picture."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol":      {"type": "string", "description": "Deriv symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "ema_period":  {"type": "integer", "description": "EMA period to check price vs (default 50)", "default": 50},
                    "rsi_period":  {"type": "integer", "description": "RSI period (default 14)", "default": 14},
                    "adx_period":  {"type": "integer", "description": "ADX period (default 14)", "default": 14}
                },
                "required": ["symbol"]
            }
        ),

        # ── Tier 2: Macro Context ──────────────────────────────────────────────
        Tool(
            name="deriv-correlation",
            description=(
                "Pearson price & return correlation between two Deriv instruments. "
                "Key correlations for XAUUSD: "
                "frxEURUSD (positive ~0.7 — both move inversely to USD strength), "
                "frxUSDJPY (negative ~-0.6 — risk-off asset divergence), "
                "frxXAGUSD Silver (strong positive ~0.85). "
                "Use DXY proxy: if EURUSD and GBPUSD are both falling = USD strengthening = headwind for Gold. "
                "Correlation > 0.7 = move together. Correlation < -0.7 = move opposite. "
                "Divergence from correlation = mean-reversion opportunity."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol_a": {"type": "string", "description": "Primary symbol e.g. frxXAUUSD", "default": "frxXAUUSD"},
                    "symbol_b": {"type": "string", "description": "Comparison symbol e.g. frxEURUSD", "default": "frxEURUSD"},
                    "granularity": {
                        "type": "integer",
                        "description": "Candle size: 3600=1h, 14400=4h, 86400=1D",
                        "default": 3600,
                        "enum": [60, 300, 900, 1800, 3600, 7200, 14400, 28800, 86400]
                    },
                    "count":  {"type": "integer", "description": "Candles to fetch (default 100)", "default": 100},
                    "period": {"type": "integer", "description": "Correlation window (default 20)", "default": 20}
                },
                "required": ["symbol_a", "symbol_b"]
            }
        ),
    ]


# ── Tool Handlers ─────────────────────────────────────────────────────────────

_BLOCKED_SYMBOL_PREFIXES = ("cry", "crypto")
_BLOCKED_EXCHANGE_SUFFIXES = ("USDT", "BUSD", "BTC", "ETH", "USD:BTC")
_BLOCKED_EXCHANGE_KEYWORDS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "MATICUSDT", "AVAXUSDT",
    "BINANCE:", "NASDAQ:", "NYSE:", "SP:", "KUCOIN:",
)

def _is_blocked_symbol(symbol: str) -> bool:
    """Return True if the symbol is a crypto/stock exchange symbol that must not
    be routed through Deriv MCP. Deriv Forex/Gold symbols start with 'frx' or
    are volatility indices (R_*). Crypto on Deriv uses 'cry' prefix which is
    also explicitly blocked per routing rules — use TradingView MCP instead."""
    s = symbol.upper()
    if symbol.lower().startswith(_BLOCKED_SYMBOL_PREFIXES):
        return True
    for kw in _BLOCKED_EXCHANGE_KEYWORDS:
        if kw.upper() in s:
            return True
    return False


async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    # Server-side symbol guard: block crypto/exchange symbols before they reach Deriv API
    _SYMBOL_ARGS = ("symbol", "symbol_a", "symbol_b")
    for arg_key in _SYMBOL_ARGS:
        sym = arguments.get(arg_key, "")
        if sym and _is_blocked_symbol(sym):
            text = (
                f"Error: Symbol '{sym}' is not supported by Deriv MCP. "
                "Deriv MCP only handles Forex and Gold/Silver instruments (frxEURUSD, frxXAUUSD, etc.). "
                "For crypto or stock analysis, use TradingView MCP instead."
            )
            return [TextContent(type="text", text=text)]

    # Block crypto in multi-symbol tools
    raw_symbols = arguments.get("symbols")
    if isinstance(raw_symbols, list):
        for sym in raw_symbols:
            if sym and _is_blocked_symbol(sym):
                text = (
                    f"Error: Symbol '{sym}' in symbols list is not supported by Deriv MCP. "
                    "Deriv MCP only handles Forex and Gold/Silver instruments. "
                    "Remove crypto symbols from the list or use TradingView MCP for crypto."
                )
                return [TextContent(type="text", text=text)]

    try:
        text = await _dispatch(name, arguments)
    except asyncio.TimeoutError:
        text = "Error: Deriv WebSocket request timed out. Please try again."
    except Exception as e:
        text = f"Error: {type(e).__name__}: {e}"
    return [TextContent(type="text", text=text)]


async def _dispatch(name: str, args: dict) -> str:

    # ── deriv_get_price ───────────────────────────────────────────────────────
    if name == "deriv-get-price":
        symbol = args.get("symbol", "frxXAUUSD")
        resp = await deriv_request({
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": 1,
            "end": "latest",
            "style": "ticks"
        })
        if "error" in resp:
            return f"Error: {resp['error'].get('message', resp['error'])}"
        history = resp.get("history", {})
        prices = history.get("prices", [])
        times = history.get("times", [])
        if not prices:
            return f"Error: No price data returned for {symbol}"
        price = prices[-1]
        epoch = times[-1] if times else 0
        ts = datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S UTC")
        return (
            f"📈 {symbol} — Current Price\n"
            f"Price : {price}\n"
            f"Time  : {ts}"
        )

    # ── deriv_get_candles ─────────────────────────────────────────────────────
    elif name == "deriv-get-candles":
        symbol = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity")
        if granularity is None:
            return "Error: granularity/timeframe is required; choose it for this chart."
        count = min(args.get("count", 50), 5000)
        candles = await fetch_candles(symbol, granularity, count)
        gran_label = GRAN_LABEL.get(granularity, f"{granularity}s")
        lines = [f"📊 {symbol} — {gran_label} Candles (last {len(candles)})\n"]
        lines.append(f"{'Time (UTC)':<20} {'Open':>10} {'High':>10} {'Low':>10} {'Close':>10}")
        lines.append("-" * 62)
        for c in candles[-30:]:
            ts = datetime.utcfromtimestamp(c["epoch"]).strftime("%Y-%m-%d %H:%M")
            lines.append(f"{ts:<20} {c['open']:>10} {c['high']:>10} {c['low']:>10} {c['close']:>10}")
        if candles:
            change = float(candles[-1]["close"]) - float(candles[0]["open"])
            pct = change / float(candles[0]["open"]) * 100
            lines.append(f"\nPeriod change: {change:+.5f} ({pct:+.2f}%)")
        return _chart_text(
            "\n".join(lines),
            _chart_payload(symbol, granularity, candles),
        )

    # ── deriv_get_tick_history ────────────────────────────────────────────────
    elif name == "deriv-get-tick-history":
        symbol = args.get("symbol", "frxXAUUSD")
        count = min(args.get("count", 100), 5000)
        resp = await deriv_request({
            "ticks_history": symbol, "adjust_start_time": 1,
            "count": count, "end": "latest", "style": "ticks"
        })
        if "error" in resp:
            return f"Error: {resp['error'].get('message', resp['error'])}"
        history = resp.get("history", {})
        prices = history.get("prices", [])
        times = history.get("times", [])
        if not prices:
            return "No tick data returned."
        change = float(prices[-1]) - float(prices[0])
        pct = change / float(prices[0]) * 100
        lines = [f"📉 {symbol} — Tick History (last {len(prices)} ticks)\n",
                 f"Latest : {prices[-1]}", f"High   : {max(prices)}",
                 f"Low    : {min(prices)}", f"Change : {change:+.5f} ({pct:+.2f}%)"]
        if times:
            lines.append(f"From   : {datetime.utcfromtimestamp(times[0]).strftime('%H:%M:%S UTC')}")
            lines.append(f"To     : {datetime.utcfromtimestamp(times[-1]).strftime('%H:%M:%S UTC')}")
        lines.append("\nRecent 10 ticks:")
        for p, t in zip(prices[-10:], times[-10:]):
            lines.append(f"  {datetime.utcfromtimestamp(t).strftime('%H:%M:%S')}  {p}")
        return "\n".join(lines)

    # ── deriv_active_symbols ──────────────────────────────────────────────────
    elif name == "deriv-active-symbols":
        market_filter = args.get("market", "")
        resp = await deriv_request({"active_symbols": "brief", "product_type": "basic"})
        if "error" in resp:
            return f"Error: {resp['error'].get('message', resp['error'])}"
        symbols = resp.get("active_symbols", [])
        if market_filter:
            symbols = [s for s in symbols
                       if s.get("market", "").lower() == market_filter.lower()
                       or s.get("market_display_name", "").lower() == market_filter.lower()]
        lines = [f"🗂️ Deriv Active Symbols ({len(symbols)} found)\n",
                 f"{'Symbol':<20} {'Display Name':<30} {'Market':<20} {'Pip'}",
                 "-" * 80]
        for s in symbols[:80]:
            lines.append(f"{s.get('symbol',''):<20} {s.get('display_name','')[:29]:<30} "
                         f"{s.get('market_display_name','')[:19]:<20} {s.get('pip','')}")
        if len(symbols) > 80:
            lines.append(f"\n... and {len(symbols)-80} more")
        return "\n".join(lines)

    # ── deriv_market_snapshot ─────────────────────────────────────────────────
    elif name == "deriv-market-snapshot":
        symbols = args.get("symbols", ["frxXAUUSD","frxXAGUSD","frxEURUSD","frxGBPUSD","frxUSDJPY"])

        async def _snapshot_one(sym: str):
            resp = await deriv_request({
                "ticks_history": sym,
                "count": 1,
                "end": "latest",
                "granularity": 60,
                "style": "candles"
            })
            if "error" in resp:
                return sym, None
            candles = resp.get("candles", [])
            if not candles:
                return sym, None
            c = candles[-1]
            return sym, {
                "price": float(c.get("close", 0)),
                "open":  float(c.get("open",  0)),
                "high":  float(c.get("high",  0)),
                "low":   float(c.get("low",   0)),
                "epoch": c.get("epoch")
            }

        results = await asyncio.gather(*[_snapshot_one(s) for s in symbols], return_exceptions=True)
        lines = ["📊 Deriv Market Snapshot\n",
                 f"{'Symbol':<15} {'Price':>12} {'High':>12} {'Low':>12}", "-" * 55]
        for item in results:
            if isinstance(item, Exception):
                continue
            sym, data = item
            if data is None:
                lines.append(f"{sym:<15} {'ERROR':>12}")
            else:
                ts = datetime.utcfromtimestamp(data["epoch"]).strftime("%H:%M UTC") if data["epoch"] else ""
                lines.append(f"{sym:<15} {data['price']:>12} {data['high']:>12} {data['low']:>12}  {ts}")
        lines.append(f"\nSnapshot at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
        return "\n".join(lines)

    # ── deriv_pip_size ────────────────────────────────────────────────────────
    elif name == "deriv-pip-size":
        symbol = args.get("symbol", "frxXAUUSD")
        resp = await deriv_request({"active_symbols": "full", "product_type": "basic"})
        if "error" in resp:
            return f"Error: {resp['error'].get('message', resp['error'])}"
        match = next((s for s in resp.get("active_symbols", []) if s.get("symbol") == symbol), None)
        if not match:
            return f"Symbol '{symbol}' not found on Deriv."
        lines = [f"ℹ️ {symbol} — Instrument Info\n"]
        for k, v in sorted(match.items()):
            lines.append(f"{k:<30}: {v}")
        return "\n".join(lines)

    # ── deriv_rsi ─────────────────────────────────────────────────────────────
    elif name == "deriv-rsi":
        symbol = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity")
        if granularity is None:
            return "Error: granularity/timeframe is required; choose it for this indicator."
        period = args.get("period", 14)
        count = max(args.get("count", 100), period * 3)

        candles = await fetch_candles(symbol, granularity, count)
        _, _, _, closes = candles_to_ohlcv(candles)

        rsi = calc_rsi(closes, period)
        if rsi is None:
            return f"Not enough data for RSI({period}). Need at least {period+1} candles."

        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        price = closes[-1]

        if rsi >= 70:
            signal = "🔴 OVERBOUGHT — potential reversal down / sell zone"
        elif rsi <= 30:
            signal = "🟢 OVERSOLD — potential reversal up / buy zone"
        elif rsi >= 60:
            signal = "📈 Bullish momentum (approaching overbought)"
        elif rsi <= 40:
            signal = "📉 Bearish momentum (approaching oversold)"
        else:
            signal = "⚖️ Neutral — no clear RSI signal"

        text = (
            f"📊 RSI({period}) — {symbol} {gran}\n\n"
            f"RSI Value  : {rsi}\n"
            f"Signal     : {signal}\n"
            f"Close Price: {price}\n"
            f"Candles    : {len(candles)} ({gran})\n"
            f"Time       : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        rsi_series = _rsi_series_incremental(closes, period)
        return _chart_text(
            text,
            _chart_payload(
                symbol,
                granularity,
                candles,
                panels=[{
                    "name": f"RSI({period})",
                    "type": "line",
                    "min": 0,
                    "max": 100,
                    "color": "#a855f7",
                    "points": _series_points(candles, rsi_series, period),
                }],
            ),
        )

    # ── deriv_macd ────────────────────────────────────────────────────────────
    elif name == "deriv-macd":
        symbol = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity")
        if granularity is None:
            return "Error: granularity/timeframe is required; choose it for this indicator."
        fast = args.get("fast", 12)
        slow = args.get("slow", 26)
        signal = args.get("signal", 9)
        count = max(args.get("count", 150), slow + signal + 10)

        candles = await fetch_candles(symbol, granularity, count)
        _, _, _, closes = candles_to_ohlcv(candles)

        result = calc_macd(closes, fast, slow, signal)
        if result is None:
            return f"Not enough data for MACD({fast},{slow},{signal})."

        macd_val, signal_val, hist = result
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")

        if hist > 0:
            momentum = "🟢 Bullish — MACD above signal, histogram positive"
            cross = "Bullish crossover active" if macd_val > signal_val else ""
        else:
            momentum = "🔴 Bearish — MACD below signal, histogram negative"
            cross = "Bearish crossover active" if macd_val < signal_val else ""

        bullish = macd_val > 0
        trend = f"MACD line {'above' if bullish else 'below'} zero = {'uptrend' if bullish else 'downtrend'} territory"

        text = (
            f"📊 MACD({fast},{slow},{signal}) — {symbol} {gran}\n\n"
            f"MACD Line  : {macd_val}\n"
            f"Signal Line: {signal_val}\n"
            f"Histogram  : {hist:+}\n\n"
            f"Momentum   : {momentum}\n"
            f"Trend Zone : {trend}\n"
            f"Candles    : {len(candles)} ({gran})\n"
            f"Time       : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        hist_series, _ = _macd_histogram_series(closes, fast, slow, signal)
        return _chart_text(
            text,
            _chart_payload(
                symbol,
                granularity,
                candles,
                panels=[{
                    "name": f"MACD({fast},{slow},{signal}) histogram",
                    "type": "bar",
                    "color": "#0ea5e9",
                    "points": _series_points(
                        candles,
                        hist_series,
                        (slow - 1) + (signal - 1),
                    ),
                }],
            ),
        )

    # ── deriv_bbands ──────────────────────────────────────────────────────────
    elif name == "deriv-bbands":
        symbol = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity")
        if granularity is None:
            return "Error: granularity/timeframe is required; choose it for this indicator."
        period = args.get("period", 20)
        std_mult = args.get("std_mult", 2.0)
        count = max(args.get("count", 100), period * 3)

        candles = await fetch_candles(symbol, granularity, count)
        _, _, _, closes = candles_to_ohlcv(candles)

        result = calc_bbands(closes, period, std_mult)
        if result is None:
            return f"Not enough data for BB({period})."

        upper, mid, lower = result
        price = closes[-1]
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        band_width = round(upper - lower, 5)
        pct_b = round((price - lower) / (upper - lower) * 100, 1) if upper != lower else 50

        if price >= upper:
            zone = "🔴 Price AT/ABOVE upper band — overbought, watch for reversal"
        elif price >= mid + (upper - mid) * 0.7:
            zone = "📈 Price near upper band — bullish but stretched"
        elif price <= lower:
            zone = "🟢 Price AT/BELOW lower band — oversold, watch for bounce"
        elif price <= mid - (mid - lower) * 0.7:
            zone = "📉 Price near lower band — bearish but stretched"
        else:
            zone = "⚖️ Price near middle band — no extreme signal"

        text = (
            f"📊 Bollinger Bands({period}, {std_mult}) — {symbol} {gran}\n\n"
            f"Upper Band : {upper}\n"
            f"Middle (SMA): {mid}\n"
            f"Lower Band : {lower}\n"
            f"Band Width : {band_width}\n"
            f"Current Price: {price}\n"
            f"%B (position): {pct_b}% (0=lower, 50=mid, 100=upper)\n\n"
            f"Signal     : {zone}\n"
            f"Candles    : {len(candles)} ({gran})\n"
            f"Time       : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        upper_series, middle_series, lower_series = _bbands_series(closes, period, std_mult)
        return _chart_text(
            text,
            _chart_payload(
                symbol,
                granularity,
                candles,
                overlays=[
                    {"name": "Upper", "color": "#f97316", "points": _series_points(candles, upper_series, period - 1)},
                    {"name": "Middle", "color": "#94a3b8", "points": _series_points(candles, middle_series, period - 1)},
                    {"name": "Lower", "color": "#f97316", "points": _series_points(candles, lower_series, period - 1)},
                ],
            ),
        )

    # ── deriv_ema ─────────────────────────────────────────────────────────────
    elif name == "deriv-ema":
        symbol = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity")
        if granularity is None:
            return "Error: granularity/timeframe is required; choose it for this indicator."
        periods = args.get("periods", [9, 21, 50, 100, 200])
        count = max(args.get("count", 250), max(periods) * 2)

        candles = await fetch_candles(symbol, granularity, count)
        _, _, _, closes = candles_to_ohlcv(candles)

        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        price = closes[-1]
        lines = [f"📊 EMA — {symbol} {gran}\n", f"Current Price: {price}\n"]

        ema_values = {}
        for p in sorted(periods):
            val = calc_ema_series(closes, p)
            ema_values[p] = val
            if val:
                diff = round(price - val, 5)
                pct = round(diff / val * 100, 3)
                above = "✅ Above" if price > val else "❌ Below"
                lines.append(f"EMA{p:<5}: {val}  ({above} by {abs(diff):.5f} / {abs(pct):.3f}%)")
            else:
                lines.append(f"EMA{p:<5}: N/A (need more data)")

        # Trend alignment
        valid_emas = [(p, v) for p, v in ema_values.items() if v]
        if len(valid_emas) >= 2:
            sorted_emas = sorted(valid_emas, key=lambda x: x[0])
            ascending = all(sorted_emas[i][1] >= sorted_emas[i+1][1] for i in range(len(sorted_emas)-1))
            descending = all(sorted_emas[i][1] <= sorted_emas[i+1][1] for i in range(len(sorted_emas)-1))
            if ascending:
                lines.append("\n🟢 BULLISH ALIGNMENT — short EMA > long EMA (strong uptrend)")
            elif descending:
                lines.append("\n🔴 BEARISH ALIGNMENT — short EMA < long EMA (strong downtrend)")
            else:
                lines.append("\n⚠️ MIXED — EMAs not aligned (no clear trend)")

        lines.append(f"\nCandles: {len(candles)} | Time: {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}")
        overlays = []
        for p in sorted(periods):
            values = _ema(closes, p)
            if values:
                overlays.append({
                    "name": f"EMA{p}",
                    "color": ["#38bdf8", "#a78bfa", "#fbbf24", "#fb7185", "#4ade80"][len(overlays) % 5],
                    "points": _series_points(candles, values, p - 1),
                })
        return _chart_text(
            "\n".join(lines),
            _chart_payload(symbol, granularity, candles, overlays=overlays),
        )

    # ── deriv_atr ─────────────────────────────────────────────────────────────
    elif name == "deriv-atr":
        symbol = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        period = args.get("period", 14)
        count = max(args.get("count", 100), period * 3)

        candles = await fetch_candles(symbol, granularity, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)

        atr = calc_atr(highs, lows, closes, period)
        if atr is None:
            return f"Not enough data for ATR({period})."

        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        price = closes[-1]
        atr_pct = round(atr / price * 100, 3)

        sl_1x = round(atr * 1.0, 5)
        sl_15x = round(atr * 1.5, 5)
        sl_2x = round(atr * 2.0, 5)
        tp_2r = round(atr * 2.0, 5)
        tp_3r = round(atr * 3.0, 5)

        volatility = "🔴 High" if atr_pct > 0.5 else ("🟡 Medium" if atr_pct > 0.2 else "🟢 Low")

        return (
            f"📊 ATR({period}) — {symbol} {gran}\n\n"
            f"ATR Value  : {atr}\n"
            f"ATR % Price: {atr_pct}%\n"
            f"Volatility : {volatility}\n"
            f"Price      : {price}\n\n"
            f"── SL Suggestions ──\n"
            f"SL (1× ATR): {sl_1x}  → tight\n"
            f"SL (1.5×)  : {sl_15x} → balanced\n"
            f"SL (2× ATR): {sl_2x}  → wider, safer\n\n"
            f"── TP Suggestions ──\n"
            f"TP (2× ATR): {tp_2r}  → 1:2 R:R\n"
            f"TP (3× ATR): {tp_3r}  → 1:3 R:R\n\n"
            f"Candles    : {len(candles)} ({gran})\n"
            f"Time       : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # ── deriv_stoch ───────────────────────────────────────────────────────────
    elif name == "deriv-stoch":
        symbol = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        k_period = args.get("k_period", 14)
        d_period = args.get("d_period", 3)
        count = max(args.get("count", 100), (k_period + d_period) * 3)

        candles = await fetch_candles(symbol, granularity, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)

        result = calc_stoch(highs, lows, closes, k_period, d_period)
        if result is None:
            return f"Not enough data for Stochastic({k_period},{d_period})."

        k, d = result
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")

        if k >= 80 and d >= 80:
            signal = "🔴 OVERBOUGHT — both %K and %D above 80, watch for reversal down"
        elif k <= 20 and d <= 20:
            signal = "🟢 OVERSOLD — both %K and %D below 20, watch for bounce up"
        elif k > d and k < 80:
            signal = "📈 Bullish — %K crossed above %D (buy signal)"
        elif k < d and k > 20:
            signal = "📉 Bearish — %K crossed below %D (sell signal)"
        else:
            signal = "⚖️ Neutral"

        return (
            f"📊 Stochastic({k_period},{d_period}) — {symbol} {gran}\n\n"
            f"%K (fast)  : {k}\n"
            f"%D (slow)  : {d}\n"
            f"Signal     : {signal}\n"
            f"Candles    : {len(candles)} ({gran})\n"
            f"Time       : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # ── deriv_technical_analysis ──────────────────────────────────────────────
    elif name == "deriv-technical-analysis":
        symbol = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        count = max(args.get("count", 350), 350)

        candles = await fetch_candles(symbol, granularity, count)
        opens, highs, lows, closes = candles_to_ohlcv(candles)
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        price = closes[-1]
        ts = datetime.utcfromtimestamp(candles[-1]["epoch"]).strftime("%Y-%m-%d %H:%M UTC")

        # ── All indicators ──
        rsi       = calc_rsi(closes, 14)
        macd_res  = calc_macd(closes, 12, 26, 9)
        bb_res    = calc_bbands(closes, 20, 2.0)
        atr       = calc_atr(highs, lows, closes, 14)
        stoch_res = calc_stoch(highs, lows, closes, 14, 3)
        adx_res   = calc_adx(highs, lows, closes, 14)
        ema9      = calc_ema_series(closes, 9)
        ema21     = calc_ema_series(closes, 21)
        ema50     = calc_ema_series(closes, 50)
        ema200    = calc_ema_series(closes, 200)

        # ── Swing points & S/R ──
        swings    = calc_swing_points(highs, lows, lookback=5)
        sr        = calc_support_resistance(swings["highs"], swings["lows"], price)
        mstruct   = calc_market_structure(swings["highs"], swings["lows"])
        rsi_div   = calc_rsi_divergence(highs, lows, closes)
        macd_div  = calc_macd_divergence(highs, lows, closes)

        # ── ADX-gated signal scoring ──
        is_trending = adx_res["trending"] if adx_res else True
        bullish_signals = 0
        bearish_signals = 0
        signals = []

        # Market structure (highest weight — price action)
        if mstruct["structure"] == "bullish":
            bullish_signals += 4; signals.append("✅ Market Structure: Bullish (HH+HL)")
        elif mstruct["structure"] == "bearish":
            bearish_signals += 4; signals.append("⚠️ Market Structure: Bearish (LH+LL)")

        # RSI
        if rsi:
            if rsi < 30:   bullish_signals += 3; signals.append(f"✅ RSI oversold ({rsi})")
            elif rsi > 70: bearish_signals += 3; signals.append(f"⚠️ RSI overbought ({rsi})")
            elif rsi > 55: bullish_signals += 1; signals.append(f"📈 RSI bullish ({rsi})")
            elif rsi < 45: bearish_signals += 1; signals.append(f"📉 RSI bearish ({rsi})")

        # RSI Divergence (strong reversal signal)
        if rsi_div["bullish"]:
            bullish_signals += 3; signals.append(f"✅ RSI Bullish Divergence — {rsi_div['detail']}")
        elif rsi_div["bearish"]:
            bearish_signals += 3; signals.append(f"⚠️ RSI Bearish Divergence — {rsi_div['detail']}")

        # MACD (only meaningful in trending market)
        if macd_res:
            mv, sv, hv = macd_res
            if is_trending:
                if hv > 0 and mv > sv: bullish_signals += 2; signals.append("✅ MACD bullish crossover")
                elif hv < 0 and mv < sv: bearish_signals += 2; signals.append("⚠️ MACD bearish crossover")
            if mv > 0: bullish_signals += 1; signals.append("📈 MACD above zero")
            else: bearish_signals += 1; signals.append("📉 MACD below zero")

        # MACD Divergence
        if macd_div["bullish"]:
            bullish_signals += 2; signals.append(f"✅ MACD Divergence Bullish — {macd_div['detail']}")
        elif macd_div["bearish"]:
            bearish_signals += 2; signals.append(f"⚠️ MACD Divergence Bearish — {macd_div['detail']}")

        # Bollinger Bands
        if bb_res:
            upper, mid_bb, lower = bb_res
            if price < lower: bullish_signals += 2; signals.append("✅ Price below lower BB (oversold zone)")
            elif price > upper: bearish_signals += 2; signals.append("⚠️ Price above upper BB (overbought zone)")
            elif price > mid_bb: bullish_signals += 1; signals.append("📈 Price above BB midline")
            else: bearish_signals += 1; signals.append("📉 Price below BB midline")

        # EMA alignment + EMA200 (macro trend filter)
        if ema9 and ema21 and ema50:
            if ema9 > ema21 > ema50: bullish_signals += 3; signals.append("✅ EMA bullish stack (9>21>50)")
            elif ema9 < ema21 < ema50: bearish_signals += 3; signals.append("⚠️ EMA bearish stack (9<21<50)")
            if ema200:
                if price > ema200: bullish_signals += 2; signals.append(f"✅ Price above EMA200 (macro uptrend)")
                else: bearish_signals += 2; signals.append(f"⚠️ Price below EMA200 (macro downtrend)")

        # Stochastic
        if stoch_res:
            k, d = stoch_res
            if k < 20 and d < 20: bullish_signals += 2; signals.append(f"✅ Stoch oversold (%K={k})")
            elif k > 80 and d > 80: bearish_signals += 2; signals.append(f"⚠️ Stoch overbought (%K={k})")
            elif k > d: bullish_signals += 1; signals.append("📈 Stoch %K > %D")
            else: bearish_signals += 1; signals.append("📉 Stoch %K < %D")

        # ADX direction (only if trending)
        if adx_res and adx_res["trending"]:
            if adx_res["di_bias"] == "bullish": bullish_signals += 2; signals.append(f"✅ ADX +DI>{adx_res['plus_di']} (trend bullish, kuat)")
            else: bearish_signals += 2; signals.append(f"⚠️ ADX -DI>{adx_res['minus_di']} (trend bearish, kuat)")

        total = bullish_signals + bearish_signals
        bull_pct = round(bullish_signals / total * 100) if total else 50

        if bull_pct >= 70:   overall = "🟢 STRONG BUY"; bias = "BUY"
        elif bull_pct >= 58: overall = "📈 BUY (konfirmasi dulu)"; bias = "WEAK BUY"
        elif bull_pct <= 30: overall = "🔴 STRONG SELL"; bias = "SELL"
        elif bull_pct <= 42: overall = "📉 SELL (konfirmasi dulu)"; bias = "WEAK SELL"
        else:                overall = "⚖️ NEUTRAL — tunggu sinyal jelas"; bias = "NEUTRAL"

        # ADX override: jika sideways (ADX < 20), downgrade semua sinyal
        if adx_res and not adx_res["trending"] and bias not in ("NEUTRAL",):
            overall += f" ⚠️ [ADX={adx_res['adx']} Sideways — sinyal kurang reliable]"

        # Smart SL/TP
        atr_str = f"{atr}" if atr else "N/A"
        sl_tp_lines = []
        if atr and bias != "NEUTRAL":
            sl = smart_sl_snap(bias if bias in ("BUY","SELL") else ("BUY" if "BUY" in bias else "SELL"),
                               price, swings["highs"], swings["lows"], atr)
            sl_dist = abs(price - sl)
            tp1 = round(price + sl_dist * 2.0, 5) if "BUY" in bias else round(price - sl_dist * 2.0, 5)
            tp2 = round(price + sl_dist * 3.0, 5) if "BUY" in bias else round(price - sl_dist * 3.0, 5)
            rr1 = round(abs(tp1 - price) / sl_dist, 1) if sl_dist else 0
            rr2 = round(abs(tp2 - price) / sl_dist, 1) if sl_dist else 0
            sl_note = "(swing low)" if "BUY" in bias else "(swing high)"
            sl_tp_lines = [
                f"\n── Entry Suggestion ({bias}) ──",
                f"Entry  : ~{price}",
                f"SL     : {sl}  {sl_note}",
                f"TP1    : {tp1}  (R:R 1:{rr1})",
                f"TP2    : {tp2}  (R:R 1:{rr2})",
            ]
            if sr["support"]: sl_tp_lines.append(f"Support: {sr['support']}")
            if sr["resistance"]: sl_tp_lines.append(f"Resist : {sr['resistance']}")

        lines = [
            f"🔬 Full Technical Analysis — {symbol} {gran}",
            f"{'═'*58}",
            f"Price          : {price}",
            f"Time           : {ts}",
            f"Candles        : {len(candles)}",
            f"",
            f"── Market Context ──────────────────────────────",
            f"Structure      : {mstruct['label']}",
            f"ADX({14})       : {adx_res['adx'] if adx_res else 'N/A'} — {adx_res['strength'] if adx_res else 'N/A'}",
            f"  +DI / -DI   : {adx_res['plus_di'] if adx_res else 'N/A'} / {adx_res['minus_di'] if adx_res else 'N/A'}",
            f"",
            f"── S/R Levels ───────────────────────────────────",
            f"Resistance     : {sr['resistance'] or 'N/A'}",
            f"Support        : {sr['support'] or 'N/A'}",
            f"",
            f"── Indicators ───────────────────────────────────",
            f"RSI(14)        : {rsi or 'N/A'}" + (" 🔴" if rsi and rsi>70 else " 🟢" if rsi and rsi<30 else ""),
            f"MACD           : {f'{macd_res[0]:+}  Hist: {macd_res[2]:+}' if macd_res else 'N/A'}",
            f"BB Upper/Lo    : {f'{bb_res[0]} / {bb_res[2]}' if bb_res else 'N/A'}  (mid: {bb_res[1] if bb_res else 'N/A'})",
            f"EMA 9/21/50    : {ema9} / {ema21} / {ema50}",
            f"EMA200         : {ema200 or 'N/A'}",
            f"ATR(14)        : {atr_str}",
            f"Stoch %K/%D    : {f'{stoch_res[0]} / {stoch_res[1]}' if stoch_res else 'N/A'}",
            f"",
            f"── Divergence ───────────────────────────────────",
            f"RSI Div        : {'✅ Bullish — '+rsi_div['detail'] if rsi_div['bullish'] else '⚠️ Bearish — '+rsi_div['detail'] if rsi_div['bearish'] else 'None'}",
            f"MACD Div       : {'✅ Bullish — '+macd_div['detail'] if macd_div['bullish'] else '⚠️ Bearish — '+macd_div['detail'] if macd_div['bearish'] else 'None'}",
            f"",
            f"── Signals ──────────────────────────────────────",
        ]
        lines.extend(signals)
        lines += [
            f"",
            f"── Overall ──────────────────────────────────────",
            f"Bull/Bear      : {bullish_signals}/{bearish_signals} ({bull_pct}% bullish)",
            f"Bias           : {overall}",
        ]
        lines.extend(sl_tp_lines)
        return "\n".join(lines)

    # ── deriv_smart_analysis ──────────────────────────────────────────────────
    elif name == "deriv-smart-analysis":
        symbol = args.get("symbol", "frxXAUUSD")

        # Fetch 3 timeframes + extra candles for divergence computation
        d1_candles, h4_candles, h1_candles = await asyncio.gather(
            fetch_candles(symbol, 86400, 250),   # D1 — trend + structure
            fetch_candles(symbol, 14400, 250),   # H4 — setup
            fetch_candles(symbol, 3600,  300),   # H1 — entry + divergence
        )

        def _score_tf(candles: list[dict], label: str) -> dict:
            """
            Professional scoring per timeframe.
            Weights: Market Structure > Divergence > EMA200 > EMA Stack > ADX >
                     RSI extreme > MACD cross > Stoch > BB > others.
            """
            opens_, highs_, lows_, closes_ = candles_to_ohlcv(candles)
            price_ = closes_[-1]
            bull = 0; bear = 0; sigs = []

            # 1. Market structure (price action — highest conviction)
            sw  = calc_swing_points(highs_, lows_, lookback=5)
            ms  = calc_market_structure(sw["highs"], sw["lows"])
            sr_ = calc_support_resistance(sw["highs"], sw["lows"], price_)
            if ms["structure"] == "bullish":
                bull += 4; sigs.append(f"Structure: Bullish HH+HL")
            elif ms["structure"] == "bearish":
                bear += 4; sigs.append(f"Structure: Bearish LH+LL")

            # 2. ADX — trending vs sideways
            adx = calc_adx(highs_, lows_, closes_, 14)
            is_trend = adx["trending"] if adx else True
            if adx:
                if adx["trending"]:
                    if adx["di_bias"] == "bullish": bull += 2; sigs.append(f"ADX trend bullish (+DI={adx['plus_di']})")
                    else: bear += 2; sigs.append(f"ADX trend bearish (-DI={adx['minus_di']})")

            # 3. EMA200 macro filter
            ema200_ = calc_ema_series(closes_, 200)
            if ema200_:
                if price_ > ema200_: bull += 2; sigs.append("Above EMA200 (macro bull)")
                else: bear += 2; sigs.append("Below EMA200 (macro bear)")

            # 4. EMA stack
            ema9_  = calc_ema_series(closes_, 9)
            ema21_ = calc_ema_series(closes_, 21)
            ema50_ = calc_ema_series(closes_, 50)
            if ema9_ and ema21_ and ema50_:
                if ema9_ > ema21_ > ema50_: bull += 3; sigs.append("EMA stack bullish (9>21>50)")
                elif ema9_ < ema21_ < ema50_: bear += 3; sigs.append("EMA stack bearish (9<21<50)")

            # 5. RSI + divergence
            rsi_ = calc_rsi(closes_, 14)
            if rsi_:
                if rsi_ < 30: bull += 3; sigs.append(f"RSI oversold ({rsi_})")
                elif rsi_ > 70: bear += 3; sigs.append(f"RSI overbought ({rsi_})")
                elif rsi_ > 55: bull += 1; sigs.append(f"RSI bullish ({rsi_})")
                elif rsi_ < 45: bear += 1; sigs.append(f"RSI bearish ({rsi_})")
            rsi_dv = calc_rsi_divergence(highs_, lows_, closes_)
            if rsi_dv["bullish"]: bull += 3; sigs.append(f"RSI div bullish")
            elif rsi_dv["bearish"]: bear += 3; sigs.append(f"RSI div bearish")

            # 6. MACD (stronger weight in trending market)
            macd_ = calc_macd(closes_, 12, 26, 9)
            macd_dv = calc_macd_divergence(highs_, lows_, closes_)
            if macd_:
                mv, sv, hv = macd_
                w = 2 if is_trend else 1
                if hv > 0 and mv > sv: bull += w; sigs.append("MACD bullish cross")
                elif hv < 0 and mv < sv: bear += w; sigs.append("MACD bearish cross")
                if mv > 0: bull += 1; sigs.append("MACD > 0")
                else: bear += 1; sigs.append("MACD < 0")
            if macd_dv["bullish"]: bull += 2; sigs.append("MACD div bullish")
            elif macd_dv["bearish"]: bear += 2; sigs.append("MACD div bearish")

            # 7. Bollinger Bands
            bb_ = calc_bbands(closes_, 20, 2.0)
            if bb_:
                upper_, mid_bb_, lower_ = bb_
                if price_ < lower_: bull += 2; sigs.append("Price below lower BB")
                elif price_ > upper_: bear += 2; sigs.append("Price above upper BB")
                elif price_ > mid_bb_: bull += 1; sigs.append("Above BB mid")
                else: bear += 1; sigs.append("Below BB mid")

            # 8. Stochastic
            stoch_ = calc_stoch(highs_, lows_, closes_, 14, 3)
            if stoch_:
                k_, d_ = stoch_
                if k_ < 20 and d_ < 20: bull += 2; sigs.append(f"Stoch oversold ({k_})")
                elif k_ > 80 and d_ > 80: bear += 2; sigs.append(f"Stoch overbought ({k_})")
                elif k_ > d_: bull += 1; sigs.append("Stoch %K>%D")
                else: bear += 1; sigs.append("Stoch %K<%D")

            atr_ = calc_atr(highs_, lows_, closes_, 14)
            total_ = bull + bear
            pct_  = round(bull / total_ * 100) if total_ else 50
            return {
                "price": price_, "bull": bull, "bear": bear, "pct": pct_,
                "signals": sigs, "atr": atr_, "rsi": rsi_,
                "ema9": ema9_, "ema21": ema21_, "ema50": ema50_, "ema200": ema200_,
                "macd": macd_, "bb": bb_, "stoch": stoch_,
                "adx": adx, "market_structure": ms, "sr": sr_,
                "rsi_div": rsi_dv, "macd_div": macd_dv,
                "swing_highs": sw["highs"], "swing_lows": sw["lows"],
                "is_trending": is_trend
            }

        d1 = _score_tf(d1_candles, "D1")
        h4 = _score_tf(h4_candles, "H4")
        h1 = _score_tf(h1_candles, "H1")

        current_price = h1["price"]
        ts = datetime.utcfromtimestamp(h1_candles[-1]["epoch"]).strftime("%Y-%m-%d %H:%M UTC")

        # ── Confluence (D1=4x, H4=2x, H1=1x — D1 is master trend) ──
        total_bull = d1["bull"] * 4 + h4["bull"] * 2 + h1["bull"]
        total_bear = d1["bear"] * 4 + h4["bear"] * 2 + h1["bear"]
        total_all  = total_bull + total_bear
        confluence_pct = round(total_bull / total_all * 100) if total_all else 50

        # ── Sideways penalty: if D1 ADX < 20, reduce confidence ──
        d1_sideways = d1["adx"] and not d1["adx"]["trending"]
        h4_sideways = h4["adx"] and not h4["adx"]["trending"]

        def _trend_label(pct, is_sw=False):
            suffix = " [Sideways]" if is_sw else ""
            if pct >= 65: return f"🟢 BULLISH{suffix}"
            elif pct >= 55: return f"📈 Sedikit Bullish{suffix}"
            elif pct <= 35: return f"🔴 BEARISH{suffix}"
            elif pct <= 45: return f"📉 Sedikit Bearish{suffix}"
            else: return f"⚖️ NEUTRAL{suffix}"

        d1_trend = _trend_label(d1["pct"], d1_sideways)
        h4_trend = _trend_label(h4["pct"], h4_sideways)
        h1_trend = _trend_label(h1["pct"])

        # ── Overall bias ──
        if confluence_pct >= 68:
            bias = "BUY"; bias_label = "🟢 STRONG BUY"; confidence = "Tinggi"
        elif confluence_pct >= 58:
            bias = "BUY"; bias_label = "📈 BUY (lemah, tunggu konfirmasi)"; confidence = "Sedang"
        elif confluence_pct <= 32:
            bias = "SELL"; bias_label = "🔴 STRONG SELL"; confidence = "Tinggi"
        elif confluence_pct <= 42:
            bias = "SELL"; bias_label = "📉 SELL (lemah, tunggu konfirmasi)"; confidence = "Sedang"
        else:
            bias = "WAIT"; bias_label = "⚖️ TUNGGU — sinyal belum jelas"; confidence = "Rendah"

        # D1 sideways override — lower confidence
        if d1_sideways and bias in ("BUY", "SELL"):
            confidence = "Rendah"
            bias_label += " ⚠️ (D1 sideways — hati-hati)"

        # ── Smart SL/TP — snap to nearest swing ──
        h1_atr = h1["atr"] or 0
        sl_tp_section = ""
        if h1_atr and bias != "WAIT":
            entry = current_price
            sl = smart_sl_snap(bias, entry, h1["swing_highs"], h1["swing_lows"], h1_atr)
            sl_dist = abs(entry - sl)

            # TP targets: nearest S/R level, then ATR multiples
            if bias == "BUY":
                r1 = h1["sr"]["resistance"]
                r2 = h4["sr"]["resistance"]
                tp1 = r1 if (r1 and r1 > entry + sl_dist * 1.2) else round(entry + sl_dist * 2.0, 5)
                tp2 = r2 if (r2 and r2 > tp1) else round(entry + sl_dist * 3.0, 5)
            else:
                s1 = h1["sr"]["support"]
                s2 = h4["sr"]["support"]
                tp1 = s1 if (s1 and s1 < entry - sl_dist * 1.2) else round(entry - sl_dist * 2.0, 5)
                tp2 = s2 if (s2 and s2 < tp1) else round(entry - sl_dist * 3.0, 5)

            rr1 = round(abs(tp1 - entry) / sl_dist, 1) if sl_dist else 0
            rr2 = round(abs(tp2 - entry) / sl_dist, 1) if sl_dist else 0

            sl_note = "↓ swing low" if bias == "BUY" else "↑ swing high"
            sl_tp_section = (
                f"\n── Entry Plan ({bias}) {'─'*30}\n"
                f"Entry  : ~{entry}\n"
                f"SL     : {sl}  ({sl_note}, dist: {round(sl_dist, 2)})\n"
                f"TP1    : {tp1}  (R:R 1:{rr1})\n"
                f"TP2    : {tp2}  (R:R 1:{rr2})\n"
                f"ATR(H1): {round(h1_atr, 3)}\n"
            )

        # ── Divergence summary ──
        div_lines = []
        for tf_name, tf_data in [("D1", d1), ("H4", h4), ("H1", h1)]:
            if tf_data["rsi_div"]["type"] != "none":
                icon = "✅" if tf_data["rsi_div"]["bullish"] else "⚠️"
                div_lines.append(f"  {icon} {tf_name} RSI Div {tf_data['rsi_div']['type'].upper()} — {tf_data['rsi_div']['detail']}")
            if tf_data["macd_div"]["type"] != "none":
                icon = "✅" if tf_data["macd_div"]["bullish"] else "⚠️"
                div_lines.append(f"  {icon} {tf_name} MACD Div {tf_data['macd_div']['type'].upper()} — {tf_data['macd_div']['detail']}")
        if not div_lines:
            div_lines.append("  — Tidak ada divergence terdeteksi")

        # ── Structure summary ──
        struct_lines = [
            f"D1: {d1['market_structure']['label']}  {' | '.join(d1['market_structure']['detail'])}",
            f"H4: {h4['market_structure']['label']}  {' | '.join(h4['market_structure']['detail'])}",
            f"H1: {h1['market_structure']['label']}  {' | '.join(h1['market_structure']['detail'])}",
        ]

        # ── Plain language explanation ──
        def _explain(bias, d1, h4, h1, confluence_pct, confidence):
            d1_dir  = "naik" if d1["pct"] >= 55 else ("turun" if d1["pct"] <= 45 else "sideways")
            h4_dir  = "naik" if h4["pct"] >= 55 else ("turun" if h4["pct"] <= 45 else "sideways")
            ms_ok   = d1["market_structure"]["structure"] == ("bullish" if bias == "BUY" else "bearish")
            adx_str = d1["adx"]["strength"] if d1["adx"] else "tidak diketahui"
            div_ok  = any(
                (tf["rsi_div"]["bullish"] or tf["macd_div"]["bullish"]) if bias == "BUY"
                else (tf["rsi_div"]["bearish"] or tf["macd_div"]["bearish"])
                for tf in [d1, h4, h1]
            )

            if bias == "BUY":
                return (
                    f"Tren harian sedang {d1_dir} ({adx_str}). "
                    f"{'Market structure bullish (HH+HL) terkonfirmasi. ' if ms_ok else 'Structure belum sepenuhnya bullish. '}"
                    f"H4 setup {'mendukung' if h4_dir == 'naik' else 'belum sepenuhnya selaras'}. "
                    f"{'Divergence bullish terdeteksi — momentum reversal naik. ' if div_ok else ''}"
                    f"Confluence {confluence_pct}% — keyakinan {confidence}."
                )
            elif bias == "SELL":
                return (
                    f"Tren harian sedang {d1_dir} ({adx_str}). "
                    f"{'Market structure bearish (LH+LL) terkonfirmasi. ' if ms_ok else 'Structure belum sepenuhnya bearish. '}"
                    f"H4 tekanan jual {'kuat' if h4_dir == 'turun' else 'belum sepenuhnya terkonfirmasi'}. "
                    f"{'Divergence bearish terdeteksi — momentum melemah. ' if div_ok else ''}"
                    f"Confluence {100-confluence_pct}% bearish — keyakinan {confidence}."
                )
            else:
                adx_note = f" ADX={d1['adx']['adx']} ({adx_str})." if d1["adx"] else ""
                return (
                    f"Tren harian {d1_dir}.{adx_note} "
                    f"Confluence {confluence_pct}% — sinyal belum cukup kuat dari satu arah. "
                    f"Lebih baik tunggu konfirmasi breakout atau candlestick pattern sebelum masuk."
                )

        # ── Market Regime Classification ──────────────────────────────────────
        regime_info = classify_market_regime(d1, h4, h1, current_price)
        regime      = regime_info["regime"]

        # ── Candlestick Patterns (H1 — entry timeframe) ───────────────────────
        h1_opens_, h1_highs_, h1_lows_, h1_closes_ = candles_to_ohlcv(h1_candles)
        h1_patterns = detect_candlestick_patterns(h1_opens_, h1_highs_, h1_lows_, h1_closes_)

        # ── TF Alignment Count (how many TFs agree with bias) ─────────────────
        tf_alignment = sum(1 for tf in [d1, h4, h1]
                          if (bias == "BUY" and tf["pct"] >= 55)
                          or (bias == "SELL" and tf["pct"] <= 45))

        # ── Divergence present anywhere? ──────────────────────────────────────
        has_divergence = any(
            (tf["rsi_div"]["bullish"] or tf["macd_div"]["bullish"]) if bias == "BUY"
            else (tf["rsi_div"]["bearish"] or tf["macd_div"]["bearish"])
            for tf in [d1, h4, h1]
        )

        # ── Near S/R? ─────────────────────────────────────────────────────────
        sr_near_buy  = h1["sr"]["support"] and abs(current_price - h1["sr"]["support"]) / current_price < 0.005
        sr_near_sell = h1["sr"]["resistance"] and abs(current_price - h1["sr"]["resistance"]) / current_price < 0.005
        near_sr      = (bias == "BUY" and sr_near_buy) or (bias == "SELL" and sr_near_sell)

        # ── Pattern at key level? ─────────────────────────────────────────────
        has_pattern = any("DOJI" not in p and "Tidak ada" not in p for p in h1_patterns)

        # ── Setup quality grade ───────────────────────────────────────────────
        grade_info = grade_setup_quality(
            regime         = regime,
            confluence_pct = confluence_pct,
            has_pattern    = has_pattern,
            has_divergence = has_divergence,
            near_sr        = near_sr,
            d1_trending    = not d1_sideways,
            tf_alignment   = tf_alignment
        )

        # ── Invalidation level ────────────────────────────────────────────────
        if bias == "BUY":
            inv_price = h1["sr"]["support"] or (round(current_price - h1_atr * 2, 5) if h1_atr else None)
            inv_note  = f"Harga close H1 DI BAWAH {inv_price} = setup BUY batal" if inv_price else "Harga buat lower low baru = setup batal"
        elif bias == "SELL":
            inv_price = h1["sr"]["resistance"] or (round(current_price + h1_atr * 2, 5) if h1_atr else None)
            inv_note  = f"Harga close H1 DI ATAS {inv_price} = setup SELL batal" if inv_price else "Harga buat higher high baru = setup batal"
        else:
            inv_note  = regime_info.get("invalidate", "Tunggu dulu sebelum masuk")

        # ── Regime-aware explanation ──────────────────────────────────────────
        adx_str  = d1["adx"]["strength"] if d1["adx"] else "tidak diketahui"
        d1_dir   = "naik" if d1["pct"] >= 55 else ("turun" if d1["pct"] <= 45 else "sideways")
        explanation = (
            f"{regime_info['icon']} Regime: {regime_info['name']}\n"
            f"Strategi    : {regime_info['strategy']}\n\n"
            f"{regime_info['approach']}\n\n"
            f"Tunggu      : {regime_info['watch_for']}\n"
            f"Invalidasi  : {inv_note}"
        )

        # ── Format final output ────────────────────────────────────────────────
        result = [
            f"🏆 Smart Analysis PRO — {symbol}",
            f"{'═' * 60}",
            f"Harga Sekarang : {current_price}",
            f"Waktu          : {ts}",
            f"",
            f"{'━'*60}",
            f"  {regime_info['icon']}  MARKET REGIME: {regime_info['name']}",
            f"  Strategi: {regime_info['strategy']}",
            f"{'━'*60}",
            f"",
            f"── Market Structure ─────────────────────────────────────",
        ]
        result.extend(struct_lines)
        result += [
            f"",
            f"── ADX (Trending vs Sideways) ───────────────────────────",
            f"D1 ADX  : {d1['adx']['adx'] if d1['adx'] else 'N/A'} — {d1['adx']['strength'] if d1['adx'] else 'N/A'}",
            f"H4 ADX  : {h4['adx']['adx'] if h4['adx'] else 'N/A'} — {h4['adx']['strength'] if h4['adx'] else 'N/A'}",
            f"H1 ADX  : {h1['adx']['adx'] if h1['adx'] else 'N/A'} — {h1['adx']['strength'] if h1['adx'] else 'N/A'}",
            f"",
            f"── Support / Resistance ─────────────────────────────────",
            f"H1 Resist  : {h1['sr']['resistance'] or 'N/A'}  |  Resist2: {h1['sr']['resistance2'] or 'N/A'}",
            f"H1 Support : {h1['sr']['support'] or 'N/A'}  |  Supp2 : {h1['sr']['support2'] or 'N/A'}",
            f"H4 Resist  : {h4['sr']['resistance'] or 'N/A'}",
            f"H4 Support : {h4['sr']['support'] or 'N/A'}",
            f"",
            f"── Divergence ───────────────────────────────────────────",
        ]
        result.extend(div_lines)
        result += [
            f"",
            f"── Candlestick Pattern (H1 candle terkini) ──────────────",
        ]
        result.extend(h1_patterns)
        result += [
            f"",
            f"── Timeframe Confluence ─────────────────────────────────",
            f"D1 (Trend×4)   : {d1_trend}  [{d1['pct']}% bull]",
            f"H4 (Setup×2)   : {h4_trend}  [{h4['pct']}% bull]",
            f"H1 (Entry×1)   : {h1_trend}  [{h1['pct']}% bull]",
            f"Aligned TFs    : {tf_alignment}/3",
            f"",
            f"── Confluence & Grade ───────────────────────────────────",
            f"Score          : {confluence_pct}% bullish  (D1×4 + H4×2 + H1×1)",
            f"Keyakinan      : {confidence}",
            f"",
            f"  {grade_info['label']}",
            f"  Position Size  : {grade_info['size_note']}",
            f"",
            f"── Keputusan ────────────────────────────────────────────",
            f"Signal         : {bias_label}",
            sl_tp_section,
            f"── Pendekatan (Regime-Specific) ─────────────────────────",
            explanation,
            f"",
            f"── Detail Indikator (H1) ────────────────────────────────",
            f"RSI(14)  : {h1['rsi'] or 'N/A'}",
            f"MACD     : {'Bullish ✅' if h1['macd'] and h1['macd'][2] > 0 else 'Bearish ⚠️' if h1['macd'] else 'N/A'}  Hist: {h1['macd'][2] if h1['macd'] else 'N/A'}",
            f"EMA Stack: {'9='+str(h1['ema9'])+' 21='+str(h1['ema21'])+' 50='+str(h1['ema50']) if h1['ema9'] else 'N/A'}",
            f"EMA200   : {h1['ema200'] or 'N/A'}",
            f"ATR(H1)  : {round(h1['atr'], 3) if h1['atr'] else 'N/A'}",
            f"Stoch    : %K={h1['stoch'][0] if h1['stoch'] else 'N/A'} %D={h1['stoch'][1] if h1['stoch'] else 'N/A'}",
            f"",
            f"⚠️  Analisis teknikal — bukan jaminan profit.",
            f"   Selalu gunakan manajemen risiko. Jangan risk lebih dari 1-2% modal per trade.",
        ]

        return "\n".join(str(r) for r in result)

    # ── deriv_fibonacci ───────────────────────────────────────────────────────
    elif name == "deriv-fibonacci":
        symbol       = args.get("symbol", "frxXAUUSD")
        granularity  = args.get("granularity", 14400)
        swing_lb     = args.get("swing_lookback", 50)
        count        = max(args.get("count", 200), swing_lb + 10)

        candles = await fetch_candles(symbol, granularity, count)
        opens, highs, lows, closes = candles_to_ohlcv(candles)
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        price = closes[-1]

        # Find swing high/low over lookback window
        window_h = highs[-swing_lb:]
        window_l = lows[-swing_lb:]
        swing_high = max(window_h)
        swing_low  = min(window_l)
        # Determine trend: is the most recent close closer to swing_high (up) or swing_low (down)?
        trend = "up" if (price - swing_low) >= (swing_high - price) else "down"

        fib = calc_fibonacci(swing_high, swing_low, trend)
        r   = fib["retracements"]
        e   = fib["extensions"]

        lines = [
            f"📐 Fibonacci — {symbol} {gran}",
            f"{'═'*54}",
            f"Current Price : {price}",
            f"Swing High    : {swing_high}",
            f"Swing Low     : {swing_low}",
            f"Trend Detected: {'↑ Uptrend (retracements = support)' if trend == 'up' else '↓ Downtrend (retracements = resistance)'}",
            f"",
            f"── Retracement Levels ({'support zones ↑' if trend == 'up' else 'resistance zones ↓'}) ──",
        ]
        for label, level in r.items():
            marker = " ◀ PRICE" if abs(level - price) / price < 0.002 else ""
            near_note = " (near)" if abs(level - price) / (swing_high - swing_low) < 0.05 else ""
            lines.append(f"  Fib {label:<8}: {level}{marker}{near_note}")

        lines += [f"", f"── Extension Levels (profit targets) ──"]
        for label, level in e.items():
            lines.append(f"  Ext {label:<8}: {level}")

        lines += [
            f"",
            f"⚡ Key levels: 38.2% and 61.8% are highest-probability reversal zones.",
            f"   61.8% = 'Golden Ratio' — strongest confluence with other indicators.",
            f"Candles : {len(candles)} ({gran}) | Time: {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        return "\n".join(lines)

    # ── deriv_pivot_points ────────────────────────────────────────────────────
    elif name == "deriv-pivot-points":
        symbol = args.get("symbol", "frxXAUUSD")
        period = args.get("period", "daily")

        # Fetch enough candles to get the previous period
        gran_map = {"daily": 86400, "weekly": 604800}
        gran_sec = gran_map.get(period, 86400)
        # Fallback: weekly isn't directly supported by Deriv so use 5 daily candles
        if period == "weekly":
            candles = await fetch_candles(symbol, 86400, 10)
            # Aggregate last week's candles (5 trading days) = candles[-6:-1]
            prev = candles[-6:-1] if len(candles) >= 6 else candles[:-1]
            _, h_list, l_list, c_list = candles_to_ohlcv(prev)
            ph = max(h_list); pl = min(l_list); pc = c_list[-1]
            label = "Weekly"
            gran_label = "D1 (weekly aggregate)"
        else:
            candles = await fetch_candles(symbol, 86400, 5)
            if len(candles) < 2:
                return "Not enough daily candles for pivot points."
            prev = candles[-2]  # previous day
            ph = float(prev["high"]); pl = float(prev["low"]); pc = float(prev["close"])
            label = "Daily"
            gran_label = "D1"

        piv = calc_pivot_points(ph, pl, pc)
        price = float(candles[-1]["close"])

        # Determine price location
        if price > piv["r2"]:   zone = f"🔴 Price above R2 — extreme overbought territory"
        elif price > piv["r1"]: zone = f"📈 Price between PP and R2 — bullish (resistance at R2={piv['r2']})"
        elif price > piv["pp"]: zone = f"📈 Price above PP — mild bullish bias"
        elif price > piv["s1"]: zone = f"📉 Price between S1 and PP — mild bearish bias"
        elif price > piv["s2"]: zone = f"📉 Price between S1 and S2 — bearish"
        else:                   zone = f"🟢 Price below S2 — extreme oversold territory"

        lines = [
            f"📌 {label} Pivot Points — {symbol}",
            f"{'═'*50}",
            f"Previous {label}: H={ph}  L={pl}  C={pc}",
            f"Current Price : {price}",
            f"",
            f"── Resistance ──────────────────────────────────",
            f"R3 : {piv['r3']}",
            f"R2 : {piv['r2']}",
            f"R1 : {piv['r1']}",
            f"",
            f"── Pivot (PP) ──────────────────────────────────",
            f"PP : {piv['pp']}  ◀ KEY LEVEL",
            f"",
            f"── Support ─────────────────────────────────────",
            f"S1 : {piv['s1']}",
            f"S2 : {piv['s2']}",
            f"S3 : {piv['s3']}",
            f"",
            f"── Price Position ──────────────────────────────",
            f"{zone}",
            f"",
            f"💡 PP = institutional reference. R1/S1 = first target. R2/S2 = second target.",
            f"   Market makers often push price toward PP before major moves.",
        ]
        return "\n".join(lines)

    # ── deriv_ichimoku ────────────────────────────────────────────────────────
    elif name == "deriv-ichimoku":
        symbol    = args.get("symbol", "frxXAUUSD")
        gran      = args.get("granularity", 14400)
        tenkan    = args.get("tenkan", 9)
        kijun     = args.get("kijun", 26)
        senkou_b  = args.get("senkou_b", 52)
        count     = max(args.get("count", 300), senkou_b + 50)

        candles = await fetch_candles(symbol, gran, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)
        gran_label = GRAN_LABEL.get(gran, f"{gran}s")
        price = closes[-1]

        ichi = calc_ichimoku(highs, lows, closes, tenkan, kijun, senkou_b)
        if ichi is None:
            return f"Not enough data for Ichimoku (need {senkou_b} candles minimum)."

        tk_signal = "🟢 Bullish cross (Tenkan > Kijun)" if ichi["tk_cross"] == "bullish" else "🔴 Bearish cross (Tenkan < Kijun)"

        if ichi["cloud_signal"] == "bullish":
            cloud_color = "🟢 Green (Senkou A > B)" if ichi["senkou_a"] > ichi["senkou_b"] else "🔴 Red (Senkou B > A)"
        elif ichi["cloud_signal"] == "bearish":
            cloud_color = "🔴 Red (Senkou B > A)" if ichi["senkou_b"] > ichi["senkou_a"] else "🟢 Green (Senkou A > B)"
        else:
            cloud_color = "Mixed"

        chikou_compare = closes[-kijun] if len(closes) > kijun else closes[0]
        chikou_signal = "🟢 Chikou above price 26 periods ago (bullish)" if ichi["chikou"] > chikou_compare else "🔴 Chikou below price 26 periods ago (bearish)"

        lines = [
            f"☁️  Ichimoku Cloud({tenkan},{kijun},{senkou_b}) — {symbol} {gran_label}",
            f"{'═'*56}",
            f"Current Price  : {price}",
            f"",
            f"── Ichimoku Lines ──────────────────────────────────",
            f"Tenkan-sen  ({tenkan:2}): {ichi['tenkan']}  (Conversion Line)",
            f"Kijun-sen   ({kijun:2}): {ichi['kijun']}  (Base Line)",
            f"Senkou Sp A    : {ichi['senkou_a']}  (Leading Span A)",
            f"Senkou Sp B    : {ichi['senkou_b']}  (Leading Span B)",
            f"Chikou Span    : {ichi['chikou']}  (Lagging Span)",
            f"",
            f"── Cloud ───────────────────────────────────────────",
            f"Cloud Top      : {ichi['cloud_top']}",
            f"Cloud Bot      : {ichi['cloud_bot']}",
            f"Cloud Color    : {cloud_color}",
            f"",
            f"── Signals ─────────────────────────────────────────",
            f"Cloud Signal   : {ichi['cloud_desc']}",
            f"TK Cross       : {tk_signal}",
            f"Chikou Signal  : {chikou_signal}",
            f"",
            f"── Interpretation ──────────────────────────────────",
        ]

        # Confluence scoring
        bull = 0; bear = 0; notes = []
        if ichi["cloud_signal"] == "bullish": bull += 3; notes.append("✅ Price above cloud")
        elif ichi["cloud_signal"] == "bearish": bear += 3; notes.append("⚠️ Price below cloud")
        else: notes.append("⚖️ Price inside cloud (indecision)")
        if ichi["tk_cross"] == "bullish": bull += 2; notes.append("✅ Tenkan > Kijun")
        else: bear += 2; notes.append("⚠️ Tenkan < Kijun")
        if ichi["senkou_a"] > ichi["senkou_b"]: bull += 1; notes.append("✅ Cloud green (A>B)")
        else: bear += 1; notes.append("⚠️ Cloud red (B>A)")
        if ichi["chikou"] > chikou_compare: bull += 2; notes.append("✅ Chikou bullish")
        else: bear += 2; notes.append("⚠️ Chikou bearish")

        total = bull + bear
        pct = round(bull / total * 100) if total else 50
        overall = "🟢 BULLISH" if pct >= 65 else ("🔴 BEARISH" if pct <= 35 else "⚖️ NEUTRAL")
        lines.extend(notes)
        lines += [f"", f"Overall Bias   : {overall} ({pct}% bullish signals)"]
        lines.append(f"\nCandles: {len(candles)} | Time: {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}")
        return "\n".join(lines)

    # ── deriv_parabolic_sar ───────────────────────────────────────────────────
    elif name == "deriv-parabolic-sar":
        symbol     = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        af_start   = args.get("af_start", 0.02)
        af_step    = args.get("af_step", 0.02)
        af_max     = args.get("af_max", 0.2)
        count      = max(args.get("count", 200), 50)

        candles = await fetch_candles(symbol, granularity, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)
        gran_label = GRAN_LABEL.get(granularity, f"{granularity}s")
        price = closes[-1]

        result = calc_parabolic_sar(highs, lows, af_start, af_step, af_max)
        if result is None:
            return "Not enough data for Parabolic SAR (need at least 10 candles)."

        sar_val, trend = result
        dist = abs(price - sar_val)
        dist_pct = round(dist / price * 100, 3)

        if trend == "up":
            signal = f"🟢 UPTREND — SAR below price at {sar_val}"
            action = "BUY / Hold Long — SAR acts as trailing stop below price"
            flip_warn = f"Trend reversal if price drops to {sar_val}"
        else:
            signal = f"🔴 DOWNTREND — SAR above price at {sar_val}"
            action = "SELL / Hold Short — SAR acts as trailing stop above price"
            flip_warn = f"Trend reversal if price rises to {sar_val}"

        lines = [
            f"⚡ Parabolic SAR — {symbol} {gran_label}",
            f"{'═'*50}",
            f"Current Price  : {price}",
            f"SAR Value      : {sar_val}",
            f"Distance       : {round(dist, 5)} ({dist_pct}% from price)",
            f"",
            f"── Signal ──────────────────────────────────────",
            f"{signal}",
            f"Action         : {action}",
            f"",
            f"── Reversal Alert ──────────────────────────────",
            f"{flip_warn}",
            f"",
            f"⚙️  AF Settings: start={af_start}, step={af_step}, max={af_max}",
            f"   Higher AF = SAR tracks price tighter (more signals, more noise)",
            f"   Lower AF  = SAR tracks price looser (fewer signals, cleaner trend)",
            f"Candles: {len(candles)} ({gran_label}) | Time: {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        return "\n".join(lines)

    # ── deriv_supertrend ──────────────────────────────────────────────────────
    elif name == "deriv-supertrend":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        period      = args.get("period", 10)
        multiplier  = args.get("multiplier", 3.0)
        count       = max(args.get("count", 200), period * 8 + 10)

        candles = await fetch_candles(symbol, granularity, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)
        gran_label = GRAN_LABEL.get(granularity, f"{granularity}s")
        price = closes[-1]

        result = calc_supertrend(highs, lows, closes, period, multiplier)
        if result is None:
            return f"Not enough data for Supertrend({period}, {multiplier})."

        st_val, direction = result
        dist = abs(price - st_val)
        dist_pct = round(dist / price * 100, 3)

        if direction == "up":
            signal = f"🟢 BULLISH — Price above Supertrend ({st_val})"
            action = "BUY / Hold Long — Supertrend is dynamic support"
            note   = f"Bearish flip if price closes below {st_val}"
        else:
            signal = f"🔴 BEARISH — Price below Supertrend ({st_val})"
            action = "SELL / Hold Short — Supertrend is dynamic resistance"
            note   = f"Bullish flip if price closes above {st_val}"

        lines = [
            f"🔺 Supertrend({period}, {multiplier}×ATR) — {symbol} {gran_label}",
            f"{'═'*54}",
            f"Current Price   : {price}",
            f"Supertrend Line : {st_val}",
            f"Distance        : {round(dist, 5)} ({dist_pct}%)",
            f"",
            f"── Signal ───────────────────────────────────────",
            f"{signal}",
            f"Action          : {action}",
            f"",
            f"── Reversal Watch ───────────────────────────────",
            f"{note}",
            f"",
            f"⚙️  Settings: ATR Period={period}, Multiplier={multiplier}",
            f"   Period 7, Mult 2.0 = scalping (tight, more signals)",
            f"   Period 14, Mult 4.0 = swing (wide, fewer signals)",
            f"Candles: {len(candles)} ({gran_label}) | Time: {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        return "\n".join(lines)

    # ── deriv_keltner ─────────────────────────────────────────────────────────
    elif name == "deriv-keltner":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        ema_period  = args.get("ema_period", 20)
        atr_period  = args.get("atr_period", 10)
        multiplier  = args.get("multiplier", 2.0)
        count       = max(args.get("count", 200), max(ema_period, atr_period) * 3)

        candles = await fetch_candles(symbol, granularity, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)
        gran_label = GRAN_LABEL.get(granularity, f"{granularity}s")
        price = closes[-1]

        kc = calc_keltner(highs, lows, closes, ema_period, atr_period, multiplier)
        if kc is None:
            return f"Not enough data for Keltner Channel({ema_period}, {atr_period})."

        width = round(kc["upper"] - kc["lower"], 5)
        pct_pos = round((price - kc["lower"]) / (kc["upper"] - kc["lower"]) * 100, 1) if width else 50

        if price > kc["upper"]:
            zone = "🔴 Price ABOVE upper band — strong bullish breakout / overbought"
        elif price >= kc["middle"] + (kc["upper"] - kc["middle"]) * 0.7:
            zone = "📈 Price near upper band — bullish momentum"
        elif price < kc["lower"]:
            zone = "🟢 Price BELOW lower band — strong bearish breakout / oversold"
        elif price <= kc["middle"] - (kc["middle"] - kc["lower"]) * 0.7:
            zone = "📉 Price near lower band — bearish momentum"
        else:
            zone = "⚖️ Price near middle (EMA) — neutral zone"

        lines = [
            f"📊 Keltner Channel(EMA{ema_period}, ATR{atr_period}, ×{multiplier}) — {symbol} {gran_label}",
            f"{'═'*58}",
            f"Current Price  : {price}",
            f"Upper Band     : {kc['upper']}",
            f"Middle (EMA)   : {kc['middle']}",
            f"Lower Band     : {kc['lower']}",
            f"Channel Width  : {width}",
            f"ATR            : {kc['atr']}",
            f"%Position      : {pct_pos}% (0=lower, 50=mid, 100=upper)",
            f"",
            f"── Signal ───────────────────────────────────────",
            f"{zone}",
            f"",
            f"💡 Squeeze: if Bollinger Bands fit inside Keltner → explosive breakout incoming.",
            f"Candles: {len(candles)} ({gran_label}) | Time: {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        return "\n".join(lines)

    # ── deriv_donchian ────────────────────────────────────────────────────────
    elif name == "deriv-donchian":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        period      = args.get("period", 20)
        count       = max(args.get("count", 150), period + 20)

        candles = await fetch_candles(symbol, granularity, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)
        gran_label = GRAN_LABEL.get(granularity, f"{granularity}s")
        price = closes[-1]

        dc = calc_donchian(highs, lows, period)
        if dc is None:
            return f"Not enough data for Donchian({period})."

        width = round(dc["upper"] - dc["lower"], 5)
        pct_pos = round((price - dc["lower"]) / width * 100, 1) if width else 50

        if price >= dc["upper"]:
            signal = f"🔴 Price AT/ABOVE upper band ({dc['upper']}) — fresh {period}-bar HIGH breakout"
            action = "Breakout BUY signal — Turtle system would enter long here"
        elif price <= dc["lower"]:
            signal = f"🟢 Price AT/BELOW lower band ({dc['lower']}) — fresh {period}-bar LOW breakout"
            action = "Breakout SELL signal — Turtle system would enter short here"
        elif pct_pos > 66:
            signal = f"📈 Price in upper third of channel ({pct_pos}%) — bullish momentum"
            action = "Approaching breakout zone — watch for upper band test"
        elif pct_pos < 33:
            signal = f"📉 Price in lower third of channel ({pct_pos}%) — bearish momentum"
            action = "Approaching breakdown zone — watch for lower band test"
        else:
            signal = f"⚖️ Price in middle of channel ({pct_pos}%) — no breakout signal"
            action = "Wait for price to approach channel extremes"

        lines = [
            f"📊 Donchian Channel({period}) — {symbol} {gran_label}",
            f"{'═'*52}",
            f"Current Price  : {price}",
            f"Upper Band     : {dc['upper']}  ({period}-bar high)",
            f"Middle         : {dc['middle']}",
            f"Lower Band     : {dc['lower']}  ({period}-bar low)",
            f"Channel Width  : {width}",
            f"%Position      : {pct_pos}% (0=lower, 50=mid, 100=upper)",
            f"",
            f"── Signal ───────────────────────────────────────",
            f"{signal}",
            f"Action         : {action}",
            f"",
            f"💡 Period 20 = medium-term. Period 55 = classic Turtle long-term breakout.",
            f"Candles: {len(candles)} ({gran_label}) | Time: {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        return "\n".join(lines)

    # ── deriv_cci ─────────────────────────────────────────────────────────────
    elif name == "deriv-cci":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        period      = args.get("period", 20)
        count       = max(args.get("count", 150), period * 3)

        candles = await fetch_candles(symbol, granularity, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)
        gran_label = GRAN_LABEL.get(granularity, f"{granularity}s")
        price = closes[-1]

        cci_val = calc_cci(highs, lows, closes, period)
        if cci_val is None:
            return f"Not enough data for CCI({period})."

        if cci_val > 200:
            signal = "🔴 EXTREME OVERBOUGHT (>200) — strong reversal risk / sell zone"
        elif cci_val > 100:
            signal = "🔴 Overbought (>100) — bearish reversal possible"
        elif cci_val < -200:
            signal = "🟢 EXTREME OVERSOLD (<-200) — strong reversal risk / buy zone"
        elif cci_val < -100:
            signal = "🟢 Oversold (<-100) — bullish reversal possible"
        elif cci_val > 0:
            signal = "📈 Bullish momentum (above zero line)"
        else:
            signal = "📉 Bearish momentum (below zero line)"

        lines = [
            f"📊 CCI({period}) — {symbol} {gran_label}",
            f"{'═'*48}",
            f"CCI Value      : {cci_val}",
            f"Current Price  : {price}",
            f"",
            f"── Signal ───────────────────────────────────────",
            f"{signal}",
            f"",
            f"── CCI Reference ────────────────────────────────",
            f"  > +200 : Extreme overbought / strong reversal risk",
            f"  > +100 : Overbought zone",
            f"    0    : Neutral / zero line",
            f"  < -100 : Oversold zone",
            f"  < -200 : Extreme oversold / strong reversal risk",
            f"",
            f"💡 CCI zero-line cross: +0 = bullish entry, -0 = bearish entry.",
            f"   Designed for commodities — especially effective for XAUUSD and XAGUSD.",
            f"Candles: {len(candles)} ({gran_label}) | Time: {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        return "\n".join(lines)

    # ── deriv_williams_r ──────────────────────────────────────────────────────
    elif name == "deriv-williams-r":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        period      = args.get("period", 14)
        count       = max(args.get("count", 100), period * 3)

        candles = await fetch_candles(symbol, granularity, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)
        gran_label = GRAN_LABEL.get(granularity, f"{granularity}s")
        price = closes[-1]

        wr = calc_williams_r(highs, lows, closes, period)
        if wr is None:
            return f"Not enough data for Williams %R({period})."

        if wr >= -20:
            signal = "🔴 OVERBOUGHT (≥ -20) — potential reversal / sell zone"
        elif wr <= -80:
            signal = "🟢 OVERSOLD (≤ -80) — potential reversal / buy zone"
        elif wr >= -40:
            signal = "📈 Approaching overbought — bullish momentum"
        elif wr <= -60:
            signal = "📉 Approaching oversold — bearish momentum"
        else:
            signal = "⚖️ Neutral zone (-40 to -60)"

        lines = [
            f"📊 Williams %R({period}) — {symbol} {gran_label}",
            f"{'═'*50}",
            f"Williams %R    : {wr}",
            f"Current Price  : {price}",
            f"",
            f"── Signal ───────────────────────────────────────",
            f"{signal}",
            f"",
            f"── %R Reference ─────────────────────────────────",
            f"  -0 to -20  : Overbought zone (bearish)",
            f"  -20 to -80 : Neutral zone",
            f"  -80 to -100: Oversold zone (bullish)",
            f"",
            f"💡 Williams %R reacts faster than Stochastic.",
            f"   Use %R(7-9) for scalping, %R(14) for intraday, %R(21) for swing.",
            f"Candles: {len(candles)} ({gran_label}) | Time: {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        return "\n".join(lines)

    # ── deriv_heikin_ashi ─────────────────────────────────────────────────────
    elif name == "deriv-heikin-ashi":
        symbol       = args.get("symbol", "frxXAUUSD")
        granularity  = args.get("granularity", 3600)
        analyze_last = args.get("analyze_last", 10)
        count        = max(args.get("count", 150), analyze_last + 20)

        candles = await fetch_candles(symbol, granularity, count)
        opens, highs, lows, closes = candles_to_ohlcv(candles)
        gran_label = GRAN_LABEL.get(granularity, f"{granularity}s")

        ha = calc_heikin_ashi(opens, highs, lows, closes, analyze_last)
        if ha is None:
            return "Not enough data for Heikin Ashi."

        last = ha["last_candle"]
        price = closes[-1]
        ha_body = round(abs(last["close"] - last["open"]), 5)
        ha_upper_shadow = round(last["high"] - max(last["open"], last["close"]), 5)
        ha_lower_shadow = round(min(last["open"], last["close"]) - last["low"], 5)

        # Candle type interpretation
        if last["close"] > last["open"]:
            ha_color = "🟢 Bullish HA candle"
        else:
            ha_color = "🔴 Bearish HA candle"

        # Strong signal detection
        if ha["strong_bull"]:
            trend_desc = "🟢 STRONG UPTREND — 3+ consecutive bullish HA with no lower shadow"
        elif ha["strong_bear"]:
            trend_desc = "🔴 STRONG DOWNTREND — 3+ consecutive bearish HA with no upper shadow"
        elif ha["trend"] == "BULLISH":
            trend_desc = "📈 Bullish trend (more bullish HA candles in recent window)"
        elif ha["trend"] == "BEARISH":
            trend_desc = "📉 Bearish trend (more bearish HA candles in recent window)"
        else:
            trend_desc = "⚖️ Neutral / indecision — mixed HA candles"

        lines = [
            f"🕯️  Heikin Ashi — {symbol} {gran_label}",
            f"{'═'*52}",
            f"Current Price   : {price}",
            f"",
            f"── Last HA Candle ───────────────────────────────",
            f"HA Open  : {last['open']}",
            f"HA High  : {last['high']}",
            f"HA Low   : {last['low']}",
            f"HA Close : {last['close']}",
            f"Color    : {ha_color}",
            f"Body Size: {ha_body}",
            f"Upper Shadow: {ha_upper_shadow}  {'(no upper = strong bull)' if ha_upper_shadow == 0 else ''}",
            f"Lower Shadow: {ha_lower_shadow}  {'(no lower = strong bear)' if ha_lower_shadow == 0 else ''}",
            f"",
            f"── Trend Analysis (last {analyze_last} HA candles) ─────────────",
            f"Bullish candles : {ha['bullish_candles']}",
            f"Bearish candles : {ha['bearish_candles']}",
            f"Trend           : {trend_desc}",
            f"",
            f"── Recent HA Candles ────────────────────────────",
        ]
        for c in ha["recent_5"]:
            color_mark = "🟢" if c["close"] > c["open"] else "🔴"
            lines.append(f"  {color_mark} O:{c['open']} H:{c['high']} L:{c['low']} C:{c['close']}")

        lines += [
            f"",
            f"💡 Doji-shaped HA candle = trend exhaustion / watch for reversal.",
            f"   Use HA to filter noise — combine with RSI or MACD for entry timing.",
            f"Candles: {len(candles)} ({gran_label}) | Time: {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        return "\n".join(lines)

    # ── deriv-volume-profile ──────────────────────────────────────────────────
    elif name == "deriv-volume-profile":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        count       = min(args.get("count", 200), 500)
        bins        = min(args.get("bins", 20), 50)
        gran_label  = GRAN_LABEL.get(granularity, f"{granularity}s")
        candles     = await fetch_candles(symbol, granularity, count)
        if not candles:
            return f"Error: No candle data returned for {symbol}"
        vp = calc_volume_profile(candles, bins=bins)
        if not vp:
            return "Error: Could not compute volume profile — insufficient price range."
        current = float(candles[-1]["close"])
        poc_dist = (current - vp["poc"]) / vp["poc"] * 100
        in_va    = vp["val"] <= current <= vp["vah"]
        lines = [
            f"📊 Volume Profile — {symbol} ({gran_label}, {len(candles)} candles)",
            f"",
            f"── Value Area (70% of volume) ──────────────────",
            f"  VAH (Value Area High) : {vp['vah']}",
            f"  POC (Point of Control): {vp['poc']}  ← magnet price",
            f"  VAL (Value Area Low)  : {vp['val']}",
            f"",
            f"── Current Price ───────────────────────────────",
            f"  Price : {current}",
            f"  vs POC: {poc_dist:+.2f}%  {'(inside value area ✓)' if in_va else '(outside value area — expect reversion to VA)'}",
            f"",
            f"── High Volume Nodes (HVN = strong S/R) ─────────",
        ]
        for h in vp["hvn_nodes"]:
            dist = (current - h) / h * 100
            lines.append(f"  HVN: {h}  ({dist:+.2f}% from price)")
        lines += [f"", f"── Low Volume Nodes (LVN = fast-move zones) ──────"]
        for l in vp["lvn_nodes"]:
            dist = (current - l) / l * 100
            lines.append(f"  LVN: {l}  ({dist:+.2f}% from price)")
        lines += [
            f"",
            f"Price range: {vp['price_min']} — {vp['price_max']}",
            f"",
            f"💡 If price is below POC → expect upward pull toward POC.",
            f"   If price is above VAH → breakout mode, next target = extension.",
            f"   HVN = likely to slow/reverse price. LVN = price moves fast through it.",
        ]
        return "\n".join(lines)

    # ── deriv-fvg ─────────────────────────────────────────────────────────────
    elif name == "deriv-fvg":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        count       = min(args.get("count", 150), 500)
        gran_label  = GRAN_LABEL.get(granularity, f"{granularity}s")
        candles     = await fetch_candles(symbol, granularity, count)
        if not candles:
            return f"Error: No candle data returned for {symbol}"
        fvgs    = detect_fvg(candles)
        current = float(candles[-1]["close"])
        unfilled = [f for f in fvgs if not f["filled"]]
        filled   = [f for f in fvgs if f["filled"]]
        lines = [
            f"🔲 Fair Value Gaps (FVG) — {symbol} ({gran_label})",
            f"Total detected: {len(fvgs)}  |  Unfilled: {len(unfilled)}  |  Filled: {len(filled)}",
            f"Current price: {current}",
            f"",
        ]
        if unfilled:
            lines.append("── Unfilled FVGs (active magnets) ──────────────────")
            for fvg in unfilled[:8]:
                ts   = datetime.utcfromtimestamp(fvg["epoch"]).strftime("%Y-%m-%d %H:%M")
                dist = (current - fvg["mid"]) / fvg["mid"] * 100
                icon = "🟢" if fvg["type"] == "bullish" else "🔴"
                lines.append(
                    f"  {icon} {fvg['type'].upper():8s} | {fvg['bottom']} — {fvg['top']} "
                    f"(mid: {fvg['mid']}, size: {fvg['size']}) | {dist:+.2f}% | {ts}"
                )
        if filled:
            lines += [f"", "── Filled FVGs (recent) ────────────────────────────"]
            for fvg in filled[:4]:
                ts   = datetime.utcfromtimestamp(fvg["epoch"]).strftime("%Y-%m-%d %H:%M")
                icon = "🟢" if fvg["type"] == "bullish" else "🔴"
                lines.append(f"  {icon} {fvg['type'].upper():8s} | {fvg['bottom']} — {fvg['top']} | filled ✓ | {ts}")
        lines += [
            f"",
            f"💡 Bullish FVG below price = buy zone on pullback.",
            f"   Bearish FVG above price = sell zone on rally.",
            f"   FVG + Order Block overlap = highest-confluence entry.",
        ]
        return "\n".join(lines)

    # ── deriv-order-blocks ────────────────────────────────────────────────────
    elif name == "deriv-order-blocks":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        count       = min(args.get("count", 150), 500)
        lookback    = args.get("lookback", 5)
        gran_label  = GRAN_LABEL.get(granularity, f"{granularity}s")
        candles     = await fetch_candles(symbol, granularity, count)
        if not candles:
            return f"Error: No candle data returned for {symbol}"
        obs     = detect_order_blocks(candles, lookback=lookback)
        current = float(candles[-1]["close"])
        active  = [o for o in obs if not o["mitigated"]]
        done    = [o for o in obs if o["mitigated"]]
        lines = [
            f"📦 Order Blocks — {symbol} ({gran_label})",
            f"Total detected: {len(obs)}  |  Active (unmitigated): {len(active)}  |  Mitigated: {len(done)}",
            f"Current price: {current}",
            f"",
        ]
        if active:
            lines.append("── Active Order Blocks (price likely to return here) ──")
            for ob in active[:6]:
                ts   = datetime.utcfromtimestamp(ob["epoch"]).strftime("%Y-%m-%d %H:%M")
                icon = "🟢" if ob["type"] == "bullish" else "🔴"
                lines.append(
                    f"  {icon} {ob['type'].upper():8s} OB | {ob['low']} — {ob['high']} "
                    f"(mid: {ob['mid']}) | dist: {ob['distance_pct']:+.2f}% | {ts}"
                )
        if done:
            lines += [f"", "── Mitigated OBs (already tested) ──────────────────"]
            for ob in done[:4]:
                ts   = datetime.utcfromtimestamp(ob["epoch"]).strftime("%Y-%m-%d %H:%M")
                icon = "🟢" if ob["type"] == "bullish" else "🔴"
                lines.append(f"  {icon} {ob['type'].upper():8s} OB | {ob['low']} — {ob['high']} | mitigated ✓ | {ts}")
        lines += [
            f"",
            f"💡 Bullish OB below price = buy on retest (SL below OB low).",
            f"   Bearish OB above price = sell on retest (SL above OB high).",
            f"   OB + FVG confluence = premium setup.",
        ]
        return "\n".join(lines)

    # ── deriv-swing-structure ─────────────────────────────────────────────────
    elif name == "deriv-swing-structure":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 14400)
        count       = min(args.get("count", 100), 500)
        lookback    = args.get("lookback", 3)
        gran_label  = GRAN_LABEL.get(granularity, f"{granularity}s")
        candles     = await fetch_candles(symbol, granularity, count)
        if not candles:
            return f"Error: No candle data returned for {symbol}"
        ms = detect_swing_structure(candles, lookback=lookback)
        if not ms:
            return "Error: Insufficient candles for swing structure analysis."
        struct_icon = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(ms["structure"], "⚪")
        lines = [
            f"📐 Market Structure — {symbol} ({gran_label})",
            f"",
            f"Structure : {struct_icon} {ms['structure'].upper()}",
            f"Current   : {ms['current_price']}",
            f"",
            f"── Swing Highs (last 5) ───────────────────────────",
        ]
        for sh in ms["swing_highs"]:
            ts = datetime.utcfromtimestamp(sh["epoch"]).strftime("%Y-%m-%d %H:%M")
            lines.append(f"  H: {sh['price']}  @ {ts}")
        lines += [f"", f"── Swing Lows (last 5) ────────────────────────────"]
        for sl in ms["swing_lows"]:
            ts = datetime.utcfromtimestamp(sl["epoch"]).strftime("%Y-%m-%d %H:%M")
            lines.append(f"  L: {sl['price']}  @ {ts}")
        lines += [f"", f"── Pattern ────────────────────────────────────────"]
        if ms["last_hh"] and ms["prev_hh"]:
            hh_label = "HH ✓" if ms["last_hh"] > ms["prev_hh"] else "LH ⚠"
            lines.append(f"  Highs: {ms['prev_hh']} → {ms['last_hh']}  [{hh_label}]")
        if ms["last_hl"] and ms["prev_hl"]:
            hl_label = "HL ✓" if ms["last_hl"] > ms["prev_hl"] else "LL ⚠"
            lines.append(f"  Lows : {ms['prev_hl']} → {ms['last_hl']}  [{hl_label}]")
        if ms["bos"]:
            b = ms["bos"]
            lines += [f"", f"  ✅ BOS ({b['direction'].upper()}): broke {b['level']} → reached {b['broken_at']}"]
        if ms["choch"]:
            c = ms["choch"]
            lines += [f"", f"  ⚠️  CHoCH ({c['type']}): watch level {c['level']}"]
        lines += [
            f"",
            f"💡 Bullish structure (HH+HL) = look for buy setups on pullbacks to HL.",
            f"   Bearish structure (LH+LL) = look for sell setups on rallies to LH.",
            f"   CHoCH = first warning of potential trend reversal.",
        ]
        return "\n".join(lines)

    # ── deriv-liquidity-sweep ─────────────────────────────────────────────────
    elif name == "deriv-liquidity-sweep":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        count       = min(args.get("count", 100), 300)
        lookback    = args.get("lookback", 20)
        gran_label  = GRAN_LABEL.get(granularity, f"{granularity}s")
        candles     = await fetch_candles(symbol, granularity, count)
        if not candles:
            return f"Error: No candle data returned for {symbol}"
        sweeps  = detect_liquidity_sweeps(candles, lookback=lookback)
        current = float(candles[-1]["close"])
        lines = [
            f"🎯 Liquidity Sweeps / Stop Hunts — {symbol} ({gran_label})",
            f"Detected: {len(sweeps)} sweeps in last {len(candles)} candles",
            f"Current price: {current}",
            f"",
        ]
        if not sweeps:
            lines.append("No liquidity sweeps detected in this window.")
        else:
            lines.append("── Recent Sweeps (newest first) ────────────────────")
            for sw in sweeps:
                ts   = datetime.utcfromtimestamp(sw["epoch"]).strftime("%Y-%m-%d %H:%M")
                icon = "🔴" if sw["type"] == "bearish_sweep" else "🟢"
                candles_ago = len(candles) - 1 - sw["idx"]
                lines.append(
                    f"  {icon} {sw['type'].replace('_', ' ').upper()}"
                    f"  | swept: {sw['swept_level']}"
                    f"  | size: {sw['sweep_size']}"
                    f"  | {ts} ({candles_ago} candles ago)"
                )
                lines.append(f"     → Signal: {sw['signal']}")
            # Most recent sweep signal
            last = sweeps[0]
            candles_ago = len(candles) - 1 - last["idx"]
            lines += [
                f"",
                f"Latest sweep: {candles_ago} candles ago",
                f"💡 Best entries occur 1-3 candles AFTER the sweep candle closes.",
                f"   Combine with FVG / OB at the swept level for maximum confluence.",
            ]
        return "\n".join(lines)

    # ── deriv-session-levels ──────────────────────────────────────────────────
    elif name == "deriv-session-levels":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = min(args.get("granularity", 3600), 3600)
        count       = min(args.get("count", 48), 200)
        gran_label  = GRAN_LABEL.get(granularity, f"{granularity}s")
        candles     = await fetch_candles(symbol, granularity, count)
        if not candles:
            return f"Error: No candle data returned for {symbol}"
        sessions = calc_session_ohlc(candles, granularity)
        current  = float(candles[-1]["close"])
        now_utc  = datetime.utcnow()
        now_h    = now_utc.hour
        active_sess = ("new_york" if 13 <= now_h < 22 else
                       "london" if 7 <= now_h < 16 else
                       "asia" if now_h < 8 else "off-hours")
        lines = [
            f"🕐 Session OHLC Levels — {symbol} ({gran_label})",
            f"Current price: {current}  |  UTC time: {now_utc.strftime('%H:%M')}  |  Active session: {active_sess.upper()}",
            f"",
        ]
        sess_names = {"asia": "Asia (00:00-08:00 UTC)", "london": "London (07:00-16:00 UTC)", "new_york": "New York (13:00-22:00 UTC)"}
        for sess_key, sess_label in sess_names.items():
            s = sessions.get(sess_key)
            active_mark = " ← ACTIVE" if sess_key == active_sess else ""
            if s:
                range_sz = s["high"] - s["low"]
                lines += [
                    f"── {sess_label}{active_mark} ─────────────────",
                    f"  H: {s['high']}   L: {s['low']}   Range: {range_sz:.5f}",
                    f"  O: {s['open']}   C: {s['close']}  ({s['candle_count']} candles)",
                    f"",
                ]
            else:
                lines += [f"── {sess_label}{active_mark} ─────────────────", f"  No data (session may not have started yet)", f""]
        lines += [
            f"💡 Asia High/Low = initial liquidity zones for London to sweep.",
            f"   London breakout direction = bias for NY session.",
            f"   Price trading above London open = bullish intraday bias.",
        ]
        return "\n".join(lines)

    # ── deriv-prev-levels ─────────────────────────────────────────────────────
    elif name == "deriv-prev-levels":
        symbol  = args.get("symbol", "frxXAUUSD")
        count   = min(args.get("count", 60), 200)
        candles = await fetch_candles(symbol, 86400, count)
        if not candles:
            return f"Error: No candle data returned for {symbol}"
        levels  = calc_prev_levels(candles)
        current = float(candles[-1]["close"])
        lines   = [f"📅 Previous Levels (PDH/PDL/PWH/PWL/PMH/PML) — {symbol}", f"Current price: {current}", f""]
        if "prev_day" in levels:
            pd = levels["prev_day"]
            above_pdh = current > pd["high"]
            below_pdl = current < pd["low"]
            in_range  = pd["low"] <= current <= pd["high"]
            status = "ABOVE PDH ↑" if above_pdh else ("BELOW PDL ↓" if below_pdl else "inside range")
            lines += [
                f"── Previous Day ({pd['date']}) ──────────────────────",
                f"  PDH: {pd['high']}",
                f"  PDC: {pd['close']}",
                f"  PDL: {pd['low']}",
                f"  Status: {status}",
                f"",
            ]
        if "current_day" in levels:
            cd = levels["current_day"]
            lines += [
                f"── Current Day ({cd['date']}) ─────────────────────",
                f"  Today H: {cd['high']}   Today L: {cd['low']}",
                f"  Today O: {cd['open']}   Current: {current}",
                f"",
            ]
        if "prev_week" in levels:
            pw = levels["prev_week"]
            lines += [f"── Previous Week ─────────────────────────────────",
                      f"  PWH: {pw['high']}   PWL: {pw['low']}", f""]
        if "prev_month" in levels:
            pm = levels["prev_month"]
            lines += [f"── Previous Month ────────────────────────────────",
                      f"  PMH: {pm['high']}   PML: {pm['low']}", f""]
        lines += [
            f"💡 PDH/PDL = most targeted intraday liquidity levels.",
            f"   Break and close above PDH = bullish continuation.",
            f"   Rejection at PDH = look for sell setup.",
            f"   PWH/PWL = swing trader reference for weekly bias.",
        ]
        return "\n".join(lines)

    # ── deriv-seasonality ─────────────────────────────────────────────────────
    elif name == "deriv-seasonality":
        symbol  = args.get("symbol", "frxXAUUSD")
        count   = min(args.get("count", 750), 2000)
        candles = await fetch_candles(symbol, 86400, count)
        if not candles:
            return f"Error: No candle data returned for {symbol}"
        sea = calc_seasonality(candles)
        if not sea:
            return "Error: Insufficient historical data for seasonality analysis (need 30+ daily candles)."
        lines = [
            f"📆 Seasonality Analysis — {symbol}",
            f"Based on {len(candles)} daily candles ({len(sea.get('all_months', {}))} months of data)",
            f"",
        ]
        if sea.get("current_month"):
            m = sea["current_month"]
            bias_icon = "🟢" if m["bias"] == "bullish" else ("🔴" if m["bias"] == "bearish" else "⚪")
            lines += [
                f"── Current Month: {m['name']} ─────────────────────────",
                f"  Avg Return : {m['avg_ret']:+.3f}%",
                f"  Win Rate   : {m['win_rate']:.1f}%",
                f"  Bias       : {bias_icon} {m['bias'].upper()}",
                f"  Samples    : {m['samples']} years",
                f"",
            ]
        if sea.get("next_month"):
            m = sea["next_month"]
            bias_icon = "🟢" if m["bias"] == "bullish" else ("🔴" if m["bias"] == "bearish" else "⚪")
            lines += [
                f"── Next Month: {m['name']} ──────────────────────────────",
                f"  Avg Return : {m['avg_ret']:+.3f}%  |  Win Rate: {m['win_rate']:.1f}%  |  {bias_icon} {m['bias'].upper()}",
                f"",
            ]
        if sea.get("best_month"):
            b = sea["best_month"]
            w = sea["worst_month"]
            lines += [
                f"── Historical Extremes ──────────────────────────────",
                f"  Best month : {b['name']} (avg {b['avg_ret']:+.3f}%, {b['win_rate']:.0f}% win rate)",
                f"  Worst month: {w['name']} (avg {w['avg_ret']:+.3f}%, {w['win_rate']:.0f}% win rate)",
                f"",
            ]
        lines += [
            f"── All Months ───────────────────────────────────────",
        ]
        for m_num in sorted(sea.get("all_months", {}).keys()):
            m = sea["all_months"][m_num]
            icon = "🟢" if m["bias"] == "bullish" else ("🔴" if m["bias"] == "bearish" else "⚪")
            lines.append(f"  {icon} {m['name']:3s} | {m['avg_ret']:+.3f}% | WR: {m['win_rate']:.0f}% | n={m['samples']}")
        lines += [
            f"",
            f"💡 Trade WITH seasonality — it adds edge but isn't a standalone signal.",
            f"   Bullish season + bullish structure = higher-probability long setups.",
        ]
        return "\n".join(lines)

    # ── deriv-correlation ─────────────────────────────────────────────────────
    elif name == "deriv-correlation":
        symbol_a    = args.get("symbol_a", "frxXAUUSD")
        symbol_b    = args.get("symbol_b", "frxEURUSD")
        granularity = args.get("granularity", 3600)
        count       = min(args.get("count", 100), 300)
        period      = args.get("period", 20)
        gran_label  = GRAN_LABEL.get(granularity, f"{granularity}s")
        candles_a, candles_b = await asyncio.gather(
            fetch_candles(symbol_a, granularity, count),
            fetch_candles(symbol_b, granularity, count),
        )
        corr = calc_correlation(candles_a, candles_b, period=period)
        if not corr or corr.get("price_correlation") is None:
            note = corr.get("note", "insufficient data") if corr else "insufficient data"
            return f"Error: Could not compute correlation — {note}"
        pc   = corr["price_correlation"]
        rc   = corr["return_correlation"]
        bar_len  = int(abs(pc) * 20)
        bar_sign = "+" if pc >= 0 else "-"
        bar      = f"[{bar_sign * bar_len}{' ' * (20 - bar_len)}]"
        corr_icon = ("🟢" if pc > 0.4 else "🔴" if pc < -0.4 else "⚪")
        a_price = float(candles_a[-1]["close"]) if candles_a else 0
        b_price = float(candles_b[-1]["close"]) if candles_b else 0
        lines = [
            f"🔗 Correlation — {symbol_a} vs {symbol_b} ({gran_label}, {period}-bar window)",
            f"",
            f"  Price correlation  : {pc:+.4f}  {bar}  {corr_icon} {corr['label']}",
            f"  Return correlation : {rc:+.4f}" if rc else "  Return correlation : N/A",
            f"  Common candles     : {corr['common_candles']}",
            f"",
            f"  {symbol_a} current price: {a_price}",
            f"  {symbol_b} current price: {b_price}",
            f"",
            f"── Interpretation ──────────────────────────────────",
        ]
        if pc > 0.7:
            lines.append(f"  ✅ Strong positive — both tend to move in the same direction.")
            lines.append(f"     If {symbol_b} is rising, {symbol_a} likely to follow.")
        elif pc > 0.4:
            lines.append(f"  📈 Moderate positive — general same-direction tendency.")
        elif pc < -0.7:
            lines.append(f"  ✅ Strong negative — they move opposite (classic inverse).")
            lines.append(f"     If {symbol_b} rises, {symbol_a} likely to fall.")
        elif pc < -0.4:
            lines.append(f"  📉 Moderate negative — general inverse tendency.")
        else:
            lines.append(f"  ⚪ Weak/no correlation — these instruments move independently.")
        lines += [
            f"",
            f"💡 DXY proxy: check frxEURUSD + frxGBPUSD simultaneously.",
            f"   If both falling = USD strengthening = bearish for Gold (frxXAUUSD).",
            f"   Correlation divergence = potential mean-reversion trade.",
        ]
        return "\n".join(lines)

    # ── deriv-adx ──────────────────────────────────────────────────────────────
    elif name == "deriv-adx":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        period      = args.get("period", 14)
        count       = max(args.get("count", 100), period * 4)
        candles = await fetch_candles(symbol, granularity, count)
        opens, highs, lows, closes = candles_to_ohlcv(candles)
        result = calc_adx(highs, lows, closes, period)
        if result is None:
            return f"Not enough data for ADX({period}). Need at least {period * 2 + 1} candles."
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        adx_bar = "▓" * min(int(result["adx"] / 5), 20)
        di_diff = abs(result["plus_di"] - result["minus_di"])
        return (
            f"📊 ADX({period}) — {symbol} {gran}\n\n"
            f"ADX (Trend Strength) : {result['adx']} {adx_bar}\n"
            f"Strength Label       : {result['strength']}\n"
            f"+DI (Bullish Power)  : {result['plus_di']}\n"
            f"-DI (Bearish Power)  : {result['minus_di']}\n"
            f"DI Spread            : {di_diff:.2f}\n"
            f"Directional Bias     : {'🟢 Bullish' if result['di_bias'] == 'bullish' else '🔴 Bearish'} (+DI {'>' if result['di_bias'] == 'bullish' else '<'} -DI)\n\n"
            f"Market Mode          : {'✅ TRENDING — trend-following strategies effective' if result['trending'] else '⚠️ RANGING — oscillators more reliable than trend tools'}\n"
            f"Candles              : {len(candles)} ({gran})\n"
            f"Time                 : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # ── deriv-sma ──────────────────────────────────────────────────────────────
    elif name == "deriv-sma":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        periods     = args.get("periods", [20, 50, 200])
        count       = max(args.get("count", 250), max(periods) + 10)
        candles = await fetch_candles(symbol, granularity, count)
        _, _, _, closes = candles_to_ohlcv(candles)
        gran  = GRAN_LABEL.get(granularity, f"{granularity}s")
        price = closes[-1]
        lines = [f"📊 SMA — {symbol} {gran}\n", f"Current Price : {price}\n"]
        results = {}
        for p in sorted(periods):
            val = calc_sma_series(closes, p)
            if val is None:
                lines.append(f"  SMA({p:<4}) : N/A (need {p} candles, only {len(closes)} available)")
            else:
                diff_pct = round((price - val) / val * 100, 3)
                bias = "🟢" if price > val else "🔴"
                lines.append(f"  SMA({p:<4}) : {val}   {bias} price {'+' if diff_pct >= 0 else ''}{diff_pct}% {'above' if diff_pct >= 0 else 'below'}")
                results[p] = val
        if len(results) >= 2:
            skeys = sorted(results.keys())
            for i in range(len(skeys) - 1):
                s1, s2 = skeys[i], skeys[i+1]
                if results[s1] > results[s2]:
                    lines.append(f"\n📈 SMA({s1}) > SMA({s2}) — short-term above long-term = bullish alignment")
                else:
                    lines.append(f"\n📉 SMA({s1}) < SMA({s2}) — short-term below long-term = bearish alignment")
        lines.append(f"\nCandles : {len(candles)} ({gran})")
        lines.append(f"Time    : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}")
        return "\n".join(lines)

    # ── deriv-hma ──────────────────────────────────────────────────────────────
    elif name == "deriv-hma":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        period      = args.get("period", 20)
        count       = max(args.get("count", 150), period * 3)
        candles = await fetch_candles(symbol, granularity, count)
        _, _, _, closes = candles_to_ohlcv(candles)
        hma_val = calc_hma(closes, period)
        if hma_val is None:
            return f"Not enough data for HMA({period}). Need at least {period * 2} candles."
        gran  = GRAN_LABEL.get(granularity, f"{granularity}s")
        price = closes[-1]
        diff_pct = round((price - hma_val) / hma_val * 100, 3)
        bias = "🟢 BULLISH" if price > hma_val else "🔴 BEARISH"
        hma_prev = calc_hma(closes[:-1], period) if len(closes) > period * 2 else None
        trend_dir = ""
        if hma_prev:
            trend_dir = "📈 Rising (bullish momentum)" if hma_val > hma_prev else "📉 Falling (bearish momentum)"
        return (
            f"📊 HMA({period}) — {symbol} {gran}\n\n"
            f"HMA Value    : {hma_val}\n"
            f"Current Price: {price}\n"
            f"Diff         : {'+' if diff_pct >= 0 else ''}{diff_pct}% {'above' if diff_pct >= 0 else 'below'} HMA\n"
            f"Bias         : {bias}\n"
            f"Trend        : {trend_dir}\n\n"
            f"💡 HMA advantage: near-zero lag — responds faster than EMA/SMA to price changes.\n"
            f"Candles      : {len(candles)} ({gran})\n"
            f"Time         : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # ── deriv-stochrsi ─────────────────────────────────────────────────────────
    elif name == "deriv-stochrsi":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        rsi_period  = args.get("rsi_period", 14)
        stoch_period= args.get("stoch_period", 14)
        k_smooth    = args.get("k_smooth", 3)
        d_smooth    = args.get("d_smooth", 3)
        count       = max(args.get("count", 150), rsi_period + stoch_period + k_smooth + d_smooth + 20)
        candles = await fetch_candles(symbol, granularity, count)
        _, _, _, closes = candles_to_ohlcv(candles)
        result = calc_stoch_rsi(closes, rsi_period, stoch_period, k_smooth, d_smooth)
        if result is None:
            return f"Not enough data for StochRSI. Need at least {rsi_period + stoch_period + k_smooth + d_smooth + 10} candles."
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        cross_txt = "✅ Bullish K/D crossover just formed!" if result["bullish_cross"] else ""
        return (
            f"📊 Stochastic RSI({rsi_period},{stoch_period}) — {symbol} {gran}\n\n"
            f"K (fast)  : {result['k']}\n"
            f"D (slow)  : {result['d']}\n"
            f"Signal    : {result['signal']}\n"
            f"{cross_txt}\n"
            f"Zone      : {'🔴 Overbought (K > 80) — consider short bias' if result['k'] >= 80 else '🟢 Oversold (K < 20) — consider long bias' if result['k'] <= 20 else '⚖️ Mid-zone'}\n\n"
            f"💡 StochRSI is more sensitive than plain RSI — great for fast intraday moves.\n"
            f"Candles   : {len(candles)} ({gran})\n"
            f"Time      : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # ── deriv-roc ──────────────────────────────────────────────────────────────
    elif name == "deriv-roc":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        period      = args.get("period", 14)
        count       = max(args.get("count", 100), period + 5)
        candles = await fetch_candles(symbol, granularity, count)
        _, _, _, closes = candles_to_ohlcv(candles)
        roc = calc_roc(closes, period)
        if roc is None:
            return f"Not enough data for ROC({period})."
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        roc_prev = calc_roc(closes[:-1], period) if len(closes) > period + 1 else None
        accel = ""
        if roc_prev is not None:
            if roc > 0 and roc > roc_prev:
                accel = "📈 Accelerating bullish momentum"
            elif roc > 0 and roc < roc_prev:
                accel = "📉 Bullish but decelerating"
            elif roc < 0 and roc < roc_prev:
                accel = "📉 Accelerating bearish momentum"
            elif roc < 0 and roc > roc_prev:
                accel = "📈 Bearish but decelerating (potential reversal)"
        zero_cross = roc_prev is not None and ((roc > 0) != (roc_prev > 0))
        return (
            f"📊 ROC({period}) — {symbol} {gran}\n\n"
            f"ROC Value   : {'+' if roc >= 0 else ''}{roc}%\n"
            f"Momentum    : {'🟢 Positive — price higher than {period} bars ago' if roc > 0 else '🔴 Negative — price lower than ' + str(period) + ' bars ago'}\n"
            f"{accel}\n"
            f"{'⚡ Zero-line cross detected — potential trend change!' if zero_cross else ''}\n"
            f"Current Price: {closes[-1]}\n"
            f"Candles     : {len(candles)} ({gran})\n"
            f"Time        : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # ── deriv-awesome-oscillator ───────────────────────────────────────────────
    elif name == "deriv-awesome-oscillator":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        count       = max(args.get("count", 100), 50)
        candles = await fetch_candles(symbol, granularity, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)
        result = calc_awesome_oscillator(highs, lows)
        if result is None:
            return "Not enough data for Awesome Oscillator. Need at least 34 candles."
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        hist_char = "▲" if result["histogram"] == "rising" else "▼"
        return (
            f"📊 Awesome Oscillator — {symbol} {gran}\n\n"
            f"AO Value    : {'+' if result['ao'] >= 0 else ''}{result['ao']}\n"
            f"Previous    : {'+' if result['prev'] >= 0 else ''}{result['prev']}\n"
            f"Histogram   : {hist_char} {result['histogram'].upper()}\n"
            f"Momentum    : {result['momentum']}\n"
            f"Zero Cross  : {'⚡ YES — potential reversal signal!' if result['zero_line_cross'] else 'No'}\n\n"
            f"💡 AO = SMA(5,midpoint) - SMA(34,midpoint). Zero-line cross = trend shift.\n"
            f"Candles     : {len(candles)} ({gran})\n"
            f"Time        : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # ── deriv-choppiness ───────────────────────────────────────────────────────
    elif name == "deriv-choppiness":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        period      = args.get("period", 14)
        count       = max(args.get("count", 100), period + 5)
        candles = await fetch_candles(symbol, granularity, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)
        ci = calc_choppiness(highs, lows, closes, period)
        if ci is None:
            return f"Not enough data for Choppiness Index({period})."
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        if ci > 61.8:
            market_mode = "🔴 SIDEWAYS/CHOPPY — avoid breakout & trend strategies. Mean-reversion preferred."
            strategy    = "Use: RSI extremes, BB bands, oscillators. Avoid: EMA crossovers, MACD trend-follow."
        elif ci < 38.2:
            market_mode = "🟢 STRONG TREND — trend-following strategies highly effective."
            strategy    = "Use: EMA crossovers, Supertrend, MACD momentum. Avoid: counter-trend reversals."
        else:
            market_mode = "🟡 TRANSITIONAL — market between trend and chop. Wait for breakout."
            strategy    = "Monitor: volume, squeeze, or structure break for directional confirmation."
        ci_bar_filled = int((ci - 38.2) / (100 - 38.2) * 20)
        bar = "🟢" * max(0, 5 - ci_bar_filled // 4) + "🟡" * 2 + "🔴" * max(0, ci_bar_filled // 4 - 5)
        return (
            f"📊 Choppiness Index({period}) — {symbol} {gran}\n\n"
            f"CI Value    : {ci}\n"
            f"Scale       : 38.2 (max trend) ◄──── {ci} ────► 100 (max chop)\n"
            f"Market Mode : {market_mode}\n"
            f"Strategy    : {strategy}\n\n"
            f"Candles     : {len(candles)} ({gran})\n"
            f"Time        : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # ── deriv-squeeze ──────────────────────────────────────────────────────────
    elif name == "deriv-squeeze":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        bb_period   = args.get("bb_period", 20)
        bb_mult     = args.get("bb_mult", 2.0)
        kc_period   = args.get("kc_period", 20)
        kc_mult     = args.get("kc_mult", 1.5)
        count       = max(args.get("count", 100), max(bb_period, kc_period) + 10)
        candles = await fetch_candles(symbol, granularity, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)
        result = calc_squeeze_momentum(highs, lows, closes, bb_period, bb_mult, kc_period, kc_mult)
        if result is None:
            return f"Not enough data for Squeeze Momentum. Need at least {max(bb_period, kc_period) + 5} candles."
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        if result["squeeze_on"]:
            status = "🔴 SQUEEZE ON — BB inside KC. Market coiling. Wait for breakout direction."
            action = "HOLD — monitor momentum direction for breakout signal."
        elif result["squeeze_off"]:
            status = f"🚀 SQUEEZE FIRING — BB broke KC. Momentum releasing: {result['direction']}"
            action = f"TRADE — momentum is {'📈 BULLISH' if result['momentum'] > 0 else '📉 BEARISH'}. Accel: {'Yes ✅' if result['accelerating'] else 'No'}"
        else:
            status = "⚪ No active squeeze"
            action = f"Momentum: {result['direction']}"
        return (
            f"📊 Squeeze Momentum (BB{bb_period}/{bb_mult} + KC{kc_period}/{kc_mult}) — {symbol} {gran}\n\n"
            f"Status      : {status}\n"
            f"Signal      : {action}\n\n"
            f"Momentum    : {'+' if result['momentum'] >= 0 else ''}{result['momentum']}\n"
            f"Prev Momen  : {'+' if result['momentum_prev'] >= 0 else ''}{result['momentum_prev']}\n"
            f"Accelerating: {'✅ Yes' if result['accelerating'] else '❌ No'}\n\n"
            f"BB Upper/Lower : {result['bb_upper']} / {result['bb_lower']}\n"
            f"KC Upper/Lower : {result['kc_upper']} / {result['kc_lower']}\n"
            f"Candles     : {len(candles)} ({gran})\n"
            f"Time        : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # ── deriv-linreg ───────────────────────────────────────────────────────────
    elif name == "deriv-linreg":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        period      = args.get("period", 50)
        channel_std = args.get("channel_std", 2.0)
        count       = max(args.get("count", 200), period + 5)
        candles = await fetch_candles(symbol, granularity, count)
        _, _, _, closes = candles_to_ohlcv(candles)
        result = calc_linear_regression(closes, period, channel_std)
        if result is None:
            return f"Not enough data for Linear Regression({period})."
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        slope_dir = "📈 Upward" if result["slope"] > 0 else "📉 Downward"
        return (
            f"📊 Linear Regression Channel({period}, ±{channel_std}σ) — {symbol} {gran}\n\n"
            f"Midline (Fair Value) : {result['midline']}\n"
            f"Upper Band (+{channel_std}σ)  : {result['upper']}\n"
            f"Lower Band (-{channel_std}σ)  : {result['lower']}\n"
            f"Current Price        : {result['current_price']}\n"
            f"Price Position       : {result['position']}\n\n"
            f"Slope Direction : {slope_dir} ({result['slope_pct_per_bar']:+.4f}% per bar)\n"
            f"Std Deviation   : {result['std_dev']}\n\n"
            f"💡 Price at upper band = statistically overextended — potential mean-reversion.\n"
            f"   Price at lower band = statistically underextended — potential bounce.\n"
            f"Candles         : {len(candles)} ({gran})\n"
            f"Time            : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # ── deriv-divergence ───────────────────────────────────────────────────────
    elif name == "deriv-divergence":
        symbol      = args.get("symbol", "frxXAUUSD")
        granularity = args.get("granularity", 3600)
        rsi_period  = args.get("rsi_period", 14)
        count       = max(args.get("count", 150), rsi_period + 40)
        candles = await fetch_candles(symbol, granularity, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)
        rsi_div  = calc_rsi_divergence(highs, lows, closes, rsi_period)
        macd_div = calc_macd_divergence(highs, lows, closes)
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        rsi_txt = (
            f"  RSI Divergence  : {'🟢 BULLISH — ' + rsi_div['detail'] if rsi_div['bullish'] else '🔴 BEARISH — ' + rsi_div['detail'] if rsi_div['bearish'] else '⚪ None detected'}"
        )
        macd_txt = (
            f"  MACD Divergence : {'🟢 BULLISH — ' + macd_div['detail'] if macd_div['bullish'] else '🔴 BEARISH — ' + macd_div['detail'] if macd_div['bearish'] else '⚪ None detected'}"
        )
        any_div = rsi_div["type"] != "none" or macd_div["type"] != "none"
        confluence = ""
        if rsi_div["bullish"] and macd_div["bullish"]:
            confluence = "\n🔥 CONFLUENCE: Both RSI + MACD show BULLISH divergence — high-probability reversal signal!"
        elif rsi_div["bearish"] and macd_div["bearish"]:
            confluence = "\n🔥 CONFLUENCE: Both RSI + MACD show BEARISH divergence — high-probability reversal signal!"
        return (
            f"📊 Divergence Scanner — {symbol} {gran}\n\n"
            f"{rsi_txt}\n"
            f"{macd_txt}\n"
            f"{confluence}\n"
            f"\n{'✅ Divergence found — potential reversal. Confirm with S/R level or FVG.' if any_div else '✅ No divergence currently. Price and momentum aligned.'}\n"
            f"\nCandles  : {len(candles)} ({gran})\n"
            f"Time     : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    # ── deriv-zigzag ───────────────────────────────────────────────────────────
    elif name == "deriv-zigzag":
        symbol        = args.get("symbol", "frxXAUUSD")
        granularity   = args.get("granularity", 3600)
        threshold_pct = args.get("threshold_pct", 1.0)
        count         = max(args.get("count", 200), 100)
        candles = await fetch_candles(symbol, granularity, count)
        _, highs, lows, closes = candles_to_ohlcv(candles)
        pivots = calc_zigzag(highs, lows, closes, threshold_pct)
        gran = GRAN_LABEL.get(granularity, f"{granularity}s")
        if not pivots:
            return f"No significant pivots detected with threshold {threshold_pct}%. Try reducing threshold_pct."
        lines = [f"📊 ZigZag (threshold ≥{threshold_pct}%) — {symbol} {gran}\n"]
        lines.append(f"Last {len(pivots)} significant swing pivots:\n")
        for i, p in enumerate(pivots):
            icon = "🔺 HIGH" if p["type"] == "high" else "🔻 LOW"
            lines.append(f"  {i+1:2}. {icon}  Price: {p['price']}  (bar index {p['index']})")
        if len(pivots) >= 2:
            last_two = pivots[-2:]
            move = round((last_two[-1]["price"] - last_two[-2]["price"]) / last_two[-2]["price"] * 100, 3)
            lines.append(f"\nLast swing move: {'+' if move >= 0 else ''}{move}% from {last_two[-2]['price']} to {last_two[-1]['price']}")
        lines.append(f"\nCandles : {len(candles)} ({gran})")
        lines.append(f"Time    : {datetime.utcfromtimestamp(candles[-1]['epoch']).strftime('%Y-%m-%d %H:%M UTC')}")
        return "\n".join(lines)

    # ── deriv-multitf ──────────────────────────────────────────────────────────
    elif name == "deriv-multitf":
        symbol     = args.get("symbol", "frxXAUUSD")
        ema_period = args.get("ema_period", 50)
        rsi_period = args.get("rsi_period", 14)
        adx_period = args.get("adx_period", 14)
        tf_configs = [
            ("D1",  86400, 150),
            ("H4",  14400, 150),
            ("H1",  3600,  100),
        ]
        import asyncio as _asyncio
        async def _fetch_tf(label, gran, cnt):
            candles = await fetch_candles(symbol, gran, cnt)
            _, highs, lows, closes = candles_to_ohlcv(candles)
            rsi_val  = calc_rsi(closes, rsi_period)
            macd_res = calc_macd(closes, 12, 26, 9)
            ema_val  = calc_ema_series(closes, ema_period)
            adx_res  = calc_adx(highs, lows, closes, adx_period)
            price    = closes[-1]
            rsi_bias = "bullish" if rsi_val and rsi_val > 50 else "bearish"
            macd_bias= "bullish" if macd_res and macd_res[2] > 0 else "bearish"
            ema_bias = "bullish" if ema_val and price > ema_val else "bearish"
            adx_str  = adx_res["strength"] if adx_res else "N/A"
            adx_val  = adx_res["adx"] if adx_res else None
            return {
                "label": label, "price": price,
                "rsi": round(rsi_val, 1) if rsi_val else None,
                "rsi_bias": rsi_bias,
                "macd_hist": round(macd_res[2], 5) if macd_res else None,
                "macd_bias": macd_bias,
                "ema": ema_val, "ema_bias": ema_bias,
                "adx": adx_val, "adx_strength": adx_str,
            }
        results = await _asyncio.gather(*[_fetch_tf(lb, g, c) for lb, g, c in tf_configs])
        lines = [f"📊 Multi-Timeframe Confluence — {symbol}\n"]
        lines.append(f"{'TF':<6} {'Price':<12} {'RSI':<8} {'MACD Hist':<14} {f'EMA{ema_period}':<12} {'ADX':<8} {'Strength'}")
        lines.append("─" * 72)
        bullish_count = 0
        bearish_count = 0
        for r in results:
            rsi_icon  = "🟢" if r["rsi_bias"] == "bullish" else "🔴"
            macd_icon = "🟢" if r["macd_bias"] == "bullish" else "🔴"
            ema_icon  = "🟢" if r["ema_bias"] == "bullish" else "🔴"
            if r["rsi_bias"] == "bullish": bullish_count += 1
            else: bearish_count += 1
            rsi_str  = f"{rsi_icon}{r['rsi']}" if r["rsi"] else "N/A"
            macd_str = f"{macd_icon}{'+' if r['macd_hist'] and r['macd_hist'] >= 0 else ''}{r['macd_hist']}" if r["macd_hist"] is not None else "N/A"
            ema_str  = f"{ema_icon}{r['ema']}" if r["ema"] else "N/A"
            adx_str  = f"{r['adx']}" if r["adx"] else "N/A"
            lines.append(f"{r['label']:<6} {str(r['price']):<12} {rsi_str:<10} {macd_str:<16} {ema_str:<14} {adx_str:<8} {r['adx_strength']}")
        lines.append("─" * 72)
        if bullish_count == 3:
            confluence = "🟢🟢🟢 ALL TIMEFRAMES BULLISH — high-conviction long setup"
        elif bearish_count == 3:
            confluence = "🔴🔴🔴 ALL TIMEFRAMES BEARISH — high-conviction short setup"
        elif bullish_count == 2:
            confluence = "🟢🟢⚪ 2/3 Timeframes bullish — moderate long bias. Check conflicting TF for entry risk."
        elif bearish_count == 2:
            confluence = "🔴🔴⚪ 2/3 Timeframes bearish — moderate short bias. Check conflicting TF for entry risk."
        else:
            confluence = "⚠️ MIXED — no clear TF alignment. Wait for confluence or reduce size."
        lines.append(f"\nConclusion: {confluence}")
        return "\n".join(lines)

    else:
        return f"Unknown tool: {name}"


async def _handle_list_tools(_context, _params):
    return ListToolsResult(tools=await list_tools())


async def _handle_call_tool(_context, params):
    return CallToolResult(content=await call_tool(params.name, params.arguments or {}))


app = Server("deriv-mcp", on_list_tools=_handle_list_tools, on_call_tool=_handle_call_tool)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
