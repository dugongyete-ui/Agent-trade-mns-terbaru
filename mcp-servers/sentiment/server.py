#!/usr/bin/env python3
"""
Sentiment MCP Server
Market sentiment tools: Long/Short Ratio, Top Trader Positioning,
Open Interest, and Fear & Greed Index.

Data sources (all free, no API key required):
- Binance Futures API  → L/S Ratio, Top Traders, Open Interest (crypto)
- Alternative.me API   → Crypto Fear & Greed Index
- OKX API             → fallback L/S ratio

Cache: 5-minute in-memory cache per tool call to avoid hammering APIs.
"""

import asyncio
import json
import time
from datetime import datetime, timezone

import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool


TIMEOUT = 10
CACHE_TTL = 300  # 5 minutes

_cache: dict = {}


def _get_cached(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def _set_cached(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


# ── Binance symbol normalizer ─────────────────────────────────────────────────

def _normalize_binance_symbol(symbol: str) -> str:
    """Convert common variants to Binance futures symbol format."""
    s = symbol.upper().replace("-", "").replace("/", "").replace(":", "")
    # Strip exchange prefix e.g. BINANCE:BTCUSDT → BTCUSDT
    if "BTCUSDT" in s:
        return "BTCUSDT"
    if "ETHUSDT" in s:
        return "ETHUSDT"
    if "SOLUSDT" in s:
        return "SOLUSDT"
    if "BNBUSDT" in s:
        return "BNBUSDT"
    if "XRPUSDT" in s:
        return "XRPUSDT"
    if "ADAUSDT" in s:
        return "ADAUSDT"
    if "DOGEUSDT" in s:
        return "DOGEUSDT"
    if "LINKUSDT" in s:
        return "LINKUSDT"
    if "AVAXUSDT" in s:
        return "AVAXUSDT"
    if "DOTUSDT" in s:
        return "DOTUSDT"
    if "POLUSDT" in s or "MATICUSDT" in s:
        return "POLUSDT"
    # Generic: strip exchange prefix
    if ":" in symbol:
        return symbol.split(":")[-1].upper()
    return s


PERIOD_MAP = {
    "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h",
    "6h": "6h", "12h": "12h", "1d": "1d",
}


# ── Tools ─────────────────────────────────────────────────────────────────────

async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="sentiment-ls-ratio",
            description=(
                "Long/Short Ratio for a crypto futures pair from Binance. "
                "Shows percentage of traders holding Long vs Short positions RIGHT NOW. "
                "High Long% (>65%) = crowded long = contrarian bearish signal. "
                "High Short% (>65%) = crowded short = contrarian bullish signal (short squeeze risk). "
                "Returns ratio trend over last N periods so you can see if sentiment is shifting."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Crypto pair, e.g. BTCUSDT, ETHUSDT, SOLUSDT (Binance futures format)"
                    },
                    "period": {
                        "type": "string",
                        "description": "Candle period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d",
                        "default": "1h"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of periods to return (1-30). Use 5-10 to see trend.",
                        "default": 8
                    }
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="sentiment-top-traders",
            description=(
                "Long/Short Ratio specifically for TOP TRADERS (institutional / large accounts) on Binance. "
                "This is more meaningful than the general L/S ratio — big players are often on the right side. "
                "When top traders are heavily Long while retail is Short = strong bullish signal. "
                "When top traders are Short while retail is Long = smart money expects a drop."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Crypto pair, e.g. BTCUSDT, ETHUSDT"
                    },
                    "period": {
                        "type": "string",
                        "description": "Period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d",
                        "default": "1h"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of periods (1-30)",
                        "default": 6
                    }
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="sentiment-open-interest",
            description=(
                "Open Interest (OI) history for a crypto futures pair. "
                "OI = total number of open derivative contracts. "
                "Rising OI + Rising Price = strong uptrend (new money flowing in). "
                "Rising OI + Falling Price = strong downtrend (new shorts being added). "
                "Falling OI + Price move = weak move (positions closing, not new conviction). "
                "OI spike then drop = liquidation event happened."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Crypto pair, e.g. BTCUSDT, ETHUSDT"
                    },
                    "period": {
                        "type": "string",
                        "description": "Period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d",
                        "default": "1h"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of periods (1-30)",
                        "default": 8
                    }
                },
                "required": ["symbol"]
            }
        ),
        Tool(
            name="sentiment-fear-greed",
            description=(
                "Crypto Fear & Greed Index (0-100). "
                "0-24 = Extreme Fear (markets very bearish, potential buying opportunity). "
                "25-49 = Fear. "
                "50 = Neutral. "
                "51-74 = Greed. "
                "75-100 = Extreme Greed (markets very bullish, potential reversal risk). "
                "Returns today's value + historical trend for context. "
                "Most useful for BTC and the broader crypto market. Not applicable to Forex/Gold."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of days of history (1-30). Default 7 = one week.",
                        "default": 7
                    }
                },
                "required": []
            }
        ),
    ]


