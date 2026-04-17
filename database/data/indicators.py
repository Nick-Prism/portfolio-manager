"""
data/indicators.py — Technical indicator computation helpers.

These are helper functions for Person 2's technical agent.
Takes a price DataFrame (from fetchers.py) and returns indicator values.

Usage:
    from data.indicators import compute_rsi, compute_macd, compute_bollinger
"""

import pandas as pd
import pandas_ta as ta
from typing import Optional


# ── RSI ────────────────────────────────────────────────────────────────────────

def compute_rsi(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """
    Compute RSI and return the latest value.

    RSI > 70 → overbought (bearish signal)
    RSI < 30 → oversold (bullish signal)
    """
    if df is None or df.empty or "Close" not in df.columns:
        return None
    try:
        rsi_series = ta.rsi(df["Close"], length=period)
        if rsi_series is None or rsi_series.empty:
            return None
        latest = rsi_series.dropna().iloc[-1]
        return round(float(latest), 2)
    except Exception as e:
        print(f"[indicators] RSI computation failed: {e}")
        return None


# ── MACD ───────────────────────────────────────────────────────────────────────

def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
    """
    Compute MACD and return the latest MACD line, signal line, and histogram.

    MACD > signal → bullish momentum
    MACD < signal → bearish momentum
    Histogram crossing zero → trend change
    """
    if df is None or df.empty or "Close" not in df.columns:
        return None
    try:
        macd_df = ta.macd(df["Close"], fast=fast, slow=slow, signal=signal)
        if macd_df is None or macd_df.empty:
            return None
        latest = macd_df.dropna().iloc[-1]
        return {
            "macd":      round(float(latest[f"MACD_{fast}_{slow}_{signal}"]), 4),
            "signal":    round(float(latest[f"MACDs_{fast}_{slow}_{signal}"]), 4),
            "histogram": round(float(latest[f"MACDh_{fast}_{slow}_{signal}"]), 4),
        }
    except Exception as e:
        print(f"[indicators] MACD computation failed: {e}")
        return None


# ── Bollinger Bands ────────────────────────────────────────────────────────────

def compute_bollinger(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> Optional[dict]:
    """
    Compute Bollinger Bands and return upper, middle, lower band values.

    Price near upper band → overbought
    Price near lower band → oversold
    """
    if df is None or df.empty or "Close" not in df.columns:
        return None
    try:
        bb_df = ta.bbands(df["Close"], length=period, std=std)
        if bb_df is None or bb_df.empty:
            return None
        latest = bb_df.dropna().iloc[-1]
        return {
            "upper":  round(float(latest[f"BBU_{period}_{std}"]), 2),
            "middle": round(float(latest[f"BBM_{period}_{std}"]), 2),
            "lower":  round(float(latest[f"BBL_{period}_{std}"]), 2),
        }
    except Exception as e:
        print(f"[indicators] Bollinger computation failed: {e}")
        return None


# ── EMA ────────────────────────────────────────────────────────────────────────

def compute_ema(df: pd.DataFrame, period: int = 21) -> Optional[float]:
    """
    Compute EMA for a given period. Returns latest value.

    Price above EMA 21 & 50 → uptrend
    Price below EMA 21 & 50 → downtrend
    EMA 21 crossing EMA 50 → trend change signal
    """
    if df is None or df.empty or "Close" not in df.columns:
        return None
    try:
        ema_series = ta.ema(df["Close"], length=period)
        if ema_series is None or ema_series.empty:
            return None
        latest = ema_series.dropna().iloc[-1]
        return round(float(latest), 2)
    except Exception as e:
        print(f"[indicators] EMA-{period} computation failed: {e}")
        return None


# ── Support & Resistance ───────────────────────────────────────────────────────

def compute_key_levels(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Simple support/resistance using recent high and low.

    Returns: { "support": float, "resistance": float }
    """
    if df is None or df.empty:
        return {}
    try:
        recent = df.tail(lookback)
        support    = round(float(recent["Low"].min()), 2)
        resistance = round(float(recent["High"].max()), 2)
        return {"support": support, "resistance": resistance}
    except Exception as e:
        print(f"[indicators] Key levels computation failed: {e}")
        return {}


# ── All indicators at once ─────────────────────────────────────────────────────

def compute_all(df: pd.DataFrame) -> dict:
    """
    Compute all indicators for a given price DataFrame.
    Returns a single dict with all values — convenience for Person 2's technical agent.
    """
    return {
        "rsi":        compute_rsi(df),
        "macd":       compute_macd(df),
        "bollinger":  compute_bollinger(df),
        "ema_21":     compute_ema(df, 21),
        "ema_50":     compute_ema(df, 50),
        "key_levels": compute_key_levels(df),
    }


# ── Quick local test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    from data.fetchers import get_price_data

    print("Testing indicators with HDFCBANK...\n")
    df = get_price_data("HDFCBANK")
    if df is not None:
        indicators = compute_all(df)
        print(f"RSI:        {indicators['rsi']}")
        print(f"MACD:       {indicators['macd']}")
        print(f"Bollinger:  {indicators['bollinger']}")
        print(f"EMA 21:     {indicators['ema_21']}")
        print(f"EMA 50:     {indicators['ema_50']}")
        print(f"Key levels: {indicators['key_levels']}")
    else:
        print("Could not fetch price data.")