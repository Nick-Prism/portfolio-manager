"""
agents/technical.py
Technical analysis agent.

Computes RSI, MACD, Bollinger Bands, EMA 21/50, ATR, VWAP from price data
using pandas-ta, then asks the LLM to interpret the combined signals and
produce a structured TechnicalSignal.
"""

from __future__ import annotations
import logging
from typing import Any

import pandas as pd
import pandas_ta as ta

from llm.router import call_llm_json
from llm.schemas import TechnicalSignal, MACDResult, BollingerResult, KeyLevels

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a professional technical analyst specialising in Indian equity markets (NSE/BSE).
You will receive computed technical indicator values for a stock and must:
1. Interpret the combined signals holistically
2. Determine if the overall technical picture is Bullish, Bearish, or Neutral
3. Assign a strength score 0-100 (0=strongly bearish, 50=neutral, 100=strongly bullish)
4. Identify key support and resistance levels from the price data
5. Write a concise 2-3 sentence reasoning

Respond ONLY with valid JSON matching this exact structure (no markdown, no extra keys):
{
  "signal": "Bullish" | "Bearish" | "Neutral",
  "strength": <number 0-100>,
  "key_levels": {"support": <float>, "resistance": <float>},
  "reasoning": "<2-3 sentences>"
}"""


def run_technical_agent(symbol: str, ohlcv: pd.DataFrame) -> TechnicalSignal:
    """
    Run technical analysis on the given OHLCV dataframe.

    Args:
        symbol: Stock ticker e.g. 'HDFCBANK'
        ohlcv: DataFrame with columns [open, high, low, close, volume]
               indexed by datetime, minimum 60 rows recommended

    Returns:
        TechnicalSignal with all computed indicators and LLM interpretation
    """
    logger.info(f"[Technical] Analysing {symbol} — {len(ohlcv)} candles")

    # Ensure lowercase column names
    df = ohlcv.copy()
    df.columns = [c.lower() for c in df.columns]

    indicators = _compute_indicators(df)
    llm_input = _build_llm_prompt(symbol, indicators, df)
    llm_result = call_llm_json(SYSTEM_PROMPT, llm_input, max_tokens=512)

    return _build_signal(symbol, indicators, llm_result)


# ---------------------------------------------------------------------------
# Indicator computation
# ---------------------------------------------------------------------------

def _compute_indicators(df: pd.DataFrame) -> dict[str, Any]:
    """Compute all required technical indicators using pandas-ta."""

    # RSI (14)
    rsi_series = ta.rsi(df["close"], length=14)
    rsi = float(rsi_series.iloc[-1]) if rsi_series is not None and not rsi_series.empty else 50.0

    # MACD (12, 26, 9)
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        macd_line = float(macd_df.iloc[-1, 0])
        macd_hist = float(macd_df.iloc[-1, 1])
        macd_signal = float(macd_df.iloc[-1, 2])
        # Determine crossover: check last two rows
        if len(macd_df) >= 2:
            prev_diff = macd_df.iloc[-2, 0] - macd_df.iloc[-2, 2]
            curr_diff = macd_df.iloc[-1, 0] - macd_df.iloc[-1, 2]
            if prev_diff < 0 and curr_diff >= 0:
                crossover = "bullish"
            elif prev_diff > 0 and curr_diff <= 0:
                crossover = "bearish"
            else:
                crossover = "none"
        else:
            crossover = "none"
    else:
        macd_line = macd_hist = macd_signal = 0.0
        crossover = "none"

    # Bollinger Bands (20, 2)
    bb_df = ta.bbands(df["close"], length=20, std=2)
    if bb_df is not None and not bb_df.empty:
        bb_upper = float(bb_df.iloc[-1, 0])
        bb_mid = float(bb_df.iloc[-1, 1])
        bb_lower = float(bb_df.iloc[-1, 2])
        bb_bw = float(bb_df.iloc[-1, 3]) if bb_df.shape[1] > 3 else 0.0
        bb_pct = float(bb_df.iloc[-1, 4]) if bb_df.shape[1] > 4 else 0.5
    else:
        close = float(df["close"].iloc[-1])
        bb_upper = bb_mid = bb_lower = close
        bb_bw = 0.0
        bb_pct = 0.5

    # EMA 21 and 50
    ema21_series = ta.ema(df["close"], length=21)
    ema50_series = ta.ema(df["close"], length=50)
    ema_21 = float(ema21_series.iloc[-1]) if ema21_series is not None and not ema21_series.empty else float(df["close"].iloc[-1])
    ema_50 = float(ema50_series.iloc[-1]) if ema50_series is not None and not ema50_series.empty else float(df["close"].iloc[-1])

    # ATR (14)
    atr_series = ta.atr(df["high"], df["low"], df["close"], length=14)
    atr = float(atr_series.iloc[-1]) if atr_series is not None and not atr_series.empty else 0.0

    # VWAP (if volume exists)
    try:
        vwap_series = ta.vwap(df["high"], df["low"], df["close"], df["volume"])
        vwap = float(vwap_series.iloc[-1]) if vwap_series is not None and not vwap_series.empty else float(df["close"].iloc[-1])
    except Exception:
        vwap = float(df["close"].iloc[-1])

    current_price = float(df["close"].iloc[-1])

    return {
        "rsi": rsi,
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "crossover": crossover,
        "bb_upper": bb_upper,
        "bb_mid": bb_mid,
        "bb_lower": bb_lower,
        "bb_bw": bb_bw,
        "bb_pct": bb_pct,
        "ema_21": ema_21,
        "ema_50": ema_50,
        "atr": atr,
        "vwap": vwap,
        "current_price": current_price,
        "high_52w": float(df["close"].tail(252).max()),
        "low_52w": float(df["close"].tail(252).min()),
    }


def _build_llm_prompt(symbol: str, ind: dict, df: pd.DataFrame) -> str:
    cp = ind["current_price"]
    return f"""Stock: {symbol}
