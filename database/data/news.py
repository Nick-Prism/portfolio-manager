"""
data/news.py — RSS news ingestion for Zeta.

Pulls articles from Moneycontrol, Economic Times, and Zerodha Pulse.
Filters by stock symbol/company name so only relevant news is returned.

Usage:
    from data.news import get_news_for_symbol
"""

import feedparser
import asyncio
import httpx
from datetime import datetime, timezone
from typing import Optional


# ── RSS Feed URLs ──────────────────────────────────────────────────────────────

FEEDS = {
    "moneycontrol": "https://www.moneycontrol.com/rss/latestnews.xml",
    "economic_times": "https://economictimes.indiatimes.com/markets/rss.cms",
    "zerodha_pulse": "https://zerodha.com/z-connect/feed",
}

# Common company name aliases for symbol matching
# Extend this as needed for your portfolio stocks
SYMBOL_ALIASES: dict[str, list[str]] = {
    "HDFCBANK":  ["HDFC Bank", "HDFCBANK", "HDFC"],
    "TCS":       ["TCS", "Tata Consultancy", "Tata Consultancy Services"],
    "INFY":      ["Infosys", "INFY"],
    "RELIANCE":  ["Reliance", "RIL", "Reliance Industries"],
    "ITC":       ["ITC"],
    "TATAMOTORS":["Tata Motors", "TATAMOTORS"],
    "WIPRO":     ["Wipro"],
    "SBIN":      ["SBI", "State Bank", "SBIN"],
    "AXISBANK":  ["Axis Bank", "AXISBANK"],
    "ICICIBANK": ["ICICI Bank", "ICICIBANK"],
}


# ── Fetch a single feed ────────────────────────────────────────────────────────

async def _fetch_feed(name: str, url: str) -> list[dict]:
    """
    Fetch and parse a single RSS feed. Returns list of article dicts.
    Uses httpx for async fetching, then feedparser to parse the XML.
    """
    articles = []
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ZetaBot/1.0)"
    }

    try:
        async with httpx.AsyncClient(timeout=10, headers=headers, follow_redirects=True) as client:
            response = await client.get(url)

        if response.status_code != 200:
            print(f"[news] ⚠️  {name} returned {response.status_code}")
            return articles

        feed = feedparser.parse(response.text)

        for entry in feed.entries:
            article = {
                "source": name,
                "headline": entry.get("title", "").strip(),
                "url": entry.get("link", ""),
                "summary": entry.get("summary", "").strip(),
                "published": _parse_date(entry.get("published", "")),
            }
            if article["headline"]:
                articles.append(article)

        print(f"[news] ✅ {name}: {len(articles)} articles fetched")

    except Exception as e:
        print(f"[news] ❌ Failed to fetch {name}: {e}")

    return articles


def _parse_date(date_str: str) -> Optional[datetime]:
    """Try to parse an RSS date string into a datetime."""
    if not date_str:
        return None
    try:
        import email.utils
        parsed = email.utils.parsedate_to_datetime(date_str)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


# ── Filter articles by symbol ──────────────────────────────────────────────────

def _is_relevant(article: dict, symbol: str) -> bool:
    """
    Check if an article is relevant to a given stock symbol.
    Matches against symbol itself and known company name aliases.
    """
    symbol = symbol.upper()
    aliases = SYMBOL_ALIASES.get(symbol, [symbol])

    text = (article.get("headline", "") + " " + article.get("summary", "")).lower()

    for alias in aliases:
        if alias.lower() in text:
            return True
    return False


# ── Public API ─────────────────────────────────────────────────────────────────

async def fetch_all_news() -> list[dict]:
    """
    Fetch all articles from all RSS feeds concurrently.
    Returns a flat list of all articles (unfiltered).
    """
    tasks = [_fetch_feed(name, url) for name, url in FEEDS.items()]
    results = await asyncio.gather(*tasks)

    all_articles = []
    for feed_articles in results:
        all_articles.extend(feed_articles)

    print(f"[news] 📰 Total articles fetched: {len(all_articles)}")
    return all_articles


async def get_news_for_symbol(symbol: str, limit: int = 10) -> list[dict]:
    """
    Fetch and filter news relevant to a specific stock symbol.

    Args:
        symbol: NSE symbol e.g. "HDFCBANK"
        limit:  Max number of articles to return

    Returns:
        List of article dicts with keys: source, headline, url, summary, published
    """
    all_articles = await fetch_all_news()
    relevant = [a for a in all_articles if _is_relevant(a, symbol)]

    # Sort by date (newest first), handle None dates
    relevant.sort(
        key=lambda a: a["published"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True
    )

    print(f"[news] 🔍 {symbol}: {len(relevant)} relevant articles found")
    return relevant[:limit]


async def get_news_for_portfolio(symbols: list[str]) -> dict[str, list[dict]]:
    """
    Fetch news for multiple symbols in one shot (fetches feeds once, filters per symbol).
    More efficient than calling get_news_for_symbol() in a loop.

    Returns: { "HDFCBANK": [...articles], "TCS": [...articles], ... }
    """
    all_articles = await fetch_all_news()

    result = {}
    for symbol in symbols:
        relevant = [a for a in all_articles if _is_relevant(a, symbol)]
        relevant.sort(
            key=lambda a: a["published"] or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True
        )
        result[symbol.upper()] = relevant[:10]
        print(f"[news] 🔍 {symbol}: {len(result[symbol.upper()])} articles")

    return result


# ── Quick local test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    async def main():
        print("Testing news ingestion for HDFCBANK and TCS...\n")
        news = await get_news_for_portfolio(["HDFCBANK", "TCS", "RELIANCE"])
        for symbol, articles in news.items():
            print(f"\n── {symbol} ({len(articles)} articles) ──")
            for a in articles[:3]:
                print(f"  [{a['source']}] {a['headline']}")

    asyncio.run(main())