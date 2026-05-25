"""
main.py
Zeta AI Portfolio Manager — Agent Engine Entry Point

Usage:
  python main.py                  # Run continuous loop (interval driven by Telegram bot)
  python main.py --run-once       # Run one full cycle then exit (demo / testing)
  python main.py --symbol HDFCBANK --run-once   # Single stock

Environment variables (loaded from .env):
  GEMINI_API_KEY, GROQ_API_KEY, ANTHROPIC_API_KEY  — at least one required
  MONGODB_URI        — if absent, results are printed to stdout
  ZERODHA_API_KEY    — if absent, mock portfolio is used
  TELEGRAM_BOT_TOKEN / TOKEN — Telegram bot
  CHAT_ID            — Telegram chat to push decisions to

Scheduling:
  The cycle interval is set by the user each morning via Telegram.
  main.py polls MongoDB system_state for the current interval and kill flags,
  scheduling cycles dynamically. APScheduler is NOT used for fixed intervals here;
  instead a self-rescheduling async loop respects the user's chosen interval.
"""

from __future__ import annotations
import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

# Load .env before any other module reads environment variables
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass

import pandas as pd
import yfinance as yf

from agents.graph import run_analysis_cycle
from llm.schemas import CycleResult

# P3 database layer
try:
    from database.db.client import get_db
    _db          = get_db()
    _dec_col     = _db["decisions"]    if _db is not None else None
    _state_col   = _db["system_state"] if _db is not None else None
    _logs_col    = _db["logs"]         if _db is not None else None
    _DB_AVAILABLE = _dec_col is not None
except Exception:
    _db = _dec_col = _state_col = _logs_col = None
    _DB_AVAILABLE = False

# Legacy db layer (P3)
try:
    from database.db.models import (
        DecisionDocument, TechnicalSignal as DBTechnicalSignal,
        FundamentalSignal as DBFundamentalSignal, SentimentSignal as DBSentimentSignal,
        RiskSignal as DBRiskSignal, NewsArticle as DBNewsArticle,
        MACDData, BollingerData, KeyLevels as DBKeyLevels,
    )
    _MODELS_AVAILABLE = True
except Exception:
    _MODELS_AVAILABLE = False

# P3 data fetchers
try:
    from database.data.fetchers import get_fundamentals, get_price_data
    from database.data.news import get_news_for_symbol
    _FETCHERS_AVAILABLE = True
except Exception:
    _FETCHERS_AVAILABLE = False

# P1 Zerodha MCP
try:
    from mcp.tools import get_holdings as zerodha_get_holdings
    _MCP_AVAILABLE = True
except Exception:
    _MCP_AVAILABLE = False

# Telegram bot
try:
    # Skip Telegram bot in agent-engine container (separate telegram-bot container handles it)
    if os.getenv("SKIP_TELEGRAM_BOT", "false").lower() == "true":
        raise ImportError("Skipping Telegram bot in agent-engine container")
    from bot.telegram_bot import build_app, push_decisions, _load_state as tg_load_state
    _tg_app       = build_app()
    _TG_AVAILABLE = True
except Exception:
    _tg_app       = None
    _TG_AVAILABLE = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("zeta.main")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MARKET_OPEN  = time(9, 15)
MARKET_CLOSE = time(15, 30)
IST          = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Mock portfolio
# ---------------------------------------------------------------------------

MOCK_PORTFOLIO = [
    {"symbol": "RELIANCE",  "exchange": "NSE", "qty": 10,  "avg_price": 2850.0},
    {"symbol": "TCS",       "exchange": "NSE", "qty": 5,   "avg_price": 3920.0},
    {"symbol": "INFY",      "exchange": "NSE", "qty": 8,   "avg_price": 1640.0},
    {"symbol": "HDFCBANK",  "exchange": "NSE", "qty": 15,  "avg_price": 1580.0},
    {"symbol": "ITC",       "exchange": "NSE", "qty": 50,  "avg_price": 410.0},
]

# ---------------------------------------------------------------------------
# MongoDB helpers
# ---------------------------------------------------------------------------

