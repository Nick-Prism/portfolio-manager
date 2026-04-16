"""
agents/fundamental.py
Fundamental analysis agent.

Scrapes PE, PB, ROE, D/E, promoter holding, sales/profit growth from
Screener.in, then asks the LLM to produce a FundamentalSignal verdict.
"""

from __future__ import annotations
import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from llm.router import call_llm_json
from llm.schemas import FundamentalSignal

logger = logging.getLogger(__name__)

SCREENER_BASE = "https://www.screener.in/company/{symbol}/consolidated/"

SYSTEM_PROMPT = """You are a senior equity analyst specialising in Indian listed companies.
You will receive fundamental financial ratios for a stock. Your job is to:
1. Assess whether the stock is Undervalued, Fairly Valued, or Overvalued
2. Assign a quality score 0-100 (business quality, not valuation)
3. List any red flags (e.g. high debt, declining margins, promoter pledge)
4. Write 2-3 sentence reasoning

Respond ONLY with valid JSON (no markdown, no extra keys):
{
  "verdict": "Undervalued" | "Fairly Valued" | "Overvalued" | "Insufficient Data",
  "quality_score": <number 0-100>,
  "red_flags": ["<flag1>", "<flag2>"],
  "reasoning": "<2-3 sentences>"
}"""


def run_fundamental_agent(symbol: str, fundamentals: Optional[dict] = None) -> FundamentalSignal:
    """
    Run fundamental analysis.

    Args:
        symbol: NSE ticker e.g. 'HDFCBANK'
        fundamentals: Pre-fetched dict from P3's data layer. If None,
                      this agent will attempt to scrape Screener.in directly.

    Returns:
        FundamentalSignal
    """
    logger.info(f"[Fundamental] Analysing {symbol}")

    if fundamentals is None:
        fundamentals = _scrape_screener(symbol)

    llm_input = _build_llm_prompt(symbol, fundamentals)
    llm_result = call_llm_json(SYSTEM_PROMPT, llm_input, max_tokens=512)

    return _build_signal(symbol, fundamentals, llm_result)


# ---------------------------------------------------------------------------
# Screener.in scraper
# ---------------------------------------------------------------------------

def _scrape_screener(symbol: str) -> dict:
    """
    Scrape key ratios from Screener.in.
    Returns a dict of metric name → value (float or None).
    Falls back to empty dict on any error.
    """
    url = SCREENER_BASE.format(symbol=symbol.upper())
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            # Try standalone (non-consolidated) URL
            url_standalone = url.replace("/consolidated/", "/")
            resp = httpx.get(url_standalone, headers=headers, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            logger.warning(f"Screener.in returned {resp.status_code} for {symbol}")
            return {}

        return _parse_screener_html(resp.text)

    except Exception as e:
        logger.warning(f"Screener.in scrape failed for {symbol}: {e}")
        return {}


def _parse_screener_html(html: str) -> dict:
    """Parse Screener.in company page HTML into a metrics dict."""
    soup = BeautifulSoup(html, "html.parser")
    metrics: dict[str, Optional[float]] = {}

    # Key ratios are in <li> elements with a name span and a number span
    for li in soup.select("#top-ratios li"):
        name_el = li.select_one(".name")
        value_el = li.select_one(".number")
        if not name_el or not value_el:
            continue
        name = name_el.get_text(strip=True).lower()
        raw = value_el.get_text(strip=True).replace(",", "").replace("%", "")
        try:
            val = float(re.search(r"-?\d+\.?\d*", raw).group())
        except (AttributeError, ValueError):
            val = None
        metrics[name] = val

    # Promoter holding from the shareholding table
    try:
        promo_row = soup.find("td", string=re.compile(r"Promoters", re.I))
        if promo_row:
            cells = promo_row.find_parent("tr").find_all("td")
            if len(cells) >= 2:
                raw = cells[-1].get_text(strip=True).replace("%", "")
                metrics["promoter holding"] = float(raw)
    except Exception:
        pass

    return metrics


def _build_llm_prompt(symbol: str, m: dict) -> str:
    def fmt(key: str, unit: str = "") -> str:
        keys_to_try = [key, key.lower(), key.upper()]
        for k in keys_to_try:
            if k in m and m[k] is not None:
                return f"{m[k]:.2f}{unit}"
        return "N/A"

    return f"""Stock: {symbol} (NSE)

FUNDAMENTAL RATIOS:
- P/E Ratio:          {fmt('p/e')} (sector context: BSE500 median ~25)
- P/B Ratio:          {fmt('p/b')}
- ROE:                {fmt('roe', '%')}
- Debt / Equity:      {fmt('debt / equity')}
- Sales Growth (3yr): {fmt('sales growth', '%')}
- Profit Growth (3yr):{fmt('profit growth', '%')}
- Promoter Holding:   {fmt('promoter holding', '%')}
- Dividend Yield:     {fmt('dividend yield', '%')}
- ROCE:               {fmt('roce', '%')}

Analyse the above and return the required JSON."""


def _build_signal(symbol: str, m: dict, llm: dict) -> FundamentalSignal:
    def get(key: str) -> Optional[float]:
        for k in [key, key.lower()]:
            if k in m and m[k] is not None:
                return float(m[k])
        return None

    verdict = llm.get("verdict", "Insufficient Data")
    if verdict not in ("Undervalued", "Fairly Valued", "Overvalued", "Insufficient Data"):
        verdict = "Insufficient Data"

    return FundamentalSignal(
        symbol=symbol,
        verdict=verdict,
        quality_score=float(llm.get("quality_score", 50)),
        pe_ratio=get("p/e"),
        pb_ratio=get("p/b"),
        roe=get("roe"),
        debt_to_equity=get("debt / equity"),
        promoter_holding_pct=get("promoter holding"),
        sales_growth_pct=get("sales growth"),
        profit_growth_pct=get("profit growth"),
        red_flags=llm.get("red_flags", []),
        reasoning=llm.get("reasoning", "No reasoning provided."),
    )
