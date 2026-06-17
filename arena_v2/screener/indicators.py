#!/usr/bin/env python3
"""Pure numpy technical indicator computation.

All functions work on pre-fetched numpy arrays (close, high, low, volume).
No yfinance or TradingView dependencies — purely numerical.
Extracted and cleaned from the original data_collector.py.

Indicators computed:
  - RSI(14, Wilder's)
  - SMA(5,10,20,30,50,100,200)
  - EMA(5,10,20,30,50,100,200)
  - MACD(12,26,9) + signal + histogram
  - Bollinger Bands(20,2) + %b + width%
  - Stochastic(14,3) K & D
  - ADX(14) + DI+/DI-
  - VWMA(20)
  - ATR(14) + ATR%
  - CCI(20)
  - Volume ratio (50d / 20d)
  - Volatility (20d)
  - Williams %R(14)
  - Classic Pivot Points
  - 52-week high/low
  - MA slope flags (MA50>MA200, SMA10>SMA30)
"""

import numpy as np
from typing import Dict, Optional


# ─── Helpers ──────────────────────────────────────────────────────────────

def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """Compute exponential moving average using numpy convolution.

    Args:
        values: 1D numpy array of prices.
        period: EMA period length.

    Returns:
        Array of EMA values (length = len(values) - period + 1).
    """
    weights = np.exp(np.linspace(-1, 0, period))
    weights /= weights.sum()
    return np.convolve(values, weights, mode="valid")


def compute_rsi(close: np.ndarray, period: int = 14) -> float:
    """Compute Wilder's RSI from a numpy array of close prices.

    Args:
        close: 1D numpy array of close prices (at least period+1 length).
        period: RSI period (default 14).

    Returns:
        RSI value (0-100).
    """
    delta = np.diff(close)
    gains = np.where(delta > 0, delta, 0)
    losses = np.where(delta < 0, -delta, 0)
    avg_gain = float(np.mean(gains[-period:])) if len(gains) >= period else float(np.mean(gains))
    avg_loss = float(np.mean(losses[-period:])) if len(losses) >= period else float(np.mean(losses))
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


# ─── Full indicator computation ──────────────────────────────────────────