async def _db_log(level: str, message: str, source: str = "main",
                  cycle_id: Optional[str] = None, symbol: Optional[str] = None) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc),
        "level":     level,
        "message":   message,
        "source":    source,
    }
    if cycle_id:
        entry["cycle_id"] = cycle_id
    if symbol:
        entry["symbol"] = symbol
    if _DB_AVAILABLE and _logs_col:
        try:
            await _logs_col.insert_one(entry)
        except Exception:
            pass


async def _read_system_state() -> dict:
    """Read system_state from MongoDB. Returns {} if unavailable."""
    if not (_DB_AVAILABLE and _state_col):
        return {}
    try:
        doc = await _state_col.find_one({"_id": "zeta_state"})
        return doc or {}
    except Exception:
        return {}


async def _sync_token_from_db() -> None:
    """Pull Zerodha token from MongoDB into os.environ (set by bot after /refresh_token)."""
    state = await _read_system_state()
    token = state.get("zerodha_access_token")
    if token and token != os.environ.get("ZERODHA_ACCESS_TOKEN"):
        os.environ["ZERODHA_ACCESS_TOKEN"] = token
        logger.info("Zerodha token synced from MongoDB")


async def _consume_reanalyse_queue() -> list[str]:
    """
    Pop all symbols from the reanalyse_queue (set by Track & Optimize).
    Returns list of symbols to re-analyse immediately.
    """
    if not (_DB_AVAILABLE and _state_col):
        return []
    try:
        doc = await _state_col.find_one_and_update(
            {"_id": "zeta_state"},
            {"$set": {"reanalyse_queue": []}},
        )
        return (doc or {}).get("reanalyse_queue", [])
    except Exception:
        return []


async def _set_cycle_lock(locked: bool) -> None:
    if not (_DB_AVAILABLE and _state_col):
        return
    try:
        await _state_col.update_one(
            {"_id": "zeta_state"},
            {"$set": {"cycle_running": locked}},
            upsert=True,
        )
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def fetch_ohlcv(symbol: str, exchange: str = "NSE", period: str = "1y") -> pd.DataFrame:
    if exchange == "NSE" and _FETCHERS_AVAILABLE:
        try:
            df = get_price_data(symbol, period=period)
            if df is not None and not df.empty:
                df.columns = [c.lower() for c in df.columns]
                cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
                return df[cols].dropna()
        except Exception as e:
            logger.warning(f"NSE fetcher failed for {symbol}: {e} — falling back to yfinance")

    suffix     = ".NS" if exchange == "NSE" else ".BO"
    ticker_str = f"{symbol}{suffix}"
    try:
        ticker = yf.Ticker(ticker_str)
        df     = ticker.history(period=period, auto_adjust=True)
        if df.empty:
            raise ValueError(f"No data returned for {ticker_str}")
        df.columns = [c.lower() for c in df.columns]
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        return df[cols].dropna()
    except Exception as e:
        logger.error(f"Failed to fetch OHLCV for {symbol}: {e}")
        raise


def fetch_benchmark(period: str = "1y") -> pd.Series:
    if _FETCHERS_AVAILABLE:
        try:
            df = get_price_data("NIFTY", period=period)
            if df is not None and not df.empty:
                col = "Close" if "Close" in df.columns else "close"
                return df[col].pct_change().dropna()
        except Exception:
            pass
    try:
        nifty = yf.Ticker("^NSEI").history(period=period, auto_adjust=True)
        return nifty["Close"].pct_change().dropna()
    except Exception as e:
        logger.warning(f"Could not fetch Nifty benchmark: {e}")
        return pd.Series(dtype=float)


def fetch_live_price(symbol: str, exchange: str = "NSE") -> Optional[float]:
    """Best-effort live price for a symbol."""
    try:
        df = fetch_ohlcv(symbol, exchange, period="1d")
        if not df.empty:
            return float(df["close"].iloc[-1])
    except Exception:
        pass
    return None


def compute_portfolio_allocation(holdings: list[dict],
                                  prices: dict[str, float]) -> dict[str, float]:
    values = {}
    for h in holdings:
        sym = h["symbol"]
        prices_val = prices.get(sym, h["avg_price"])
        values[sym] = prices_val * h["qty"]
    total = sum(values.values())
    if total == 0:
        return {h["symbol"]: 0.0 for h in holdings}
    return {sym: (v / total) * 100 for sym, v in values.items()}

