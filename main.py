"""
main.py
Zeta AI Portfolio Manager — Agent Engine Entry Point

Usage:
  python main.py                  # Run continuous loop (APScheduler, 30-min cycles)
  python main.py --run-once       # Run one full cycle then exit (demo / testing)
  python main.py --symbol HDFCBANK --run-once   # Single stock

Environment variables (loaded from GCP Secret Manager in production):
  GEMINI_API_KEY, GROQ_API_KEY, ANTHROPIC_API_KEY  — at least one required
  MONGODB_URI        — set by P3; if absent, results are printed to stdout
  ZERODHA_API_KEY    — set by P1; if absent, mock portfolio is used
"""

from __future__ import annotations
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Load .env before any other module reads environment variables
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed -- env vars must be set manually

import pandas as pd
import yfinance as yf
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agents.graph import run_analysis_cycle
from llm.schemas import CycleResult

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
# Mock portfolio (used when Zerodha MCP is not available)
# P1 replaces this with live holdings via mcp/zerodha_mcp.py
# ---------------------------------------------------------------------------

MOCK_PORTFOLIO = [
    {"symbol": "RELIANCE",  "exchange": "NSE", "qty": 10,  "avg_price": 2850.0},
    {"symbol": "TCS",       "exchange": "NSE", "qty": 5,   "avg_price": 3920.0},
    {"symbol": "INFY",      "exchange": "NSE", "qty": 8,   "avg_price": 1640.0},
    {"symbol": "HDFCBANK",  "exchange": "NSE", "qty": 15,  "avg_price": 1580.0},
    {"symbol": "ITC",       "exchange": "NSE", "qty": 50,  "avg_price": 410.0},
]

# ---------------------------------------------------------------------------
# Data helpers (standalone — P3 replaces with their fetchers in production)
# ---------------------------------------------------------------------------

def fetch_ohlcv(symbol: str, exchange: str = "NSE", period: str = "1y") -> pd.DataFrame:
    """
    Fetch OHLCV data using yfinance.
    Uses Ticker.history() to avoid the MultiIndex columns introduced in yfinance 1.2.0.
    NSE tickers need '.NS' suffix; BSE need '.BO'.
    """
    suffix = ".NS" if exchange == "NSE" else ".BO"
    ticker_str = f"{symbol}{suffix}"
    try:
        ticker = yf.Ticker(ticker_str)
        df = ticker.history(period=period, auto_adjust=True)
        if df.empty:
            raise ValueError(f"No data returned for {ticker_str}")
        # Ticker.history() always returns flat string columns — just lowercase them
        df.columns = [c.lower() for c in df.columns]
        # history() may include Dividends and Stock Splits columns — keep only OHLCV
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[cols].dropna()
        logger.info(f"Fetched {len(df)} candles for {symbol}")
        return df
    except Exception as e:
        logger.error(f"Failed to fetch OHLCV for {symbol}: {e}")
        raise


def fetch_benchmark(period: str = "1y") -> pd.Series:
    """Fetch Nifty 50 daily returns for beta computation."""
    try:
        nifty = yf.Ticker("^NSEI").history(period=period, auto_adjust=True)
        returns = nifty["Close"].pct_change().dropna()
        return returns
    except Exception as e:
        logger.warning(f"Could not fetch Nifty benchmark: {e}")
        return pd.Series(dtype=float)


def compute_portfolio_allocation(holdings: list[dict], prices: dict[str, float]) -> dict[str, float]:
    """Compute each holding's % weight in the total portfolio."""
    values = {}
    for h in holdings:
        sym = h["symbol"]
        price = prices.get(sym, h["avg_price"])
        values[sym] = price * h["qty"]
    total = sum(values.values())
    if total == 0:
        return {h["symbol"]: 0.0 for h in holdings}
    return {sym: (v / total) * 100 for sym, v in values.items()}


# ---------------------------------------------------------------------------
# Result output
# ---------------------------------------------------------------------------

