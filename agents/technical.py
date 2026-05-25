"""
agents/technical.py
Technical analysis agent.

Computes RSI, MACD, Bollinger Bands, EMA 21/50, ATR, VWAP from price data
using the `ta` library, then asks the LLM to interpret the combined signals.
"""

from __future__ import annotations
import logging
from typing import Any

import numpy as np
import pandas as pd
import ta
import ta.momentum
import ta.trend
import ta.volatility
import ta.volume

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
    logger.info(f"[Technical] Analysing {symbol} — {len(ohlcv)} candles")

    df = ohlcv.copy()
    df.columns = [c.lower() for c in df.columns]

    indicators = _compute_indicators(df)
    llm_input  = _build_llm_prompt(symbol, indicators)
    llm_result = call_llm_json(SYSTEM_PROMPT, llm_input, max_tokens=512)

    return _build_signal(symbol, indicators, llm_result)


def _compute_indicators(df: pd.DataFrame) -> dict[str, Any]:
    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    # RSI (14)
    try:
        rsi = float(ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1])
    except Exception:
        rsi = 50.0  # fallback

    # MACD (12, 26, 9)
    try:
        macd_ind    = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
        macd_line   = float(macd_ind.macd().iloc[-1])
        macd_signal = float(macd_ind.macd_signal().iloc[-1])
        macd_hist   = float(macd_ind.macd_diff().iloc[-1])
        prev_diff   = macd_ind.macd().iloc[-2] - macd_ind.macd_signal().iloc[-2]
        curr_diff   = macd_line - macd_signal
        if prev_diff < 0 and curr_diff >= 0:
            crossover = "bullish"
        elif prev_diff > 0 and curr_diff <= 0:
            crossover = "bearish"
        else:
            crossover = "none"
    except Exception:
        macd_line = macd_signal = macd_hist = 0.0
        crossover = "none"

    # Bollinger Bands (20, 2)
    try:
        bb        = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        bb_upper  = float(bb.bollinger_hband().iloc[-1])
        bb_mid    = float(bb.bollinger_mavg().iloc[-1])
        bb_lower  = float(bb.bollinger_lband().iloc[-1])
        bb_bw     = float(bb.bollinger_wband().iloc[-1])
        bb_pct    = float(bb.bollinger_pband().iloc[-1])
    except Exception:
        cp = float(close.iloc[-1])
        bb_upper = bb_mid = bb_lower = cp
        bb_bw = 0.0
        bb_pct = 0.5

    # EMA 21 and 50
    try:
        ema_21 = float(ta.trend.EMAIndicator(close, window=21).ema_indicator().iloc[-1])
    except Exception:
        ema_21 = float(close.iloc[-1])
    try:
        ema_50 = float(ta.trend.EMAIndicator(close, window=50).ema_indicator().iloc[-1])
    except Exception:
        ema_50 = float(close.iloc[-1])

    # ATR (14)
    try:
        atr = float(ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range().iloc[-1])
    except Exception:
        atr = 0.0

    # VWAP
    try:
        vwap = float(ta.volume.VolumeWeightedAveragePrice(high, low, close, volume).volume_weighted_average_price().iloc[-1])
    except Exception:
        vwap = float(close.iloc[-1])

    cp = float(close.iloc[-1])

    return {
        "rsi":          rsi,
        "macd_line":    macd_line,
        "macd_signal":  macd_signal,
        "macd_hist":    macd_hist,
        "crossover":    crossover,
        "bb_upper":     bb_upper,
        "bb_mid":       bb_mid,
        "bb_lower":     bb_lower,
        "bb_bw":        bb_bw,
        "bb_pct":       bb_pct,
        "ema_21":       ema_21,
        "ema_50":       ema_50,
        "atr":          atr,
        "vwap":         vwap,
        "current_price": cp,
        "high_52w":     float(close.tail(252).max()),
        "low_52w":      float(close.tail(252).min()),
    }


def _build_llm_prompt(symbol: str, ind: dict) -> str:
    cp = ind["current_price"]
    return f"""Stock: {symbol}
Current price: ₹{cp:.2f}

INDICATORS:
- RSI(14): {ind['rsi']:.1f}  [Overbought >70, Oversold <30]
- MACD Line: {ind['macd_line']:.4f}  Signal: {ind['macd_signal']:.4f}  Hist: {ind['macd_hist']:.4f}  Crossover: {ind['crossover']}
- Bollinger Bands: Upper={ind['bb_upper']:.2f}  Mid={ind['bb_mid']:.2f}  Lower={ind['bb_lower']:.2f}
  %B: {ind['bb_pct']:.2f}  Bandwidth: {ind['bb_bw']:.2f}
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

    import math
    for k, v in ind.items():
        if isinstance(v, float) and math.isnan(v):
            ind[k] = 50.0 if k == "rsi" else 0.0


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
