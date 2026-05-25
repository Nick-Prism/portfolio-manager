"""
bot/telegram_bot.py
Zeta Telegram Bot — push-based approval system with full day management.

State stored in MongoDB `system_state` collection (one doc, _id="zeta_state").
Logs stored in MongoDB `logs` collection (one doc per entry).

Flow:
  1. Bot starts at 9 AM, asks user for interval.
  2. main.py calls push_decisions() after each cycle.
  3. User approves/rejects per-decision or via batch controls in the menu.
  4. Price validation runs before order placement.
  5. Track & Optimize monitors positive movers between cycles.

Kill hierarchy (independent flags in MongoDB):
  kill_system  — halted indefinitely, cleared only by /start_system
  kill_day     — halted today, resets at midnight automatically
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)
from dotenv import load_dotenv

load_dotenv()

TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHAT_ID")

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not set in .env")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("zeta.bot")

# ---------------------------------------------------------------------------
# MongoDB helpers (graceful fallback if DB unavailable)
# ---------------------------------------------------------------------------

try:
    from database.db.client import get_db
    _db = get_db()
    _state_col = _db["system_state"] if _db is not None else None
    _logs_col  = _db["logs"]         if _db is not None else None
    _dec_col   = _db["decisions"]    if _db is not None else None
    _DB_AVAILABLE = _state_col is not None
except Exception:
    _state_col = _logs_col = _dec_col = None
    _DB_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MARKET_OPEN  = time(9, 15)   # IST
MARKET_CLOSE = time(15, 30)  # IST
INTERVAL_OPTIONS = [10, 15, 30, 45, 60, 120, 180]

PRICE_DROP_THRESHOLD    = 0.0115  # 1.15% negative → skip order
PRICE_RISE_THRESHOLD    = 0.0115  # 1.15% positive → Track & Optimize prompt
DEFAULT_PULLBACK_PCT    = 0.5     # % dip from peak to trigger re-analysis
TRACKING_CHECK_INTERVAL = 120     # seconds between price checks

# ---------------------------------------------------------------------------
# In-memory state (backed by MongoDB; survives restart via DB)
# ---------------------------------------------------------------------------

_state: dict = {
    "kill_system":         False,
    "kill_day":            False,
    "interval_minutes":    None,   # set by user each morning
    "today_date":          None,   # ISO date string
    "interval_prompt_job": None,   # APScheduler job id
    "special_days":        {},     # {"2024-01-15": {"open": "10:00", "close": "13:00"}}
    "pullback_pct":        DEFAULT_PULLBACK_PCT,
    "cycle_running":       False,
    "pending_start_system": False, # /start_system arrived during cycle
    # tracking: {symbol: {peak_price, buy_price, batch_id, analysis_price, chat_msg_id}}
    "tracking":            {},
}

# Map batch_id → list of message_ids so we can reference them later
_batch_messages: dict[str, list[int]] = {}

# ---------------------------------------------------------------------------
# MongoDB state persistence
# ---------------------------------------------------------------------------

async def _load_state() -> None:
    """Load persisted state from MongoDB on startup."""
    if not _DB_AVAILABLE:
        return
    try:
        doc = await _state_col.find_one({"_id": "zeta_state"})
        if doc:
            for key in ("kill_system", "kill_day", "interval_minutes",
                        "today_date", "special_days", "pullback_pct"):
                if key in doc:
                    _state[key] = doc[key]
        logger.info("State loaded from MongoDB")
    except Exception as e:
        logger.warning(f"Could not load state from MongoDB: {e}")


async def _save_state() -> None:
    """Persist relevant state keys to MongoDB."""
    if not _DB_AVAILABLE:
        return
    try:
        payload = {k: _state[k] for k in (
            "kill_system", "kill_day", "interval_minutes",
            "today_date", "special_days", "pullback_pct",
        )}
        await _state_col.update_one(
            {"_id": "zeta_state"},
            {"$set": payload},
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"Could not save state to MongoDB: {e}")


async def _log(level: str, message: str, source: str = "bot",
               cycle_id: Optional[str] = None, symbol: Optional[str] = None) -> None:
    """Write a structured log entry to MongoDB."""
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
    logger.log(getattr(logging, level.upper(), logging.INFO), message)
    if _DB_AVAILABLE and _logs_col is not None:
        try:
            await _logs_col.insert_one(entry)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _now_ist() -> datetime:
    IST = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(IST)


def _today_str() -> str:
    return _now_ist().date().isoformat()


def _is_market_open() -> bool:
    now = _now_ist().time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


def _is_weekday() -> bool:
    return _now_ist().weekday() < 5  # 0=Mon … 4=Fri


def _get_special_day(date_str: Optional[str] = None) -> Optional[dict]:
    date_str = date_str or _today_str()
    return _state["special_days"].get(date_str)


def _should_trade_today() -> bool:
    """True if trading should happen today (weekday or declared special day)."""
    today = _today_str()
    if _get_special_day(today):
        return True
    return _is_weekday()


def _market_hours_today() -> tuple[time, time]:
    """Return (open, close) for today considering special days."""
    today = _today_str()
    special = _get_special_day(today)
    if special:
        def _parse(t: str) -> time:
            h, m = map(int, t.split(":"))
            return time(h, m)
        return (
            _parse(special.get("open",  "09:15")),
            _parse(special.get("close", "15:30")),
        )
    return MARKET_OPEN, MARKET_CLOSE


def _compute_cycle_schedule(interval_min: int, start: Optional[time] = None) -> list[time]:
    """Return list of cycle start times for today given interval."""
    mopen, mclose = _market_hours_today()
    cursor = start or mopen
    now    = _now_ist().time()
    # If starting late, first cycle = now (rounded up to nearest minute)
    if now > cursor:
        cursor = now
    cycles: list[time] = []
    IST = timezone(timedelta(hours=5, minutes=30))
    while True:
        dt = datetime.combine(_now_ist().date(), cursor, tzinfo=IST)
        dt_next = dt + timedelta(minutes=interval_min)
        if dt_next.time() > mclose:
            # Last cycle: only if there's at least 15 min before close
            remaining = (
                datetime.combine(_now_ist().date(), mclose, tzinfo=IST) - dt
            ).seconds // 60
            if remaining >= 15:
                cycles.append(cursor)
            break
        cycles.append(cursor)
        cursor = dt_next.time()
    return cycles


def _format_schedule(cycles: list[time]) -> str:
    if not cycles:
        return "No cycles fit in the remaining market hours."
    times = ", ".join(t.strftime("%I:%M %p") for t in cycles)
    return f"{len(cycles)} cycle(s): {times}"


async def _reject_batch_decisions(batch_id: str) -> int:
    """Reject all pending decisions for a batch. Returns count rejected."""
    count = 0
    if _DB_AVAILABLE and _dec_col is not None:
        try:
            result = await _dec_col.update_many(
                {"batch_id": batch_id, "approved": None},
                {"$set": {"approved": False, "expired": True}},
            )
            count = result.modified_count
        except Exception as e:
            await _log("error", f"Failed to reject batch {batch_id}: {e}")
    return count


async def _reject_all_pending() -> int:
    """Reject all globally pending decisions. Returns count."""
    count = 0
    if _DB_AVAILABLE and _dec_col is not None:
        try:
            result = await _dec_col.update_many(
                {"approved": None},
                {"$set": {"approved": False, "expired": True}},
            )
            count = result.modified_count
        except Exception as e:
            await _log("error", f"Failed to reject all pending: {e}")
    return count


async def _get_batch_decisions(batch_id: str) -> list[dict]:
    if not (_DB_AVAILABLE and _dec_col):
        return []
    try:
        cursor = _dec_col.find({"batch_id": batch_id, "approved": None})
        return await cursor.to_list(length=100)
    except Exception:
        return []


async def _get_holdings_symbols() -> set[str]:
    """Return set of symbols currently in the Zerodha portfolio."""
    try:
        from mcp.tools import get_holdings
        raw = get_holdings()
        return {h["tradingsymbol"] for h in raw if h.get("quantity", 0) > 0}
    except Exception:
        # Fall back to mock portfolio symbols
        return {"RELIANCE", "TCS", "INFY", "HDFCBANK", "ITC"}


async def _get_live_price(symbol: str) -> Optional[float]:
    """Fetch current live price for a symbol."""
    try:
        from database.data.fetchers import get_price_data
        df = get_price_data(symbol, period="1d")
        if df is not None and not df.empty:
            col = "Close" if "Close" in df.columns else "close"
            return float(df[col].iloc[-1])
    except Exception:
        pass
    try:
        import yfinance as yf
        t = yf.Ticker(f"{symbol}.NS")
        hist = t.history(period="1d", interval="1m")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None

# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------

def _decision_keyboard(symbol: str, batch_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_{symbol}_{batch_id}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"reject_{symbol}_{batch_id}"),
        ],
        [InlineKeyboardButton("☰ Menu",    callback_data=f"menu_{batch_id}")],
    ])


def _menu_keyboard(batch_id: str, batch_label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Change Interval",              callback_data="menu_change_interval")],
        [InlineKeyboardButton(f"✅ Approve Batch [{batch_label}]", callback_data=f"batch_approve_{batch_id}")],
        [InlineKeyboardButton("📋 Show Remaining",               callback_data=f"menu_show_remaining_{batch_id}")],
        [InlineKeyboardButton(f"💀 Kill Batch [{batch_label}]",   callback_data=f"batch_kill_{batch_id}")],
        [InlineKeyboardButton("🌙 Kill Day",                     callback_data="menu_kill_day")],
        [InlineKeyboardButton("🛑 Kill System",                  callback_data="menu_kill_system")],
        [InlineKeyboardButton("◀️ Go Back",                      callback_data=f"menu_back_{batch_id}")],
    ])


def _interval_keyboard(context_cb: str = "interval") -> InlineKeyboardMarkup:
    rows = []
    row  = []
    for i, m in enumerate(INTERVAL_OPTIONS):
        label = f"{m} min" if m < 60 else f"{m // 60}h"
        row.append(InlineKeyboardButton(label, callback_data=f"{context_cb}_{m}"))
        if len(row) == 4 or i == len(INTERVAL_OPTIONS) - 1:
            rows.append(row)
            row = []
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(rows)


def _confirm_keyboard(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes", callback_data=yes_cb),
        InlineKeyboardButton("❌ No",  callback_data=no_cb),
    ]])

# ---------------------------------------------------------------------------
# Message builder
# ---------------------------------------------------------------------------

def _decision_text(d: dict) -> str:
    arb = d.get("arbitrage") or {}
    exchange_line = ""
    if arb and arb.get("viable"):
        exchange_line = (
            f"\n🔀 Route: Buy {arb.get('recommended_buy_exchange')} / "
            f"Sell {arb.get('recommended_sell_exchange')} "
            f"(spread {arb.get('spread_pct', 0):.3f}%)"
        )
    gtt_line = f"\n🎯 GTT Price: ₹{d['gtt_price']:.2f}" if d.get("gtt_price") else ""
    analysis_price = d.get("analysis_price", 0)
    price_line = f"\n💰 Analysis Price: ₹{analysis_price:.2f}" if analysis_price else ""

    return (
        f"📊 *{d['symbol']}* — `{d['decision']}`\n"
        f"Confidence: {d['confidence']:.0f}%{gtt_line}{price_line}{exchange_line}\n\n"
        f"🐂 Bull: {str(d.get('bull_argument', ''))[:200]}\n\n"
        f"🐻 Bear: {str(d.get('bear_argument', ''))[:200]}"
    )

# ---------------------------------------------------------------------------
# Interval prompt logic (9 AM + re-ask every 15 min until 10 AM)
# ---------------------------------------------------------------------------

async def _send_interval_prompt(app, attempt: int = 1) -> None:
    """Send the morning interval selection prompt."""
    if not CHAT_ID:
        return

    # Don't prompt if already set today
    if (_state["today_date"] == _today_str() and
            _state["interval_minutes"] is not None):
        return

    # Don't prompt on weekends unless special day
    if not _should_trade_today():
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text="📅 Today is a non-trading day (weekend). No cycles will run.",
        )
        return

    mopen, mclose = _market_hours_today()
    special = _get_special_day()
    if special:
        hours_note = (
            f"📅 *Special market day* — Hours: "
            f"{mopen.strftime('%I:%M %p')} – {mclose.strftime('%I:%M %p')} IST\n"
        )
    else:
        hours_note = ""

    now = _now_ist().time()
    if now > time(10, 0) and attempt > 1:
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"{hours_note}"
                "⏰ *No interval selected by 10:00 AM.*\n"
                "Zeta will *not* run today.\n\n"
                "Tap below to start manually at any time:"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("▶️ Start Manually", callback_data="manual_start"),
            ]]),
            parse_mode="Markdown",
        )
        return

    attempt_note = (
        f"_(Reminder {attempt} — will stop asking at 10:00 AM)_\n\n"
        if attempt > 1 else ""
    )
    await app.bot.send_message(
        chat_id=CHAT_ID,
        text=(
            f"{hours_note}"
            f"🌅 *Good morning! Select today's cycle interval:*\n{attempt_note}"
        ),
        reply_markup=_interval_keyboard("interval"),
        parse_mode="Markdown",
    )


async def _schedule_interval_reprompt(app, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called by APScheduler every 15 min from 9 AM until 10 AM."""
    if (_state["today_date"] == _today_str() and
            _state["interval_minutes"] is not None):
        return  # Already set, nothing to do

    now = _now_ist().time()
    attempt = getattr(_schedule_interval_reprompt, "_attempt", 1)
    _schedule_interval_reprompt._attempt = attempt + 1

    if now > time(10, 0):
        await _send_interval_prompt(app, attempt=99)  # trigger "no response" path
        return

    await _send_interval_prompt(app, attempt=attempt)

