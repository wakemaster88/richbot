"""Multi-Timeframe Analysis — three timeframes for professional entry decisions.

Higher TF (4h):  trend direction
Medium TF (1h):  regime / timing
Lower  TF (15m): entry precision

Pi-optimised: cached OHLCV, bounded buffers, < 50 KB per pair.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np

from bot import indicators as ind

logger = logging.getLogger(__name__)

_TF_CONFIG = {
    "15m": {"limit": 96, "stale_sec": 900},
    "1h":  {"limit": 72, "stale_sec": 3600},
    "4h":  {"limit": 42, "stale_sec": 14400},
}


@dataclass
class TimeframeAnalysis:
    timeframe: str
    trend: str = "neutral"
    strength: float = 0.0
    rsi: float = 50.0
    adx: float = 0.0
    ema_cross: str = "neutral"
    squeeze: bool = False
    squeeze_duration: int = 0
    macd_histogram: float = 0.0
    volume_trend: str = "flat"
    atr_pct: float = 0.0

    def to_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "trend": self.trend,
            "strength": round(self.strength, 3),
            "rsi": round(self.rsi, 1),
            "adx": round(self.adx, 1),
            "ema_cross": self.ema_cross,
            "squeeze": self.squeeze,
            "squeeze_duration": self.squeeze_duration,
            "macd_histogram": round(self.macd_histogram, 6),
            "volume_trend": self.volume_trend,
            "atr_pct": round(self.atr_pct, 3),
        }


@dataclass
class MultiTimeframeSignal:
    trend_alignment: float = 0.0
    entry_quality: float = 0.0
    suggested_bias: str = "balanced"
    confluence_score: int = 0
    size_mult: float = 1.0
    timeframes: dict[str, TimeframeAnalysis] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "trend_alignment": round(self.trend_alignment, 3),
            "entry_quality": round(self.entry_quality, 3),
            "suggested_bias": self.suggested_bias,
            "confluence_score": self.confluence_score,
            "size_mult": round(self.size_mult, 3),
            "timeframes": {k: v.to_dict() for k, v in self.timeframes.items()},
        }


def _analyse_tf(tf: str, ohlcv: np.ndarray) -> TimeframeAnalysis:
    """Run indicator suite on a single timeframe's OHLCV data."""
    a = TimeframeAnalysis(timeframe=tf)
    n = len(ohlcv)
    if n < 5:
        return a

    highs = ohlcv[:, 2].astype(np.float64)
    lows = ohlcv[:, 3].astype(np.float64)
    closes = ohlcv[:, 4].astype(np.float64)
    volumes = ohlcv[:, 5].astype(np.float64)

    a.rsi = ind.rsi(closes, 14)
    a.adx = ind.adx(highs, lows, closes, 14)
    a.atr_pct = ind.atr_percent(highs, lows, closes, 14)

    ema9 = ind.ema(closes, 9)
    ema21 = ind.ema(closes, 21)
    e9 = float(ema9[-1])
    e21 = float(ema21[-1])

    if e9 > e21 * 1.001:
        a.ema_cross = "bullish"
    elif e9 < e21 * 0.999:
        a.ema_cross = "bearish"
    else:
        a.ema_cross = "neutral"

    _, _, hist = ind.macd(closes)
    a.macd_histogram = hist

    sq = ind.squeeze_detector(highs, lows, closes)
    a.squeeze = sq["is_squeeze"]
    a.squeeze_duration = sq["squeeze_duration"]

    if n >= 10:
        obv_arr = ind.obv(closes, volumes)
        obv_slope = float(np.mean(np.diff(obv_arr[-10:])))
        vol_mean = float(np.mean(volumes))
        if vol_mean > 0:
            norm_slope = obv_slope / vol_mean
            if norm_slope > 0.05:
                a.volume_trend = "rising"
            elif norm_slope < -0.05:
                a.volume_trend = "falling"
            else:
                a.volume_trend = "flat"

    bullish = 0
    bearish = 0
    if a.ema_cross == "bullish":
        bullish += 1
    elif a.ema_cross == "bearish":
        bearish += 1
    if a.macd_histogram > 0:
        bullish += 1
    elif a.macd_histogram < 0:
        bearish += 1
    if a.rsi > 55:
        bullish += 1
    elif a.rsi < 45:
        bearish += 1
    if a.adx > 20:
        if bullish > bearish:
            bullish += 1
        elif bearish > bullish:
            bearish += 1

    total = bullish + bearish
    if total == 0:
        a.trend = "neutral"
        a.strength = 0.0
    elif bullish > bearish:
        a.trend = "up"
        a.strength = min(1.0, bullish / max(total, 1))
    elif bearish > bullish:
        a.trend = "down"
        a.strength = min(1.0, bearish / max(total, 1))
    else:
        a.trend = "neutral"
        a.strength = 0.0

    return a


