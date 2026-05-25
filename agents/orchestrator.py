"""
agents/orchestrator.py
Orchestrator agent.

Receives all four agent signals plus the Bull/Bear debate arguments and
produces the final trading decision: HOLD | SELL | GTT_TARGET | GTT_STOP | ABSTAIN
along with a confidence score and optional GTT price.
"""

from __future__ import annotations
import logging
from typing import Optional

from llm.router import call_llm_json
from llm.schemas import (
    TechnicalSignal,
    FundamentalSignal,
    SentimentSignal,
    RiskSignal,
    DebateArgument,
    OrchestratorDecision,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the head portfolio manager for a disciplined Indian retail investor on Zerodha.

You have received analysis from five specialist agents (technical, fundamental, sentiment, risk, arbitrage) and a Bull vs Bear debate.
Your job is to make the FINAL decision for this stock position.

DECISION OPTIONS:
- HOLD: Keep the position, no action needed
- SELL: Exit the position at market price now
- GTT_TARGET: Place a Good Till Triggered order at a target price above current price
- GTT_STOP: Place a GTT stop-loss order below current price
- ABSTAIN: Signals are too mixed or data is insufficient to act

RULES:
1. Only recommend SELL if conviction is high (confidence > 65)
2. GTT_TARGET: Only if technical shows bullish momentum AND fundamental is not overvalued
3. GTT_STOP: Only if risk level is High/Very High OR technical is strongly bearish
4. Be conservative — if in doubt, HOLD
5. GTT price must be a specific number (not null) if decision is GTT_TARGET or GTT_STOP
6. The execute_on field is determined automatically from the arbitrage signal — do NOT include it in your response

Respond ONLY with valid JSON (no markdown):
{
  "decision": "HOLD" | "SELL" | "GTT_TARGET" | "GTT_STOP" | "ABSTAIN",
  "confidence": <0-100>,
  "gtt_price": <float or null>,
  "reasoning": "<3-4 sentences explaining the decision>",
  "signal_summary": "<one sentence summary for the dashboard>"
}"""


def run_orchestrator(
    symbol: str,
    current_price: float,
    technical: TechnicalSignal,
    fundamental: FundamentalSignal,
    sentiment: SentimentSignal,
    risk: RiskSignal,
    bull: DebateArgument,
    bear: DebateArgument,
) -> OrchestratorDecision:
    """
    Run the orchestrator to produce the final decision.

    Args:
        symbol: NSE ticker
        current_price: Latest close price
        technical, fundamental, sentiment, risk: Agent outputs
        bull, bear: Debate outputs

    Returns:
        OrchestratorDecision
    """
    logger.info(f"[Orchestrator] Making final decision for {symbol}")

    llm_input = _build_prompt(
        symbol, current_price, technical, fundamental, sentiment, risk, bull, bear
    )
    llm_result = call_llm_json(SYSTEM_PROMPT, llm_input, max_tokens=768)

    return _build_decision(symbol, current_price, technical, llm_result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_prompt(
    symbol: str,
    price: float,
    tech: TechnicalSignal,
    fund: FundamentalSignal,
    sent: SentimentSignal,
    risk: RiskSignal,
    bull: DebateArgument,
    bear: DebateArgument,
) -> str:
    return f"""Stock: {symbol}  |  Current price: ₹{price:.2f}

═══ AGENT SIGNALS ═══

TECHNICAL: {tech.signal} (strength {tech.strength:.0f}/100)
  RSI {tech.rsi:.1f} | MACD {tech.macd.crossover} crossover
  EMA21 {tech.ema_21:.2f} | EMA50 {tech.ema_50:.2f}
  Support ₹{tech.key_levels.support:.2f} | Resistance ₹{tech.key_levels.resistance:.2f}
  {tech.reasoning}

FUNDAMENTAL: {fund.verdict} (quality {fund.quality_score:.0f}/100)
  PE {fund.pe_ratio or 'N/A'} | ROE {fund.roe or 'N/A'}% | D/E {fund.debt_to_equity or 'N/A'}
  Red flags: {', '.join(fund.red_flags) if fund.red_flags else 'None'}
  {fund.reasoning}

SENTIMENT: {sent.label} (score {sent.score:.0f})
  {sent.analyst_consensus}

RISK: {risk.level}  Beta {risk.beta:.2f}  VaR {risk.var_95:.2f}%  Alloc {risk.portfolio_allocation_pct:.1f}%

═══ DEBATE ═══

BULL CASE (confidence {bull.confidence:.0f}%):
{bull.argument}
Key points: {'; '.join(bull.key_points)}

BEAR CASE (confidence {bear.confidence:.0f}%):
{bear.argument}
Key points: {'; '.join(bear.key_points)}

Make your final decision."""


def _build_decision(
    symbol: str,
    current_price: float,
    tech: TechnicalSignal,
    llm: dict,
) -> OrchestratorDecision:
    decision = llm.get("decision", "ABSTAIN")
    if decision not in ("HOLD", "SELL", "GTT_TARGET", "GTT_STOP", "ABSTAIN"):
        decision = "ABSTAIN"

    gtt_price: Optional[float] = None
    raw_gtt = llm.get("gtt_price")
    if raw_gtt is not None:
        try:
            gtt_price = float(raw_gtt)
        except (TypeError, ValueError):
            gtt_price = None

    # Sanity check GTT prices
    if decision == "GTT_TARGET" and (gtt_price is None or gtt_price <= current_price):
        gtt_price = round(tech.key_levels.resistance, 2)

    if decision == "GTT_STOP" and (gtt_price is None or gtt_price >= current_price):
        gtt_price = round(tech.key_levels.support, 2)

    return OrchestratorDecision(
        symbol=symbol,
        decision=decision,
        confidence=float(llm.get("confidence", 0)),
        gtt_price=gtt_price,
        bull_argument=llm.get("bull_argument", ""),
        bear_argument=llm.get("bear_argument", ""),
        reasoning=llm.get("reasoning", ""),
        signal_summary=llm.get("signal_summary", f"{symbol}: {decision}"),
    )