# ---------------------------------------------------------------------------
# Core push function (called from main.py after each analysis cycle)
# ---------------------------------------------------------------------------

async def push_decisions(app, decisions: list[dict], batch_id: Optional[str] = None) -> None:
    """
    Push a batch of decisions to Telegram.
    Each decision must include: symbol, decision, confidence, bull_argument,
    bear_argument, and optionally gtt_price, arbitrage, analysis_price.
    batch_id should be a unique ID for this analysis cycle.
    """
    if not CHAT_ID:
        return

    if _state["kill_system"]:
        await _log("warning", "push_decisions called but kill_system is active")
        return
    if _state["kill_day"]:
        await _log("warning", "push_decisions called but kill_day is active")
        return

    # Auto-reset kill_day if it's a new calendar day
    if _state["today_date"] != _today_str():
        _state["kill_day"]            = False
        _state["today_date"]          = _today_str()
        _state["interval_minutes"]    = None
        await _save_state()

    if batch_id is None:
        batch_id = str(uuid.uuid4())

    batch_label = _now_ist().strftime("%I:%M %p")  # e.g. "10:30 AM"

    # Filter: only symbols already in portfolio
    portfolio_symbols = await _get_holdings_symbols()
    decisions = [d for d in decisions if d.get("symbol") in portfolio_symbols]

    # Warn if using mock portfolio (token expired) but still push decisions
    try:
        from mcp.tools import get_holdings
        raw = get_holdings()
        if not raw:
            raise Exception("empty holdings")
    except Exception:
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "⚠️ Zerodha token expired or invalid.\n"
                "Analysis below is based on the mock portfolio.\n"
                "Use /refresh_token to update."
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔑 Refresh Zerodha Token", callback_data="trigger_refresh_token")
            ]]),
        )

    if not decisions:
        await app.bot.send_message(
            chat_id=CHAT_ID,
            text="✅ Analysis cycle complete — no actionable decisions for your portfolio.",
        )
        return

    # Expire any unactioned decisions from previous batches
    if _DB_AVAILABLE and _dec_col is not None:
        try:
            await _dec_col.update_many(
                {"approved": None, "batch_id": {"$ne": batch_id}},
                {"$set": {"approved": False, "expired": True}},
            )
        except Exception:
            pass

    header_msg = await app.bot.send_message(
        chat_id=CHAT_ID,
        text=(
            f"🔔 *Zeta — {batch_label} Batch* ({len(decisions)} decision(s))\n"
            f"Batch ID: `{batch_id}`"
        ),
        parse_mode="Markdown",
    )

    sent_ids: list[int] = [header_msg.message_id]

    for d in decisions:
        d["batch_id"] = batch_id
        # Price validation
        analysis_price = d.get("analysis_price")
        if analysis_price:
            live_price = await _get_live_price(d["symbol"])
            if live_price:
                pct_change = (live_price - analysis_price) / analysis_price
                if pct_change < -PRICE_DROP_THRESHOLD:
                    msg = await app.bot.send_message(
                        chat_id=CHAT_ID,
                        text=(
                            f"⚠️ *{d['symbol']}* — Price dropped "
                            f"{abs(pct_change)*100:.2f}% since analysis "
                            f"(₹{analysis_price:.2f} → ₹{live_price:.2f}).\n"
                            "Skipping this order."
                        ),
                        parse_mode="Markdown",
                    )
                    sent_ids.append(msg.message_id)
                    await _log("warning",
                               f"{d['symbol']} skipped — price dropped {pct_change*100:.2f}%",
                               symbol=d["symbol"])
                    continue

                if pct_change > PRICE_RISE_THRESHOLD:
                    kb = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton(
                                "🚀 Place Now",
                                callback_data=f"place_now_{d['symbol']}_{batch_id}",
                            ),
                            InlineKeyboardButton(
                                "📈 Track & Optimize",
                                callback_data=f"track_{d['symbol']}_{batch_id}_{analysis_price}",
                            ),
                            InlineKeyboardButton(
                                "⏭ Skip",
                                callback_data=f"skip_{d['symbol']}_{batch_id}",
                            ),
                        ]
                    ])
                    msg = await app.bot.send_message(
                        chat_id=CHAT_ID,
                        text=(
                            f"📈 *{d['symbol']}* moved +{pct_change*100:.2f}% since analysis "
                            f"(₹{analysis_price:.2f} → ₹{live_price:.2f}).\n"
                            "What would you like to do?"
                        ),
                        reply_markup=kb,
                        parse_mode="Markdown",
                    )
                    sent_ids.append(msg.message_id)
                    continue

        text = _decision_text(d)
        msg  = await app.bot.send_message(
            chat_id=CHAT_ID,
            text=text,
            reply_markup=_decision_keyboard(d["symbol"], batch_id),
            parse_mode="Markdown",
        )
        sent_ids.append(msg.message_id)

    _batch_messages[batch_id] = sent_ids
    await _log("info", f"Pushed batch {batch_label} ({len(decisions)} decisions)", cycle_id=batch_id)

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/check — manually fetch and show pending decisions."""
    if _state["kill_system"]:
        await update.message.reply_text(
            "🛑 System is halted. Use /start_system to restart."
        )
        return

    if not (_DB_AVAILABLE and _dec_col):
        await update.message.reply_text("⚠️ Database unavailable.")
        return

    pending = await _dec_col.find({"approved": None}).to_list(length=50)
    if not pending:
        await update.message.reply_text("✅ No pending decisions right now.")
        return

    # Group by batch_id
    batches: dict[str, list] = {}
    for d in pending:
        bid = d.get("batch_id", "unknown")
        batches.setdefault(bid, []).append(d)

    for bid, docs in batches.items():
        label = bid[:8]
        await update.message.reply_text(
            f"📋 Batch `{label}` — {len(docs)} pending:",
            parse_mode="Markdown",
        )
        for d in docs[:5]:
            text = _decision_text(d)
            await update.message.reply_text(
                text,
                reply_markup=_decision_keyboard(d["symbol"], bid),
                parse_mode="Markdown",
            )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Zeta Bot — Available Commands*\n\n"
        "*Daily Operations*\n"
        "/refresh\\_token — Refresh Zerodha access token\n"
        "/refresh\\_upstox\\_token — Refresh Upstox access token\n"
        "/check — Show pending decisions\n"
        "/status — System status\n\n"
        "*Trading Controls*\n"
        "/resume\\_today — Override Kill Day\n"
        "/declare\\_holiday — Mark today as holiday\n"
        "/special\\_day — Declare tomorrow as special market day\n"
        "/cancel\\_special\\_day — Cancel special day\n\n"
        "*System Controls*\n"
        "/start\\_system — Restart after Kill System\n"
        "/set\\_pullback <pct> — Set Track & Optimize threshold (default 0.5%)\n\n"
        "*How decisions work*\n"
        "After each cycle, decisions arrive automatically.\n"
        "Each has ✅ Approve / ❌ Reject / ☰ Menu buttons.\n"
        "Menu has batch controls, kill options, and interval change.",
        parse_mode="Markdown",
    )

async def cmd_refresh_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/refresh_token — get a fresh Zerodha access token without touching the terminal."""
    api_key = os.environ.get("ZERODHA_API_KEY", "").strip()
    if not api_key:
        await update.message.reply_text(
            "⚠️ ZERODHA_API_KEY not set in .env. Cannot generate login URL."
        )
        return
    try:
        from kiteconnect import KiteConnect
        kite      = KiteConnect(api_key=api_key)
        login_url = kite.login_url()
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not generate login URL: {e}")
        return

    context.user_data["awaiting_zerodha_token"] = True
    await update.message.reply_text(
        f"🔑 Zerodha Token Refresh\n\n"
        f"1. Tap the link below to log in:\n{login_url}\n\n"
        f"2. After login, your browser redirects to a URL like:\n"
        f"https://zeta-portfolio.duckdns.org/?request_token=XXXXX...\n"
        f"(browser may show an error — that's fine)\n\n"
        f"3. Copy that full URL and paste it here.",
        disable_web_page_preview=True,
    )

