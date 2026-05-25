"""
db/models.py — Pydantic schemas for all MongoDB documents in Zeta.

These define the shape of every document stored in Atlas.
Person 2 (agents) imports these to validate output before writing.
Person 4 (dashboard) imports these to parse documents when reading.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from bson import ObjectId


# ── Sub-documents (nested inside DecisionDocument) ────────────────────────────

class MACDData(BaseModel):
    macd: Optional[float] = None
    signal: Optional[float] = None
    histogram: Optional[float] = None


class BollingerData(BaseModel):
    upper: Optional[float] = None
    middle: Optional[float] = None
    lower: Optional[float] = None


class KeyLevels(BaseModel):
    support: Optional[float] = None
    resistance: Optional[float] = None


class TechnicalSignal(BaseModel):
    signal: str                          # "Bullish" | "Bearish" | "Neutral"
    strength: float                      # 0–100
    rsi: Optional[float] = None
    macd: Optional[MACDData] = None
    bollinger: Optional[BollingerData] = None
    ema_21: Optional[float] = None
    ema_50: Optional[float] = None
    key_levels: Optional[KeyLevels] = None
    reasoning: str = ""


class FundamentalSignal(BaseModel):
    verdict: str                         # "Undervalued" | "Fairly Valued" | "Overvalued"
    quality_score: float                 # 0–100
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    roe: Optional[float] = None
    debt_to_equity: Optional[float] = None
    promoter_holding_pct: Optional[float] = None
    red_flags: list[str] = []


class NewsArticle(BaseModel):
    headline: str
    source: str
    score: float                         # -100 to +100
    reason: str = ""


class SentimentSignal(BaseModel):
    score: float                         # -100 to +100
    analyst_consensus: str = ""
    articles: list[NewsArticle] = []


class RiskSignal(BaseModel):
    level: str                           # "Low" | "Medium" | "High" | "Very High"
    beta: Optional[float] = None
    var_95: Optional[float] = None       # Value at Risk at 95% confidence
    portfolio_allocation_pct: Optional[float] = None


# ── Top-level document ─────────────────────────────────────────────────────────

class DecisionDocument(BaseModel):
    """
    One document per stock per analysis cycle.
    Written by Person 2's orchestrator; read by Person 4's dashboard.
    """
    timestamp: datetime
    cycle_id: str
    batch_id: Optional[str] = None       # same as cycle_id, used for batch controls
    symbol: str
    exchange: str = "NSE"
    analysis_price: Optional[float] = None  # price at time of analysis for validation

    technical: Optional[TechnicalSignal] = None
    fundamental: Optional[FundamentalSignal] = None
    sentiment: Optional[SentimentSignal] = None
    risk: Optional[RiskSignal] = None

    bull_argument: str = ""
    bear_argument: str = ""

    decision: str = "ABSTAIN"            # "HOLD"|"SELL"|"GTT_TARGET"|"GTT_STOP"|"ABSTAIN"
    confidence: float = 0.0             # 0–100
    gtt_price: Optional[float] = None
    mandate_id: Optional[str] = None
    approved: Optional[bool] = None     # None = pending, True = approved, False = rejected
    outcome_pct: Optional[float] = None # Back-filled when GTT triggers
    charges_inr: Optional[float] = None

    def to_mongo(self) -> dict:
        """Convert to a dict safe for MongoDB insertion."""
        return self.model_dump(exclude_none=False)


# ── GTT Tracker document ───────────────────────────────────────────────────────

class GTTDocument(BaseModel):
    """Tracks open and closed GTT orders."""
    symbol: str
    trigger_price: float
    limit_price: float
    gtt_type: str                        # "target" | "stop_loss"
    placed_time: datetime
    trigger_time: Optional[datetime] = None
    net_result_pct: Optional[float] = None
    status: str = "open"                 # "open" | "triggered" | "cancelled"


# ── Risk Budget document ───────────────────────────────────────────────────────

class RiskBudgetDocument(BaseModel):
    """Current period risk allocation settings."""
    period_start: datetime
    period_end: datetime
    low_risk_pct: float = 50.0
    medium_risk_pct: float = 30.0
    high_risk_pct: float = 20.0
    low_used: float = 0.0
    medium_used: float = 0.0
    high_used: float = 0.0
    decisions_queued: int = 0


# ── System State document ──────────────────────────────────────────────────────────────────────────────────

class SpecialDay(BaseModel):
    date: str                            # ISO date string YYYY-MM-DD
    is_holiday: bool = False
    is_special_open: bool = False
    market_open: Optional[str] = None   # "09:15" format
    market_close: Optional[str] = None


class SystemState(BaseModel):
    """
    Singleton document (upserted by key="singleton") tracking all runtime flags.
    """
    key: str = "singleton"
    interval_minutes: int = 30           # current cycle interval
    kill_day: bool = False               # stops today, resets tomorrow
    kill_system: bool = False            # stops indefinitely until /start_system
    current_batch_id: Optional[str] = None
    current_batch_time: Optional[str] = None  # HH:MM label
    special_days: list[SpecialDay] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def to_mongo(self) -> dict:
        return self.model_dump()


# ── Log Entry document ─────────────────────────────────────────────────────────────────────────────────────────

class LogEntry(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    level: str = "INFO"                  # DEBUG | INFO | WARNING | ERROR
    message: str
    source: str = ""                     # module name
    cycle_id: Optional[str] = None
    symbol: Optional[str] = None

    def to_mongo(self) -> dict:
        return self.model_dump()
