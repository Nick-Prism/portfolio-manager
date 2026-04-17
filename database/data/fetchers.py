"""
data/fetchers.py — Market data and fundamentals fetcher.

Pulls:
  - Stock price history from yfinance (NSE tickers)
  - Company fundamentals from Screener.in (PE, ROE, D/E, promoter holding)

Usage:
    from data.fetchers import get_price_data, get_fundamentals
"""

import asyncio
import httpx
import pandas as pd
import yfinance as yf
from bs4 import BeautifulSoup
from typing import Optional


# ── Helpers ────────────────────────────────────────────────────────────────────

def nse_ticker(symbol: str) -> str:
    """Convert NSE symbol to yfinance format. e.g. HDFCBANK → HDFCBANK.NS"""
    symbol = symbol.upper().strip()
    if not symbol.endswith(".NS") and not symbol.endswith(".BO"):
        return symbol + ".NS"
    return symbol


# ── Price data ─────────────────────────────────────────────────────────────────

def get_price_data(symbol: str, period: str = "6mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV price history from yfinance.

    Args:
        symbol:   NSE stock symbol, e.g. "HDFCBANK"
        period:   How far back — "1mo", "3mo", "6mo", "1y"
        interval: Candle size — "1d", "1h", "15m"

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume
        None if fetch fails.
    """
    ticker = nse_ticker(symbol)
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        if df.empty:
            print(f"[fetchers] No price data returned for {ticker}")
            return None
        df.index = pd.to_datetime(df.index)
        # Flatten multi-level columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        print(f"[fetchers] ✅ Price data fetched for {symbol}: {len(df)} rows")
        return df
    except Exception as e:
        print(f"[fetchers] ❌ Price fetch failed for {symbol}: {e}")
        return None


def get_current_price(symbol: str) -> Optional[float]:
    """
    Get the latest closing price for a symbol.
    Quick check without downloading full history.
    """
    ticker = nse_ticker(symbol)
    try:
        data = yf.Ticker(ticker)
        info = data.fast_info
        price = info.last_price
        print(f"[fetchers] ✅ Current price for {symbol}: ₹{price:.2f}")
        return float(price)
    except Exception as e:
        print(f"[fetchers] ❌ Current price fetch failed for {symbol}: {e}")
        return None


# ── Fundamentals scraper ───────────────────────────────────────────────────────

SCREENER_BASE = "https://www.screener.in/company"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _parse_number(text: str) -> Optional[float]:
    """Parse a number string like '22.4', '1,234.5', '45%' into a float."""
    if not text:
        return None
    cleaned = text.replace(",", "").replace("%", "").replace("₹", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


async def get_fundamentals(symbol: str) -> dict:
    """
    Scrape key fundamental ratios from Screener.in.

    Returns a dict with: pe_ratio, pb_ratio, roe, debt_to_equity,
    promoter_holding_pct, market_cap_cr, roce

    Returns empty dict if scraping fails (agent should handle gracefully).
    """
    url = f"{SCREENER_BASE}/{symbol.upper()}/"
    result: dict = {"symbol": symbol.upper(), "source": "screener.in"}

    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
            response = await client.get(url)

        if response.status_code == 404:
            # Try consolidated view
            url_cons = f"{SCREENER_BASE}/{symbol.upper()}/consolidated/"
            async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
                response = await client.get(url_cons)

        if response.status_code != 200:
            print(f"[fetchers] ❌ Screener returned {response.status_code} for {symbol}")
            return result

        soup = BeautifulSoup(response.text, "html.parser")

        # ── Parse the top ratio list ───────────────────────────────────────────
        # Screener.in puts key ratios in a <ul class="ranges"> or similar list
        ratios: dict[str, Optional[float]] = {}

        # Find all ratio list items — Screener uses <li> with <span> name and value
        for li in soup.select("ul.ranges li, #top-ratios li"):
            spans = li.find_all("span")
            if len(spans) >= 2:
                name = spans[0].get_text(strip=True).lower()
                value_text = spans[-1].get_text(strip=True)
                value = _parse_number(value_text)
                ratios[name] = value

        # Map Screener labels to our field names
        label_map = {
            "stock p/e":            "pe_ratio",
            "p/e":                  "pe_ratio",
            "price to book value":  "pb_ratio",
            "p/b":                  "pb_ratio",
            "roe":                  "roe",
            "return on equity":     "roe",
            "debt to equity":       "debt_to_equity",
            "d/e":                  "debt_to_equity",
            "roce":                 "roce",
            "market cap":           "market_cap_cr",
        }

        for label, field in label_map.items():
            for key, val in ratios.items():
                if label in key:
                    result[field] = val
                    break

        # ── Promoter holding ──────────────────────────────────────────────────
        # Found in the shareholding section
        for row in soup.select("table.data-table tr"):
            cells = row.find_all("td")
            if cells and "promoter" in cells[0].get_text(strip=True).lower():
                # Last cell is usually the most recent quarter's value
                last_val = cells[-1].get_text(strip=True)
                result["promoter_holding_pct"] = _parse_number(last_val)
                break

        if result.get("pe_ratio") or result.get("roe"):
            print(f"[fetchers] ✅ Fundamentals fetched for {symbol}: PE={result.get('pe_ratio')}, ROE={result.get('roe')}")
        else:
            print(f"[fetchers] ⚠️  Fundamentals partially fetched for {symbol} — some fields missing")

        return result

    except Exception as e:
        print(f"[fetchers] ❌ Fundamentals fetch failed for {symbol}: {e}")
        return result


# ── Convenience: fetch both at once ───────────────────────────────────────────

async def get_stock_data(symbol: str) -> dict:
    """
    Fetch price history + fundamentals for a symbol.
    Returns a combined dict. Price df is under key 'price_df'.
    """
    price_df = get_price_data(symbol)
    fundamentals = await get_fundamentals(symbol)
    return {
        "symbol": symbol.upper(),
        "price_df": price_df,
        "current_price": get_current_price(symbol),
        **fundamentals
    }


# ── Quick local test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    async def main():
        print("Testing fetchers with HDFCBANK...\n")
        data = await get_stock_data("HDFCBANK")
        print(f"\nCurrent price: ₹{data['current_price']}")
        print(f"PE Ratio: {data.get('pe_ratio')}")
        print(f"ROE: {data.get('roe')}")
        print(f"Promoter holding: {data.get('promoter_holding_pct')}%")
        if data["price_df"] is not None:
            print(f"Price rows: {len(data['price_df'])}")
            print(data["price_df"].tail(3))

    asyncio.run(main())