# ---------------------------------------------------------------------------
# Result output
# ---------------------------------------------------------------------------

def _print_result(result: CycleResult) -> None:
    import textwrap
    W   = 72
    sep = "─" * W
    ind = "    "

    def wrap(text: str) -> str:
        return textwrap.fill(text, width=W - 4, initial_indent=ind,
                             subsequent_indent=ind)

    print(f"\n{sep}")
    header = f"  {result.symbol}  |  {result.decision}  |  confidence {result.confidence:.0f}%"
    if result.gtt_price:
        header += f"  |  GTT ₹{result.gtt_price:.2f}"
    print(header)
    print(sep)
    print(f"  Technical   : {result.technical.signal} (strength {result.technical.strength:.0f}/100)")
    print(f"  Fundamental : {result.fundamental.verdict} (quality {result.fundamental.quality_score:.0f}/100)")
    if result.fundamental.red_flags:
        print(f"  Red flags   : {', '.join(result.fundamental.red_flags)}")
    print(f"  Sentiment   : {result.sentiment.label} (score {result.sentiment.score:.0f})")
    print(f"  Risk        : {result.risk.level}  beta={result.risk.beta:.2f}  "
          f"VaR={result.risk.var_95:.1f}%  vol={result.risk.volatility_30d:.0f}%")
    print(f"  Allocation  : {result.risk.portfolio_allocation_pct:.1f}% of portfolio")
    print(f"\n  BULL CASE:")
    print(wrap(result.bull_argument))
    print(f"\n  BEAR CASE:")
    print(wrap(result.bear_argument))
    print(f"\n  DECISION REASONING:")
    print(wrap(result.reasoning))
    print(f"{sep}\n")


async def _write_decision_to_mongo(result: CycleResult, batch_id: str,
                                    analysis_price: Optional[float] = None) -> None:
    if not (_DB_AVAILABLE and _MODELS_AVAILABLE):
        return
    tech = result.technical
    fund = result.fundamental
    sent = result.sentiment
    risk = result.risk

    doc = DecisionDocument(
        timestamp=datetime.now(timezone.utc),
        cycle_id=batch_id,
        batch_id=batch_id,
        symbol=result.symbol,
        exchange=result.exchange,
        analysis_price=analysis_price,
        technical=DBTechnicalSignal(
            signal=tech.signal,
            strength=tech.strength,
            rsi=tech.rsi,
            macd=MACDData(
                macd=tech.macd.macd_line,
                signal=tech.macd.signal_line,
                histogram=tech.macd.histogram,
            ),
            bollinger=BollingerData(
                upper=tech.bollinger.upper,
                middle=tech.bollinger.middle,
                lower=tech.bollinger.lower,
            ),
            ema_21=tech.ema_21,
            ema_50=tech.ema_50,
            key_levels=DBKeyLevels(
                support=tech.key_levels.support,
                resistance=tech.key_levels.resistance,
            ),
            reasoning=tech.reasoning,
        ) if tech else None,
        fundamental=DBFundamentalSignal(
            verdict=fund.verdict,
            quality_score=fund.quality_score,
            pe_ratio=fund.pe_ratio,
            pb_ratio=fund.pb_ratio,
            roe=fund.roe,
            debt_to_equity=fund.debt_to_equity,
            promoter_holding_pct=fund.promoter_holding_pct,
            red_flags=fund.red_flags,
        ) if fund else None,
        sentiment=DBSentimentSignal(
            score=sent.score,
            analyst_consensus=sent.analyst_consensus,
            articles=[
                DBNewsArticle(
                    headline=a.headline,
                    source=a.source,
                    score=a.score,
                    reason=a.reason,
                )
                for a in (sent.articles or [])
            ],
        ) if sent else None,
        risk=DBRiskSignal(
            level=risk.level,
            beta=risk.beta,
            var_95=risk.var_95,
            portfolio_allocation_pct=risk.portfolio_allocation_pct,
        ) if risk else None,
        bull_argument=result.bull_argument,
        bear_argument=result.bear_argument,
        decision=result.decision,
        confidence=result.confidence,
        gtt_price=result.gtt_price,
    )
    await _dec_col.insert_one(doc.to_mongo())
    logger.info(f"[{result.symbol}] Written to MongoDB (batch {batch_id[:8]})")

# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------

def _get_holdings() -> list[dict]:
    zerodha_key = os.getenv("ZERODHA_API_KEY")
    if zerodha_key and _MCP_AVAILABLE:
        try:
            raw = zerodha_get_holdings()
            if raw:
                holdings = [
                    {
                        "symbol":    h["tradingsymbol"].replace("-BE", "").replace("-BL", ""),
                        "exchange":  h.get("exchange", "NSE"),
                        "qty":       h["quantity"],
                        "avg_price": h.get("average_price", 0.0),
                    }
                    for h in raw
                    if h.get("quantity", 0) > 0
                ]
                logger.info(f"Fetched {len(holdings)} holdings from Zerodha")
                return holdings
        except Exception as e:
            logger.warning(f"Zerodha get_holdings failed: {e} — falling back to mock")
    logger.info("Using mock portfolio")
    return MOCK_PORTFOLIO

# ---------------------------------------------------------------------------
# Single-stock analysis
# ---------------------------------------------------------------------------

async def analyse_holding(
    holding: dict,
    benchmark_returns: pd.Series,
    allocations: dict[str, float],
) -> Optional[CycleResult]:
    symbol   = holding["symbol"]
    exchange = holding.get("exchange", "NSE")

    try:
        ohlcv   = fetch_ohlcv(symbol, exchange)
        returns = ohlcv["close"].pct_change().dropna()

        fundamentals = None
        articles     = None
        if _FETCHERS_AVAILABLE:
            try:
                fundamentals = await get_fundamentals(symbol)
            except Exception as e:
                logger.warning(f"[{symbol}] Fundamentals fetch failed: {e}")
            try:
                articles = await get_news_for_symbol(symbol, limit=10)
            except Exception as e:
                logger.warning(f"[{symbol}] News fetch failed: {e}")

        result = await run_analysis_cycle(
            symbol=symbol,
            ohlcv=ohlcv,
            returns=returns,
            benchmark_returns=benchmark_returns if not benchmark_returns.empty else None,
            portfolio_allocation_pct=allocations.get(symbol, 0.0),
            fundamentals=fundamentals,
            articles=articles,
            exchange=exchange,
        )
        _print_result(result)
        return result

    except Exception as e:
        logger.error(f"Failed to analyse {symbol}: {e}", exc_info=True)
        return None

# ---------------------------------------------------------------------------
# Full cycle
# ---------------------------------------------------------------------------

