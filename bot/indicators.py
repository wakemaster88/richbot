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


# ---------------------------------------------------------------------------
# Extended professional indicators
# ---------------------------------------------------------------------------

def obv(closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    """On-Balance Volume — cumulative volume weighted by price direction.

    OBV diverging from price is a strong leading signal:
    price falling + OBV rising → accumulation (bullish).
    """
    n = len(closes)
    if n < 2:
        return np.zeros(max(n, 1), dtype=np.float64)
    out = np.empty(n, dtype=np.float64)
    out[0] = volumes[0]
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            out[i] = out[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            out[i] = out[i - 1] - volumes[i]
        else:
            out[i] = out[i - 1]
    return out


def macd(closes: np.ndarray, fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple[float, float, float]:
    """MACD — Moving Average Convergence/Divergence.

    Returns (macd_line, signal_line, histogram).
    Histogram > 0 = bullish momentum.
    """
    n = len(closes)
    if n < 2:
        return (0.0, 0.0, 0.0)
    fast = min(fast, n)
    slow = min(slow, n)

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_arr = ema_fast - ema_slow

    signal = min(signal, n)
    alpha = 2.0 / (signal + 1)
    sig_arr = np.empty(n, dtype=np.float64)
    sig_arr[0] = macd_arr[0]
    for i in range(1, n):
        sig_arr[i] = alpha * macd_arr[i] + (1.0 - alpha) * sig_arr[i - 1]

    macd_val = float(macd_arr[-1])
    sig_val = float(sig_arr[-1])
    return (macd_val, sig_val, macd_val - sig_val)


def stoch_rsi(closes: np.ndarray, period: int = 14,
              smooth_k: int = 3, smooth_d: int = 3) -> tuple[float, float]:
    """Stochastic RSI — RSI mapped into a 0-100 stochastic oscillator.

    Faster than plain RSI, better for entry/exit timing.
    Returns (%K, %D).
    """
    n = len(closes)
    if n < period + 2:
        return (50.0, 50.0)

    period = min(period, n - 1)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    rsi_arr = np.empty(len(deltas), dtype=np.float64)
    avg_g = float(np.mean(gains[:period]))
    avg_l = float(np.mean(losses[:period]))
    for i in range(len(deltas)):
        if i < period:
            rsi_arr[i] = 50.0
        else:
            avg_g = (avg_g * (period - 1) + gains[i]) / period
            avg_l = (avg_l * (period - 1) + losses[i]) / period
            if avg_l == 0:
                rsi_arr[i] = 100.0
            else:
                rsi_arr[i] = 100.0 - 100.0 / (1.0 + avg_g / avg_l)

    usable = rsi_arr[period:]
    if len(usable) < period:
        usable = rsi_arr

    stoch_k_raw = np.empty(len(usable), dtype=np.float64)
    for i in range(len(usable)):
        window_start = max(0, i - period + 1)
        w = usable[window_start:i + 1]
        lo = float(np.min(w))
        hi = float(np.max(w))
        stoch_k_raw[i] = ((usable[i] - lo) / (hi - lo) * 100.0) if hi > lo else 50.0

    smooth_k = min(smooth_k, len(stoch_k_raw))
    if smooth_k >= 2 and len(stoch_k_raw) >= smooth_k:
        k_vals = np.convolve(stoch_k_raw, np.ones(smooth_k) / smooth_k, mode="valid")
    else:
        k_vals = stoch_k_raw

    smooth_d = min(smooth_d, len(k_vals))
    if smooth_d >= 2 and len(k_vals) >= smooth_d:
        d_vals = np.convolve(k_vals, np.ones(smooth_d) / smooth_d, mode="valid")
    else:
        d_vals = k_vals

    k_out = float(k_vals[-1]) if len(k_vals) > 0 else 50.0
    d_out = float(d_vals[-1]) if len(d_vals) > 0 else 50.0
    return (max(0.0, min(100.0, k_out)), max(0.0, min(100.0, d_out)))


def volume_profile(highs: np.ndarray, lows: np.ndarray,
                   closes: np.ndarray, volumes: np.ndarray,
                   bins: int = 20) -> dict:
    """Volume Profile with Point of Control and Value Area.

    POC = price level with the most traded volume — acts as a magnet.
    Value Area = price range containing ~70% of total volume.
    """
    n = len(closes)
    if n < 2:
        p = float(closes[-1]) if n == 1 else 0.0
        return {"poc_price": p, "value_area_high": p, "value_area_low": p}

    price_low = float(np.min(lows))
    price_high = float(np.max(highs))
    if price_high <= price_low:
        return {"poc_price": price_low, "value_area_high": price_high, "value_area_low": price_low}

    bins = max(5, min(bins, 50))
    bin_edges = np.linspace(price_low, price_high, bins + 1)
    bin_vol = np.zeros(bins, dtype=np.float64)

    for i in range(n):
        typical = (highs[i] + lows[i] + closes[i]) / 3.0
        idx = int((typical - price_low) / (price_high - price_low) * (bins - 1))
        idx = max(0, min(idx, bins - 1))
        bin_vol[idx] += volumes[i]

    poc_idx = int(np.argmax(bin_vol))
    poc_price = float((bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2.0)

    total_vol = float(np.sum(bin_vol))
    if total_vol <= 0:
        return {"poc_price": poc_price, "value_area_high": price_high, "value_area_low": price_low}

    target = total_vol * 0.70
    accumulated = bin_vol[poc_idx]
    lo_idx = poc_idx
    hi_idx = poc_idx
    while accumulated < target and (lo_idx > 0 or hi_idx < bins - 1):
        expand_lo = bin_vol[lo_idx - 1] if lo_idx > 0 else -1.0
        expand_hi = bin_vol[hi_idx + 1] if hi_idx < bins - 1 else -1.0
        if expand_lo >= expand_hi:
            lo_idx -= 1
            accumulated += bin_vol[lo_idx]
        else:
            hi_idx += 1
            accumulated += bin_vol[hi_idx]

    va_low = float(bin_edges[lo_idx])
    va_high = float(bin_edges[hi_idx + 1])
    return {"poc_price": poc_price, "value_area_high": va_high, "value_area_low": va_low}


def atr_percent(highs: np.ndarray, lows: np.ndarray,
                closes: np.ndarray, period: int = 14) -> float:
    """ATR as a percentage of the current price — comparable across assets."""
    n = len(closes)
    if n < 2:
        return 0.0
    atr_val = atr(highs, lows, closes, period)
    price = float(closes[-1])
    if price <= 0:
        return 0.0
    return atr_val / price * 100.0


def keltner(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
            ema_period: int = 20, atr_mult: float = 1.5) -> tuple[float, float, float]:
    """Keltner Channel — EMA ± ATR multiplier.

    Returns (upper, middle, lower).
    When Bollinger Bands sit inside Keltner → squeeze (breakout imminent).
    """
    n = len(closes)
    if n < 2:
        p = float(closes[-1]) if n == 1 else 0.0
        return (p, p, p)
    ema_arr = ema(closes, min(ema_period, n))
    mid = float(ema_arr[-1])
    atr_val = atr(highs, lows, closes, min(14, n - 1))
    upper = mid + atr_mult * atr_val
    lower = mid - atr_mult * atr_val
    return (upper, mid, lower)


def squeeze_detector(highs: np.ndarray, lows: np.ndarray,
                     closes: np.ndarray) -> dict:
    """Squeeze Detector — Bollinger Bands inside Keltner Channel.

    A squeeze indicates compressed volatility that precedes a breakout.
    Momentum sign hints at the likely breakout direction.

    Returns:
        is_squeeze: bool — BB inside KC right now
        squeeze_duration: int — consecutive candles in squeeze
        momentum: float — positive = bullish breakout expected
    """
    n = len(closes)
    if n < 5:
        return {"is_squeeze": False, "squeeze_duration": 0, "momentum": 0.0}

    bb_period = min(20, n)
    kc_ema_period = min(20, n)
    kc_atr_period = min(14, n - 1)

    ema_arr = ema(closes, kc_ema_period)

    duration = 0
    for t in range(n - 1, max(n - 51, -1), -1):
        start = max(0, t - bb_period + 1)
        window = closes[start:t + 1]
        mid_bb = float(np.mean(window))
        std = float(np.std(window, ddof=0))
        bb_upper = mid_bb + 2.0 * std
        bb_lower = mid_bb - 2.0 * std

        mid_kc = float(ema_arr[t])
        if t >= 1:
            tr_slice_end = t
            tr_arr = np.empty(min(kc_atr_period, tr_slice_end), dtype=np.float64)
            count = 0
            for k in range(max(1, t - kc_atr_period + 1), t + 1):
                hl = highs[k] - lows[k]
                hc = abs(highs[k] - closes[k - 1])
                lc = abs(lows[k] - closes[k - 1])
                if count < len(tr_arr):
                    tr_arr[count] = max(hl, hc, lc)
                    count += 1
            atr_val = float(np.mean(tr_arr[:count])) if count > 0 else 0.0
        else:
            atr_val = 0.0

        kc_upper = mid_kc + 1.5 * atr_val
        kc_lower = mid_kc - 1.5 * atr_val

        if bb_upper < kc_upper and bb_lower > kc_lower:
            duration += 1
        else:
            break

    is_squeeze = duration > 0

    mid_line = float(ema_arr[-1])
    price = float(closes[-1])
    prev_price = float(closes[-2]) if n >= 2 else price
    momentum = (price - mid_line) + (price - prev_price)

    return {
        "is_squeeze": is_squeeze,
        "squeeze_duration": duration,
        "momentum": round(momentum, 6),
    }
