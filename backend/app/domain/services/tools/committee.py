"""Committee toolkit — a multi-agent debate: bull vs bear → risk officer → decision.

Port of the Vibe-Trading "investment committee" swarm preset, rebuilt natively
on this codebase's Plan-Act machinery:

- Layer 1: bull researcher and bear researcher work IN PARALLEL, each building
  their strongest honest case with real tool calls (live price, candles,
  indicators, sentiment, calendar).
- Layer 2: the chief risk officer reviews BOTH reports — checks the bull side
  for confirmation bias and the bear side for excessive pessimism, finds
  blind spots, and issues position-sizing guidance + a verdict.
- Layer 3: the portfolio manager weighs everything and makes ONE executable
  final decision.

Anti-hallucination is structural: every worker gets the SAME ground-truth
block (recent candles fetched before the debate) plus a hard data-citation
discipline clause — numbers from memory are forbidden.

Workers are ephemeral: they share the committee's toolkits but use a throwaway
in-memory Memory and a no-op repository, so debate transcripts never pollute
the main agent's persistent memory.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from langchain.tools import tool

from app.domain.models.event import MessageEvent
from app.domain.models.memory import Memory
from app.domain.models.tool_result import ToolResult
from app.domain.services.agents.base import BaseAgent
from app.domain.services.tools.base import BaseToolkit
from app.domain.services.tools.market_data import fetch_ohlcv

logger = logging.getLogger(__name__)

_WORKER_TIMEOUT_S = 600
_MAX_REPORT_CHARS = 7000


# ── Role prompts (adapted from HKUDS/Vibe-Trading investment_committee) ──────

_DATA_DISCIPLINE = """## Data Citation Discipline (HARD RULE)
Every specific number you cite — prices, percentages, indicator values,
returns — MUST be traceable to (a) a tool call result obtained in THIS run, or
(b) the Ground Truth block in your prompt. You may NOT cite numbers from
memory or training data; markets have moved since your cutoff, so any price
you recall is wrong by default. If you cannot back a number: call a data tool
(preferred) or omit the number and qualify the statement. Levels you propose
(entry / stop / target) must sit inside the observed price range or be derived
with a visible formula from observed values."""

_BULL_PROMPT = """You are a senior bull-side researcher on a trading committee, dedicated to building the strongest HONEST bullish case for the target. Stay professional and objective; every point must be data-backed — no hand-waving.

{ground_truth}

## Objective
{query}

## Method
Use your market-data tools (live price, OHLCV candles, indicators such as
ATR / EMA / RSI divergence, sentiment, economic calendar) with parameters YOU
choose to fit the current market condition. Verify every bullish claim against
real data. Do not restate the bear case — another agent covers it — but you
MUST honestly list what would invalidate your thesis.

## Required output (respond in the same language as the Objective)
1. Bull thesis — 3-5 one-line strongest bull points, each tagged with confidence (high/medium) and the tool evidence behind it.
2. Key numbers — the exact values you rely on, each with its source (tool name + symbol + timeframe).
3. Invalidation — 2-3 concrete conditions that would break the bull thesis (specific observable levels/closes).
4. Suggested risk framing for a long position (stop distance logic from observed volatility).

{data_discipline}"""

_BEAR_PROMPT = """You are a senior bear-side researcher on a trading committee, dedicated to surfacing risk and building the strongest HONEST bearish case for the target. Stay independent — challenge consensus instead of getting swept up in it.

{ground_truth}

## Objective
{query}

## Method
Use your market-data tools with parameters YOU choose. Hunt for what the
optimists would miss: deteriorating momentum, exhaustion signals, overhead
supply, event risk, stretched positioning, weak volume. Verify every bearish
claim against real data. Do not restate the bull case — another agent covers
it — but you MUST honestly concede what would invalidate YOUR thesis.

## Required output (respond in the same language as the Objective)
1. Bear thesis — 3-5 one-line strongest bear points, each tagged with confidence (high/medium) and the tool evidence behind it.
2. Key numbers — the exact values you rely on, each with its source (tool name + symbol + timeframe).
3. What would prove you wrong — 2-3 concrete conditions that break the bear thesis.
4. Downside scenario — realistic bearish path with levels derived from observed data only.

{data_discipline}"""

_RISK_PROMPT = """You are the chief risk officer of a trading committee — independent of the research team. Your job is NOT to side with bull or bear but to make sure the committee fully understands and quantifies the material risks before any decision.

## Ground Truth (authoritative prices for this run)
{ground_truth}

## Objective
{query}

## Upstream Context (from previous agents)
### Bull report
{bull_report}

### Bear report
{bear_report}