async def run_full_cycle(
    holdings: Optional[list[dict]] = None,
    batch_id: Optional[str] = None,
    reanalyse_symbols: Optional[list[str]] = None,
) -> list[CycleResult]:
    """
    Run one complete analysis cycle across all holdings (or a subset for re-analysis).
    batch_id is shared across all decisions in this cycle for Telegram batch controls.
    """
    if holdings is None:
        holdings = _get_holdings()

    if reanalyse_symbols:
        # Narrow holdings to only the symbols that need re-analysis
        holdings = [h for h in holdings if h["symbol"] in reanalyse_symbols]

    if not holdings:
        logger.info("No holdings to analyse this cycle.")
        return []

    if batch_id is None:
        batch_id = str(uuid.uuid4())

    await _sync_token_from_db()
    await _set_cycle_lock(True)
    logger.info(f"Starting analysis cycle {batch_id[:8]} — {len(holdings)} holding(s)")
    await _db_log("info", f"Cycle started — {len(holdings)} holdings", cycle_id=batch_id)

    benchmark = fetch_benchmark()

    prices = {}
    for h in holdings:
        try:
            df = fetch_ohlcv(h["symbol"], h.get("exchange", "NSE"), period="5d")
            prices[h["symbol"]] = float(df["close"].iloc[-1])
        except Exception:
            prices[h["symbol"]] = h["avg_price"]

    allocations = compute_portfolio_allocation(holdings, prices)

    tasks   = [analyse_holding(h, benchmark, allocations) for h in holdings]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    completed = [r for r in results if r is not None]

    logger.info(f"Cycle {batch_id[:8]} complete — {len(completed)}/{len(holdings)} analysed")

    # Write to MongoDB and build Telegram payload
    tg_decisions: list[dict] = []
    for result in completed:
        analysis_price = prices.get(result.symbol)
        if _DB_AVAILABLE:
            try:
                await _write_decision_to_mongo(result, batch_id, analysis_price)
            except Exception as e:
                logger.error(f"MongoDB write failed for {result.symbol}: {e}")

        arb = getattr(result, "arbitrage", None)

        tg_decisions.append({
            "symbol":         result.symbol,
            "decision":       result.decision,
            "confidence":     result.confidence,
            "gtt_price":      result.gtt_price,
            "bull_argument":  result.bull_argument,
            "bear_argument":  result.bear_argument,
            "analysis_price": analysis_price,
            "arbitrage":      arb.model_dump() if arb is not None else None,
            "batch_id":       batch_id,
        })

    # Push to Telegram
    if _TG_AVAILABLE and _tg_app and tg_decisions:
        try:
            await push_decisions(_tg_app, tg_decisions, batch_id=batch_id)
        except Exception as e:
            logger.warning(f"Telegram push failed: {e}")

    await _set_cycle_lock(False)
    await _db_log("info", f"Cycle complete — {len(completed)} results", cycle_id=batch_id)

    # If /start_system was waiting for cycle to finish, clear kill_system now
    state = await _read_system_state()
    if state.get("pending_start_system"):
        if _state_col:
            await _state_col.update_one(
                {"_id": "zeta_state"},
                {"$set": {"kill_system": False, "pending_start_system": False}},
            )
        logger.info("kill_system cleared — pending /start_system processed")

    return completed

# ---------------------------------------------------------------------------
# Scheduling helpers
# ---------------------------------------------------------------------------

def _now_ist() -> datetime:
    return datetime.now(IST)


def _is_market_open(mopen: time = MARKET_OPEN, mclose: time = MARKET_CLOSE) -> bool:
    now = _now_ist().time()
    return mopen <= now <= mclose


def _seconds_until(t: time) -> float:
    """Seconds from now (IST) until a given time today (or next day if past)."""
    now = _now_ist()
    target = datetime.combine(now.date(), t, tzinfo=IST)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _should_trade_today(state: dict) -> bool:
    today = _now_ist().date().isoformat()
    special = state.get("special_days", {}).get(today, {})
    if special.get("holiday"):
        return False
    if special:
        return True  # explicitly open
    return _now_ist().weekday() < 5  # Mon–Fri


def _market_hours_today(state: dict) -> tuple[time, time]:
    today   = _now_ist().date().isoformat()
    special = state.get("special_days", {}).get(today, {})
    if special and not special.get("holiday"):
        def _p(s: str) -> time:
            h, m = map(int, s.split(":"))
            return time(h, m)
        return (
            _p(special.get("open",  "09:15")),
            _p(special.get("close", "15:30")),
        )
    return MARKET_OPEN, MARKET_CLOSE

# ---------------------------------------------------------------------------
# Continuous scheduling loop
# ---------------------------------------------------------------------------

