"""
test_local.py — Full end-to-end test of Person 3's code.

Tests:
  1. Price data fetch (yfinance)
  2. Fundamentals scrape (Screener.in)
  3. News ingestion (RSS feeds)
  4. Technical indicators
  5. Write mock decision to MongoDB Atlas
  6. Run all dashboard queries

Run: python test_local.py
"""

import asyncio
from datetime import datetime, timezone


# ── Test 1: Price data ─────────────────────────────────────────────────────────

def test_price_data():
    print("\n📈  Test 1: Price data (yfinance)")
    from data.fetchers import get_price_data, get_current_price
    df = get_price_data("HDFCBANK", period="1mo")
    price = get_current_price("HDFCBANK")
    if df is not None and not df.empty:
        print(f"    ✅  {len(df)} rows fetched. Latest close: ₹{df['Close'].iloc[-1]:.2f}")
    else:
        print("    ❌  No price data returned")
    if price:
        print(f"    ✅  Current price: ₹{price:.2f}")
    else:
        print("    ⚠️   Current price fetch failed (non-critical)")


# ── Test 2: Fundamentals ───────────────────────────────────────────────────────

async def test_fundamentals():
    print("\n📊  Test 2: Fundamentals (Screener.in)")
    from data.fetchers import get_fundamentals
    data = await get_fundamentals("HDFCBANK")
    if data.get("pe_ratio") or data.get("roe"):
        print(f"    ✅  PE: {data.get('pe_ratio')}, ROE: {data.get('roe')}, "
              f"D/E: {data.get('debt_to_equity')}, Promoter: {data.get('promoter_holding_pct')}%")
    else:
        print("    ⚠️   Fundamentals returned empty — Screener.in may have blocked the scrape")
        print("         This is non-critical for now; will work from the VM's IP")


# ── Test 3: News ───────────────────────────────────────────────────────────────

async def test_news():
    print("\n📰  Test 3: News ingestion (RSS feeds)")
    from data.news import get_news_for_symbol
    articles = await get_news_for_symbol("RELIANCE", limit=5)
    if articles:
        print(f"    ✅  {len(articles)} articles found for RELIANCE:")
        for a in articles[:3]:
            print(f"        [{a['source']}] {a['headline'][:70]}...")
    else:
        print("    ⚠️   No articles matched RELIANCE — feeds may not have recent stories")
        print("         Try a broader symbol like HDFCBANK or ITC")


# ── Test 4: Technical indicators ──────────────────────────────────────────────

def test_indicators():
    print("\n📉  Test 4: Technical indicators (pandas-ta)")
    from data.fetchers import get_price_data
    from data.indicators import compute_all
    df = get_price_data("TCS", period="3mo")
    if df is None:
        print("    ❌  Could not fetch price data for indicators test")
        return
    indicators = compute_all(df)
    print(f"    ✅  RSI:        {indicators['rsi']}")
    print(f"    ✅  MACD:       {indicators['macd']}")
    print(f"    ✅  Bollinger:  {indicators['bollinger']}")
    print(f"    ✅  EMA 21/50:  {indicators['ema_21']} / {indicators['ema_50']}")
    print(f"    ✅  Key levels: {indicators['key_levels']}")


# ── Test 5: Write mock decision to Atlas ──────────────────────────────────────

async def test_mongo_write() -> str:
    print("\n💾  Test 5: Write mock decision document to MongoDB Atlas")
    from db.client import decisions_col
    from db.models import (DecisionDocument, TechnicalSignal, FundamentalSignal,
                            SentimentSignal, RiskSignal, NewsArticle, MACDData,
                            BollingerData, KeyLevels)

    mock_decision = DecisionDocument(
        timestamp=datetime.now(timezone.utc),
        cycle_id="test-cycle-001",
        symbol="HDFCBANK",
        exchange="NSE",
        technical=TechnicalSignal(
            signal="Bullish",
            strength=72.5,
            rsi=58.3,
            macd=MACDData(macd=12.4, signal=10.1, histogram=2.3),
            bollinger=BollingerData(upper=1720.0, middle=1680.0, lower=1640.0),
            ema_21=1675.0,
            ema_50=1650.0,
            key_levels=KeyLevels(support=1630.0, resistance=1730.0),
            reasoning="RSI in healthy range, price above both EMAs, MACD positive."
        ),
        fundamental=FundamentalSignal(
            verdict="Fairly Valued",
            quality_score=74.0,
            pe_ratio=18.2,
            pb_ratio=2.4,
            roe=16.8,
            debt_to_equity=0.9,
            promoter_holding_pct=26.2,
            red_flags=[]
        ),
        sentiment=SentimentSignal(
            score=35.0,
            analyst_consensus="Mildly bullish",
            articles=[
                NewsArticle(
                    headline="HDFC Bank Q3 results beat estimates",
                    source="moneycontrol",
                    score=45.0,
                    reason="Strong NII growth reported"
                )
            ]
        ),
        risk=RiskSignal(
            level="Medium",
            beta=0.85,
            var_95=2.3,
            portfolio_allocation_pct=18.5
        ),
        bull_argument="Strong fundamentals, technical uptrend, positive sentiment.",
        bear_argument="Valuation fairly priced, limited near-term upside.",
        decision="HOLD",
        confidence=68.0,
    )

    try:
        result = await decisions_col.insert_one(mock_decision.to_mongo())
        doc_id = str(result.inserted_id)
        print(f"    ✅  Document written. ID: {doc_id}")
        return doc_id
    except Exception as e:
        print(f"    ❌  Write failed: {e}")
        return ""


# ── Test 6: Dashboard queries ─────────────────────────────────────────────────

async def test_queries():
    print("\n🔍  Test 6: Dashboard queries")
    from db.queries import (get_last_decisions, get_decisions_by_symbol,
                             get_decision_distribution, get_open_gtts)
    try:
        last = await get_last_decisions(limit=5)
        print(f"    ✅  get_last_decisions: {len(last)} docs returned")

        by_symbol = await get_decisions_by_symbol("HDFCBANK")
        print(f"    ✅  get_decisions_by_symbol(HDFCBANK): {len(by_symbol)} docs")

        dist = await get_decision_distribution()
        print(f"    ✅  get_decision_distribution: {dist}")

        gtts = await get_open_gtts()
        print(f"    ✅  get_open_gtts: {len(gtts)} open GTTs")

    except Exception as e:
        print(f"    ❌  Query failed: {e}")


# ── Cleanup ────────────────────────────────────────────────────────────────────

async def cleanup_test_docs():
    from db.client import decisions_col
    result = await decisions_col.delete_many({"cycle_id": "test-cycle-001"})
    print(f"\n🧹  Cleaned up {result.deleted_count} test document(s)")


# ── Main ───────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 55)
    print("  Zeta — Person 3 Full Local Test")
    print("=" * 55)

    # Sync tests
    test_price_data()
    test_indicators()

    # Async tests
    await test_fundamentals()
    await test_news()
    await test_mongo_write()
    await test_queries()
    await cleanup_test_docs()

    print("\n" + "=" * 55)
    print("  All tests complete!")
    print("  Share any ❌ errors with your team.")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    asyncio.run(main())