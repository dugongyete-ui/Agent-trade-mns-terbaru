#!/usr/bin/env python3
"""
Economic Calendar MCP Server
Real-time economic calendar: CPI, FOMC, NFP, GDP, PMI, rate decisions and more.
Data source: TradingView Economic Calendar API (free, no API key required).
In-process disk-backed cache (60 min TTL) — one fetch per hour regardless of
how many tool calls the agent makes.
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

# --- legacy mcp SDK compatibility (mcp>=1.27 removed constructor handlers) ---
import sys as _compat_sys
from pathlib import Path as _compat_path
_compat_sys.path.insert(0, str(_compat_path(__file__).resolve().parent.parent))
from _legacy_mcp_compat import make_server  # noqa: E402
# -----------------------------------------------------------------------------


# ── Constants ─────────────────────────────────────────────────────────────────

TV_URL    = "https://economic-calendar.tradingview.com/events"
TIMEOUT   = 15
CACHE_TTL = 3600          # 60 minutes
LOOKAHEAD = 21            # days to fetch ahead
DISK_CACHE = "/tmp/ecocal_cache.json"

TIMEZONES = {
    "WIB": "Asia/Jakarta", "UTC": "UTC", "SGT": "Asia/Singapore",
    "EST": "America/New_York", "GMT": "Europe/London", "MYT": "Asia/Kuala_Lumpur",
}

# TradingView uses importance: -1 = N/A, 0 = Low, 1 = High
# We expose 3 levels to the agent
IMP_RANK  = {-1: 0, 0: 1, 1: 3}   # internal numeric rank
IMP_LABEL = {-1: "N/A", 0: "Low", 1: "High"}
IMP_ICON  = {-1: "⚪", 0: "🟢", 1: "🔴"}

# TradingView 2-letter country → currency code
COUNTRY_TO_CCY = {
    "US": "USD", "EU": "EUR", "GB": "GBP", "JP": "JPY",
    "AU": "AUD", "NZ": "NZD", "CA": "CAD", "CH": "CHF",
    "CN": "CNY", "HK": "HKD", "SG": "SGD",
}

CCY_TO_COUNTRY = {v: k for k, v in COUNTRY_TO_CCY.items()}

COUNTRY_FLAG = {
    "US": "🇺🇸", "EU": "🇪🇺", "GB": "🇬🇧", "JP": "🇯🇵",
    "AU": "🇦🇺", "NZ": "🇳🇿", "CA": "🇨🇦", "CH": "🇨🇭",
    "CN": "🇨🇳", "HK": "🇭🇰", "SG": "🇸🇬",
}

EVENT_KEYWORDS = {
    "CPI":      ["inflation rate", "consumer price index", "cpi"],
    "PPI":      ["producer price", "ppi"],
    "PCE":      ["pce", "personal consumption", "core pce"],
    "FOMC":     ["fed interest rate", "federal funds", "fomc", "fed press conference",
                 "fomc economic projections"],
    "NFP":      ["non-farm", "nonfarm", "nfp", "payroll", "employment change"],
    "ADP":      ["adp"],
    "GDP":      ["gdp", "gross domestic product"],
    "PMI":      ["pmi", "purchasing managers", "s&p global"],
    "ISM":      ["ism manufacturing", "ism services"],
    "RETAIL":   ["retail sales"],
    "JOBS":     ["unemployment rate", "jobless claims", "initial claims"],
    "HOUSING":  ["building permits", "housing starts", "existing home", "new home"],
    "TRADE":    ["balance of trade", "trade balance", "current account"],
    "OIL":      ["crude oil", "oil inventories"],
    "DURABLE":  ["durable goods"],
    "CONSUMER": ["consumer confidence", "consumer sentiment"],
    "ECB":      ["ecb", "main refinancing"],
    "BOE":      ["boe interest rate", "bank of england"],
    "BOJ":      ["boj interest rate", "bank of japan"],
    "RBA":      ["rba interest rate", "reserve bank of australia"],
    "RBNZ":     ["rbnz", "reserve bank of new zealand"],
    "BOC":      ["boc", "bank of canada"],
    "PCE":      ["core pce", "personal spending", "personal income"],
}

# ── Cache ─────────────────────────────────────────────────────────────────────

_mem: dict = {"events": [], "fetched_at": 0.0}


def _load_disk_cache() -> bool:
    """Try to load from disk cache. Returns True if cache is fresh enough."""
    try:
        if not os.path.exists(DISK_CACHE):
            return False
        with open(DISK_CACHE) as f:
            data = json.load(f)
        if time.time() - data.get("fetched_at", 0) < CACHE_TTL:
            _mem["events"]     = data["events"]
            _mem["fetched_at"] = data["fetched_at"]
            return True
    except Exception:
        pass
    return False


def _save_disk_cache(events: list, fetched_at: float) -> None:
    try:
        with open(DISK_CACHE, "w") as f:
            json.dump({"events": events, "fetched_at": fetched_at}, f)
    except Exception:
        pass


def _fetch_tv() -> list[dict]:
    """Fetch from TradingView Economic Calendar with disk + memory cache."""
    now = time.time()

    # 1. Memory cache (fastest — same process)
    if _mem["events"] and (now - _mem["fetched_at"]) < CACHE_TTL:
        return _mem["events"]

    # 2. Disk cache (survives process restarts)
    if _load_disk_cache():
        return _mem["events"]

    # 3. Fetch fresh
    now_utc   = datetime.now(timezone.utc)
    frm       = now_utc.strftime("%Y-%m-%dT00:00:00.000Z")
    to        = (now_utc + timedelta(days=LOOKAHEAD)).strftime("%Y-%m-%dT23:59:59.000Z")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Origin":  "https://www.tradingview.com",
        "Referer": "https://www.tradingview.com/economic-calendar/",
        "Accept":  "application/json",
    }

    resp = requests.get(
        TV_URL,
        params={"from": frm, "to": to, "countries": "US,EU,GB,JP,AU,NZ,CA,CH"},
        headers=headers,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    events = resp.json().get("result", [])

    _mem["events"]     = events
    _mem["fetched_at"] = now
    _save_disk_cache(events, now)
    return events


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _to_local(dt_utc: datetime, tz_code: str) -> str:
    iana = TIMEZONES.get(tz_code.upper(), tz_code)
    try:
        local = dt_utc.astimezone(ZoneInfo(iana))
        return local.strftime("%a %d %b %Y  %H:%M ") + tz_code
    except Exception:
        return dt_utc.strftime("%Y-%m-%d %H:%M UTC")


def _countdown(dt_utc: datetime) -> str:
    now  = datetime.now(timezone.utc)
    mins = int((dt_utc - now).total_seconds() / 60)
    if mins <= 0:
        return "  🔴 RELEASED" if mins > -120 else ""
    h, m = divmod(mins, 60)
    d, h = divmod(h, 24)
    if d:   return f"  ⏱ in {d}d {h}h {m:02d}m"
    if h:   return f"  ⏱ in {h}h {m:02d}m"
    return      f"  ⏱ in {m}m"


def _fmt_val(val, unit: str = "") -> str:
    if val is None:
        return "—"
    unit = unit or ""
    return f"{val}{unit}"


def _format_event(ev: dict, tz: str = "WIB") -> str:
    dt_utc   = _parse_dt(ev.get("date", ""))
    imp      = ev.get("importance", 0)
    icon     = IMP_ICON.get(imp, "⚪")
    country  = ev.get("country", "")
    flag     = COUNTRY_FLAG.get(country, "")
    ccy      = COUNTRY_TO_CCY.get(country, country)
    title    = ev.get("title", "Unknown")
    unit     = ev.get("unit") or ""
    forecast = _fmt_val(ev.get("forecast"), unit)
    previous = _fmt_val(ev.get("previous"), unit)
    actual   = ev.get("actual")
    label    = IMP_LABEL.get(imp, "?")

    time_str   = _to_local(dt_utc, tz) if dt_utc else "Time TBD"
    has_actual = actual is not None
    countdown  = _countdown(dt_utc) if dt_utc and not has_actual else ""
    actual_str = f"\n   ✅ Actual: {actual}{unit}" if has_actual else ""

    return (
        f"{icon}[{label}] {flag}{ccy}  {title}\n"
        f"   📅 {time_str}{countdown}\n"
        f"   Forecast: {forecast}  |  Previous: {previous}{actual_str}"
    )


def _filter(events: list[dict], ccys: list[str] | None, min_imp_label: str) -> list[dict]:
    """Filter by currency (converted to TV 2-letter code) and minimum importance."""
    min_imp_val = {"High": 1, "Medium": 0, "Low": -1}.get(min_imp_label, -1)
    out = []
    for ev in events:
        if ev.get("importance", -1) < min_imp_val:
            continue
        if ccys:
            ev_ccy = COUNTRY_TO_CCY.get(ev.get("country", ""), "")
            if ev_ccy.upper() not in [c.upper() for c in ccys]:
                continue
        out.append(ev)
    return out


def _sort(events: list[dict]) -> list[dict]:
    _FAR = datetime.max.replace(tzinfo=timezone.utc)
    return sorted(events, key=lambda e: _parse_dt(e.get("date", "")) or _FAR)


# ── Tool Handlers ─────────────────────────────────────────────────────────────

def _handle_today(args: dict) -> str:
    tz     = args.get("display_tz", "WIB")
    iana   = TIMEZONES.get(tz.upper(), "Asia/Jakarta")
    events = _fetch_tv()

    now_local  = datetime.now(timezone.utc).astimezone(ZoneInfo(iana))
    today_date = now_local.date()

    today = _sort([
        ev for ev in events
        if (_parse_dt(ev.get("date", "")) or datetime.min.replace(tzinfo=timezone.utc))
            .astimezone(ZoneInfo(iana)).date() == today_date
    ])

    high   = [e for e in today if e.get("importance") == 1]
    medium = [e for e in today if e.get("importance") == 0]
    low    = [e for e in today if e.get("importance", -1) < 0]

    day_str = now_local.strftime("%A, %d %b %Y")
    lines = [
        f"📅 Today's Economic Calendar — {day_str} ({tz})",
        f"   🔴 High: {len(high)}  🟢 Low: {len(medium) + len(low)}",
        ""
    ]
    if not today:
        lines.append("No economic events scheduled today.")
    else:
        for ev in today:
            lines.append(_format_event(ev, tz))
            lines.append("")

    lines.append(f"Source: TradingView  |  {len(today)} events today")
    return "\n".join(lines)


def _handle_upcoming(args: dict) -> str:
    count     = min(max(int(args.get("count", 5)), 1), 20)
    min_imp   = args.get("min_impact", "High")
    ccys      = args.get("countries") or []
    tz        = args.get("display_tz", "WIB")

    events   = _fetch_tv()
    events   = _filter(events, ccys if ccys else None, min_imp)
    now_utc  = datetime.now(timezone.utc)
    upcoming = _sort([
        ev for ev in events
        if (_parse_dt(ev.get("date", "")) or now_utc) > now_utc
    ])[:count]

    if not upcoming:
        return (
            f"No upcoming {min_imp}-impact events found in the next {LOOKAHEAD} days.\n"
            f"Try min_impact='Low' to see all events."
        )

    lines = [
        f"⏰ Next {len(upcoming)} Upcoming Events  [{min_imp} Impact]",
        f"   {now_utc.strftime('%d %b %Y  %H:%M UTC')}",
        ""
    ]
    for ev in upcoming:
        lines.append(_format_event(ev, tz))
        lines.append("")

    lines.append(f"Source: TradingView  |  Times in {tz}")
    return "\n".join(lines)


def _handle_get_week(args: dict) -> str:
    min_imp = args.get("min_impact", "High")
    ccys    = args.get("countries") or []
    tz      = args.get("display_tz", "WIB")

    events = _sort(_filter(_fetch_tv(), ccys if ccys else None, min_imp))

    if not events:
        return (
            f"No {min_imp}-impact events"
            + (f" for {', '.join(ccys)}" if ccys else "")
            + f" in the next {LOOKAHEAD} days.\n"
            f"Try min_impact='Low' to see all events."
        )

    now_utc = datetime.now(timezone.utc)
    ccy_str = ", ".join(ccys[:4]) + ("..." if len(ccys) > 4 else "") if ccys else "all major"
    lines = [
        f"📅 Calendar  [{min_imp} Impact | {ccy_str}]",
        f"   {now_utc.strftime('%A, %d %b %Y  %H:%M UTC')}  |  {len(events)} events",
        ""
    ]

    current_day = None
    for ev in events:
        dt      = _parse_dt(ev.get("date", ""))
        day_str = dt.strftime("%A, %d %b %Y") if dt else "Unknown Date"
        if day_str != current_day:
            lines.append(f"── {day_str} {'─'*40}")
            current_day = day_str
        lines.append(_format_event(ev, tz))
        lines.append("")

    lines.append(f"Source: TradingView  |  Times in {tz}")
    return "\n".join(lines)


def _handle_find_event(args: dict) -> str:
    query   = args.get("event_name", "").strip()
    ccy     = args.get("country", "").upper()
    tz      = args.get("display_tz", "WIB")

    if not query:
        return "Please provide an event name, e.g. 'CPI', 'FOMC', 'NFP', 'BOJ'."

    events      = _fetch_tv()
    query_lower = query.lower()

    # Match known categories or use raw query
    keywords = [query_lower]
    for _cat, kws in EVENT_KEYWORDS.items():
        if query.upper() == _cat or any(k in query_lower for k in kws):
            keywords = kws
            break

    # Convert CCY filter to 2-letter country code
    country_filter = CCY_TO_COUNTRY.get(ccy, ccy) if ccy else ""

    matches = []
    for ev in events:
        ev_title   = ev.get("title", "").lower()
        ev_country = ev.get("country", "").upper()
        if country_filter and ev_country != country_filter:
            continue
        if any(kw in ev_title for kw in keywords):
            matches.append(ev)

    now_utc = datetime.now(timezone.utc)
    _FAR    = datetime.max.replace(tzinfo=timezone.utc)
    matches.sort(key=lambda e: (
        0 if (_parse_dt(e.get("date")) or now_utc) >= now_utc else 1,
        _parse_dt(e.get("date")) or _FAR,
    ))
    matches = matches[:5]

    if not matches:
        return (
            f"No events found matching '{query}'"
            + (f" ({ccy})" if ccy else "")
            + f" in the next {LOOKAHEAD} days.\n"
            f"The event may be beyond the current window, or try a broader keyword "
            f"e.g. 'inflation' instead of 'cpi core flash'."
        )

    lines = [
        f"🔍 '{query.upper()}'"
        + (f"  [{ccy}]" if ccy else "")
        + f"  — {len(matches)} result(s)",
        ""
    ]
    for ev in matches:
        lines.append(_format_event(ev, tz))
        dt = _parse_dt(ev.get("date"))
        if dt:
            mins = int((dt - now_utc).total_seconds() / 60)
            if mins > 0:
                h, m = divmod(mins, 60)
                d, h = divmod(h, 24)
                if d:   lines.append(f"   ⏱ Countdown: {d}d {h}h {m:02d}m")
                elif h: lines.append(f"   ⏱ Countdown: {h}h {m:02d}m")
                else:   lines.append(f"   ⏱ Countdown: {m}m")
        lines.append("")

    lines.append(f"Source: TradingView  |  Times in {tz}")
    return "\n".join(lines)


# ── MCP Tool Definitions ──────────────────────────────────────────────────────

async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="calendar-today",
            description=(
                "Get today's economic events (current day in WIB). "
                "Shows everything releasing today: CPI, rate decisions, NFP, PMI etc. "
                "with impact level, forecast, previous, actual value, and countdown. "
                "Best for: daily briefing, 'ada event hari ini?'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "display_tz": {
                        "type": "string",
                        "description": "Timezone: WIB, UTC, SGT, EST, GMT",
                        "default": "WIB"
                    }
                }
            }
        ),
        Tool(
            name="calendar-upcoming",
            description=(
                "Get the next N upcoming high-impact economic events from right now. "
                "Returns events with countdown timer. "
                "Covers: FOMC (Fed rate), BOJ, BOE, RBA, CPI, NFP, GDP, PMI, PCE, retail sales, etc. "
                "ALWAYS call this during Phase 0 scan to check event risk before trade entry. "
                "If a High-impact event is within 4 hours → flag it in the trading decision."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of upcoming events (1–20)",
                        "default": 5
                    },
                    "min_impact": {
                        "type": "string",
                        "description": "'High' = major events only (FOMC/CPI/BOJ etc.) | 'Low' = all events",
                        "default": "High"
                    },
                    "countries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Currency filter: ['USD','EUR','GBP','JPY']. Empty = all.",
                        "default": []
                    },
                    "display_tz": {
                        "type": "string",
                        "description": "Timezone: WIB, UTC, SGT, EST, GMT",
                        "default": "WIB"
                    }
                }
            }
        ),
        Tool(
            name="calendar-find-event",
            description=(
                "Find a specific upcoming economic event by name. "
                "Supported searches: CPI, FOMC, NFP, GDP, PMI, PPI, PCE, ADP, "
                "BOE, ECB, BOJ, RBA, RBNZ, BOC, retail sales, jobless claims, "
                "building permits, crude oil, durable goods, trade balance. "
                "Returns exact date/time (in WIB), forecast vs previous, and countdown. "
                "Use for: 'kapan FOMC?', 'NFP jam berapa?', 'berapa forecast BOJ rate?'"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "event_name": {
                        "type": "string",
                        "description": "Event keyword: CPI, FOMC, NFP, BOJ, GDP, PMI, BOE, RBA, etc."
                    },
                    "country": {
                        "type": "string",
                        "description": "Optional currency: USD, EUR, GBP, JPY, AUD, NZD, CAD, CHF",
                        "default": ""
                    },
                    "display_tz": {
                        "type": "string",
                        "description": "Timezone: WIB, UTC, SGT, EST, GMT",
                        "default": "WIB"
                    }
                },
                "required": ["event_name"]
            }
        ),
        Tool(
            name="calendar-get-week",
            description=(
                "Get full calendar for the next 3 weeks filtered by impact and currency. "
                "Events grouped by day. "
                "Best for: 'tampilkan semua event minggu ini', weekly trade planning, "
                "understanding macro schedule before opening positions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "min_impact": {
                        "type": "string",
                        "description": "'High' = major only | 'Low' = all events",
                        "default": "High"
                    },
                    "countries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Currency filter e.g. ['USD','EUR','GBP']. Empty = all major.",
                        "default": []
                    },
                    "display_tz": {
                        "type": "string",
                        "description": "Timezone: WIB, UTC, SGT, EST, GMT",
                        "default": "WIB"
                    }
                }
            }
        ),
    ]


async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if   name == "calendar-today":       text = _handle_today(arguments)
        elif name == "calendar-upcoming":     text = _handle_upcoming(arguments)
        elif name == "calendar-find-event":   text = _handle_find_event(arguments)
        elif name == "calendar-get-week":     text = _handle_get_week(arguments)
        else:                                 text = f"Unknown tool: {name}"
    except requests.exceptions.RequestException as e:
        text = (
            f"⚠️  Economic Calendar — Network Error ({type(e).__name__})\n\n"
            f"Could not reach TradingView calendar API. "
            f"Fallback: use info-search-web with 'economic calendar this week high impact events'."
        )
    except Exception as e:
        text = f"Economic Calendar Error: {type(e).__name__}: {e}"

    return [TextContent(type="text", text=text)]


async def _handle_list_tools(_context, _params):
    return ListToolsResult(tools=await list_tools())


async def _handle_call_tool(_context, params):
    return CallToolResult(content=await call_tool(params.name, params.arguments or {}))


app = make_server("economic-calendar-mcp", on_list_tools=_handle_list_tools, on_call_tool=_handle_call_tool)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