def compute_all_indicators(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
) -> Dict[str, float]:
    """Compute 30+ technical indicators from OHLCV numpy arrays.

    All indicators computed in a single pass to avoid redundant work.
    Returns a dict of {indicator_name: value}.

    Args:
        close: 1D numpy array of close prices (min 20 bars).
        high: 1D numpy array of high prices.
        low: 1D numpy array of low prices.
        volume: 1D numpy array of volume.

    Returns:
        Dict with computed indicator values. Some indicators may be
        omitted if insufficient data (e.g., SMA200 requires 200 bars).
    """
    result: Dict[str, float] = {}
    price = float(close[-1])

    # ── RSI(14) ──────────────────────────────────────────────────────────
    result["RSI"] = compute_rsi(close, 14)

    # ── SMAs ─────────────────────────────────────────────────────────────
    for period in [5, 10, 20, 30, 50, 100, 200]:
        if len(close) >= period:
            result[f"SMA{period}"] = round(float(np.mean(close[-period:])), 2)

    # ── EMAs ─────────────────────────────────────────────────────────────
    for period in [5, 10, 20, 30, 50, 100, 200]:
        if len(close) >= period:
            result[f"EMA{period}"] = round(float(_ema(close, period)[-1]), 2)

    # ── MACD(12, 26, 9) ──────────────────────────────────────────────────
    if len(close) >= 26:
        ema12 = _ema(close, 12)
        ema26 = _ema(close, 26)
        min_len = min(len(ema12), len(ema26))
        macd_line = ema12[-min_len:] - ema26[-min_len:]
        signal = _ema(macd_line, 9)
        result["MACD"] = round(float(macd_line[-1]), 4)
        result["MACD_signal"] = round(float(signal[-1]), 4)
        result["MACD_histogram"] = round(float(macd_line[-1] - signal[-1]), 4)

    # ── Bollinger Bands(20, 2) ──────────────────────────────────────────
    if len(close) >= 20:
        sma20 = np.mean(close[-20:])
        std20 = np.std(close[-20:])
        result["BB_upper"] = round(float(sma20 + 2 * std20), 2)
        result["BB_middle"] = round(float(sma20), 2)
        result["BB_lower"] = round(float(sma20 - 2 * std20), 2)
        bb_width = 4 * std20 / sma20 * 100 if sma20 != 0 else 0
        result["BB_width_pct"] = round(float(bb_width), 2)
        if result["BB_upper"] > result["BB_lower"]:
            bb_pct = (price - result["BB_lower"]) / (result["BB_upper"] - result["BB_lower"])
            result["BB_pct_b"] = round(float(bb_pct), 4)

    # ── Stoch(14, 3) ────────────────────────────────────────────────────
    if len(close) >= 14:
        low14 = float(np.min(low[-14:]))
        high14 = float(np.max(high[-14:]))
        k = (price - low14) / (high14 - low14) * 100 if (high14 - low14) > 0 else 50
        result["Stoch_K"] = round(float(k), 2)
        if len(close) >= 16:
            k_vals = []
            for i in range(-3, 0):
                l14 = float(np.min(low[-14 + i:i]))
                h14 = float(np.max(high[-14 + i:i]))
                kv = (close[i] - l14) / (h14 - l14) * 100 if (h14 - l14) > 0 else 50
                k_vals.append(kv)
            result["Stoch_D"] = round(float(np.mean(k_vals)), 2)

    # ── ADX(14) + DI+/DI- ───────────────────────────────────────────────
    if len(high) >= 15:
        high_diff = np.diff(high)
        low_diff = np.diff(low)
        plus_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0)
        minus_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0)
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
        )
        atr_vals = np.array([np.mean(tr[max(0, i - 14):i + 1]) for i in range(len(tr))])
        plus_di = 100 * np.array([
            np.mean(plus_dm[max(0, i - 14):i + 1]) / a if a > 0 else 0
            for i, a in enumerate(atr_vals)
        ])
        minus_di = 100 * np.array([
            np.mean(minus_dm[max(0, i - 14):i + 1]) / a if a > 0 else 0
            for i, a in enumerate(atr_vals)
        ])
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = float(np.mean(dx[-14:])) if len(dx) >= 14 else float(np.mean(dx))
        result["ADX"] = round(adx, 2)
        result["plus_DI"] = round(float(plus_di[-1]), 2)
        result["minus_DI"] = round(float(minus_di[-1]), 2)

    # ── VWMA(20) ────────────────────────────────────────────────────────
    if len(close) >= 20 and len(volume) >= 20:
        vol_sum = float(np.sum(volume[-20:]))
        if vol_sum > 0:
            vwma = float(np.sum(close[-20:] * volume[-20:]) / vol_sum)
            result["VWMA20"] = round(vwma, 2)

    # ── ATR(14) + ATR% ──────────────────────────────────────────────────
    if len(high) >= 15:
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])),
        )
        atr = float(np.mean(tr[-14:]))
        result["ATR"] = round(atr, 4)
        result["ATR_pct"] = round(atr / price * 100, 2) if price > 0 else 0

    # ── CCI(20) ─────────────────────────────────────────────────────────
    if len(close) >= 20:
        tp = (high[-20:] + low[-20:] + close[-20:]) / 3
        tp_sma = float(np.mean(tp))
        mean_dev = float(np.mean(np.abs(tp - tp_sma)))
        cci = (float(tp[-1]) - tp_sma) / (0.015 * mean_dev) if mean_dev > 0 else 0
        result["CCI20"] = round(cci, 2)

    # ── Volume ratio + Volume ───────────────────────────────────────────
    if len(volume) >= 50:
        avg_vol = float(np.mean(volume[-50:]))
        result["Volume_ratio"] = round(float(volume[-1] / avg_vol), 2) if avg_vol > 0 else 1.0
    elif len(volume) >= 20:
        avg_vol = float(np.mean(volume[-20:]))
        result["Volume_ratio"] = round(float(volume[-1] / avg_vol), 2) if avg_vol > 0 else 1.0
    result["Volume"] = int(volume[-1])

    # ── Volatility (20d) ────────────────────────────────────────────────
    if len(close) >= 20:
        result["Volatility_20d"] = round(float(np.std(close[-20:]) / price * 100), 2)

    # ── Williams %R(14) ──────────────────────────────────────────────────
    if len(close) >= 14:
        high14 = float(np.max(high[-14:]))
        low14 = float(np.min(low[-14:]))
        wr = -100 * (high14 - price) / (high14 - low14) if (high14 - low14) > 0 else -50
        result["Williams_R"] = round(wr, 2)

    # ── Classic Pivot Points ─────────────────────────────────────────────
    if len(high) >= 2:
        prev_high = high[-2]
        prev_low = low[-2]
        prev_close = close[-2]
        pp = (prev_high + prev_low + prev_close) / 3
        result["Pivot"] = round(float(pp), 2)
        result["R1"] = round(float(2 * pp - prev_low), 2)
        result["R2"] = round(float(pp + (prev_high - prev_low)), 2)
        result["S1"] = round(float(2 * pp - prev_high), 2)
        result["S2"] = round(float(pp - (prev_high - prev_low)), 2)

    # ── Price & Change% ──────────────────────────────────────────────────
    result["Price"] = round(price, 2)
    if len(close) >= 2:
        result["Change_pct"] = round(float((close[-1] - close[-2]) / close[-2] * 100), 2)

    # ── 52-week high/low ────────────────────────────────────────────────
    if len(close) >= 252:
        result["52w_high"] = round(float(np.max(close[-252:])), 2)
        result["52w_low"] = round(float(np.min(close[-252:])), 2)
    elif len(close) >= 1:
        result["52w_high"] = round(float(np.max(close)), 2)
        result["52w_low"] = round(float(np.min(close)), 2)

    # ── MA slope flags ───────────────────────────────────────────────────
    if "SMA50" in result and "SMA200" in result:
        result["MA50_above_MA200"] = 1.0 if result["SMA50"] > result["SMA200"] else 0.0
    if "SMA10" in result and "SMA30" in result:
        result["SMA10_above_SMA30"] = 1.0 if result["SMA10"] > result["SMA30"] else 0.0

    return result