async def cmd_refresh_upstox_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    api_key  = os.environ.get("UPSTOX_API_KEY", "").strip()
    redirect = os.environ.get("UPSTOX_REDIRECT_URL", "https://zeta-portfolio.duckdns.org").strip()
    if not api_key:
        await update.message.reply_text("⚠️ UPSTOX_API_KEY not set in .env.")
        return
    login_url = (
        f"https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code&client_id={api_key}&redirect_uri={redirect}"
    )
    context.user_data["awaiting_upstox_token"] = True
    await update.message.reply_text(
        f"🔑 Upstox Token Refresh\n\n"
        f"1. Tap to log in:\n{login_url}\n\n"
        f"2. After login, paste the full redirect URL here.",
        disable_web_page_preview=True,
    )


async def _handle_zerodha_token_paste(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the redirect URL pasted after Zerodha login."""
    import re
    from pathlib import Path
    from dotenv import set_key

    raw   = update.message.text.strip()
    match = re.search(r"request_token=([A-Za-z0-9]+)", raw)
    if not match:
        await update.message.reply_text(
            "⚠️ Could not find `request_token` in that URL. Please paste the full redirect URL."
        )
        return

    request_token = match.group(1)
    api_key    = os.environ.get("ZERODHA_API_KEY", "").strip()
    api_secret = os.environ.get("ZERODHA_API_SECRET", "").strip()

    if not api_key or not api_secret:
        await update.message.reply_text(
            "⚠️ ZERODHA_API_KEY or ZERODHA_API_SECRET not set in .env."
        )
        context.user_data.pop("awaiting_zerodha_token", None)
        return

    try:
        from kiteconnect import KiteConnect
        kite         = KiteConnect(api_key=api_key)
        session      = kite.generate_session(request_token, api_secret=api_secret)
        access_token = session["access_token"]
    except Exception as e:
        await update.message.reply_text(
            f"❌ Failed to generate session: {e}\n"
            "Make sure the token is fresh (they expire quickly)."
        )
        context.user_data.pop("awaiting_zerodha_token", None)
        return

    # Save to .env
    env_path = Path(__file__).resolve().parent.parent / ".env"
    set_key(str(env_path), "ZERODHA_ACCESS_TOKEN", access_token)
    # Also update the running process environment
    os.environ["ZERODHA_ACCESS_TOKEN"] = access_token

    context.user_data.pop("awaiting_zerodha_token", None)
    await update.message.reply_text(
        f"✅ *Zerodha token refreshed!*\n"
        f"Token: `{access_token[:8]}...{access_token[-4:]}`\n"
        f"Valid until 6 AM IST tomorrow.",
        parse_mode="Markdown",
    )
    # Share token via MongoDB so agent-engine container picks it up
    if _DB_AVAILABLE and _state_col is not None:
        try:
            await _state_col.update_one(
                {"_id": "zeta_state"},
                {"$set": {"zerodha_access_token": access_token}},
                upsert=True,
            )
        except Exception:
            pass
    await _log("info", "Zerodha access token refreshed via Telegram")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — show system status."""
    total = approved = pending = 0
    if _DB_AVAILABLE and _dec_col is not None:
        all_docs = await _dec_col.find({}).to_list(length=1000)
        total    = len(all_docs)
        approved = sum(1 for d in all_docs if d.get("approved") is True)
        pending  = sum(1 for d in all_docs if d.get("approved") is None)

    mopen, mclose = _market_hours_today()
    interval = _state["interval_minutes"]
    schedule_note = ""
    if interval:
        cycles = _compute_cycle_schedule(interval)
        schedule_note = f"\nToday's schedule: {_format_schedule(cycles)}"

    status_str = (
        "🛑 HALTED (kill_system)" if _state["kill_system"] else
        "🌙 Paused for today (kill_day)" if _state["kill_day"] else
        "✅ Running"
    )

    await update.message.reply_text(
        f"*Zeta System Status*\n"
        f"Status: {status_str}\n"
        f"Interval: {interval or 'not set'} min\n"
        f"Market hours: {mopen.strftime('%I:%M %p')} – {mclose.strftime('%I:%M %p')}\n"
        f"Total decisions: {total}\n"
        f"Approved: {approved} | Pending: {pending}\n"
        f"Pullback threshold: {_state['pullback_pct']}%"
        f"{schedule_note}",
        parse_mode="Markdown",
    )


async def cmd_start_system(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start_system — the ONLY way to clear kill_system."""
    if not _state["kill_system"]:
        await update.message.reply_text(
            "ℹ️ System is not halted. Nothing to restart."
        )
        return

    if _state["cycle_running"]:
        # Queue restart for after cycle finishes
        _state["pending_start_system"] = True
        await update.message.reply_text(
            "⏳ A cycle is currently running. System will restart once it completes."
        )
        return

    _state["kill_system"]          = False
    _state["pending_start_system"] = False
    await _save_state()

    # Check if interval already set today
    if (_state["today_date"] == _today_str() and
            _state["interval_minutes"] is not None):
        await update.message.reply_text(
            f"✅ System restarted. Resuming with {_state['interval_minutes']}-min interval."
        )
    else:
        _state["today_date"]       = _today_str()
        _state["interval_minutes"] = None
        await _save_state()
        # Re-send interval prompt (late start)
        app = context.application
        await _send_interval_prompt(app)

    await _log("info", "System restarted via /start_system")


async def cmd_resume_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/resume_today — override kill_day and resume trading today."""
    if not _state["kill_day"]:
        await update.message.reply_text("ℹ️ Kill Day is not active.")
        return
    _state["kill_day"] = False
    await _save_state()
    await update.message.reply_text(
        "✅ Kill Day overridden. Trading resumes today.\n"
        "Use the interval prompt to pick a schedule, or /check for pending decisions."
    )
    await _log("info", "kill_day cleared via /resume_today")


async def cmd_declare_holiday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/declare_holiday — mark today as a holiday."""
    today = _today_str()
    _state["special_days"][today] = {"holiday": True}
    _state["kill_day"] = True
    await _save_state()
    await update.message.reply_text(
        f"📅 Today ({today}) declared as a market holiday. No trading today."
    )
    await _log("info", f"Holiday declared for {today}")


async def cmd_special_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/special_day — declare tomorrow as an open market day with custom hours."""
    tomorrow = (_now_ist().date() + timedelta(days=1)).isoformat()
    await update.message.reply_text(
        f"📅 Declaring *{tomorrow}* as a special market day.\n\n"
        "Reply with open and close times in format: `HH:MM HH:MM`\n"
        "Example: `10:00 13:00`\n\n"
        "Or type `default` to use standard hours (9:15 AM – 3:30 PM).",
        parse_mode="Markdown",
    )
    context.user_data["awaiting_special_day"] = tomorrow


async def cmd_cancel_special_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/cancel_special_day — cancel a previously declared special day."""
    tomorrow = (_now_ist().date() + timedelta(days=1)).isoformat()
    if tomorrow in _state["special_days"]:
        del _state["special_days"][tomorrow]
        await _save_state()
        await update.message.reply_text(f"✅ Special day for {tomorrow} cancelled.")
    else:
        await update.message.reply_text(f"ℹ️ No special day declared for {tomorrow}.")


async def cmd_set_pullback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/set_pullback <pct> — set Track & Optimize pullback threshold."""
    try:
        pct = float(context.args[0])
        if not (0.1 <= pct <= 10.0):
            raise ValueError
        _state["pullback_pct"] = pct
        await _save_state()
        await update.message.reply_text(
            f"✅ Pullback threshold set to {pct}%."
        )
    except (IndexError, ValueError):
        await update.message.reply_text(
            f"Usage: /set_pullback <percent>\nExample: /set_pullback 0.5\n"
            f"Current: {_state['pullback_pct']}%"
        )

# ---------------------------------------------------------------------------
# Text message handler (special-day hour input)
# ---------------------------------------------------------------------------

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-text replies (special day hours, Zerodha token paste, etc.)."""
    if _state["kill_system"] and not context.user_data.get("awaiting_zerodha_token"):
        await update.message.reply_text(
            "🛑 System is halted. Use /start_system to restart."
        )
        return

    # Zerodha token paste
    if context.user_data.get("awaiting_zerodha_token"):
        await _handle_zerodha_token_paste(update, context)
        return

    if context.user_data.get("awaiting_upstox_token"):
        import re, httpx
        from pathlib import Path
        from dotenv import set_key
        raw   = update.message.text.strip()
        match = re.search(r"[?&]code=([A-Za-z0-9_\-]+)", raw)
        code  = match.group(1) if match else raw
        api_key    = os.environ.get("UPSTOX_API_KEY", "")
        api_secret = os.environ.get("UPSTOX_API_SECRET", "")
        redirect   = os.environ.get("UPSTOX_REDIRECT_URL", "https://zeta-portfolio.duckdns.org")
        try:
            resp = httpx.post(
                "https://api.upstox.com/v2/login/authorization/token",
                data={"code": code, "client_id": api_key, "client_secret": api_secret,
                      "redirect_uri": redirect, "grant_type": "authorization_code"},
                headers={"Accept": "application/json"}, timeout=15,
            )
            resp.raise_for_status()
            token = resp.json()["access_token"]
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {e}")
            context.user_data.pop("awaiting_upstox_token", None)
            return
        env_path = Path(__file__).resolve().parent.parent / ".env"
        set_key(str(env_path), "UPSTOX_ACCESS_TOKEN", token)
        os.environ["UPSTOX_ACCESS_TOKEN"] = token
        context.user_data.pop("awaiting_upstox_token", None)
        await update.message.reply_text("✅ *Upstox token refreshed!*", parse_mode="Markdown")
        return

    awaiting = context.user_data.get("awaiting_special_day")
    if awaiting:
        text = update.message.text.strip()
        if text.lower() == "default":
            _state["special_days"][awaiting] = {"open": "09:15", "close": "15:30"}
            await _save_state()
            context.user_data.pop("awaiting_special_day", None)
            await update.message.reply_text(
                f"✅ {awaiting} set as special market day with standard hours."
            )
        else:
            parts = text.split()
            if len(parts) == 2:
                try:
                    open_h, open_m   = map(int, parts[0].split(":"))
                    close_h, close_m = map(int, parts[1].split(":"))
                    _state["special_days"][awaiting] = {
                        "open":  f"{open_h:02d}:{open_m:02d}",
                        "close": f"{close_h:02d}:{close_m:02d}",
                    }
                    await _save_state()
                    context.user_data.pop("awaiting_special_day", None)

                    # Show cycle schedule preview
                    interval = _state.get("interval_minutes") or 30
                    cycles   = _compute_cycle_schedule(interval)
                    await update.message.reply_text(
                        f"✅ {awaiting} declared as special market day.\n"
                        f"Hours: {parts[0]} – {parts[1]}\n"
                        f"Preview ({interval}-min interval): {_format_schedule(cycles)}"
                    )
                except ValueError:
                    await update.message.reply_text(
                        "⚠️ Invalid format. Use `HH:MM HH:MM` (e.g. `10:00 13:00`)"
                    )
            else:
                await update.message.reply_text(
                    "⚠️ Please send two times: open and close. Example: `10:00 13:00`"
                )
        return

    # Any other message — just acknowledge
    await update.message.reply_text(
        "ℹ️ Use /check, /status, /start_system, or tap buttons in decision messages."
    )

# ---------------------------------------------------------------------------
# Callback query handler (buttons)
# ---------------------------------------------------------------------------

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch all inline button taps."""
    query = update.callback_query
    await query.answer()
    data  = query.data

    # ── Guard: kill_system blocks most actions ──────────────────────────────
    if _state["kill_system"] and not data.startswith(("confirm_kill_system_yes",
                                                       "confirm_kill_system_no",
                                                       "manual_start")):
        await query.edit_message_text(
            "🛑 System is halted. Use /start_system in the terminal to restart."
        )
        return

    # ── Guard: kill_day blocks approval/rejection ───────────────────────────
    if _state["kill_day"] and data.startswith(("approve_", "batch_approve_")):
        await query.edit_message_text(
            "🌙 Kill Day is active. No new orders today.\n"
            "Use /resume_today to override."
        )
        return

    # ── Interval selection (morning prompt) ─────────────────────────────────
    if data.startswith("interval_"):
        await _handle_interval_selection(query, context, data)

    # ── Change interval (from menu) ─────────────────────────────────────────
    elif data == "menu_change_interval":
        await query.edit_message_text(
            "🔁 Select new interval (takes effect after current cycle):",
            reply_markup=_interval_keyboard("change_interval"),
        )

    elif data.startswith("change_interval_"):
        await _handle_change_interval(query, data)

    # ── Approve single decision ──────────────────────────────────────────────
    elif data.startswith("approve_"):
        await _handle_approve(query, data)

    # ── Reject single decision ───────────────────────────────────────────────
    elif data.startswith("reject_"):
        await _handle_reject(query, data)

    # ── Menu open ───────────────────────────────────────────────────────────
    elif data.startswith("menu_") and not any(data.startswith(p) for p in (
        "menu_change_interval", "menu_show_remaining_",
        "menu_back_", "menu_kill_day", "menu_kill_system",
    )):
        batch_id = data[5:]
        batch_label = _now_ist().strftime("%I:%M %p")
        await query.edit_message_reply_markup(
            reply_markup=_menu_keyboard(batch_id, batch_label),
        )

    # ── Show remaining ───────────────────────────────────────────────────────
    elif data.startswith("menu_show_remaining_"):
        batch_id = data[len("menu_show_remaining_"):]
        await _handle_show_remaining(query, context, batch_id)

    # ── Go back ──────────────────────────────────────────────────────────────
    elif data.startswith("menu_back_"):
        batch_id = data[len("menu_back_"):]
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("☰ Menu", callback_data=f"menu_{batch_id}"),
            ]])
        )

    # ── Approve batch ────────────────────────────────────────────────────────
    elif data.startswith("batch_approve_"):
        batch_id = data[len("batch_approve_"):]
        await _handle_batch_approve(query, batch_id)

    # ── Kill batch ───────────────────────────────────────────────────────────
    elif data.startswith("batch_kill_"):
        batch_id = data[len("batch_kill_"):]
        await _handle_batch_kill(query, batch_id)

    # ── Kill Day ─────────────────────────────────────────────────────────────
    elif data == "menu_kill_day":
        await query.edit_message_text(
            "🌙 *Kill Day* — stops trading for the rest of today.\n"
            "All pending decisions will be rejected.\n"
            "Trading resumes tomorrow at 9 AM.\n\nAre you sure?",
            reply_markup=_confirm_keyboard("confirm_kill_day_yes", "confirm_kill_day_no"),
            parse_mode="Markdown",
        )

    elif data == "confirm_kill_day_yes":
        _state["kill_day"] = True
        await _save_state()
        rejected = await _reject_all_pending()
        await query.edit_message_text(
            f"🌙 Kill Day activated. {rejected} pending decision(s) rejected.\n"
            "Trading will resume tomorrow at 9 AM."
        )
        await _log("warning", f"Kill Day activated — {rejected} decisions rejected")

    elif data == "confirm_kill_day_no":
        await query.edit_message_text("↩️ Kill Day cancelled.")

    # ── Kill System ──────────────────────────────────────────────────────────
    elif data == "menu_kill_system":
        await query.edit_message_text(
            "🛑 *Kill System* — halts Zeta *indefinitely*.\n"
            "All pending decisions will be rejected.\n"
            "You will need to run `/start_system` in the terminal to restart.\n\n"
            "*Are you absolutely sure?*",
            reply_markup=_confirm_keyboard("confirm_kill_system_yes", "confirm_kill_system_no"),
            parse_mode="Markdown",
        )

    elif data == "confirm_kill_system_yes":
        _state["kill_system"] = True
        await _save_state()
        rejected = await _reject_all_pending()
        await query.edit_message_text(
            f"🛑 Kill System activated. {rejected} pending decision(s) rejected.\n"
            "Run `/start_system` in the terminal to restart."
        )
        await _log("critical", f"Kill System activated — {rejected} decisions rejected")

    elif data == "confirm_kill_system_no":
        await query.edit_message_text("↩️ Kill System cancelled.")

    elif data == "back_to_menu":
        await query.edit_message_text(
            "☰ What would you like to do?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Change Interval", callback_data="menu_change_interval")],
                [InlineKeyboardButton("🌙 Kill Day",        callback_data="menu_kill_day")],
                [InlineKeyboardButton("🛑 Kill System",     callback_data="menu_kill_system")],
                [InlineKeyboardButton("🔑 Refresh Zerodha", callback_data="trigger_refresh_token")],
            ]),
        )

    elif data == "back_to_decision":
        await query.edit_message_text(
            "Tap a button to act on this decision:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data="noop"),
                    InlineKeyboardButton("❌ Reject",  callback_data="noop"),
                ],
                [InlineKeyboardButton("☰ Menu", callback_data="noop")],
            ]),
        )

    # ── Manual start (missed 9 AM prompt) ────────────────────────────────────
    elif data == "manual_start":
        if _state["kill_system"]:
            await query.edit_message_text(
                "🛑 System is halted. Use /start_system in the terminal."
            )
            return
        _state["kill_day"] = False
        await query.edit_message_text(
            "▶️ *Manual start* — select your interval:",
            reply_markup=_interval_keyboard("interval"),
            parse_mode="Markdown",
        )

    # ── Price movement: Place Now ────────────────────────────────────────────
    elif data.startswith("place_now_"):
        parts    = data.split("_", 3)
        symbol   = parts[2]
        batch_id = parts[3]
        await _do_approve(query, symbol, batch_id)

    # ── Price movement: Track & Optimize ────────────────────────────────────
    elif data.startswith("track_"):
        parts          = data.split("_", 3)
        symbol         = parts[1]
        batch_id       = parts[2]
        analysis_price = float(parts[3])
        await _start_tracking(query, context, symbol, batch_id, analysis_price)

    # ── Price movement: Skip ─────────────────────────────────────────────────
    elif data.startswith("skip_"):
        parts  = data.split("_", 2)
        symbol = parts[1]
        await query.edit_message_text(f"⏭ Skipped {symbol} for this cycle.")

    # ── Stop tracking ────────────────────────────────────────────────────────
    elif data.startswith("stop_track_"):
        symbol = data[len("stop_track_"):]
        _state["tracking"].pop(symbol, None)
        await query.edit_message_text(f"⏹ Stopped tracking {symbol}.")

    elif data == "trigger_refresh_token":
        api_key = os.environ.get("ZERODHA_API_KEY", "").strip()
        if not api_key:
            await query.edit_message_text("⚠️ ZERODHA_API_KEY not set in .env.")
            return
        try:
            from kiteconnect import KiteConnect
            kite      = KiteConnect(api_key=api_key)
            login_url = kite.login_url()
        except Exception as e:
            await query.edit_message_text(f"⚠️ Could not generate login URL: {e}")
            return
        context.user_data["awaiting_zerodha_token"] = True
        await query.edit_message_text(
            f"🔑 *Zerodha Token Refresh*\n\n"
            f"1. Tap the link below to log in:\n{login_url}\n\n"
            f"2. After login, copy the full redirect URL from your browser address bar and paste it here.",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------

async def _handle_interval_selection(query, context, data: str) -> None:
    minutes = int(data.split("_")[1])
    now     = _now_ist()
    mopen, mclose = _market_hours_today()
    cycles  = _compute_cycle_schedule(minutes)

    if not cycles:
        await query.edit_message_text(
            f"⚠️ No cycles fit before market close ({mclose.strftime('%I:%M %p')}) "
            f"with a {minutes}-min interval.\nPlease select a shorter interval.",
            reply_markup=_interval_keyboard("interval"),
        )
        return

    is_late = now.time() > mopen
    warning = ""
    if is_late:
        warning = (
            f"\n⚠️ *Late start* — market opened at {mopen.strftime('%I:%M %p')}."
        )
    if len(cycles) == 1:
        warning += f"\n⚠️ Only 1 cycle will run today."

    _state["interval_minutes"] = minutes
    _state["today_date"]       = _today_str()
    await _save_state()

    await query.edit_message_text(
        f"✅ Interval set to *{minutes} min*{warning}\n\n"
        f"📅 Today's schedule: {_format_schedule(cycles)}",
        parse_mode="Markdown",
    )
    await _log("info", f"Interval set to {minutes} min")


async def _handle_change_interval(query, data: str) -> None:
    minutes = int(data.split("_")[2])
    now     = _now_ist()
    _, mclose = _market_hours_today()
    cycles  = _compute_cycle_schedule(minutes)

    if not cycles:
        await query.edit_message_text(
            f"⚠️ No cycles fit before market close ({mclose.strftime('%I:%M %p')}) "
            f"with {minutes}-min interval. Interval not changed.",
            reply_markup=_interval_keyboard("change_interval"),
        )
        return

    # Warn if only 1 cycle or last cycle is very close to close
    last_cycle = cycles[-1]
    IST = timezone(timedelta(hours=5, minutes=30))
    last_dt  = datetime.combine(now.date(), last_cycle, tzinfo=IST)
    close_dt = datetime.combine(now.date(), mclose, tzinfo=IST)
    mins_before_close = (close_dt - last_dt).seconds // 60

    warning = ""
    if mins_before_close < minutes:
        warning = (
            f"\n⚠️ Last cycle at {last_cycle.strftime('%I:%M %p')} is "
            f"only {mins_before_close} min before close — confirm?"
        )
        await query.edit_message_text(
            f"Changing interval to *{minutes} min*.{warning}\n\n"
            f"Schedule: {_format_schedule(cycles)}\n\nProceed?",
            reply_markup=_confirm_keyboard(
                f"confirm_change_interval_{minutes}",
                "cancel_change_interval",
            ),
            parse_mode="Markdown",
        )
        return

    _state["interval_minutes"] = minutes
    await _save_state()
    await query.edit_message_text(
        f"✅ Interval changed to *{minutes} min* (takes effect next cycle).\n"
        f"New schedule: {_format_schedule(cycles)}",
        parse_mode="Markdown",
    )
    await _log("info", f"Interval changed to {minutes} min mid-day")


async def _do_approve(query, symbol: str, batch_id: str) -> None:
    """Execute approval: update MongoDB, trigger order placement."""
    try:
        from ui.utils.db import update_decision_status
        update_decision_status(symbol, True)
    except Exception as e:
        await _log("error", f"Failed to approve {symbol}: {e}", symbol=symbol)

    await query.edit_message_text(
        f"✅ *{symbol}* approved — order will be placed on next cycle.",
        parse_mode="Markdown",
    )
    await _log("info", f"Decision approved: {symbol}", symbol=symbol, cycle_id=batch_id)


async def _handle_approve(query, data: str) -> None:
    parts    = data.split("_", 2)
    symbol   = parts[1]
    batch_id = parts[2] if len(parts) > 2 else ""
    await _do_approve(query, symbol, batch_id)


async def _handle_reject(query, data: str) -> None:
    parts    = data.split("_", 2)
    symbol   = parts[1]
    batch_id = parts[2] if len(parts) > 2 else ""
    try:
        from ui.utils.db import update_decision_status
        update_decision_status(symbol, False)
    except Exception as e:
        await _log("error", f"Failed to reject {symbol}: {e}", symbol=symbol)
    await query.edit_message_text(f"❌ *{symbol}* rejected.", parse_mode="Markdown")
    await _log("info", f"Decision rejected: {symbol}", symbol=symbol, cycle_id=batch_id)


async def _handle_show_remaining(query, context, batch_id: str) -> None:
    docs = await _get_batch_decisions(batch_id)
    if not docs:
        await query.answer("✅ No pending decisions in this batch.", show_alert=True)
        return
    lines = [f"📋 *Remaining in this batch ({len(docs)}):*"]
    for d in docs:
        lines.append(f"• {d['symbol']} — `{d.get('decision', '?')}` ({d.get('confidence', 0):.0f}%)")
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="\n".join(lines),
        parse_mode="Markdown",
    )