# ── Handlers ──────────────────────────────────────────────────────────────────

async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "sentiment-ls-ratio":
            result = await _ls_ratio(arguments)
        elif name == "sentiment-top-traders":
            result = await _top_traders(arguments)
        elif name == "sentiment-open-interest":
            result = await _open_interest(arguments)
        elif name == "sentiment-fear-greed":
            result = await _fear_greed(arguments)
        else:
            result = f"Unknown tool: {name}"
    except Exception as e:
        result = f"Error: {e}"

    return [TextContent(type="text", text=result)]


async def _ls_ratio(args: dict) -> str:
    raw_symbol = args.get("symbol", "BTCUSDT")
    symbol = _normalize_binance_symbol(raw_symbol)
    period = PERIOD_MAP.get(args.get("period", "1h"), "1h")
    limit = min(int(args.get("limit", 8)), 30)

    cache_key = f"ls_{symbol}_{period}_{limit}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    url = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
    params = {"symbol": symbol, "period": period, "limit": limit}
    headers = {"User-Agent": "Mozilla/5.0"}

    resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        return f"No Long/Short data available for {symbol}. This symbol may not be a Binance futures pair."

    lines = [f"📊 Long/Short Ratio — {symbol} ({period} candles, last {len(data)} periods)\n"]
    lines.append(f"{'Timestamp (UTC)':<22} {'Long %':>8} {'Short %':>8} {'L/S Ratio':>10} {'Bias'}")
    lines.append("─" * 65)

    for row in data:
        ts = datetime.fromtimestamp(int(row["timestamp"]) / 1000, tz=timezone.utc)
        ts_str = ts.strftime("%Y-%m-%d %H:%M")
        long_pct = float(row["longAccount"]) * 100
        short_pct = float(row["shortAccount"]) * 100
        ratio = float(row["longShortRatio"])

        if long_pct >= 65:
            bias = "⚠️ Crowded Long"
        elif short_pct >= 65:
            bias = "⚠️ Crowded Short"
        elif long_pct >= 55:
            bias = "📈 Slightly Long"
        elif short_pct >= 55:
            bias = "📉 Slightly Short"
        else:
            bias = "⚖️ Balanced"

        lines.append(f"{ts_str:<22} {long_pct:>7.1f}% {short_pct:>7.1f}% {ratio:>10.3f} {bias}")

    # Latest snapshot summary
    latest = data[-1]
    long_pct = float(latest["longAccount"]) * 100
    short_pct = float(latest["shortAccount"]) * 100

    lines.append("\n── CURRENT SNAPSHOT ──────────────────────────────────")
    lines.append(f"Long  : {long_pct:.1f}%")
    lines.append(f"Short : {short_pct:.1f}%")

    if long_pct > 70:
        lines.append("Signal: ⚠️ EXTREME CROWDED LONG — retail heavily long. Smart money often fades this.")
    elif long_pct > 60:
        lines.append("Signal: ⚠️ Moderately crowded long. Watch for stop hunt below recent lows.")
    elif short_pct > 70:
        lines.append("Signal: ⚠️ EXTREME CROWDED SHORT — short squeeze risk is HIGH.")
    elif short_pct > 60:
        lines.append("Signal: Moderately crowded short. Potential short squeeze if price pushes up.")
    else:
        lines.append("Signal: ⚖️ Balanced positioning — no strong contrarian signal from L/S ratio alone.")

    result = "\n".join(lines)
    _set_cached(cache_key, result)
    return result


