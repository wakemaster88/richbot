"""ATR-based dynamic range calculation with LSTM integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot.config import ATRConfig, MLConfig

logger = logging.getLogger(__name__)


@dataclass
class RangeResult:
    upper: float
    lower: float
    mid: float
    atr: float
    source: str  # "atr" or "lstm"
    confidence: float = 1.0
    prediction_label: str = ""

    @property
    def spread(self) -> float:
        return self.upper - self.lower

    @property
    def spread_percent(self) -> float:
        if self.mid == 0:
            return 0.0
        return (self.spread / self.mid) * 100


def calculate_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """Calculate Average True Range."""
    if len(highs) < period + 1:
        return float(highs.max() - lows.min()) / 10

    tr_list = []
    for i in range(1, len(highs)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr_list.append(max(hl, hc, lc))

    tr_array = np.array(tr_list)
    atr = pd.Series(tr_array).ewm(span=period, adjust=False).mean().iloc[-1]
    return float(atr)


def compute_dynamic_range(
    ohlcv_df: pd.DataFrame,
    current_price: float,
    atr_config: ATRConfig,
    range_multiplier: float = 1.0,
    ml_predictor=None,
    ml_config: MLConfig | None = None,
) -> RangeResult:
    """Compute the trading range using ATR, optionally enhanced with LSTM predictions."""
    highs = ohlcv_df["high"].values
    lows = ohlcv_df["low"].values
    closes = ohlcv_df["close"].values

    atr = calculate_atr(highs, lows, closes, atr_config.period)
    half_range = atr * atr_config.multiplier * range_multiplier

    atr_upper = current_price + half_range
    atr_lower = current_price - half_range

    source = "atr"
    confidence = 1.0
    label = ""

    if ml_predictor is not None and ml_config is not None and ml_config.enabled:
        try:
            prediction = ml_predictor.predict(ohlcv_df)
            if prediction and prediction.get("confidence", 0) >= ml_config.confidence_threshold:
                pred_upper = prediction.get("upper", atr_upper)
                pred_lower = prediction.get("lower", atr_lower)
                blend = prediction["confidence"]
                final_upper = atr_upper * (1 - blend) + pred_upper * blend
                final_lower = atr_lower * (1 - blend) + pred_lower * blend
                source = "lstm"
                confidence = prediction["confidence"]
                label = prediction.get("label", "LSTM Range Shift")

                logger.info(
                    "LSTM range (conf=%.2f): [%.2f, %.2f] → blended [%.2f, %.2f]",
                    confidence, pred_lower, pred_upper, final_lower, final_upper,
                )
                return RangeResult(
                    upper=final_upper,
                    lower=final_lower,
                    mid=current_price,
                    atr=atr,
                    source=source,
                    confidence=confidence,
                    prediction_label=label,
                )
            else:
                logger.debug("LSTM confidence below threshold, falling back to ATR")
        except Exception as e:
            logger.warning("LSTM prediction failed, falling back to ATR: %s", e)

    return RangeResult(
        upper=atr_upper,
        lower=atr_lower,
        mid=current_price,
        atr=atr,
        source=source,
        confidence=confidence,
    )


def detect_range_breakout(price: float, current_range: RangeResult, buffer_pct: float = 0.5) -> str | None:
    """Detect if price has broken out of the current range.
    Returns 'up', 'down', or None."""
    buffer = current_range.spread * buffer_pct / 100
    if price > current_range.upper + buffer:
        return "up"
    if price < current_range.lower - buffer:
        return "down"
    return None


def shift_range(current_range: RangeResult, direction: str, shift_pct: float = 50.0) -> RangeResult:
    """Shift the range in the breakout direction by shift_pct of the spread."""
    shift = current_range.spread * shift_pct / 100
    if direction == "up":
        new_upper = current_range.upper + shift
        new_lower = current_range.lower + shift
    else:
        new_upper = current_range.upper - shift
        new_lower = current_range.lower - shift

    new_mid = (new_upper + new_lower) / 2
    return RangeResult(
        upper=new_upper,
        lower=new_lower,
        mid=new_mid,
        atr=current_range.atr,
        source=current_range.source,
        confidence=current_range.confidence,
        prediction_label=f"Range shifted {direction}",
    )