## Your review
- Check the bull side for confirmation bias and cherry-picked evidence.
- Check the bear side for emotion-driven excessive pessimism.
- Identify blind-spot risks NEITHER side covered (liquidity, session timing, event calendar, correlation to correlated assets).
- Position sizing: convert observed volatility (ATR / range) into concrete stop-distance and risk-per-trade guidance. Every number must be computed from data in the reports or your own tool calls — never asserted.
- Reliability scorecard for each side (1-5) with one-line justification.

## Required output (respond in the same language as the Objective)
Verdict: SUPPORT / CONDITIONAL SUPPORT / OPPOSE (with conditions spelled out),
blind spots, sizing guidance (stop distance, risk budget), and the specific
risk language the final decision-maker must include.

{data_discipline}"""

_PM_PROMPT = """You are the portfolio manager chairing a trading committee. You weigh the bull research, the bear research and the risk officer's advice — then YOU own the final call. Independent judgment, not a naive average of three votes.

## Ground Truth (authoritative prices for this run)
{ground_truth}

## Objective
{query}

## Upstream Context (from previous agents)
### Bull report
{bull_report}

### Bear report
{bear_report}

### Risk officer report
{risk_report}

## Your decision
- Direction: BUY / SELL / WAIT (in the user's language), and the specific reasoning.
- Executable plan: entry (market or zone), stop loss sized to observed volatility, take profit level(s) — every level inside or derived from the observed data.
- Conviction: HIGH / MEDIUM / LOW with the honest reason.
- Invalidation: 1-2 specific observable conditions that cancel the plan.
- Decisions must be executable — reject vague "it depends". If the evidence genuinely does not support a trade, say WAIT and give the exact condition that would change your mind.

## Required output (respond in the same language as the Objective)
One structured decision a trader can act on immediately.

{data_discipline}"""


class _NullAgentRepository:
    """Duck-typed no-op repository — committee workers keep memory in RAM only."""

    async def get_memory(self, agent_id: str, name: str) -> Memory:
        return Memory()

    async def save_memory(self, agent_id: str, name: str, memory: Memory) -> None:
        return None


class CommitteeToolkit(BaseToolkit):
    """Convenes the bull/bear/risk/PM debate as a single blocking tool call."""

    name: str = "committee"

    def __init__(self, data_toolkits: List[BaseToolkit], agent_id: str = "committee", **kwargs: Any):
        super().__init__(**kwargs)
        self._data_toolkits = data_toolkits
        self._agent_id = agent_id

    # ── ground truth ─────────────────────────────────────────────────────
    @staticmethod
    async def _build_ground_truth(target: Optional[str]) -> str:
        if not target:
            return "(no target symbol — fetch whatever data your objective requires)"
        clean = target.replace("/", "").replace(" ", "").upper()
        try:
            df, source = await fetch_ohlcv(clean, "1h", 120)
        except Exception as exc:  # noqa: BLE001
            return (
                f"(ground-truth prefetch FAILED for {clean}: {str(exc)[:200]} — "
                "fetch the data yourself with your tools before citing any price)"
            )
        try:
            from app.domain.services.tools.technical import _atr

            atr_series = _atr(df, 14)
            atr_val = float(atr_series.iloc[-1])
        except Exception:  # noqa: BLE001
            atr_val = None
        closes = df["close"].tail(5)
        rows = "\n".join(
            f"| {ts.isoformat()} | {c:g} |"
            for ts, c in zip(df["open_time"].tail(5), closes)
        )
        last_close = float(df["close"].iloc[-1])
        hi = float(df["high"].max())
        lo = float(df["low"].min())
        atr_line = f"ATR(14) 1h: {atr_val:.4g}" if atr_val else "ATR(14) 1h: unavailable"
        return (
            f"Symbol: {clean} | source: {source} | candles: {len(df)} x 1h "
            f"({df['open_time'].iloc[0].isoformat()} → {df['open_time'].iloc[-1].isoformat()} UTC)\n"
            f"Latest close: {last_close:g} | Window high: {hi:g} | Window low: {lo:g} | {atr_line}\n"
            f"Last 5 closes:\n| time (UTC) | close |\n|---|---|\n{rows}\n"
            "These are the authoritative prices for this run. Do NOT cite prices "
            "from training data — anything not in this block must come from your own tool calls."
        )

    # ── worker runner ────────────────────────────────────────────────────
    def _make_worker(self, role_prompt: str) -> BaseAgent:
        worker = BaseAgent(
            agent_id=f"{self._agent_id}-committee-{uuid.uuid4().hex[:8]}",
            agent_repository=_NullAgentRepository(),  # type: ignore[arg-type]
            tools=self._data_toolkits,
        )
        worker.system_prompt = role_prompt
        worker.max_iterations = 12
        worker.memory = Memory()
        return worker

    async def _run_worker(self, role_prompt: str, user_prompt: str) -> Dict[str, Any]:
        worker = self._make_worker(role_prompt)
        text = ""
        try:
            async def _drive() -> str:
                out = ""
                async for ev in worker.execute(user_prompt):
                    if isinstance(ev, MessageEvent) and ev.message:
                        out = ev.message
                return out

            text = await asyncio.wait_for(_drive(), timeout=_WORKER_TIMEOUT_S)
            if not text.strip():
                return {"status": "failed", "error": "worker produced no report", "report": ""}
            return {"status": "completed", "report": text.strip()[:_MAX_REPORT_CHARS]}
        except asyncio.TimeoutError:
            return {"status": "failed", "error": f"worker timed out after {_WORKER_TIMEOUT_S}s", "report": ""}
        except Exception as exc:  # noqa: BLE001
            logger.exception("committee worker failed")
            return {"status": "failed", "error": str(exc)[:300], "report": ""}

    # ── the tool ─────────────────────────────────────────────────────────
    @tool(parse_docstring=True)
    async def run_committee(
        self,
        query: str,
        target: Optional[str] = None,
        market: Optional[str] = None,
    ) -> ToolResult:
        """Convene a multi-agent investment committee: bull vs bear debate → risk officer → final decision.

        Four specialist agents work on the user's question: a bull researcher
        and a bear researcher build opposing data-backed cases in parallel, a
        chief risk officer reviews both (bias check, blind spots, position
        sizing), and a portfolio manager makes ONE executable final decision.
        All workers receive the same freshly-fetched ground-truth prices and
        are forbidden from quoting numbers from memory. Use it for contested,
        high-stakes decisions on ONE instrument where a genuine debate adds
        value; it is the most expensive tool in the kit (several model rounds).

        Args:
            query: The full question or decision to debate, verbatim from the user (workers see nothing else of the conversation).
            target: Instrument symbol the debate is about, e.g. "BTCUSDT", "XAUUSD", "frxEURUSD". Prefetches ground-truth candles for it.
            market: Market label when known, e.g. "crypto", "gold", "forex". Optional.
        """
        gt = await self._build_ground_truth(target)

        async def _fmt(res: Dict[str, Any]) -> str:
            if res["status"] == "completed":
                return res["report"]
            return f"[{res.get('role', 'worker')} report unavailable — {res.get('error', 'failed')}]"

        # Layer 1 — bull & bear in parallel
        bull_res, bear_res = await asyncio.gather(
            self._run_worker(
                _BULL_PROMPT.format(ground_truth=gt, query=query, data_discipline=_DATA_DISCIPLINE),
                f"{query}\n\n(Ground truth is embedded in your system prompt. Market context: {market or 'auto'}).",
            ),
            self._run_worker(
                _BEAR_PROMPT.format(ground_truth=gt, query=query, data_discipline=_DATA_DISCIPLINE),
                f"{query}\n\n(Ground truth is embedded in your system prompt. Market context: {market or 'auto'}).",
            ),
        )
        bull_res["role"] = "bull_advocate"
        bear_res["role"] = "bear_advocate"

        # Layer 2 — risk officer
        bull_txt = await _fmt(bull_res)
        bear_txt = await _fmt(bear_res)
        risk_res = await self._run_worker(
            _RISK_PROMPT.format(ground_truth=gt, query=query, data_discipline=_DATA_DISCIPLINE,
                                bull_report=bull_txt, bear_report=bear_txt),
            query,
        )
        risk_res["role"] = "risk_officer"
        risk_txt = await _fmt(risk_res)

        # Layer 3 — portfolio manager
        pm_res = await self._run_worker(
            _PM_PROMPT.format(ground_truth=gt, query=query, data_discipline=_DATA_DISCIPLINE,
                              bull_report=bull_txt, bear_report=bear_txt, risk_report=risk_txt),
            query,
        )
        pm_res["role"] = "portfolio_manager"

        statuses = {r["role"]: r["status"] for r in (bull_res, bear_res, risk_res, pm_res)}
        if pm_res["status"] != "completed":
            return ToolResult(
                success=False,
                message=(
                    f"Committee did not reach a decision ({statuses}). "
                    f"Risk officer input: {risk_txt[:800]}"
                ),
            )

        return ToolResult(success=True, data={
            "preset": "investment_committee (bull vs bear → risk officer → PM decision)",
            "target": target or "(inferred from query)",
            "market": market or "auto",
            "ground_truth": gt.splitlines()[0] if gt else None,
            "worker_status": statuses,
            "bull_report": bull_txt,
            "bear_report": bear_txt,
            "risk_report": risk_txt,
            "final_decision": pm_res["report"],
        })
