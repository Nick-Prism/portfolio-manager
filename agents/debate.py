"""
agents/debate.py
Bull vs Bear debate sub-agents.

Given the four agent signals, two LLM calls argue the bull and bear
case independently. The orchestrator then uses both arguments to make
the final decision.
"""

from __future__ import annotations
import logging

from llm.router import call_llm_json
from llm.schemas import (
    TechnicalSignal,
    FundamentalSignal,
    SentimentSignal,
    RiskSignal,
    DebateArgument,
)

logger = logging.getLogger(__name__)

BULL_SYSTEM = """You are an aggressive bull-side equity analyst covering Indian markets.
Your job is to make the strongest possible BUY case for this stock based on the provided signals.
Find every positive angle. Be specific. Use the actual numbers.

Respond ONLY with valid JSON (no markdown):
{
  "argument": "<2-3 sentences making the bull case>",
  "key_points": ["<point 1>", "<point 2>", "<point 3>"],
  "confidence": <0-100 how confident you are in the bull case>
}"""

BEAR_SYSTEM = """You are a cautious bear-side equity analyst covering Indian markets.
Your job is to make the strongest possible SELL/AVOID case for this stock based on the provided signals.
Find every risk, weakness, and negative factor. Be specific. Use the actual numbers.

Respond ONLY with valid JSON (no markdown):
{
  "argument": "<2-3 sentences making the bear case>",
  "key_points": ["<point 1>", "<point 2>", "<point 3>"],
  "confidence": <0-100 how confident you are in the bear case>
}"""


def run_debate(
    symbol: str,
    technical: TechnicalSignal,
    fundamental: FundamentalSignal,
    sentiment: SentimentSignal,
    risk: RiskSignal,
) -> tuple[DebateArgument, DebateArgument]:
    """
    Run bull and bear debate (two parallel LLM calls — called concurrently by graph.py).

    Returns:
        (bull_argument, bear_argument)
    """
    logger.info(f"[Debate] Running Bull/Bear debate for {symbol}")

    signal_summary = _build_signal_summary(symbol, technical, fundamental, sentiment, risk)

    bull_result = call_llm_json(BULL_SYSTEM, signal_summary, max_tokens=512)
    bear_result = call_llm_json(BEAR_SYSTEM, signal_summary, max_tokens=512)

    bull = DebateArgument(
        side="Bull",
        argument=bull_result.get("argument", "No bull argument generated."),
        key_points=bull_result.get("key_points", []),
        confidence=float(bull_result.get("confidence", 50)),
    )
    bear = DebateArgument(
        side="Bear",
        argument=bear_result.get("argument", "No bear argument generated."),
        key_points=bear_result.get("key_points", []),
        confidence=float(bear_result.get("confidence", 50)),
    )

    return bull, bear


def _build_signal_summary(
    symbol: str,
    tech: TechnicalSignal,
    fund: FundamentalSignal,
    sent: SentimentSignal,
    risk: RiskSignal,
) -> str:
    return f"""Stock: {symbol}

TECHNICAL SIGNAL: {tech.signal} (strength {tech.strength:.0f}/100)
  RSI: {tech.rsi:.1f} | MACD crossover: {tech.macd.crossover}
  Price vs EMA21/50: context in reasoning
  Reasoning: {tech.reasoning}

FUNDAMENTAL SIGNAL: {fund.verdict} (quality score {fund.quality_score:.0f}/100)
  PE: {fund.pe_ratio or 'N/A'} | ROE: {fund.roe or 'N/A'}% | D/E: {fund.debt_to_equity or 'N/A'}
  Red flags: {', '.join(fund.red_flags) if fund.red_flags else 'None'}
  Reasoning: {fund.reasoning}

SENTIMENT SIGNAL: {sent.label} (score {sent.score:.0f}/100)
  Analyst consensus: {sent.analyst_consensus}
  Reasoning: {sent.reasoning}

RISK SIGNAL: {risk.level}
  Beta: {risk.beta:.2f} | VaR 95%: {risk.var_95:.2f}% | Vol 30d: {risk.volatility_30d:.1f}%
  Portfolio allocation: {risk.portfolio_allocation_pct:.1f}%
  Reasoning: {risk.reasoning}

Make your case."""
