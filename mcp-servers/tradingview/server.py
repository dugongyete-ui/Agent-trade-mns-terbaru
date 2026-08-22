"""
mcp-servers/tradingview/server.py

Wrapper around tradingview-mcp that routes all scanner.tradingview.com
requests through TV_PROXY_BASE before starting the MCP server.

TV_PROXY_BASE must expose a reverse-proxy that accepts requests in the form:
    {TV_PROXY_BASE}/?url={original_scanner_url}

If TV_PROXY_BASE is not set the server connects directly to
scanner.tradingview.com (default behaviour).

Patches applied:
  1. tradingview_screener.query.URL  — module-level URL template (format-safe)
  2. requests.Session.request        — catch-all for every hardcoded URL that
                                       bypasses the template (e.g. screener_provider,
                                       tradingview_ta, options scan2, etc.)
"""
from __future__ import annotations

import os
import sys
from urllib.parse import quote

TV_PROXY_BASE = os.environ.get("TV_PROXY_BASE", "").rstrip("/")
SCANNER_ORIGIN = "https://scanner.tradingview.com"


def _proxy_url(original_url: str) -> str:
    """Rewrite a scanner.tradingview.com URL to use the proxy's ?url= format."""
    return f"{TV_PROXY_BASE}/?url={quote(original_url, safe=':/?=&%')}"


def _apply_proxy_patches() -> None:
    import requests

    # ── 1. tradingview_screener URL template ──────────────────────────────────
    # The template is 'https://scanner.tradingview.com/{market}/scan'.
    # We wrap it so that after .format(market=...) the result is already proxied.
    try:
        import tradingview_screener.query as _tsq
        original = _tsq.URL
        # Keep the {market} placeholder intact — it gets filled later by .format()
        _tsq.URL = f"{TV_PROXY_BASE}/?url={SCANNER_ORIGIN}/{{market}}/scan"
        print(
            f"[tradingview-mcp] patched tradingview_screener.query.URL:\n"
            f"  before: {original!r}\n"
            f"  after:  {_tsq.URL!r}",
            file=sys.stderr,
            flush=True,
        )
    except Exception as exc:
        print(
            f"[tradingview-mcp] WARNING: could not patch tradingview_screener.query.URL: {exc}",
            file=sys.stderr,
        )

    # ── 2. Catch-all: requests.Session intercepts every remaining hardcoded URL ─
    # Covers: screener_provider.py fetch_atr_for_ticker, tradingview_ta scan_url,
    #         screeners.py options scan2, and any future additions.
    _orig_request = requests.Session.request

    def _patched_request(self, method, url, **kwargs):  # type: ignore[override]
        if isinstance(url, str) and url.startswith(SCANNER_ORIGIN):
            url = _proxy_url(url)
        return _orig_request(self, method, url, **kwargs)

    requests.Session.request = _patched_request  # type: ignore[method-assign]

    print(
        f"[tradingview-mcp] TV_PROXY_BASE active\n"
        f"  proxy:  {TV_PROXY_BASE}\n"
        f"  format: {TV_PROXY_BASE}/?url={{original_url}}",
        file=sys.stderr,
        flush=True,
    )


def _install_mcp2_fastmcp_compat() -> None:
    """Bridge tradingview-mcp 0.9.x's old import to MCP 2.0's MCPServer."""
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore  # noqa: F401
        return
    except ImportError:
        pass

    import types
    from mcp.server.mcpserver import MCPServer

    compat = types.ModuleType("mcp.server.fastmcp")
    compat.FastMCP = MCPServer
    sys.modules["mcp.server.fastmcp"] = compat


def _install_live_market_compat() -> None:
    """Adapt tradingview-mcp 0.9.1 to its installed Yahoo price service.

    The released package passes ``exchange`` to ``get_price`` although the
    installed Yahoo implementation accepts only ``symbol``. It also returns
    ``price``/``change_pct`` while the live-market service reads
    ``current_price``/``change_percent``. Patch the module-local function at
    startup so all MCP callers receive a populated, backward-compatible
    snapshot without enabling any write/trading operation.
    """
    try:
        import inspect
        from tradingview_mcp.core.services import live_market_service as live
        from tradingview_mcp.core.services import yahoo_finance_service as yahoo

        if "exchange" in inspect.signature(yahoo.get_price).parameters:
            return
        if getattr(live.get_price, "_dzeck_compat", False):
            return

        def compatible_get_price(symbol: str, exchange: str = "") -> dict:
            requested_symbol = str(symbol).strip().upper()
            yahoo_symbol = requested_symbol
            if yahoo_symbol.endswith("USDT") and len(yahoo_symbol) > 4:
                yahoo_symbol = yahoo_symbol[:-4] + "-USD"
            elif yahoo_symbol.endswith("USDC") and len(yahoo_symbol) > 4:
                yahoo_symbol = yahoo_symbol[:-4] + "-USD"
            elif "/" in yahoo_symbol:
                yahoo_symbol = yahoo_symbol.replace("/", "-")
            data = yahoo.get_price(symbol=yahoo_symbol)
            if not isinstance(data, dict) or "error" in data:
                return {**(data if isinstance(data, dict) else {}), "symbol": requested_symbol}
            return {
                **data,
                "symbol": requested_symbol,
                "exchange": exchange.upper() if exchange else data.get("exchange", ""),
                "current_price": data.get("price"),
                "change_percent": data.get("change_pct"),
            }

        compatible_get_price._dzeck_compat = True  # type: ignore[attr-defined]
        live.get_price = compatible_get_price
        print(
            "[tradingview-mcp] installed Yahoo get_price compatibility adapter",
            file=sys.stderr,
            flush=True,
        )
    except Exception as exc:
        print(
            f"[tradingview-mcp] WARNING: could not patch Yahoo price compatibility: {exc}",
            file=sys.stderr,
            flush=True,
        )


if __name__ == "__main__":
    _install_mcp2_fastmcp_compat()
    _install_live_market_compat()
    if TV_PROXY_BASE:
        _apply_proxy_patches()
    else:
        print(
            "[tradingview-mcp] TV_PROXY_BASE not set — connecting directly to TradingView.",
            file=sys.stderr,
            flush=True,
        )

    from tradingview_mcp.server import main
    main()