async def _handle_batch_approve(query, batch_id: str) -> None:
    docs = await _get_batch_decisions(batch_id)
    if not docs:
        await query.edit_message_text("ℹ️ No pending decisions in this batch.")
        return

    approved_count = 0
    for d in docs:
        try:
            from ui.utils.db import update_decision_status
            update_decision_status(d["symbol"], True)
            approved_count += 1
        except Exception as e:
            await _log("error", f"Batch approve failed for {d['symbol']}: {e}")

    batch_label = _now_ist().strftime("%I:%M %p")
    await query.edit_message_text(
        f"✅ Batch [{batch_label}] — {approved_count} decision(s) approved."
    )
    await _log("info", f"Batch {batch_id} approved — {approved_count} decisions", cycle_id=batch_id)


async def _handle_batch_kill(query, batch_id: str) -> None:
    rejected = await _reject_batch_decisions(batch_id)
    batch_label = _now_ist().strftime("%I:%M %p")
    await query.edit_message_text(
        f"💀 Batch [{batch_label}] killed — {rejected} decision(s) rejected."
    )
    await _log("warning", f"Batch {batch_id} killed — {rejected} decisions", cycle_id=batch_id)

# ---------------------------------------------------------------------------
# Track & Optimize
# ---------------------------------------------------------------------------