async def _top_traders(args: dict) -> str:
    raw_symbol = args.get("symbol", "BTCUSDT")
    symbol = _normalize_binance_symbol(raw_symbol)
    period = PERIOD_MAP.get(args.get("period", "1h"), "1h")
    limit = min(int(args.get("limit", 6)), 30)

    cache_key = f"top_{symbol}_{period}_{limit}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    url = "https://fapi.binance.com/futures/data/topLongShortAccountRatio"
    params = {"symbol": symbol, "period": period, "limit": limit}
    headers = {"User-Agent": "Mozilla/5.0"}

    resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        return f"No top trader data for {symbol}."

    lines = [f"🏦 TOP TRADER Long/Short Ratio — {symbol} ({period}, last {len(data)} periods)\n"]
    lines.append("(Top traders = large accounts, often institutional — more informed positioning)\n")
    lines.append(f"{'Timestamp (UTC)':<22} {'Long %':>8} {'Short %':>8} {'Ratio':>8}")
    lines.append("─" * 55)

    for row in data:
        ts = datetime.fromtimestamp(int(row["timestamp"]) / 1000, tz=timezone.utc)
        ts_str = ts.strftime("%Y-%m-%d %H:%M")
        long_pct = float(row["longAccount"]) * 100
        short_pct = float(row["shortAccount"]) * 100
        ratio = float(row["longShortRatio"])
        lines.append(f"{ts_str:<22} {long_pct:>7.1f}% {short_pct:>7.1f}% {ratio:>8.3f}")

    latest = data[-1]
    top_long = float(latest["longAccount"]) * 100
    top_short = float(latest["shortAccount"]) * 100

    lines.append("\n── INTERPRETATION ────────────────────────────────────")
    lines.append(f"Top traders now: {top_long:.1f}% Long / {top_short:.1f}% Short")

    if top_long > 60:
        lines.append("→ Smart money leans LONG. Bullish bias from institutional positioning.")
    elif top_short > 60:
        lines.append("→ Smart money leans SHORT. Bearish bias from institutional positioning.")
    else:
        lines.append("→ Top traders are split. No dominant directional conviction from institutions.")

    result = "\n".join(lines)
    _set_cached(cache_key, result)
    return result


async def _open_interest(args: dict) -> str:
    raw_symbol = args.get("symbol", "BTCUSDT")
    symbol = _normalize_binance_symbol(raw_symbol)
    period = PERIOD_MAP.get(args.get("period", "1h"), "1h")
    limit = min(int(args.get("limit", 8)), 30)

    cache_key = f"oi_{symbol}_{period}_{limit}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    url = "https://fapi.binance.com/futures/data/openInterestHist"
    params = {"symbol": symbol, "period": period, "limit": limit}
    headers = {"User-Agent": "Mozilla/5.0"}

    resp = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    if not data:
        return f"No open interest data for {symbol}."

    lines = [f"📈 Open Interest History — {symbol} ({period}, last {len(data)} periods)\n"]
    lines.append(f"{'Timestamp (UTC)':<22} {'OI (contracts)':>16} {'OI Value (USD)':>16} {'Change'}")
    lines.append("─" * 72)

    prev_oi = None
    for row in data:
        ts = datetime.fromtimestamp(int(row["timestamp"]) / 1000, tz=timezone.utc)
        ts_str = ts.strftime("%Y-%m-%d %H:%M")
        oi = float(row["sumOpenInterest"])
        oi_val = float(row["sumOpenInterestValue"])

        if prev_oi is not None:
            change_pct = ((oi - prev_oi) / prev_oi) * 100
            change_str = f"{change_pct:+.2f}%"
            if change_pct > 2:
                change_str += " 🔺"
            elif change_pct < -2:
                change_str += " 🔻"
        else:
            change_str = "—"

        oi_fmt = f"{oi:,.0f}"
        val_fmt = f"${oi_val/1e6:,.1f}M" if oi_val > 1e6 else f"${oi_val:,.0f}"
        lines.append(f"{ts_str:<22} {oi_fmt:>16} {val_fmt:>16} {change_str}")
        prev_oi = oi

    # Trend summary
    first_oi = float(data[0]["sumOpenInterest"])
    last_oi = float(data[-1]["sumOpenInterest"])
    total_change = ((last_oi - first_oi) / first_oi) * 100

    lines.append("\n── OI TREND SUMMARY ──────────────────────────────────")
    lines.append(f"OI change over period: {total_change:+.2f}%")
    if total_change > 5:
        lines.append("→ OI growing significantly. New money entering the market — move has conviction.")
        lines.append("  Combine with price direction: rising price + rising OI = genuine uptrend.")
        lines.append("  Falling price + rising OI = genuine downtrend, new shorts being added.")
    elif total_change < -5:
        lines.append("→ OI declining significantly. Positions closing — potential trend exhaustion.")
        lines.append("  Price move without OI support = weak, likely to reverse.")
    else:
        lines.append("→ OI relatively stable. No major new conviction from derivatives market.")

    result = "\n".join(lines)
    _set_cached(cache_key, result)
    return result


