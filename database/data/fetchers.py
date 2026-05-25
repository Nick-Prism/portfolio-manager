"""
database/data/fetchers.py — Market data and fundamentals fetcher.

Price history  : Upstox V3 API (primary — real-time, official, free)
Current price  : Upstox LTP → NSE scrape → yfinance (fallback chain)
BSE price      : yfinance .BO (Upstox covers NSE; BSE via yfinance is fine for spread check)
Fundamentals   : Screener.in scraper
"""

import asyncio
import os
import httpx
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import Optional


# ── Upstox ─────────────────────────────────────────────────────────────────────

UPSTOX_BASE = "https://api.upstox.com/v2"

# Instrument keys for your holdings + common stocks
# Full list: https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz
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


def _upstox_headers() -> dict:
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _upstox_available() -> bool:
    return bool(os.getenv("UPSTOX_ACCESS_TOKEN", "").strip())


def _get_instrument_key(symbol: str) -> Optional[str]:
    return UPSTOX_INSTRUMENT_KEYS.get(symbol.upper())


def _upstox_get_ltp(symbol: str) -> Optional[float]:
    key = _get_instrument_key(symbol)
    if not key:
        return None
    try:
        resp = httpx.get(
            f"{UPSTOX_BASE}/market-quote/ltp",
            params={"instrument_key": key},
            headers=_upstox_headers(),
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", {})
        for _, val in data.items():
            price = float(val.get("last_price", 0))
            print(f"[fetchers] ✅ Upstox LTP for {symbol}: ₹{price:.2f}")
            return price
    except Exception as e:
        print(f"[fetchers] ⚠️  Upstox LTP failed for {symbol}: {e}")
    return None


def _upstox_get_history(symbol: str, period: str) -> Optional[pd.DataFrame]:
    key = _get_instrument_key(symbol)
    if not key:
        return None

    period_days = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730}
    days      = period_days.get(period, 180)
    to_date   = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        resp = httpx.get(
            f"{UPSTOX_BASE}/historical-candle/{key}/day/{to_date}/{from_date}",
            headers=_upstox_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            return None

        candles = resp.json().get("data", {}).get("candles", [])
        if not candles:
            return None

        # Upstox candle format: [timestamp, open, high, low, close, volume, oi]
        df = pd.DataFrame(candles, columns=["Date", "Open", "High", "Low", "Close", "Volume", "OI"])
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        df = df[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
        df = df.dropna()
        print(f"[fetchers] ✅ Upstox history for {symbol}: {len(df)} rows")
        return df
    except Exception as e:
        print(f"[fetchers] ⚠️  Upstox history failed for {symbol}: {e}")
    return None


# ── NSE scrape (secondary fallback) ───────────────────────────────────────────

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
NSE_BASE        = "https://www.nseindia.com"
NSE_HISTORY_URL = "https://www.nseindia.com/api/historical/cm/equity"
NSE_QUOTE_URL   = "https://www.nseindia.com/api/quote-equity"


def _nse_date(dt: datetime) -> str:
    return dt.strftime("%d-%m-%Y")


def _nse_get(url: str, params: dict) -> httpx.Response:
    with httpx.Client(headers=NSE_HEADERS, timeout=15, follow_redirects=True) as client:
        client.get(NSE_BASE)
        return client.get(url, params=params)


def _nse_get_history(symbol: str, period: str) -> Optional[pd.DataFrame]:
    period_days = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730}
    days      = period_days.get(period, 180)
    end_date  = datetime.now()
    start_date = end_date - timedelta(days=days)
    try:
        resp = _nse_get(NSE_HISTORY_URL, {
            "symbol": symbol.upper(), "series": "EQ",
            "from": _nse_date(start_date), "to": _nse_date(end_date),
        })
        if resp.status_code != 200:
            raise ValueError(f"NSE API returned {resp.status_code}")
        data = resp.json().get("data", [])
        if not data:
            raise ValueError("empty")
        df = pd.DataFrame(data).rename(columns={
            "CH_TIMESTAMP": "Date", "CH_OPENING_PRICE": "Open",
            "CH_TRADE_HIGH_PRICE": "High", "CH_TRADE_LOW_PRICE": "Low",
            "CH_CLOSING_PRICE": "Close", "CH_TOT_TRADED_QTY": "Volume",
        })
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        df = df[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce").dropna()
        print(f"[fetchers] ✅ NSE history for {symbol}: {len(df)} rows")
        return df
    except Exception as e:
        print(f"[fetchers] ⚠️  NSE history failed for {symbol}: {e}")
        return None


def _nse_get_ltp(symbol: str) -> Optional[float]:
    try:
        resp = _nse_get(NSE_QUOTE_URL, {"symbol": symbol.upper()})
        if resp.status_code != 200:
            raise ValueError(f"NSE returned {resp.status_code}")
        price = float(resp.json()["priceInfo"]["lastPrice"])
        print(f"[fetchers] ✅ NSE LTP for {symbol}: ₹{price:.2f}")
        return price
    except Exception as e:
        print(f"[fetchers] ⚠️  NSE LTP failed for {symbol}: {e}")
        return None


# ── yfinance (last resort) ─────────────────────────────────────────────────────

def _yf_get_history(symbol: str, period: str, suffix: str = ".NS") -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf
        df = yf.Ticker(symbol.upper() + suffix).history(period=period, auto_adjust=True)
        if df.empty:
            return None
        df.columns = [c.title() for c in df.columns]
        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[cols].dropna()
        print(f"[fetchers] ✅ yfinance history for {symbol}{suffix}: {len(df)} rows")
        return df
    except Exception as e:
        print(f"[fetchers] ❌ yfinance history failed for {symbol}{suffix}: {e}")
        return None


def _yf_get_ltp(symbol: str, suffix: str = ".NS") -> Optional[float]:
    try:
        import yfinance as yf
        price = float(yf.Ticker(symbol.upper() + suffix).fast_info.last_price)
        print(f"[fetchers] ✅ yfinance LTP for {symbol}{suffix}: ₹{price:.2f}")
        return price
    except Exception:
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

def get_price_data(symbol: str, period: str = "6mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    """Fetch OHLCV history. Tries Upstox → NSE scrape → yfinance."""
    if _upstox_available():
        df = _upstox_get_history(symbol, period)
        if df is not None:
            return df

    df = _nse_get_history(symbol, period)
    if df is not None:
        return df

    return _yf_get_history(symbol, period)


def get_current_price(symbol: str) -> Optional[float]:
    """Get real-time NSE price. Tries Upstox → NSE scrape → yfinance."""
    if _upstox_available():
        price = _upstox_get_ltp(symbol)
        if price:
            return price

    price = _nse_get_ltp(symbol)
    if price:
        return price

    return _yf_get_ltp(symbol, ".NS")


def get_bse_price(symbol: str) -> Optional[float]:
    """Get BSE price via yfinance .BO (sufficient for spread check)."""
    return _yf_get_ltp(symbol, ".BO")


def get_exchange_spread(symbol: str) -> Optional[dict]:
    """Compute NSE/BSE spread for exchange routing."""
    nse_price = get_current_price(symbol)
    bse_price = get_bse_price(symbol)

    if nse_price is None or bse_price is None:
        return None

    spread_abs = abs(nse_price - bse_price)
    spread_pct = (spread_abs / min(nse_price, bse_price)) * 100
    cheaper    = "NSE" if nse_price <= bse_price else "BSE"

    return {
        "symbol":           symbol.upper(),
        "nse_price":        round(nse_price, 2),
        "bse_price":        round(bse_price, 2),
        "spread_abs":       round(spread_abs, 2),
        "spread_pct":       round(spread_pct, 4),
        "cheaper_exchange": cheaper,
    }


# ── Fundamentals (Screener.in) ─────────────────────────────────────────────────

SCREENER_BASE    = "https://www.screener.in/company"
SCREENER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _parse_number(text: str) -> Optional[float]:
    if not text:
        return None
    try:
        return float(text.replace(",", "").replace("%", "").replace("₹", "").strip())
    except ValueError:
        return None


async def get_fundamentals(symbol: str) -> dict:
    """Scrape key ratios from Screener.in."""
    result: dict = {"symbol": symbol.upper(), "source": "screener.in"}
    for url in [
        f"{SCREENER_BASE}/{symbol.upper()}/",
        f"{SCREENER_BASE}/{symbol.upper()}/consolidated/",
    ]:
        try:
            async with httpx.AsyncClient(headers=SCREENER_HEADERS, timeout=15, follow_redirects=True) as client:
                response = await client.get(url)
            if response.status_code != 200:
                continue

            soup   = BeautifulSoup(response.text, "html.parser")
            ratios: dict[str, Optional[float]] = {}

            for li in soup.select("ul.ranges li, #top-ratios li"):
                spans = li.find_all("span")
                if len(spans) >= 2:
                    ratios[spans[0].get_text(strip=True).lower()] = _parse_number(spans[-1].get_text(strip=True))

            for label, field in {
                "stock p/e": "pe_ratio", "p/e": "pe_ratio",
                "price to book value": "pb_ratio", "p/b": "pb_ratio",
                "roe": "roe", "return on equity": "roe",
                "debt to equity": "debt_to_equity", "d/e": "debt_to_equity",
                "roce": "roce", "market cap": "market_cap_cr",
            }.items():
                for key, val in ratios.items():
                    if label in key:
                        result[field] = val
                        break

            for row in soup.select("table.data-table tr"):
                cells = row.find_all("td")
                if cells and "promoter" in cells[0].get_text(strip=True).lower():
                    result["promoter_holding_pct"] = _parse_number(cells[-1].get_text(strip=True))
                    break

            if result.get("pe_ratio") or result.get("roe"):
                print(f"[fetchers] ✅ Fundamentals for {symbol}: PE={result.get('pe_ratio')}, ROE={result.get('roe')}")
                return result
        except Exception as e:
            print(f"[fetchers] ❌ Screener failed for {symbol}: {e}")

    print(f"[fetchers] ⚠️  Fundamentals partially fetched for {symbol}")
    return result


async def get_stock_data(symbol: str) -> dict:
    price_df     = get_price_data(symbol)
    fundamentals = await get_fundamentals(symbol)
    return {"symbol": symbol.upper(), "price_df": price_df,
            "current_price": get_current_price(symbol), **fundamentals}


if __name__ == "__main__":
    async def main():
        data = await get_stock_data("HDFCBANK")
        print(f"Price: ₹{data['current_price']}, PE: {data.get('pe_ratio')}, Rows: {len(data['price_df']) if data['price_df'] is not None else 0}")
    asyncio.run(main())