async def _start_tracking(query, context, symbol: str,
                           batch_id: str, analysis_price: float) -> None:
    """Start background price tracking for a symbol."""
    live_price = await _get_live_price(symbol)
    if live_price is None:
        await query.edit_message_text(
            f"⚠️ Could not fetch live price for {symbol}. Tracking cancelled."
        )
        return

    _state["tracking"][symbol] = {
        "peak_price":     live_price,
        "buy_price":      analysis_price,
        "batch_id":       batch_id,
        "analysis_price": analysis_price,
    }
    await query.edit_message_text(
        f"📈 *Tracking {symbol}* — current ₹{live_price:.2f}\n"
        f"Will alert on {_state['pullback_pct']}% dip from peak.\n"
        f"Tracking stops at next cycle or market close.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"⏹ Stop Tracking {symbol}",
                                 callback_data=f"stop_track_{symbol}"),
        ]]),
        parse_mode="Markdown",
    )
    await _log("info", f"Started tracking {symbol} from ₹{live_price:.2f}", symbol=symbol)

    # Schedule the tracking loop
    context.application.job_queue.run_repeating(
        callback=lambda ctx: _tracking_tick(ctx, symbol),
        interval=TRACKING_CHECK_INTERVAL,
        name=f"track_{symbol}",
    )


