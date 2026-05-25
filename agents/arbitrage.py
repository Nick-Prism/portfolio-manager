"""
agents/arbitrage.py
Exchange spread agent — NSE vs BSE price comparison.

Checks real-time prices on both exchanges and:
1. Computes the spread (price difference %)
2. Determines if the spread is large enough to cover transaction costs
3. Recommends which exchange to route buy/sell orders to

This is NOT pure arbitrage (that requires simultaneous buy+sell which needs
margin/F&O). This is exchange routing — when the bot decides to act on a
stock, it routes the order to the cheaper exchange.

Transaction cost breakdown (retail CNC delivery):
  Brokerage:      ₹20 flat per order (Zerodha)
  STT:            0.1% of turnover on sell side
  Exchange txn:   ~0.00345% NSE / ~0.00375% BSE
  GST:            18% on brokerage + exchange charges
  SEBI charges:   ₹10 per crore
  Stamp duty:     0.015% on buy side

Minimum viable spread to profit after all charges: ~0.15% for a ₹50,000 position.
Below that, routing to the cheaper exchange still saves money but isn't
worth a dedicated arbitrage trade.
"""

from __future__ import annotations
import logging
from typing import Optional

from llm.schemas import ArbitrageSignal

logger = logging.getLogger(__name__)

# Minimum spread % that makes exchange routing meaningfully beneficial
# Below this the saving is less than ₹5 on a typical retail position
MIN_VIABLE_SPREAD_PCT = 0.05


def run_arbitrage_agent(symbol: str) -> Optional[ArbitrageSignal]:
    """
    Fetch NSE and BSE prices and compute exchange spread.

    Returns ArbitrageSignal if both prices are available, None otherwise.
    None is handled gracefully by the graph — analysis continues without it.
    """
    logger.info(f"[Arbitrage] Checking NSE/BSE spread for {symbol}")

    try:
        from database.data.fetchers import get_exchange_spread
        spread = get_exchange_spread(symbol)
    except Exception as e:
        logger.warning(f"[Arbitrage] Spread fetch failed for {symbol}: {e}")
        return None

    if spread is None:
        logger.info(f"[Arbitrage] Could not get both exchange prices for {symbol} — skipping")
        return None

    nse_price  = spread["nse_price"]
    bse_price  = spread["bse_price"]
    spread_pct = spread["spread_pct"]
    spread_abs = spread["spread_abs"]
    cheaper    = spread["cheaper_exchange"]

    viable = spread_pct >= MIN_VIABLE_SPREAD_PCT

    # Exchange routing logic:
    # - Buy on the cheaper exchange (lower price = less capital outlay)
    # - Sell on the more expensive exchange (higher price = more proceeds)
    # This applies whether the bot is placing a new buy or exiting a position.
    buy_exchange  = cheaper
    sell_exchange = "BSE" if cheaper == "NSE" else "NSE"

    if viable:
        reasoning = (
            f"NSE price ₹{nse_price:.2f} vs BSE price ₹{bse_price:.2f} — "
            f"spread of ₹{spread_abs:.2f} ({spread_pct:.3f}%). "
            f"Route buys to {buy_exchange} and sells to {sell_exchange} to optimise execution price."
        )
    else:
        reasoning = (
            f"NSE price ₹{nse_price:.2f} vs BSE price ₹{bse_price:.2f} — "
            f"spread of ₹{spread_abs:.2f} ({spread_pct:.3f}%) is below the {MIN_VIABLE_SPREAD_PCT}% "
            f"threshold. Prices are effectively equal; route to NSE (higher liquidity)."
        )
        # Below threshold: default to NSE for higher liquidity
        buy_exchange  = "NSE"
        sell_exchange = "NSE"

    logger.info(
        f"[Arbitrage] {symbol}: NSE ₹{nse_price:.2f} / BSE ₹{bse_price:.2f} "
        f"spread={spread_pct:.3f}% viable={viable}"
    )

    return ArbitrageSignal(
        symbol=symbol,
        nse_price=nse_price,
        bse_price=bse_price,
        spread_pct=spread_pct,
        spread_abs=spread_abs,
        cheaper_exchange=cheaper,
        recommended_buy_exchange=buy_exchange,
        recommended_sell_exchange=sell_exchange,
        viable=viable,
        reasoning=reasoning,
    )
