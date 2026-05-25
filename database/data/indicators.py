"""
database/data/indicators.py — Technical indicator computation helpers.
Uses the `ta` library (pandas-ta is no longer on PyPI).
"""

import pandas as pd
import ta
import ta.momentum
import ta.trend
import ta.volatility
from typing import Optional


def _col(df: pd.DataFrame, name: str) -> str:
    """Return the correct column name regardless of case."""
    return name if name in df.columns else name.lower()


def compute_rsi(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    if df is None or df.empty:
        return None
    try:
        c = _col(df, "Close")
        rsi = ta.momentum.RSIIndicator(df[c], window=period).rsi()
        return round(float(rsi.dropna().iloc[-1]), 2)
    except Exception as e:
        print(f"[indicators] RSI computation failed: {e}")
        return None


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
    if df is None or df.empty:
        return None
    try:
        c    = _col(df, "Close")
        macd = ta.trend.MACD(df[c], window_slow=slow, window_fast=fast, window_sign=signal)
        return {
            "macd":      round(float(macd.macd().dropna().iloc[-1]), 4),
            "signal":    round(float(macd.macd_signal().dropna().iloc[-1]), 4),
            "histogram": round(float(macd.macd_diff().dropna().iloc[-1]), 4),
        }
    except Exception as e:
        print(f"[indicators] MACD computation failed: {e}")
        return None


def compute_bollinger(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> Optional[dict]:
    if df is None or df.empty:
        return None
    try:
        c  = _col(df, "Close")
        bb = ta.volatility.BollingerBands(df[c], window=period, window_dev=std)
        return {
            "upper":  round(float(bb.bollinger_hband().dropna().iloc[-1]), 2),
            "middle": round(float(bb.bollinger_mavg().dropna().iloc[-1]), 2),
            "lower":  round(float(bb.bollinger_lband().dropna().iloc[-1]), 2),
        }
    except Exception as e:
        print(f"[indicators] Bollinger computation failed: {e}")
        return None


def compute_ema(df: pd.DataFrame, period: int = 21) -> Optional[float]:
    if df is None or df.empty:
        return None
    try:
        c   = _col(df, "Close")
        ema = ta.trend.EMAIndicator(df[c], window=period).ema_indicator()
        return round(float(ema.dropna().iloc[-1]), 2)
    except Exception as e:
        print(f"[indicators] EMA-{period} computation failed: {e}")
        return None


def compute_key_levels(df: pd.DataFrame, lookback: int = 20) -> dict:
    if df is None or df.empty:
        return {}
    try:
        recent   = df.tail(lookback)
        low_col  = _col(recent, "Low")
        high_col = _col(recent, "High")
        return {
            "support":    round(float(recent[low_col].min()), 2),
            "resistance": round(float(recent[high_col].max()), 2),
        }
    except Exception as e:
        print(f"[indicators] Key levels computation failed: {e}")
        return {}


def compute_all(df: pd.DataFrame) -> dict:
    return {
        "rsi":        compute_rsi(df),
        "macd":       compute_macd(df),
        "bollinger":  compute_bollinger(df),
        "ema_21":     compute_ema(df, 21),
        "ema_50":     compute_ema(df, 50),
        "key_levels": compute_key_levels(df),
    }


if __name__ == "__main__":
    from database.data.fetchers import get_price_data
    print("Testing indicators with HDFCBANK...\n")
    df = get_price_data("HDFCBANK")
    if df is not None:
        indicators = compute_all(df)
        for k, v in indicators.items():
            print(f"{k}: {v}")
    else:
        print("Could not fetch price data.")