async def _fear_greed(args: dict) -> str:
    limit = min(int(args.get("limit", 7)), 30)
    cache_key = f"fg_{limit}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    url = f"https://api.alternative.me/fng/?limit={limit}&format=json"
    headers = {"User-Agent": "Mozilla/5.0"}

    resp = requests.get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json().get("data", [])

    if not data:
        return "Fear & Greed Index data unavailable."

    def _label_emoji(val: int) -> str:
        if val <= 24:
            return "😱 Extreme Fear"
        elif val <= 49:
            return "😨 Fear"
        elif val == 50:
            return "😐 Neutral"
        elif val <= 74:
            return "😏 Greed"
        else:
            return "🤑 Extreme Greed"

    lines = [f"🧠 Crypto Fear & Greed Index — last {len(data)} days\n"]
    lines.append(f"{'Date':<14} {'Index':>6} {'Classification'}")
    lines.append("─" * 45)

    for row in data:
        val = int(row["value"])
        date_str = datetime.fromtimestamp(int(row["timestamp"]), tz=timezone.utc).strftime("%Y-%m-%d")
        lines.append(f"{date_str:<14} {val:>6}   {_label_emoji(val)}")

    today = int(data[0]["value"])
    lines.append(f"\n── TODAY: {today} — {_label_emoji(today)} ────────────────────────────")

    if today <= 20:
        lines.append("Market in EXTREME FEAR. Historically a buying opportunity for long-term positions.")
        lines.append("Short-term: more downside possible before capitulation bottom.")
    elif today <= 35:
        lines.append("Market in Fear. Sentiment negative but not extreme. Watch for reversal signs.")
    elif today <= 65:
        lines.append("Market sentiment neutral to moderate. No strong contrarian signal.")
    elif today <= 80:
        lines.append("Market in Greed. Buyers dominant. Watch for exhaustion / overbought conditions.")
    else:
        lines.append("EXTREME GREED. Market euphoric — historically precedes corrections.")
        lines.append("High risk of reversal. Avoid chasing new longs at current levels.")

    lines.append("\nNote: Fear & Greed reflects overall CRYPTO market sentiment, not individual pairs.")
    lines.append("Most relevant for BTC analysis. Use alongside L/S ratio for fuller picture.")

    result = "\n".join(lines)
    _set_cached(cache_key, result)
    return result


# ── Entry point ───────────────────────────────────────────────────────────────

async def _handle_list_tools(_context, _params):
    return ListToolsResult(tools=await list_tools())


async def _handle_call_tool(_context, params):
    return CallToolResult(content=await call_tool(params.name, params.arguments or {}))


app = Server("sentiment-mcp", on_list_tools=_handle_list_tools, on_call_tool=_handle_call_tool)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