async def _tracking_tick(context: ContextTypes.DEFAULT_TYPE, symbol: str) -> None:
    """Called every TRACKING_CHECK_INTERVAL seconds for a tracked symbol."""
    if symbol not in _state["tracking"]:
        # Tracking was stopped; remove job
        jobs = context.job_queue.get_jobs_by_name(f"track_{symbol}")
        for j in jobs:
            j.schedule_removal()
        return

    if _state["kill_system"] or _state["kill_day"] or not _is_market_open():
        _state["tracking"].pop(symbol, None)
        jobs = context.job_queue.get_jobs_by_name(f"track_{symbol}")
        for j in jobs:
            j.schedule_removal()
        if CHAT_ID:
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=f"⏹ Stopped tracking *{symbol}* — market closed or system halted.",
                parse_mode="Markdown",
            )
        return

    live_price = await _get_live_price(symbol)
    if live_price is None:
        return  # API hiccup, wait next tick

    info = _state["tracking"][symbol]
    buy_price  = info["buy_price"]
    peak_price = info["peak_price"]

    # Fell below buy price → stop
    if live_price < buy_price:
        _state["tracking"].pop(symbol, None)
        jobs = context.job_queue.get_jobs_by_name(f"track_{symbol}")
        for j in jobs:
            j.schedule_removal()
        if CHAT_ID:
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"🔴 *{symbol}* fell below buy price "
                    f"(₹{live_price:.2f} < ₹{buy_price:.2f}). Tracking stopped."
                ),
                parse_mode="Markdown",
            )
        return

    # Update peak
    if live_price > peak_price:
        info["peak_price"] = live_price
        peak_price = live_price

    # Check pullback from peak
    pullback_pct = (peak_price - live_price) / peak_price * 100
    if pullback_pct >= _state["pullback_pct"]:
        _state["tracking"].pop(symbol, None)
        jobs = context.job_queue.get_jobs_by_name(f"track_{symbol}")
        for j in jobs:
            j.schedule_removal()
        if CHAT_ID:
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"📉 *{symbol}* peaked at ₹{peak_price:.2f}, "
                    f"now ₹{live_price:.2f} (−{pullback_pct:.2f}%).\n"
                    "Running fresh analysis... _(will appear as new batch)_"
                ),
                parse_mode="Markdown",
            )
        await _log("info",
                   f"Pullback detected for {symbol}: peak ₹{peak_price:.2f} → ₹{live_price:.2f}",
                   symbol=symbol)
        # Signal main.py to run a fresh single-stock cycle
        # (main.py polls this flag)
        if _DB_AVAILABLE and _state_col is not None:
            try:
                await _state_col.update_one(
                    {"_id": "zeta_state"},
                    {"$push": {"reanalyse_queue": symbol}},
                    upsert=True,
                )
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Daily midnight reset (scheduled job)
# ---------------------------------------------------------------------------