async def _continuous_loop(holdings: Optional[list[dict]] = None) -> None:
    """
    Main loop: polls MongoDB for interval/kill flags, runs cycles on schedule.
    Re-evaluates state before every cycle so Telegram changes take effect.
    """
    logger.info("Zeta continuous loop started — waiting for interval from Telegram.")

    while True:
        await asyncio.sleep(30)  # poll interval
        state = await _read_system_state()

        # Kill system — pause entirely
        if state.get("kill_system"):
            logger.debug("kill_system active — sleeping")
            await asyncio.sleep(60)
            continue

        # Kill day
        if state.get("kill_day"):
            logger.debug("kill_day active — sleeping until tomorrow")
            await asyncio.sleep(_seconds_until(time(0, 1)))
            continue

        # Check if trading day
        if not _should_trade_today(state):
            logger.info("Non-trading day — sleeping 1 hour")
            await asyncio.sleep(3600)
            continue

        mopen, mclose = _market_hours_today(state)

        # Before market open — wait
        now = _now_ist().time()
        if now < mopen:
            wait = _seconds_until(mopen)
            logger.info(f"Market opens at {mopen} — waiting {wait/60:.1f} min")
            await asyncio.sleep(wait)
            continue

        # After market close
        if now > mclose:
            wait = _seconds_until(time(0, 1))  # next midnight
            logger.info("Market closed — sleeping until midnight reset")
            await asyncio.sleep(wait)
            continue

        # Interval not set yet today — wait for user input
        interval = state.get("interval_minutes")
        logger.info(f"DEBUG: interval={interval}, state keys={list(state.keys())}")
        if not interval:
            logger.info("Interval not set — waiting for Telegram input")
            await asyncio.sleep(60)
            continue

        # Check for Track & Optimize re-analyse requests
        reanalyse = await _consume_reanalyse_queue()
        if reanalyse:
            logger.info(f"Re-analysing {reanalyse} (Track & Optimize trigger)")
            await run_full_cycle(holdings=holdings, reanalyse_symbols=reanalyse)

        # Regular cycle
        logger.info(f"Running scheduled cycle (interval={interval} min)")
        await run_full_cycle(holdings=holdings)

        # Re-read state in case interval changed mid-cycle
        state    = await _read_system_state()
        interval = state.get("interval_minutes") or interval

        # Wait for next cycle, but don't overshoot market close
        IST_tz = IST
        next_run = _now_ist() + timedelta(minutes=interval)
        mclose_dt = datetime.combine(_now_ist().date(), mclose, tzinfo=IST_tz)

        if next_run > mclose_dt:
            # No more cycles today
            wait = _seconds_until(time(0, 1))
            logger.info(f"No more cycles today — sleeping until midnight")
            await asyncio.sleep(wait)
        else:
            wait = (next_run - _now_ist()).total_seconds()
            logger.info(f"Next cycle at {next_run.strftime('%I:%M %p')} — waiting {wait/60:.1f} min")
            await asyncio.sleep(max(wait, 1))

# ---------------------------------------------------------------------------
# Telegram bot runner (runs alongside the main loop in the same event loop)
# ---------------------------------------------------------------------------

async def _run_telegram_bot(app) -> None:
    """Run the Telegram bot inside the existing asyncio event loop."""
    await tg_load_state()
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot polling started")


async def _stop_telegram_bot(app) -> None:
    await app.updater.stop()
    await app.stop()
    await app.shutdown()

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Zeta AI Portfolio Manager")
    parser.add_argument("--run-once", action="store_true",
                        help="Run one analysis cycle and exit")
    parser.add_argument("--symbol",   type=str, default=None,
                        help="Analyse a single symbol only")
    parser.add_argument("--interval", type=int, default=None,
                        help="Override interval in minutes (bypasses Telegram prompt)")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    if args.symbol:
        holdings = [h for h in MOCK_PORTFOLIO if h["symbol"] == args.symbol.upper()]
        if not holdings:
            holdings = [{"symbol": args.symbol.upper(), "exchange": "NSE",
                         "qty": 1, "avg_price": 0}]
    else:
        holdings = None

    # Single cycle mode
    if args.run_once:
        logger.info("Running single cycle (--run-once)")
        await run_full_cycle(holdings)
        return

    # Override interval via CLI (dev/testing)
    if args.interval and _DB_AVAILABLE and _state_col:
        logger.info(f"CLI override: setting interval to {args.interval} min")
        await _state_col.update_one(
            {"_id": "zeta_state"},
            {"$set": {
                "interval_minutes": args.interval,
                "today_date":       _now_ist().date().isoformat(),
                "kill_system":      False,
                "kill_day":         False,
            }},
            upsert=True,
        )

    # Continuous mode: always run the scheduling loop.
    # The telegram-bot container handles Telegram independently;
    # agent-engine just needs to run cycles and write to MongoDB.
    try:
        if _TG_AVAILABLE and _tg_app:
            await asyncio.gather(
                _continuous_loop(holdings),
                _run_telegram_bot(_tg_app),
            )
        else:
            await _continuous_loop(holdings)
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        logger.info("Shutting down")
        if _TG_AVAILABLE and _tg_app:
            await _stop_telegram_bot(_tg_app)


if __name__ == "__main__":
    asyncio.run(main())
