#!/usr/bin/env python3
"""
Time MCP Server
Provides time, timezone, and forex market hours awareness for AI agents.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool


# Session hours are expressed in each venue's local time. ZoneInfo then
# applies daylight-saving rules where relevant instead of fixed UTC offsets.
FOREX_SESSIONS = {
    "Sydney":    {"open": 7, "close": 16, "tz": "Australia/Sydney"},
    "Tokyo":     {"open": 9, "close": 18, "tz": "Asia/Tokyo"},
    "London":    {"open": 8, "close": 17, "tz": "Europe/London"},
    "New York":  {"open": 8, "close": 17, "tz": "America/New_York"},
}

NEW_YORK_TZ = ZoneInfo("America/New_York")

TIMEZONES = {
    "WIB":  "Asia/Jakarta",
    "WITA": "Asia/Makassar",
    "WIT":  "Asia/Jayapura",
    "UTC":  "UTC",
    "SGT":  "Asia/Singapore",
    "MYT":  "Asia/Kuala_Lumpur",
    "EST":  "America/New_York",
    "GMT":  "Europe/London",
    "JST":  "Asia/Tokyo",
    "AEST": "Australia/Sydney",
}


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def forex_market_status() -> dict:
    now_utc = get_utc_now()
    now_new_york = now_utc.astimezone(NEW_YORK_TZ)

    # Retail FX is closed from Friday 17:00 New York through Sunday 17:00
    # New York. This boundary naturally follows New York DST transitions.
    ny_weekday = now_new_york.weekday()
    weekend = (
        (ny_weekday == 4 and now_new_york.hour >= 17)
        or ny_weekday == 5
        or (ny_weekday == 6 and now_new_york.hour < 17)
    )

    sessions = {}
    active = []
    if not weekend:
        for name, session in FOREX_SESSIONS.items():
            local_tz = ZoneInfo(session["tz"])
            local_now = now_utc.astimezone(local_tz)
            local_decimal = local_now.hour + local_now.minute / 60
            is_open = session["open"] <= local_decimal < session["close"]
            sessions[name] = {
                "open": is_open,
                "local_time": local_now.strftime("%H:%M %Z"),
            }
            if is_open:
                active.append(name)

    overlaps = []
    if "London" in active and "New York" in active:
        overlaps.append("London–New York (High volatility!)")
    if "Tokyo" in active and "London" in active:
        overlaps.append("Tokyo–London")
    if "Sydney" in active and "Tokyo" in active:
        overlaps.append("Sydney–Tokyo")

    return {
        "weekend": weekend,
        "sessions": sessions,
        "active": active,
        "overlaps": overlaps,
    }


async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get-current-time",
            description=(
                "Get the current date and time in one or more timezones. "
                "Supports: WIB, WITA, WIT, UTC, SGT, MYT, EST, GMT, JST, AEST, "
                "or any IANA timezone string like 'America/New_York'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "timezones": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of timezone codes or IANA strings",
                        "default": ["WIB", "UTC", "GMT", "EST"]
                    }
                }
            }
        ),
        Tool(
            name="forex-market-hours",
            description=(
                "Check which forex market sessions are currently open — "
                "Sydney, Tokyo, London, New York. "
                "Also shows overlap periods (high volatility), and whether it's weekend (market closed)."
            ),
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="convert-timezone",
            description="Convert a specific time from one timezone to another.",
            inputSchema={
                "type": "object",
                "properties": {
                    "time": {
                        "type": "string",
                        "description": (
                            "Time to convert. Accepts HH:MM (e.g. '14:30') "
                            "or full datetime (e.g. '2025-06-14 14:30')"
                        ),
                    },
                    "from_tz": {
                        "type": "string",
                        "description": "Source timezone code or IANA string e.g. WIB, UTC",
                    },
                    "to_tz": {
                        "type": "string",
                        "description": "Target timezone code or IANA string e.g. EST, GMT",
                    }
                },
                "required": ["time", "from_tz", "to_tz"]
            }
        ),
        Tool(
            name="time-until-market-open",
            description=(
                "Calculate how long until a specific forex session opens. "
                "Useful for knowing when London or New York opens from your local time."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session": {
                        "type": "string",
                        "description": (
                            "Session name (case-insensitive): "
                            "sydney, tokyo, london, new york"
                        ),
                    }
                },
                "required": ["session"]
            }
        ),
    ]


async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "get-current-time":
            tzs = arguments.get("timezones", ["WIB", "UTC", "GMT", "EST"])
            now_utc = get_utc_now()
            lines = [f"🕐 Current Time\n"]
            for tz_code in tzs:
                iana = TIMEZONES.get(tz_code.upper(), tz_code)
                try:
                    local = now_utc.astimezone(ZoneInfo(iana))
                    lines.append(f"{tz_code:<8} {local.strftime('%Y-%m-%d %H:%M:%S %Z')}")
                except Exception:
                    lines.append(f"{tz_code:<8} Invalid timezone")
            text = "\n".join(lines)

        elif name == "forex-market-hours":
            now_utc = get_utc_now()
            status = forex_market_status()
            lines = [f"🌍 Forex Market Status — {now_utc.strftime('%Y-%m-%d %H:%M UTC')}\n"]

            if status["weekend"]:
                lines.append("⛔ WEEKEND — All forex markets CLOSED")
                lines.append("Markets reopen: Monday 22:00 UTC (Sydney open)")
            else:
                lines.append(f"{'Session':<12} {'Status':<8} {'Local Time'}")
                lines.append("-" * 40)
                for sess, info in status["sessions"].items():
                    icon = "🟢" if info["open"] else "🔴"
                    status_str = "OPEN" if info["open"] else "CLOSED"
                    lines.append(f"{sess:<12} {icon} {status_str:<6}  {info['local_time']}")

                lines.append("")
                if status["active"]:
                    lines.append(f"Active sessions: {', '.join(status['active'])}")
                else:
                    lines.append("No sessions currently active")

                if status["overlaps"]:
                    lines.append(f"⚡ Overlaps (high volatility): {', '.join(status['overlaps'])}")

                if not status["active"]:
                    lines.append("\n⚠️  Low liquidity period — wider spreads likely on XAUUSD")

            text = "\n".join(lines)

        elif name == "convert-timezone":
            time_str = arguments["time"].strip()
            from_code = arguments["from_tz"]
            to_code = arguments["to_tz"]

            from_iana = TIMEZONES.get(from_code.upper(), from_code)
            to_iana = TIMEZONES.get(to_code.upper(), to_code)

            # Accept both "HH:MM" and "YYYY-MM-DD HH:MM" formats
            if len(time_str) > 5:
                # Try full datetime format first
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"):
                    try:
                        parsed = datetime.strptime(time_str, fmt)
                        hour, minute = parsed.hour, parsed.minute
                        ref_date = parsed.date()
                        break
                    except ValueError:
                        continue
                else:
                    # Last resort: grab last HH:MM portion
                    hm_part = time_str.split(" ")[-1]
                    hour, minute = map(int, hm_part.split(":"))
                    ref_date = datetime.now(timezone.utc).date()
            else:
                hour, minute = map(int, time_str.split(":"))
                ref_date = datetime.now(timezone.utc).date()

            dt_from = datetime(ref_date.year, ref_date.month, ref_date.day, hour, minute,
                               tzinfo=ZoneInfo(from_iana))
            dt_to = dt_from.astimezone(ZoneInfo(to_iana))

            text = (
                f"🔄 Timezone Conversion\n\n"
                f"{time_str} {from_code}  →  {dt_to.strftime('%Y-%m-%d %H:%M')} {to_code}\n"
                f"({from_iana} → {to_iana})"
            )

        elif name == "time-until-market-open":
            raw_session = arguments["session"].strip()
            # Normalise to Title Case and support "new york" → "New York"
            SESSION_ALIASES = {
                "sydney":   "Sydney",
                "tokyo":    "Tokyo",
                "london":   "London",
                "new york": "New York",
                "newyork":  "New York",
                "ny":       "New York",
                "new_york": "New York",
            }
            session = SESSION_ALIASES.get(raw_session.lower(), raw_session.title())
            if session not in FOREX_SESSIONS:
                valid = ", ".join(FOREX_SESSIONS.keys())
                text = f"❌ Unknown session '{raw_session}'. Valid options: {valid}"
                return [TextContent(type="text", text=text)]
            session_config = FOREX_SESSIONS[session]
            now_utc = get_utc_now()
            local_tz = ZoneInfo(session_config["tz"])
            now_local = now_utc.astimezone(local_tz)
            status = forex_market_status()
            is_open = status["sessions"].get(session, {}).get("open", False)

            if status["weekend"]:
                reopen = now_utc.astimezone(NEW_YORK_TZ)
                days_until_sunday = (6 - reopen.weekday()) % 7
                reopen = reopen.replace(hour=17, minute=0, second=0, microsecond=0)
                if days_until_sunday or reopen <= now_utc.astimezone(NEW_YORK_TZ):
                    reopen += timedelta(days=days_until_sunday or 7)
                delta = reopen - now_utc.astimezone(NEW_YORK_TZ)
                hours, remainder = divmod(max(0, int(delta.total_seconds())), 3600)
                minutes = remainder // 60
                text = (
                    f"⏰ {session} Market Open\n\n"
                    f"It's the weekend — markets are CLOSED.\n"
                    f"Forex reopens in approximately {hours}h {minutes}m "
                    f"(Sunday 17:00 New York time)."
                )
            elif is_open:
                text = f"✅ {session} market is currently OPEN\nLocal time: {now_local.strftime('%H:%M %Z')}"
            else:
                open_hour = session_config["open"]
                next_open = now_local.replace(
                    hour=open_hour, minute=0, second=0, microsecond=0
                )
                if next_open <= now_local:
                    next_open += timedelta(days=1)
                delta = next_open - now_local
                hours, remainder = divmod(max(0, int(delta.total_seconds())), 3600)
                minutes = remainder // 60
                text = (
                    f"⏰ {session} Market opens in: {hours}h {minutes}m\n"
                    f"Opens at {open_hour:02d}:00 {now_local.tzname()} local time\n"
                    f"Current local time: {now_local.strftime('%H:%M %Z')}"
                )
        else:
            text = f"Unknown tool: {name}"

    except Exception as e:
        text = f"Error: {type(e).__name__}: {e}"

    return [TextContent(type="text", text=text)]


async def _handle_list_tools(_context, _params):
    return ListToolsResult(tools=await list_tools())


async def _handle_call_tool(_context, params):
    return CallToolResult(content=await call_tool(params.name, params.arguments or {}))


app = Server("time-mcp", on_list_tools=_handle_list_tools, on_call_tool=_handle_call_tool)


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