def _compute_signal(analyses: dict[str, TimeframeAnalysis]) -> MultiTimeframeSignal:
    """Merge three timeframe analyses into a single composite signal."""
    sig = MultiTimeframeSignal(timeframes=analyses)

    tf_weights = {"4h": 0.50, "1h": 0.30, "15m": 0.20}
    alignment = 0.0
    for tf_key, weight in tf_weights.items():
        a = analyses.get(tf_key)
        if a is None:
            continue
        if a.trend == "up":
            alignment += weight * a.strength
        elif a.trend == "down":
            alignment -= weight * a.strength
    sig.trend_alignment = max(-1.0, min(1.0, alignment))

    confluence = 0

    trends = [a.trend for a in analyses.values()]
    if all(t == "up" for t in trends):
        confluence += 2
    elif all(t == "down" for t in trends):
        confluence += 2
    elif trends.count("up") >= 2 or trends.count("down") >= 2:
        confluence += 1

    ema_crosses = [a.ema_cross for a in analyses.values()]
    if all(c == "bullish" for c in ema_crosses) or all(c == "bearish" for c in ema_crosses):
        confluence += 1

    macds = [a.macd_histogram for a in analyses.values()]
    if all(m > 0 for m in macds) or all(m < 0 for m in macds):
        confluence += 1

    lower = analyses.get("15m")
    higher = analyses.get("4h")

    if lower and lower.squeeze:
        confluence += 1

    if higher and lower:
        if higher.trend == "up" and lower.rsi < 40:
            confluence += 1
        elif higher.trend == "down" and lower.rsi > 60:
            confluence += 1

    sig.confluence_score = min(6, confluence)

    quality = sig.confluence_score / 6.0
    if abs(sig.trend_alignment) > 0.5:
        quality = min(1.0, quality + 0.15)
    if lower and lower.squeeze and higher and higher.strength > 0.5:
        quality = min(1.0, quality + 0.1)
    sig.entry_quality = round(max(0.0, min(1.0, quality)), 3)

    if sig.trend_alignment > 0.4 and sig.confluence_score >= 3:
        sig.suggested_bias = "buy_heavy"
    elif sig.trend_alignment < -0.4 and sig.confluence_score >= 3:
        sig.suggested_bias = "sell_heavy"
    elif sig.confluence_score <= 1 and abs(sig.trend_alignment) < 0.2:
        sig.suggested_bias = "wait"
    else:
        sig.suggested_bias = "balanced"

    if sig.entry_quality >= 0.7:
        sig.size_mult = 1.2
    elif sig.entry_quality <= 0.3:
        sig.size_mult = 0.8
    else:
        sig.size_mult = 1.0

    return sig


class MultiTimeframe:
    """Manages multi-timeframe OHLCV caching and analysis for a single pair."""

    def __init__(self, pair: str):
        self.pair = pair
        self._cache: dict[str, np.ndarray | None] = {tf: None for tf in _TF_CONFIG}
        self._cache_ts: dict[str, float] = {tf: 0.0 for tf in _TF_CONFIG}
        self._last_signal: MultiTimeframeSignal = MultiTimeframeSignal()

    @property
    def signal(self) -> MultiTimeframeSignal:
        return self._last_signal

    async def update(self, exchange) -> MultiTimeframeSignal:
        """Fetch fresh candles where stale, analyse all TFs, return composite."""
        now = time.time()

        for tf, cfg in _TF_CONFIG.items():
            age = now - self._cache_ts[tf]
            if age < cfg["stale_sec"] * 0.8 and self._cache[tf] is not None:
                continue
            try:
                raw = await exchange.async_fetch_ohlcv(
                    self.pair, timeframe=tf, limit=cfg["limit"],
                )
                if raw and len(raw) >= 5:
                    self._cache[tf] = np.array(raw, dtype=np.float64)
                    self._cache_ts[tf] = now
            except Exception as e:
                logger.debug("MTF fetch %s/%s failed: %s", self.pair, tf, e)

        analyses: dict[str, TimeframeAnalysis] = {}
        for tf in _TF_CONFIG:
            data = self._cache.get(tf)
            if data is not None and len(data) >= 5:
                analyses[tf] = _analyse_tf(tf, data)
            else:
                analyses[tf] = TimeframeAnalysis(timeframe=tf)

        self._last_signal = _compute_signal(analyses)

        logger.info(
            "MTF %s: align=%.2f quality=%.2f bias=%s confluence=%d size=%.2f "
            "[4h=%s(%s) 1h=%s(%s) 15m=%s(%s)]",
            self.pair,
            self._last_signal.trend_alignment,
            self._last_signal.entry_quality,
            self._last_signal.suggested_bias,
            self._last_signal.confluence_score,
            self._last_signal.size_mult,
            analyses.get("4h", TimeframeAnalysis("4h")).trend,
            analyses.get("4h", TimeframeAnalysis("4h")).ema_cross,
            analyses.get("1h", TimeframeAnalysis("1h")).trend,
            analyses.get("1h", TimeframeAnalysis("1h")).ema_cross,
            analyses.get("15m", TimeframeAnalysis("15m")).trend,
            analyses.get("15m", TimeframeAnalysis("15m")).ema_cross,
        )

        return self._last_signal

    def get_metrics(self) -> dict:
        return self._last_signal.to_dict()
