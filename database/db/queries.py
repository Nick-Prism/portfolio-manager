"""
db/queries.py — All MongoDB queries used by the Zeta dashboard.

Person 4 imports these functions directly — no raw MongoDB in dashboard code.

Usage:
    from db.queries import get_last_decisions, get_decisions_by_symbol
"""

from datetime import datetime
from typing import Optional
from database.db.client import decisions_col, gtt_col, risk_budget_col


# ── Decision queries ───────────────────────────────────────────────────────────

async def get_last_decisions(limit: int = 20) -> list[dict]:
    """
    Last N decisions sorted newest first.
    Used by: Decision Log table on dashboard.
    """
    cursor = decisions_col.find({}).sort("timestamp", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def get_decisions_by_symbol(symbol: str, limit: int = 50) -> list[dict]:
    """
    All decisions for a specific stock, newest first.
    Used by: Per-stock signal history chart.
    """
    cursor = decisions_col.find({"symbol": symbol.upper()}).sort("timestamp", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def get_decisions_filtered(
    symbol: Optional[str] = None,
    decision_type: Optional[str] = None,
    min_confidence: Optional[float] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    limit: int = 100
) -> list[dict]:
    """
    Filtered decision search for the Decision Log browser.
    Used by: Dashboard filter controls (symbol, date range, decision type, confidence).
    """
    query: dict = {}

    if symbol:
        query["symbol"] = symbol.upper()
    if decision_type:
        query["decision"] = decision_type.upper()
    if min_confidence is not None:
        query["confidence"] = {"$gte": min_confidence}
    if from_date or to_date:
        query["timestamp"] = {}
        if from_date:
            query["timestamp"]["$gte"] = from_date
        if to_date:
            query["timestamp"]["$lte"] = to_date

    cursor = decisions_col.find(query).sort("timestamp", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def get_decision_distribution() -> list[dict]:
    """
    Count of each decision type (HOLD, SELL, GTT_TARGET etc.).
    Used by: Decision distribution bar chart on dashboard.
    """
    pipeline = [
        {"$match": {"approved": True}},
        {"$group": {"_id": "$decision", "count": {"$sum": 1}}}
    ]
    cursor = decisions_col.aggregate(pipeline)
    return await cursor.to_list(length=100)


async def get_signal_accuracy() -> list[dict]:
    """
    Closed decisions where outcome is known — used to calculate accuracy.
    Used by: Signal accuracy tracker on dashboard.
    """
    cursor = decisions_col.find({
        "approved": True,
        "outcome_pct": {"$ne": None}
    }).sort("timestamp", -1)
    return await cursor.to_list(length=500)


# ── GTT queries ────────────────────────────────────────────────────────────────

async def get_open_gtts() -> list[dict]:
    """
    GTT orders that are approved but not yet triggered.
    Used by: Portfolio page open GTT table.
    """
    cursor = decisions_col.find({
        "decision": {"$in": ["GTT_TARGET", "GTT_STOP"]},
        "approved": None   # None = pending approval
    }).sort("timestamp", -1)
    return await cursor.to_list(length=100)


async def get_gtt_history() -> list[dict]:
    """
    All GTT orders from the gtt_tracker collection.
    Used by: GTT history table.
    """
    cursor = gtt_col.find({}).sort("placed_time", -1)
    return await cursor.to_list(length=200)


# ── Risk budget queries ────────────────────────────────────────────────────────

async def get_current_risk_budget() -> Optional[dict]:
    """
    The most recent risk budget document.
    Used by: Risk Budget gauge on dashboard.
    """
    return await risk_budget_col.find_one({}, sort=[("period_start", -1)])


async def get_risk_budget_history() -> list[dict]:
    """
    All past risk budget periods.
    Used by: Risk budget history table.
    """
    cursor = risk_budget_col.find({}).sort("period_start", -1)
    return await cursor.to_list(length=50)