def _output_result(result: CycleResult) -> None:
    """
    Write the cycle result to MongoDB if MONGODB_URI is set,
    otherwise print a formatted summary to stdout.
    P3's db/client.py handles the actual MongoDB write in production.
    """
    mongodb_uri = os.getenv("MONGODB_URI")

    if mongodb_uri:
        try:
            # P3 integration: import their client and write
            # from db.client import write_decision
            # asyncio.create_task(write_decision(result))
            # Uncomment the two lines above once P3's db layer is ready.
            logger.info(f"[{result.symbol}] MongoDB write — integration point for P3")
        except Exception as e:
            logger.error(f"MongoDB write failed for {result.symbol}: {e}")
    else:
        _print_result(result)


def _print_result(result: CycleResult) -> None:
    """Pretty-print a CycleResult to stdout for local development."""
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


# ---------------------------------------------------------------------------
# Core analysis cycle
# ---------------------------------------------------------------------------

async def analyse_holding(
    holding: dict,
    benchmark_returns: pd.Series,
    allocations: dict[str, float],
) -> Optional[CycleResult]:
    """Run the full analysis pipeline for one holding."""
    symbol = holding["symbol"]
    exchange = holding.get("exchange", "NSE")

    try:
        ohlcv = fetch_ohlcv(symbol, exchange)
        returns = ohlcv["close"].pct_change().dropna()

        result = await run_analysis_cycle(
            symbol=symbol,
            ohlcv=ohlcv,
            returns=returns,
            benchmark_returns=benchmark_returns if not benchmark_returns.empty else None,
            portfolio_allocation_pct=allocations.get(symbol, 0.0),
            fundamentals=None,   # P3 injects this in production
            articles=None,       # P3 injects this in production
            exchange=exchange,
        )
        _output_result(result)
        return result

    except Exception as e:
        logger.error(f"Failed to analyse {symbol}: {e}", exc_info=True)
        return None


async def run_full_cycle(holdings: Optional[list[dict]] = None) -> list[CycleResult]:
    """Run one complete analysis cycle across all holdings."""
    if holdings is None:
        holdings = _get_holdings()

    logger.info(f"Starting analysis cycle — {len(holdings)} holdings")
    benchmark = fetch_benchmark()

    # Compute current prices for allocation calculation
    prices = {}
    for h in holdings:
        try:
            df = fetch_ohlcv(h["symbol"], h.get("exchange", "NSE"), period="5d")
            prices[h["symbol"]] = float(df["close"].iloc[-1])
        except Exception:
            prices[h["symbol"]] = h["avg_price"]

    allocations = compute_portfolio_allocation(holdings, prices)

    # Run all holdings concurrently
    tasks = [
        analyse_holding(h, benchmark, allocations)
        for h in holdings
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    completed = [r for r in results if r is not None]

    logger.info(f"Cycle complete — {len(completed)}/{len(holdings)} stocks analysed")
    return completed


def _get_holdings() -> list[dict]:
    """
    Get current portfolio holdings.
    In production: P1's Zerodha MCP wrapper provides this.
    In development: use MOCK_PORTFOLIO.
    """
    zerodha_key = os.getenv("ZERODHA_API_KEY")
    if zerodha_key:
        try:
            # P1 integration point
            # from mcp.zerodha_mcp import get_holdings
            # return get_holdings()
            logger.info("Zerodha MCP available but not yet integrated — using mock portfolio")
        except ImportError:
            pass
    logger.info("Using mock portfolio (ZERODHA_API_KEY not set)")
    return MOCK_PORTFOLIO


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Zeta AI Portfolio Manager — Agent Engine")
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run one analysis cycle and exit",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Analyse a single symbol only (e.g. HDFCBANK)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Cycle interval in minutes for continuous mode (default: 30)",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    if args.symbol:
        holdings = [h for h in MOCK_PORTFOLIO if h["symbol"] == args.symbol.upper()]
        if not holdings:
            # Allow any symbol not in mock portfolio
            holdings = [{"symbol": args.symbol.upper(), "exchange": "NSE", "qty": 1, "avg_price": 0}]
    else:
        holdings = None   # uses _get_holdings()

    if args.run_once:
        logger.info("Running single cycle (--run-once)")
        await run_full_cycle(holdings)
        return

    # Continuous mode with APScheduler
    logger.info(f"Starting continuous mode — cycle every {args.interval} minutes")
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_full_cycle,
        "interval",
        minutes=args.interval,
        args=[holdings],
        next_run_time=datetime.now(),  # run immediately on start
    )
    scheduler.start()

    try:
        await asyncio.Event().wait()   # run forever
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler")
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())