async def _token_refresh_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs at 6:15 AM IST — remind user to refresh Zerodha token."""
    if CHAT_ID:
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "🔑 *Daily token refresh needed*\n\n"
                "• /refresh_token — Zerodha\n"
                "• /refresh_upstox_token — Upstox\n\n"
                "Both expire at 6 AM IST daily."
            ),
            parse_mode="Markdown",
        )


async def _midnight_reset(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs just after midnight — reset kill_day and interval for the new day."""
    _state["kill_day"]         = False
    _state["interval_minutes"] = None
    _state["today_date"]       = _today_str()
    _state["tracking"]         = {}
    await _save_state()
    await _log("info", "Midnight reset — kill_day cleared, interval reset")


async def _morning_prompt(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs at 9:00 AM — send interval prompt if trading day."""
    _schedule_interval_reprompt._attempt = 1
    await _send_interval_prompt(context.application)

# ---------------------------------------------------------------------------
# App builder
# ---------------------------------------------------------------------------

def build_app():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa

    async def _error_handler(update, context):
        logger.error(f"Unhandled exception: {context.error}", exc_info=context.error)

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_error_handler(_error_handler)

    # Commands
    app.add_handler(CommandHandler("check",              cmd_check))
    app.add_handler(CommandHandler("start",              cmd_check))
    app.add_handler(CommandHandler("status",             cmd_status))
    app.add_handler(CommandHandler("start_system",       cmd_start_system))
    app.add_handler(CommandHandler("resume_today",       cmd_resume_today))
    app.add_handler(CommandHandler("declare_holiday",    cmd_declare_holiday))
    app.add_handler(CommandHandler("special_day",        cmd_special_day))
    app.add_handler(CommandHandler("cancel_special_day", cmd_cancel_special_day))
    app.add_handler(CommandHandler("set_pullback",       cmd_set_pullback))
    app.add_handler(CommandHandler("refresh_token",      cmd_refresh_token))
    app.add_handler(CommandHandler("refresh_upstox_token", cmd_refresh_upstox_token))
    app.add_handler(CommandHandler("help", cmd_help))

    # Buttons
    app.add_handler(CallbackQueryHandler(button_handler))

    # Free text (for special day hour input)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Scheduled jobs via PTB job queue
    jq = app.job_queue
    if jq:
        # 6:15 AM token refresh reminder (Mon-Fri)
        jq.run_daily(
            _token_refresh_reminder,
            time(6, 15, tzinfo=timezone(timedelta(hours=5, minutes=30))),
            days=(0, 1, 2, 3, 4),
            name="token_refresh_reminder",
        )
        # Upstox API refresh
        jq.run_daily(
            _token_refresh_reminder,
            time(6, 20, tzinfo=timezone(timedelta(hours=5, minutes=30))),
            days=(0, 1, 2, 3, 4),
            name="upstox_token_reminder",
        )
        # 9 AM morning prompt (Mon-Fri)
        jq.run_daily(
            _morning_prompt,
            time(9, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))),
            days=(0, 1, 2, 3, 4),
            name="morning_prompt",
        )
        # Re-ask every 15 min from 9:15 AM to 9:45 AM
        for offset_min in (15, 30, 45):
            jq.run_daily(
                _schedule_interval_reprompt,
                time(9, offset_min, tzinfo=timezone(timedelta(hours=5, minutes=30))),
                days=(0, 1, 2, 3, 4),
                name=f"interval_reprompt_{offset_min}",
            )
        # Final "no response" message at 10:00 AM
        jq.run_daily(
            _morning_prompt,  # will trigger "no response" path if interval still None
            time(10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))),
            days=(0, 1, 2, 3, 4),
            name="interval_final",
        )
        # Midnight reset
        jq.run_daily(
            _midnight_reset,
            time(0, 1, tzinfo=timezone(timedelta(hours=5, minutes=30))),
            name="midnight_reset",
        )

    return app


if __name__ == "__main__":
    import asyncio as _asyncio

    async def _run():
        await _load_state()
        app = build_app()
        print("Zeta Telegram Bot started.")
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()

    _asyncio.run(_run())

