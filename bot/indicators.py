"""Lightweight technical indicators using only numpy.

All functions accept numpy arrays and return scalars or arrays.
Designed for Raspberry Pi: max ~200 candles, no pandas, minimal allocation.
Graceful degradation when data is shorter than the requested period.
"""

from __future__ import annotations

import numpy as np


def ema(closes: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average (recursive).

    Returns an array the same length as closes.
    If len(closes) < period, uses len(closes) as the period.
    """
    n = len(closes)
    if n == 0:
        return np.empty(0, dtype=np.float64)
    period = min(period, n)
    alpha = 2.0 / (period + 1)

    out = np.empty(n, dtype=np.float64)
    out[0] = closes[0]
    for i in range(1, n):
        out[i] = alpha * closes[i] + (1.0 - alpha) * out[i - 1]
    return out


def rsi(closes: np.ndarray, period: int = 14) -> float:
    """Relative Strength Index using Wilder's smoothing.

    Returns the current RSI value (0–100).
    """
    n = len(closes)
    if n < 2:
        return 50.0
    period = min(period, n - 1)

    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
        period: int = 14) -> float:
    """Average Directional Index.

    Returns the current ADX value (0–100).
    """
    n = len(closes)
    if n < 3:
        return 0.0
    period = min(period, n - 1)

    tr = np.empty(n - 1, dtype=np.float64)
    plus_dm = np.empty(n - 1, dtype=np.float64)
    minus_dm = np.empty(n - 1, dtype=np.float64)

    for i in range(1, n):
        j = i - 1
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[j])
        lc = abs(lows[i] - closes[j])
        tr[j] = max(hl, hc, lc)

        up = highs[i] - highs[j]
        down = lows[j] - lows[i]
        plus_dm[j] = up if (up > down and up > 0) else 0.0
        minus_dm[j] = down if (down > up and down > 0) else 0.0

    atr_val = float(np.mean(tr[:period]))
    plus_di_smooth = float(np.mean(plus_dm[:period]))
    minus_di_smooth = float(np.mean(minus_dm[:period]))

    dx_values: list[float] = []

    for i in range(period, len(tr)):
        atr_val = (atr_val * (period - 1) + tr[i]) / period
        plus_di_smooth = (plus_di_smooth * (period - 1) + plus_dm[i]) / period
        minus_di_smooth = (minus_di_smooth * (period - 1) + minus_dm[i]) / period

        if atr_val > 0:
            plus_di = 100.0 * plus_di_smooth / atr_val
            minus_di = 100.0 * minus_di_smooth / atr_val
        else:
            plus_di = minus_di = 0.0

        di_sum = plus_di + minus_di
        if di_sum > 0:
            dx_values.append(abs(plus_di - minus_di) / di_sum * 100.0)

    if not dx_values:
        if atr_val > 0:
            p = 100.0 * plus_di_smooth / atr_val
            m = 100.0 * minus_di_smooth / atr_val
            s = p + m
            return abs(p - m) / s * 100.0 if s > 0 else 0.0
        return 0.0

    adx_val = float(np.mean(dx_values[:period])) if len(dx_values) >= period else float(np.mean(dx_values))
    for i in range(period, len(dx_values)):
        adx_val = (adx_val * (period - 1) + dx_values[i]) / period

    return adx_val


def bollinger(closes: np.ndarray, period: int = 20,
              std_mult: float = 2.0) -> tuple[float, float, float]:
    """Bollinger Bands.

    Returns (upper, middle, lower) for the most recent candle.
    """
    n = len(closes)
    if n == 0:
        return (0.0, 0.0, 0.0)
    period = min(period, n)
    window = closes[-period:]
    middle = float(np.mean(window))
    std = float(np.std(window, ddof=0))
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    return (upper, middle, lower)


def bollinger_width(closes: np.ndarray, period: int = 20,
                    std_mult: float = 2.0) -> float:
    """Bollinger Band Width: (upper - lower) / middle."""
    upper, middle, lower = bollinger(closes, period, std_mult)
    if middle == 0:
        return 0.0
    return (upper - lower) / middle


def atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
        period: int = 14) -> float:
    """Average True Range using Wilder's smoothing.

    Standalone version, no pandas dependency.
    """
    n = len(closes)
    if n < 2:
        return 0.0
    period = min(period, n - 1)

    tr = np.empty(n - 1, dtype=np.float64)
    for i in range(1, n):
        j = i - 1
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[j])
        lc = abs(lows[i] - closes[j])
        tr[j] = max(hl, hc, lc)

    atr_val = float(np.mean(tr[:period]))
    for i in range(period, len(tr)):
        atr_val = (atr_val * (period - 1) + tr[i]) / period
    return atr_val


def vwap(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
         volumes: np.ndarray) -> float:
    """Volume Weighted Average Price."""
    n = len(closes)
    if n == 0:
        return 0.0
    typical = (highs + lows + closes) / 3.0
    vol_sum = float(np.sum(volumes))
    if vol_sum == 0:
        return float(np.mean(typical))
    return float(np.sum(typical * volumes) / vol_sum)
