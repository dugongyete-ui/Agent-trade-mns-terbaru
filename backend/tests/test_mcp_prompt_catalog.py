"""Keep the prompt catalog aligned with locally declared MCP tools."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROMPT = ROOT / "backend/app/domain/services/prompts/system.py"
DERIV_SERVER = ROOT / "mcp-servers/deriv/server.py"
MCP_TOOLKIT = ROOT / "backend/app/domain/services/tools/mcp.py"


def _prompt_tool_names() -> set[str]:
    text = PROMPT.read_text()
    names = set(re.findall(r"^  ([a-z][a-z0-9_-]+)(?:\s+\(|\s*/|$)", text, re.MULTILINE))
    names.update(
        name
        for line in text.splitlines()
        if line.startswith("  ")
        for name in re.findall(r"\b[a-z][a-z0-9_-]+\b", line.split("Answers:", 1)[0])
        if name in {"coin_analysis", "combined_analysis", "top_gainers", "top_losers",
                    "get_live_price", "get_multi_price", "volume_breakout_scanner",
                    "smart_volume_scanner", "kelly_position_size",
                    "risk_based_position_size", "assess_trade_risk_full"}
                    | {"list_trade_signals", "save_trade_signal",
                       "compare_strategies", "recognize_market_pattern"}
    )
    return names


def test_public_deriv_tools_are_documented():
    declared = set(re.findall(r'name="(deriv-[a-z0-9-]+)"', DERIV_SERVER.read_text()))
    internal = {"deriv-keepalive", "deriv-reader", "deriv-reconnect"}
    prompt_names = _prompt_tool_names()
    assert declared - internal <= prompt_names


def test_allowed_tradingview_tools_are_documented():
    toolkit_text = MCP_TOOLKIT.read_text()
    allowed_block = re.search(
        r"_TRADINGVIEW_ALLOWED\s*=\s*\{(.*?)\n\s*\}",
        toolkit_text,
        re.DOTALL,
    )
    assert allowed_block, "TradingView allowlist is missing"
    allowed = set(re.findall(r'"([a-z][a-z0-9_]*)"', allowed_block.group(1)))
    assert allowed <= _prompt_tool_names()