Current price: ₹{cp:.2f}

INDICATORS:
- RSI(14): {ind['rsi']:.1f}  [Overbought >70, Oversold <30]
- MACD Line: {ind['macd_line']:.4f}  Signal: {ind['macd_signal']:.4f}  Hist: {ind['macd_hist']:.4f}  Crossover: {ind['crossover']}
- Bollinger Bands: Upper={ind['bb_upper']:.2f}  Mid={ind['bb_mid']:.2f}  Lower={ind['bb_lower']:.2f}
  %B (position in bands): {ind['bb_pct']:.2f}  Bandwidth: {ind['bb_bw']:.2f}
- EMA 21: {ind['ema_21']:.2f}  EMA 50: {ind['ema_50']:.2f}
  Price vs EMA21: {'above' if cp > ind['ema_21'] else 'below'}  Price vs EMA50: {'above' if cp > ind['ema_50'] else 'below'}
  EMA21 vs EMA50: {'golden cross' if ind['ema_21'] > ind['ema_50'] else 'death cross'}
- ATR(14): {ind['atr']:.2f}  VWAP: {ind['vwap']:.2f}
- 52w High: {ind['high_52w']:.2f}  52w Low: {ind['low_52w']:.2f}
  Distance from 52w high: {((cp - ind['high_52w']) / ind['high_52w'] * 100):.1f}%

Analyse the above and return the required JSON."""


def _build_signal(symbol: str, ind: dict, llm: dict) -> TechnicalSignal:
    signal_val = llm.get("signal", "Neutral")
    if signal_val not in ("Bullish", "Bearish", "Neutral"):
        signal_val = "Neutral"

    key_levels = llm.get("key_levels", {})
    cp = ind["current_price"]

    return TechnicalSignal(
        symbol=symbol,
        signal=signal_val,
        strength=float(llm.get("strength", 50)),
        rsi=ind["rsi"],
        macd=MACDResult(
            macd_line=ind["macd_line"],
            signal_line=ind["macd_signal"],
            histogram=ind["macd_hist"],
            crossover=ind["crossover"],
        ),
        bollinger=BollingerResult(
            upper=ind["bb_upper"],
            middle=ind["bb_mid"],
            lower=ind["bb_lower"],
            bandwidth=ind["bb_bw"],
            percent_b=ind["bb_pct"],
        ),
        ema_21=ind["ema_21"],
        ema_50=ind["ema_50"],
        atr=ind["atr"],
        vwap=ind["vwap"],
        key_levels=KeyLevels(
            support=float(key_levels.get("support", cp * 0.97)),
            resistance=float(key_levels.get("resistance", cp * 1.03)),
        ),
        reasoning=llm.get("reasoning", "No reasoning provided."),
    )
