"""
agents/sentiment.py
Sentiment analysis agent.

Takes a list of news articles (headline + source) and uses the LLM to
score each article and produce an aggregate SentimentSignal.
"""

from __future__ import annotations
import logging
from typing import Optional

from llm.router import call_llm_json
from llm.schemas import SentimentSignal, ArticleScore

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a financial news sentiment analyst covering Indian equity markets.
You will receive a list of recent news headlines for a stock.

For each article, assign a sentiment score from -100 (very bearish) to +100 (very bullish)
and give a one-line reason.

Then produce an overall:
- score: weighted average of article scores (-100 to +100)
- label: "Very Bearish" | "Bearish" | "Neutral" | "Bullish" | "Very Bullish"
  (Very Bearish: score < -60, Bearish: -60 to -20, Neutral: -20 to 20,
   Bullish: 20 to 60, Very Bullish: > 60)
- analyst_consensus: one sentence describing overall market mood
- reasoning: 2-3 sentences explaining the aggregate view

Respond ONLY with valid JSON (no markdown):
{
  "articles": [
    {"headline": "<exact headline>", "source": "<source>", "score": <-100 to 100>, "reason": "<one line>"},
    ...
  ],
  "score": <overall -100 to 100>,
  "label": "<label>",
  "analyst_consensus": "<one sentence>",
  "reasoning": "<2-3 sentences>"
}"""


def run_sentiment_agent(
    symbol: str,
    articles: Optional[list[dict]] = None,
) -> SentimentSignal:
    """
    Run sentiment analysis.

    Args:
        symbol: NSE ticker e.g. 'RELIANCE'
        articles: List of dicts with keys 'headline' and 'source'.
                  Provided by P3's data/news.py. Falls back to empty list.

    Returns:
        SentimentSignal
    """
    logger.info(f"[Sentiment] Analysing {symbol} — {len(articles or [])} articles")

    if not articles:
        logger.warning(f"No articles provided for {symbol} — returning neutral sentiment")
        return _neutral_signal(symbol)

    llm_input = _build_llm_prompt(symbol, articles)
    llm_result = call_llm_json(SYSTEM_PROMPT, llm_input, max_tokens=1024)

    if not llm_result:
        return _neutral_signal(symbol)

    return _build_signal(symbol, llm_result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_llm_prompt(symbol: str, articles: list[dict]) -> str:
    lines = [f"Stock: {symbol}\n\nRecent news headlines:"]
    for i, a in enumerate(articles[:15], 1):   # cap at 15 articles
        headline = a.get("headline", "")
        source = a.get("source", "Unknown")
        lines.append(f"{i}. [{source}] {headline}")
    lines.append("\nAnalyse each headline and return the required JSON.")
    return "\n".join(lines)


def _build_signal(symbol: str, llm: dict) -> SentimentSignal:
    raw_articles = llm.get("articles", [])
    scored = []
    for a in raw_articles:
        try:
            scored.append(ArticleScore(
                headline=str(a.get("headline", "")),
                source=str(a.get("source", "Unknown")),
                score=float(a.get("score", 0)),
                reason=str(a.get("reason", "")),
            ))
        except Exception:
            continue

    score = float(llm.get("score", 0))
    label = llm.get("label", "Neutral")
    if label not in ("Very Bearish", "Bearish", "Neutral", "Bullish", "Very Bullish"):
        label = _score_to_label(score)

    return SentimentSignal(
        symbol=symbol,
        score=score,
        label=label,
        analyst_consensus=llm.get("analyst_consensus", ""),
        articles=scored,
        reasoning=llm.get("reasoning", ""),
    )


def _neutral_signal(symbol: str) -> SentimentSignal:
    return SentimentSignal(
        symbol=symbol,
        score=0.0,
        label="Neutral",
        analyst_consensus="No recent news available.",
        articles=[],
        reasoning="Insufficient news data to form a sentiment view.",
    )


def _score_to_label(score: float) -> str:
    if score < -60:
        return "Very Bearish"
    elif score < -20:
        return "Bearish"
    elif score <= 20:
        return "Neutral"
    elif score <= 60:
        return "Bullish"
    else:
        return "Very Bullish"
