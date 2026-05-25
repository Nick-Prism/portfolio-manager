"""
mcp/upstox_client.py — Upstox API client for market data.

Upstox provides free real-time and historical data via their V3 API.
Get credentials from: https://developer.upstox.com

Required env vars:
    UPSTOX_API_KEY
    UPSTOX_API_SECRET
    UPSTOX_ACCESS_TOKEN  — generated daily via upstox_login.py
"""

import os
import httpx
from typing import Optional

UPSTOX_BASE = "https://api.upstox.com/v2"


def _headers() -> dict:
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def get_ltp(instrument_key: str) -> Optional[float]:
    """
    Get Last Traded Price for an instrument.
    instrument_key format: NSE_EQ|INE040A01034 (use get_instrument_key() to resolve)
    """
    try:
        resp = httpx.get(
            f"{UPSTOX_BASE}/market-quote/ltp",
            params={"instrument_key": instrument_key},
            headers=_headers(),
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", {})
        # Response: {"data": {"NSE_EQ|INE040A01034": {"last_price": 1234.5}}}
        for key, val in data.items():
            return float(val.get("last_price", 0))
    except Exception:
        return None


def get_historical_candles(
    instrument_key: str,
    interval: str = "day",
    from_date: str = "",
    to_date: str = "",
) -> Optional[list]:
    """
    Get historical OHLCV candles.
    interval: "day", "week", "month", "1minute", "30minute" etc.
    dates: "YYYY-MM-DD"
    """
    try:
        resp = httpx.get(
            f"{UPSTOX_BASE}/historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}",
            headers=_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("data", {}).get("candles", [])
    except Exception:
        return None


# Instrument key map for common NSE stocks
# Format: NSE_EQ|ISIN
# Get full list from: https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz
UPSTOX_INSTRUMENT_KEYS: dict[str, str] = {
    "RELIANCE":   "NSE_EQ|INE002A01018",
    "TCS":        "NSE_EQ|INE467B01029",
    "INFY":       "NSE_EQ|INE009A01021",
    "HDFCBANK":   "NSE_EQ|INE040A01034",
    "ITC":        "NSE_EQ|INE154A01025",
    "TATAMOTORS": "NSE_EQ|INE155A01022",
    "WIPRO":      "NSE_EQ|INE075A01022",
    "SBIN":       "NSE_EQ|INE062A01020",
    "AXISBANK":   "NSE_EQ|INE238A01034",
    "ICICIBANK":  "NSE_EQ|INE090A01021",
    "BAJFINANCE": "NSE_EQ|INE296A01024",
    "HINDUNILVR": "NSE_EQ|INE030A01027",
    "KOTAKBANK":  "NSE_EQ|INE237A01028",
    "LT":         "NSE_EQ|INE018A01030",
    "MARUTI":     "NSE_EQ|INE585B01010",
    "BHEL":       "NSE_EQ|INE257A01026",
    "GTL":        "NSE_EQ|INE043A01012",
    "GTLINFRA":   "NSE_EQ|INE221H01019",
    "MOMENTUM":   "NSE_EQ|INE274J01014",
}


def get_instrument_key(symbol: str) -> Optional[str]:
    return UPSTOX_INSTRUMENT_KEYS.get(symbol.upper())
