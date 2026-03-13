"""Advanced market regime detection — mini-ensemble of 3 sub-detectors.

Sub-detectors:
  1. Trend     — ADX, EMA alignment (9/21/50), MACD histogram
  2. Volatility — Bollinger width, ATR%, Keltner squeeze, return kurtosis
  3. Mean-Reversion — RSI band, Bollinger %B, volume profile POC, OBV slope

Ensemble voting determines the final regime with transition smoothing
(3 consecutive confirmations) and a confidence score.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum

import numpy as np

from bot import indicators as ind

logger = logging.getLogger(__name__)

_CONFIRM_COUNT = 3


class Regime(Enum):
    RANGING = "ranging"
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    VOLATILE = "volatile"


@dataclass
class RegimeParams:
    """Parameters that adapt the trading strategy to the current regime."""
    regime: Regime
    target_ratio: float
    spacing_mult: float
    size_mult: float
    max_levels: int | None
    min_distance_pct: float = 0.0
    trail_cooldown_sec: int = 0


@dataclass
class EntryFilter:
    """Regime-aware entry filter for order placement."""
    allow_buys: bool
    allow_sells: bool
    rsi_value: float


_REGIME_PARAMS: dict[Regime, RegimeParams] = {
    Regime.RANGING: RegimeParams(
        regime=Regime.RANGING,
        target_ratio=0.50, spacing_mult=1.0, size_mult=1.0, max_levels=None,
        min_distance_pct=0.15, trail_cooldown_sec=0,
    ),
    Regime.TREND_UP: RegimeParams(
        regime=Regime.TREND_UP,
        target_ratio=0.30, spacing_mult=1.8, size_mult=0.7, max_levels=None,
        min_distance_pct=0.40, trail_cooldown_sec=30,
    ),
    Regime.TREND_DOWN: RegimeParams(
        regime=Regime.TREND_DOWN,
        target_ratio=0.70, spacing_mult=1.8, size_mult=0.5, max_levels=None,
        min_distance_pct=0.40, trail_cooldown_sec=30,
    ),
    Regime.VOLATILE: RegimeParams(
        regime=Regime.VOLATILE,
        target_ratio=0.50, spacing_mult=2.5, size_mult=0.4, max_levels=4,
        min_distance_pct=0.50, trail_cooldown_sec=60,
    ),
}


# ── Sub-detector helpers ─────────────────────────────────────────────

def _safe(val: float) -> float:
    return val if math.isfinite(val) else 0.0


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, _safe(val)))


def _trend_score(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    adx_val: float,
    sentiment: float,
    sent_conf: float,
    mtf_align: float,
    mtf_qual: float,
) -> float:
    """Compute trend score in [-1, +1].

    Components:
      - ADX direction via EMA cross (weight 0.35)
      - Triple EMA alignment 9/21/50 (weight 0.25)
      - MACD histogram trend (weight 0.20)
      - Sentiment + MTF nudge (weight 0.20)
    """
    ema9 = ind.ema(closes, 9)
    ema21 = ind.ema(closes, 21)
    ema50 = ind.ema(closes, min(50, len(closes)))

    e9 = float(ema9[-1])
    e21 = float(ema21[-1])
    e50 = float(ema50[-1])

    adx_norm = _clamp(adx_val / 50.0, 0.0, 1.0)
    cross_dir = 1.0 if e9 > e21 else -1.0
    adx_component = cross_dir * adx_norm

    aligned_bull = float(e9 > e21 > e50)
    aligned_bear = float(e9 < e21 < e50)
    alignment = aligned_bull - aligned_bear

    macd_line, _, histogram = ind.macd(closes)
    macd_component = _clamp(histogram * 500.0, -1.0, 1.0)

    external = 0.0
    if sent_conf > 0.5:
        external += sentiment * sent_conf * 0.5
    if mtf_qual > 0.4:
        external += mtf_align * mtf_qual * 0.5
    external = _clamp(external, -1.0, 1.0)

    score = (
        adx_component * 0.35
        + alignment * 0.25
        + macd_component * 0.20
        + external * 0.20
    )
    return _clamp(score, -1.0, 1.0)


def _volatility_score(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    boll_width: float,
    avg_boll_width: float,
) -> float:
    """Compute volatility score in [0, 1].  >0.7 → VOLATILE.

    Components:
      - Bollinger width ratio (weight 0.30)
      - ATR% vs 30-candle average (weight 0.25)
      - Keltner squeeze inverted (weight 0.20)
      - Return kurtosis (weight 0.25)
    """
    boll_ratio = (boll_width / avg_boll_width) if avg_boll_width > 0 else 1.0
    boll_component = _clamp((boll_ratio - 1.0) / 1.5, 0.0, 1.0)

    atr_pct = ind.atr_percent(highs, lows, closes, 14)
    n = min(30, len(closes))
    atr_history = np.array([
        ind.atr_percent(highs[max(0, i - 14):i + 1], lows[max(0, i - 14):i + 1], closes[max(0, i - 14):i + 1], min(14, i + 1))
        for i in range(len(closes) - n, len(closes))
    ], dtype=np.float32) if n > 5 else np.array([atr_pct], dtype=np.float32)
    avg_atr = float(np.mean(atr_history)) if len(atr_history) > 0 else atr_pct
    atr_ratio = (atr_pct / avg_atr) if avg_atr > 0 else 1.0
    atr_component = _clamp((atr_ratio - 1.0) / 1.0, 0.0, 1.0)

    sq = ind.squeeze_detector(highs, lows, closes)
    squeeze_inv = 0.0 if sq["is_squeeze"] else _clamp(abs(sq["momentum"]) * 200, 0.0, 1.0)

    returns = np.diff(np.log(closes[-n:])) if n > 5 else np.zeros(1)
    if len(returns) > 4:
        mu = float(np.mean(returns))
        std = float(np.std(returns))
        if std > 1e-12:
            kurt = float(np.mean(((returns - mu) / std) ** 4)) - 3.0
        else:
            kurt = 0.0
    else:
        kurt = 0.0
    kurt_component = _clamp(kurt / 6.0, 0.0, 1.0)

    score = (
        boll_component * 0.30
        + atr_component * 0.25
        + squeeze_inv * 0.20
        + kurt_component * 0.25
    )
    return _clamp(score, 0.0, 1.0)


def _ranging_score(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    rsi_val: float,
) -> float:
    """Compute mean-reversion / ranging score in [0, 1].  >0.6 → RANGING.

    Components:
      - RSI proximity to 50 (weight 0.30)
      - Bollinger %B in [0.3, 0.7] (weight 0.25)
      - Price near volume POC (weight 0.25)
      - OBV slope flatness (weight 0.20)
    """
    rsi_dist = abs(rsi_val - 50.0) / 50.0
    rsi_component = _clamp(1.0 - rsi_dist * 2.0, 0.0, 1.0)

    upper, mid, lower = ind.bollinger(closes, 20, 2.0)
    brange = upper - lower
    if brange > 1e-12:
        pct_b = (closes[-1] - lower) / brange
    else:
        pct_b = 0.5
    pct_b_dist = abs(pct_b - 0.5)
    bb_component = _clamp(1.0 - pct_b_dist * 4.0, 0.0, 1.0)

    if len(volumes) >= 20:
        vp = ind.volume_profile(highs, lows, closes, volumes, bins=20)
        poc = vp["poc_price"]
        price = float(closes[-1])
        poc_dist = abs(price - poc) / price if price > 0 else 0
        poc_component = _clamp(1.0 - poc_dist * 50.0, 0.0, 1.0)
    else:
        poc_component = 0.5

    if len(volumes) >= 10:
        obv_arr = ind.obv(closes, volumes)
        n = min(20, len(obv_arr))
        obv_recent = obv_arr[-n:]
        if len(obv_recent) > 2:
            obv_range = float(np.max(obv_recent) - np.min(obv_recent))
            obv_mean = float(np.mean(np.abs(obv_recent))) if float(np.mean(np.abs(obv_recent))) > 0 else 1
            obv_slope = obv_range / obv_mean
            obv_component = _clamp(1.0 - obv_slope * 2.0, 0.0, 1.0)
        else:
            obv_component = 0.5
    else:
        obv_component = 0.5

    score = (
        rsi_component * 0.30
        + bb_component * 0.25
        + poc_component * 0.25
        + obv_component * 0.20
    )
    return _clamp(score, 0.0, 1.0)


# ── Main ensemble class ─────────────────────────────────────────────

class RegimeDetector:
    """Ensemble regime detection with 3 sub-detectors and transition smoothing."""

    def __init__(self):
        self._regime = Regime.RANGING
        self._rsi: float = 50.0
        self._adx: float = 0.0
        self._boll_width: float = 0.0
        self._avg_boll_width: float = 0.0
        self._boll_width_history: list[float] = []

        self._sentiment_score: float = 0.0
        self._sentiment_confidence: float = 0.0
        self._mtf_alignment: float = 0.0
        self._mtf_quality: float = 0.0

        self._trend_score: float = 0.0
        self._volatility_score: float = 0.0
        self._ranging_score: float = 0.0
        self._confidence: float = 0.0

        self._candidate: Regime = Regime.RANGING
        self._confirm_count: int = 0

    @property
    def regime(self) -> Regime:
        return self._regime

    @property
    def rsi_value(self) -> float:
        return self._rsi

    def set_sentiment(self, score: float, confidence: float):
        """Inject external news-sentiment signal (NaN-safe)."""
        self._sentiment_score = _clamp(score, -1.0, 1.0)
        self._sentiment_confidence = _clamp(confidence, 0.0, 1.0)

    def set_mtf(self, trend_alignment: float, entry_quality: float):
        """Inject multi-timeframe signal for regime nudging."""
        self._mtf_alignment = _clamp(trend_alignment, -1.0, 1.0)
        self._mtf_quality = _clamp(entry_quality, 0.0, 1.0)

    # ── core ──────────────────────────────────────────────────────

    def update(self, ohlcv: np.ndarray) -> Regime:
        """Run ensemble detection on OHLCV data."""
        if len(ohlcv) < 10:
            return self._regime

        highs = ohlcv[:, 2].astype(np.float64)
        lows = ohlcv[:, 3].astype(np.float64)
        closes = ohlcv[:, 4].astype(np.float64)
        volumes = ohlcv[:, 5].astype(np.float64) if ohlcv.shape[1] > 5 else np.ones_like(closes)

        self._rsi = ind.rsi(closes, 14)
        self._adx = ind.adx(highs, lows, closes, 14)
        self._boll_width = ind.bollinger_width(closes, 20, 2.0)

        self._boll_width_history.append(self._boll_width)
        if len(self._boll_width_history) > 20:
            self._boll_width_history = self._boll_width_history[-20:]
        self._avg_boll_width = (
            sum(self._boll_width_history) / len(self._boll_width_history)
            if self._boll_width_history else self._boll_width
        )

        self._trend_score = _trend_score(
            closes, highs, lows, volumes, self._adx,
            self._sentiment_score, self._sentiment_confidence,
            self._mtf_alignment, self._mtf_quality,
        )
        self._volatility_score = _volatility_score(
            closes, highs, lows, self._boll_width, self._avg_boll_width,
        )
        self._ranging_score = _ranging_score(
            closes, highs, lows, volumes, self._rsi,
        )

        proposed = self._vote()

        if self._sentiment_confidence > 0.8 and self._sentiment_score < -0.7:
            if proposed != Regime.VOLATILE:
                logger.warning(
                    "Sentiment-Override → VOLATILE (score=%.2f, conf=%.2f)",
                    self._sentiment_score, self._sentiment_confidence,
                )
                proposed = Regime.VOLATILE

        self._confidence = max(
            self._volatility_score,
            self._ranging_score,
            abs(self._trend_score),
        )

        old_regime = self._regime
        self._regime = self._smooth_transition(proposed)

        if self._confidence < 0.4:
            rp = _REGIME_PARAMS[self._regime]
            rp_copy = RegimeParams(
                regime=rp.regime,
                target_ratio=rp.target_ratio,
                spacing_mult=rp.spacing_mult * 1.2,
                size_mult=rp.size_mult * 0.8,
                max_levels=rp.max_levels,
                min_distance_pct=rp.min_distance_pct,
                trail_cooldown_sec=rp.trail_cooldown_sec,
            )
            self._low_conf_params = rp_copy
        else:
            self._low_conf_params = None

        if self._regime != old_regime:
            logger.info(
                "Regime: %s → %s [T=%.2f V=%.2f R=%.2f conf=%.0f%%] "
                "(ADX=%.1f RSI=%.1f BollW=%.4f Sent=%.2f MTF=%.2f)",
                old_regime.value, self._regime.value,
                self._trend_score, self._volatility_score, self._ranging_score,
                self._confidence * 100,
                self._adx, self._rsi, self._boll_width,
                self._sentiment_score, self._mtf_alignment,
            )

        return self._regime

    def _vote(self) -> Regime:
        """Ensemble voting logic with priority-based classification."""
        ts = self._trend_score
        vs = self._volatility_score
        rs = self._ranging_score

        if vs > 0.7:
            return Regime.VOLATILE
        if rs > 0.6 and abs(ts) < 0.3:
            return Regime.RANGING
        if ts > 0.4:
            return Regime.TREND_UP
        if ts < -0.4:
            return Regime.TREND_DOWN

        return self._regime

    def _smooth_transition(self, proposed: Regime) -> Regime:
        """Require N consecutive confirmations before switching regime."""
        if proposed == self._regime:
            self._candidate = proposed
            self._confirm_count = 0
            return self._regime

        if proposed == self._candidate:
            self._confirm_count += 1
        else:
            self._candidate = proposed
            self._confirm_count = 1

        if self._confirm_count >= _CONFIRM_COUNT:
            self._confirm_count = 0
            return proposed

        return self._regime

    # ── public API (unchanged) ────────────────────────────────────

    def get_grid_params(self) -> RegimeParams:
        if hasattr(self, "_low_conf_params") and self._low_conf_params is not None:
            return self._low_conf_params
        return _REGIME_PARAMS[self._regime]

    def get_entry_filter(self) -> EntryFilter:
        r = self._regime
        rsi = self._rsi

        if r == Regime.RANGING:
            return EntryFilter(allow_buys=True, allow_sells=True, rsi_value=rsi)
        if r == Regime.TREND_UP:
            return EntryFilter(allow_buys=True, allow_sells=True, rsi_value=rsi)
        if r == Regime.TREND_DOWN:
            return EntryFilter(allow_buys=True, allow_sells=True, rsi_value=rsi)
        return EntryFilter(allow_buys=(rsi < 15), allow_sells=(rsi > 85), rsi_value=rsi)

    def to_dict(self) -> dict:
        """Serialise current state for status/logging."""
        return {
            "regime": self._regime.value,
            "confidence": round(self._confidence * 100, 1),
            "trend_score": round(self._trend_score, 3),
            "volatility_score": round(self._volatility_score, 3),
            "ranging_score": round(self._ranging_score, 3),
            "rsi": round(self._rsi, 1),
            "adx": round(self._adx, 1),
            "boll_width": round(self._boll_width, 4),
            "avg_boll_width": round(self._avg_boll_width, 4),
            "sentiment_score": round(self._sentiment_score, 2),
            "sentiment_confidence": round(self._sentiment_confidence, 2),
            "mtf_alignment": round(self._mtf_alignment, 2),
            "mtf_quality": round(self._mtf_quality, 2),
            "transition_pending": self._candidate.value if self._confirm_count > 0 else None,
            "transition_countdown": max(0, _CONFIRM_COUNT - self._confirm_count) if self._confirm_count > 0 else 0,
        }
