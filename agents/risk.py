"""
agents/risk.py
Risk analysis agent.

Computes Beta (vs Nifty 50), Value-at-Risk (95% historical), 30-day
annualised volatility, and max drawdown from price history, then asks
the LLM to produce a risk verdict and reasoning.
"""

from __future__ import annotations
import logging
from typing import Optional

import numpy as np
import pandas as pd

from llm.router import call_llm_json
from llm.schemas import RiskSignal

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a portfolio risk manager covering Indian equity portfolios.
You will receive quantitative risk metrics for a stock position.

Assess the overall risk level as: Low | Medium | High | Very High
Explain the key risk drivers in 2-3 sentences.

Respond ONLY with valid JSON (no markdown):
{
  "level": "Low" | "Medium" | "High" | "Very High",
  "reasoning": "<2-3 sentences>"
}"""


def run_risk_agent(
    symbol: str,
    stock_returns: pd.Series,
    benchmark_returns: Optional[pd.Series] = None,
    portfolio_allocation_pct: float = 0.0,
) -> RiskSignal:
    """
    Run risk analysis.

    Args:
        symbol: NSE ticker
        stock_returns: Daily percentage returns series (float, e.g. 0.012 = 1.2%)
        benchmark_returns: Daily returns for Nifty 50. If None, beta = 1.0.
        portfolio_allocation_pct: This stock's current weight in the portfolio (0-100)

    Returns:
        RiskSignal
    """
    logger.info(f"[Risk] Analysing {symbol}")

    metrics = _compute_risk_metrics(stock_returns, benchmark_returns, portfolio_allocation_pct)
    llm_input = _build_llm_prompt(symbol, metrics)
    llm_result = call_llm_json(SYSTEM_PROMPT, llm_input, max_tokens=256)

    return _build_signal(symbol, metrics, llm_result)


# ---------------------------------------------------------------------------
# Risk computation
# ---------------------------------------------------------------------------

def _compute_risk_metrics(
    returns: pd.Series,
    benchmark: Optional[pd.Series],
    alloc_pct: float,
) -> dict:
    r = returns.dropna()

    if len(r) < 2:
        return {
            "beta": 1.0,
            "var_95": 2.0,
            "volatility_30d": 20.0,
            "max_drawdown_pct": 10.0,
            "portfolio_allocation_pct": alloc_pct,
        }

    # Beta vs benchmark
    if benchmark is not None and len(benchmark) > 10:
        b = benchmark.dropna()
        aligned = pd.concat([r, b], axis=1).dropna()
        if len(aligned) > 10:
            cov_matrix = np.cov(aligned.iloc[:, 0], aligned.iloc[:, 1])
            beta = float(cov_matrix[0, 1] / cov_matrix[1, 1])
        else:
            beta = 1.0
    else:
        beta = 1.0

    # Historical VaR at 95% confidence (as % loss)
    var_95 = float(abs(np.percentile(r * 100, 5)))   # 5th percentile of daily returns

    # 30-day annualised volatility
    recent = r.tail(30)
    vol_30d = float(recent.std() * np.sqrt(252) * 100)   # annualised %

    # Maximum drawdown from cumulative returns
    cumulative = (1 + r).cumprod()
    rolling_max = cumulative.cummax()
    drawdown = (cumulative - rolling_max) / rolling_max
    max_dd = float(abs(drawdown.min()) * 100)

    return {
        "beta": round(beta, 3),
        "var_95": round(var_95, 3),
        "volatility_30d": round(vol_30d, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "portfolio_allocation_pct": alloc_pct,
    }


def _build_llm_prompt(symbol: str, m: dict) -> str:
    return f"""Stock: {symbol}

RISK METRICS:
- Beta (vs Nifty 50):       {m['beta']:.2f}  (1.0 = market risk, >1.5 = high)
- VaR 95% (1-day):          {m['var_95']:.2f}%  (expected max daily loss 95% of the time)
- Volatility (30d ann.):    {m['volatility_30d']:.1f}%
- Max Drawdown (history):   {m['max_drawdown_pct']:.1f}%
- Portfolio allocation:     {m['portfolio_allocation_pct']:.1f}%

Assess the overall risk level and return the required JSON."""


def _build_signal(symbol: str, m: dict, llm: dict) -> RiskSignal:
    level = llm.get("level", "Medium")
    if level not in ("Low", "Medium", "High", "Very High"):
        level = _compute_risk_level(m)

    return RiskSignal(
        symbol=symbol,
        level=level,
        beta=m["beta"],
        var_95=m["var_95"],
        volatility_30d=m["volatility_30d"],
        portfolio_allocation_pct=m["portfolio_allocation_pct"],
        max_drawdown_pct=m["max_drawdown_pct"],
        reasoning=llm.get("reasoning", ""),
    )


def _compute_risk_level(m: dict) -> str:
    """Rule-based fallback if LLM doesn't return a valid level."""
    score = 0
    if m["beta"] > 1.5:
        score += 2
    elif m["beta"] > 1.2:
        score += 1
    if m["var_95"] > 3:
        score += 2
    elif m["var_95"] > 2:
        score += 1
    if m["volatility_30d"] > 40:
        score += 2
    elif m["volatility_30d"] > 25:
        score += 1
    if m["max_drawdown_pct"] > 40:
        score += 2
    elif m["max_drawdown_pct"] > 20:
        score += 1

    if score >= 6:
        return "Very High"
    elif score >= 3:
        return "High"
    elif score >= 1:
        return "Medium"
    return "Low"
