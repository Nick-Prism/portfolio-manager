"""
agents/graph.py
LangGraph directed async graph for Zeta's analysis pipeline.

Graph topology:
                    ┌─ technical_node  ─┐
                    ├─ fundamental_node ─┤
  fetch_node  ──►  ├─ sentiment_node  ──┼──► debate_node ──► orchestrator_node
                    └─ risk_node       ─┘

- fetch_node:        Resolves market data, news, fundamentals from provided fetcher
- technical/fundamental/sentiment/risk: run in parallel (fan-out)
- debate_node:       Bull/Bear sub-agents (sequential, needs all 4 signals)
- orchestrator_node: Final decision synthesis
"""

from __future__ import annotations
import asyncio
import logging
from typing import Annotated, Any, Optional
from typing_extensions import TypedDict

import pandas as pd
from langgraph.graph import StateGraph, END

from agents.technical import run_technical_agent
from agents.fundamental import run_fundamental_agent
from agents.sentiment import run_sentiment_agent
from agents.risk import run_risk_agent
from agents.debate import run_debate
from agents.orchestrator import run_orchestrator
from llm.schemas import (
    TechnicalSignal,
    FundamentalSignal,
    SentimentSignal,
    RiskSignal,
    DebateArgument,
    OrchestratorDecision,
    CycleResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    # Inputs (provided at graph invocation)
    symbol: str
    exchange: str
    current_price: float
    ohlcv: Any                          # pd.DataFrame
    returns: Any                        # pd.Series of daily returns
    benchmark_returns: Optional[Any]    # pd.Series — Nifty 50 daily returns
    portfolio_allocation_pct: float
    fundamentals: Optional[dict]        # pre-fetched from P3, or None
    articles: Optional[list[dict]]      # pre-fetched news from P3, or None

    # Outputs populated by each node
    technical: Optional[TechnicalSignal]
    fundamental: Optional[FundamentalSignal]
    sentiment: Optional[SentimentSignal]
    risk: Optional[RiskSignal]
    bull: Optional[DebateArgument]
    bear: Optional[DebateArgument]
    decision: Optional[OrchestratorDecision]
    error: Optional[str]


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------

def technical_node(state: AgentState) -> dict:
    try:
        result = run_technical_agent(
            symbol=state["symbol"],
            ohlcv=state["ohlcv"],
        )
        return {"technical": result}
    except Exception as e:
        logger.error(f"[{state['symbol']}] Technical node failed: {e}", exc_info=True)
        return {"error": f"technical: {e}"}


def fundamental_node(state: AgentState) -> dict:
    try:
        result = run_fundamental_agent(
            symbol=state["symbol"],
            fundamentals=state.get("fundamentals"),
        )
        return {"fundamental": result}
    except Exception as e:
        logger.error(f"[{state['symbol']}] Fundamental node failed: {e}", exc_info=True)
        return {"error": f"fundamental: {e}"}


def sentiment_node(state: AgentState) -> dict:
    try:
        result = run_sentiment_agent(
            symbol=state["symbol"],
            articles=state.get("articles"),
        )
        return {"sentiment": result}
    except Exception as e:
        logger.error(f"[{state['symbol']}] Sentiment node failed: {e}", exc_info=True)
        return {"error": f"sentiment: {e}"}


def risk_node(state: AgentState) -> dict:
    try:
        result = run_risk_agent(
            symbol=state["symbol"],
            stock_returns=state["returns"],
            benchmark_returns=state.get("benchmark_returns"),
            portfolio_allocation_pct=state.get("portfolio_allocation_pct", 0.0),
        )
        return {"risk": result}
    except Exception as e:
        logger.error(f"[{state['symbol']}] Risk node failed: {e}", exc_info=True)
        return {"error": f"risk: {e}"}


def debate_node(state: AgentState) -> dict:
    """Requires all four agent outputs to be populated."""
    if not all([state.get("technical"), state.get("fundamental"),
                state.get("sentiment"), state.get("risk")]):
        return {"error": "debate: missing upstream agent results"}
    try:
        bull, bear = run_debate(
            symbol=state["symbol"],
            technical=state["technical"],
            fundamental=state["fundamental"],
            sentiment=state["sentiment"],
            risk=state["risk"],
        )
        return {"bull": bull, "bear": bear}
    except Exception as e:
        logger.error(f"[{state['symbol']}] Debate node failed: {e}", exc_info=True)
        return {"error": f"debate: {e}"}


def orchestrator_node(state: AgentState) -> dict:
    if not all([state.get("bull"), state.get("bear")]):
        return {"error": "orchestrator: missing debate results"}
    try:
        decision = run_orchestrator(
            symbol=state["symbol"],
            current_price=state["current_price"],
            technical=state["technical"],
            fundamental=state["fundamental"],
            sentiment=state["sentiment"],
            risk=state["risk"],
            bull=state["bull"],
            bear=state["bear"],
        )
        return {"decision": decision}
    except Exception as e:
        logger.error(f"[{state['symbol']}] Orchestrator node failed: {e}", exc_info=True)
        return {"error": f"orchestrator: {e}"}


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    """Build and compile the LangGraph analysis pipeline."""
    workflow = StateGraph(AgentState)

    # Register nodes
    workflow.add_node("technical", technical_node)
    workflow.add_node("fundamental", fundamental_node)
    workflow.add_node("sentiment", sentiment_node)
    workflow.add_node("risk", risk_node)
    workflow.add_node("debate", debate_node)
    workflow.add_node("orchestrator", orchestrator_node)

    # Fan-out: start → all four agents in parallel
    workflow.set_entry_point("technical")
    workflow.add_edge("__start__", "technical")
    workflow.add_edge("__start__", "fundamental")
    workflow.add_edge("__start__", "sentiment")
    workflow.add_edge("__start__", "risk")

    # Fan-in: all four → debate
    workflow.add_edge("technical", "debate")
    workflow.add_edge("fundamental", "debate")
    workflow.add_edge("sentiment", "debate")
    workflow.add_edge("risk", "debate")

    # debate → orchestrator → end
    workflow.add_edge("debate", "orchestrator")
    workflow.add_edge("orchestrator", END)

    return workflow.compile()


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

# Compiled graph singleton (built once on import)
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def run_analysis_cycle(
    symbol: str,
    ohlcv: pd.DataFrame,
    returns: pd.Series,
    benchmark_returns: Optional[pd.Series] = None,
    portfolio_allocation_pct: float = 0.0,
    fundamentals: Optional[dict] = None,
    articles: Optional[list[dict]] = None,
    exchange: str = "NSE",
) -> CycleResult:
    """
    Run the full analysis pipeline for a single stock.

    This is the main entry point called by main.py's scheduler loop.

    Args:
        symbol: NSE ticker
        ohlcv: OHLCV DataFrame (from P3's data/fetchers.py or yfinance directly)
        returns: Daily return series
        benchmark_returns: Nifty 50 returns for beta computation
        portfolio_allocation_pct: Weight of this stock in portfolio (0-100)
        fundamentals: Pre-scraped fundamental ratios dict from P3
        articles: List of news article dicts from P3

    Returns:
        CycleResult ready to be written to MongoDB by P3's db layer
    """
    current_price = float(ohlcv["close"].iloc[-1])

    initial_state: AgentState = {
        "symbol": symbol,
        "exchange": exchange,
        "current_price": current_price,
        "ohlcv": ohlcv,
        "returns": returns,
        "benchmark_returns": benchmark_returns,
        "portfolio_allocation_pct": portfolio_allocation_pct,
        "fundamentals": fundamentals,
        "articles": articles,
        "technical": None,
        "fundamental": None,
        "sentiment": None,
        "risk": None,
        "bull": None,
        "bear": None,
        "decision": None,
        "error": None,
    }

    graph = get_graph()
    final_state = await graph.ainvoke(initial_state)

    if final_state.get("error"):
        logger.error(f"[{symbol}] Graph completed with error: {final_state['error']}")

    decision = final_state.get("decision")
    if decision is None:
        # Fallback if orchestrator failed
        from llm.schemas import OrchestratorDecision
        decision = OrchestratorDecision(
            symbol=symbol,
            decision="ABSTAIN",
            confidence=0,
            gtt_price=None,
            bull_argument="Pipeline error — see logs.",
            bear_argument="Pipeline error — see logs.",
            reasoning="Analysis pipeline encountered an error.",
            signal_summary=f"{symbol}: ABSTAIN (error)",
        )

    return CycleResult(
        symbol=symbol,
        exchange=exchange,
        technical=final_state["technical"],
        fundamental=final_state["fundamental"],
        sentiment=final_state["sentiment"],
        risk=final_state["risk"],
        bull_argument=final_state["bull"].argument if final_state.get("bull") else "",
        bear_argument=final_state["bear"].argument if final_state.get("bear") else "",
        decision=decision.decision,
        confidence=decision.confidence,
        gtt_price=decision.gtt_price,
        reasoning=decision.reasoning,
    )


def run_analysis_cycle_sync(
    symbol: str,
    ohlcv: pd.DataFrame,
    returns: pd.Series,
    **kwargs,
) -> CycleResult:
    """Synchronous wrapper for run_analysis_cycle (for testing / non-async contexts)."""
    return asyncio.run(run_analysis_cycle(symbol, ohlcv, returns, **kwargs))
