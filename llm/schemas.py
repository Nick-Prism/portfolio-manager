"""
llm/schemas.py
Pydantic v2 typed output schemas for every LLM call and agent result.
All agent nodes return one of these typed objects.
"""

from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Technical agent
# ---------------------------------------------------------------------------

class MACDResult(BaseModel):
    macd_line: float
    signal_line: float
    histogram: float
    crossover: Literal["bullish", "bearish", "none"]


class BollingerResult(BaseModel):
    upper: float
    middle: float
    lower: float
    bandwidth: float
    percent_b: float  # position within bands 0-1


class KeyLevels(BaseModel):
    support: float
    resistance: float


class TechnicalSignal(BaseModel):
    symbol: str
    signal: Literal["Bullish", "Bearish", "Neutral"]
    strength: float = Field(ge=0, le=100)
    rsi: float = Field(ge=0, le=100)
    macd: MACDResult
    bollinger: BollingerResult
    ema_21: float
    ema_50: float
    atr: float
    vwap: float
    key_levels: KeyLevels
    reasoning: str


# ---------------------------------------------------------------------------
# Fundamental agent
# ---------------------------------------------------------------------------

class FundamentalSignal(BaseModel):
    symbol: str
    verdict: Literal["Undervalued", "Fairly Valued", "Overvalued", "Insufficient Data"]
    quality_score: float = Field(ge=0, le=100)
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    roe: Optional[float] = None
    debt_to_equity: Optional[float] = None
    promoter_holding_pct: Optional[float] = None
    sales_growth_pct: Optional[float] = None
    profit_growth_pct: Optional[float] = None
    red_flags: list[str] = Field(default_factory=list)
    reasoning: str


# ---------------------------------------------------------------------------
# Sentiment agent
# ---------------------------------------------------------------------------

class ArticleScore(BaseModel):
    headline: str
    source: str
    score: float = Field(ge=-100, le=100)
    reason: str


class SentimentSignal(BaseModel):
    symbol: str
    score: float = Field(ge=-100, le=100)   # -100 very bearish, +100 very bullish
    label: Literal["Very Bearish", "Bearish", "Neutral", "Bullish", "Very Bullish"]
    analyst_consensus: str
    articles: list[ArticleScore] = Field(default_factory=list)
    reasoning: str


# ---------------------------------------------------------------------------
# Risk agent
# ---------------------------------------------------------------------------

class RiskSignal(BaseModel):
    symbol: str
    level: Literal["Low", "Medium", "High", "Very High"]
    beta: float
    var_95: float          # Value at Risk at 95% confidence (as % of position)
    volatility_30d: float  # 30-day annualised volatility
    portfolio_allocation_pct: float
    max_drawdown_pct: float
    reasoning: str


# ---------------------------------------------------------------------------
# Arbitrage / exchange routing agent
# ---------------------------------------------------------------------------

class ArbitrageSignal(BaseModel):
    symbol: str
    nse_price: float
    bse_price: float
    spread_pct: float          # absolute % difference between exchanges
    spread_abs: float          # rupee difference
    cheaper_exchange: Literal["NSE", "BSE"]
    recommended_buy_exchange: Literal["NSE", "BSE"]
    recommended_sell_exchange: Literal["NSE", "BSE"]
    viable: bool               # True only if spread > total transaction costs
    reasoning: str


# ---------------------------------------------------------------------------
# Debate sub-agents
# ---------------------------------------------------------------------------

class DebateArgument(BaseModel):
    side: Literal["Bull", "Bear"]
    argument: str
    key_points: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=100)


# ---------------------------------------------------------------------------
# Final orchestrator decision
# ---------------------------------------------------------------------------

class OrchestratorDecision(BaseModel):
    symbol: str
    decision: Literal["HOLD", "SELL", "GTT_TARGET", "GTT_STOP", "ABSTAIN"]
    confidence: float = Field(ge=0, le=100)
    gtt_price: Optional[float] = None
    execute_on: Literal["NSE", "BSE"] = "NSE"   # which exchange to route the order to
    bull_argument: str
    bear_argument: str
    reasoning: str
    signal_summary: str   # one-line human-readable summary


# ---------------------------------------------------------------------------
# Full cycle result (what gets written to MongoDB by P3's db layer)
# ---------------------------------------------------------------------------

class CycleResult(BaseModel):
    symbol: str
    exchange: str = "NSE"
    technical: TechnicalSignal
    fundamental: FundamentalSignal
    sentiment: SentimentSignal
    risk: RiskSignal
    arbitrage: Optional[ArbitrageSignal] = None
    bull_argument: str
    bear_argument: str
    decision: Literal["HOLD", "SELL", "GTT_TARGET", "GTT_STOP", "ABSTAIN"]
    confidence: float = Field(ge=0, le=100)
    gtt_price: Optional[float] = None
    execute_on: Literal["NSE", "BSE"] = "NSE"
    reasoning: str
