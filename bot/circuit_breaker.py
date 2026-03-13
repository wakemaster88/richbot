"""Dynamic circuit breaker — adaptive drawdown protection.

Three escalation levels with volatility-adjusted thresholds:
  GREEN  → normal trading
  YELLOW → reduced size (−30%), wider spacing (+50%)
  ORANGE → heavily reduced (−60%), sells only
  RED    → full halt, cancel all orders

Thresholds scale with current ATR vs 30-day average ATR so
that calm markets trigger earlier and volatile markets later.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 50


class CBLevel(IntEnum):
    GREEN = 0
    YELLOW = 1
    ORANGE = 2
    RED = 3


_LEVEL_NAMES = {
    CBLevel.GREEN: "GREEN",
    CBLevel.YELLOW: "YELLOW",
    CBLevel.ORANGE: "ORANGE",
    CBLevel.RED: "RED",
}

YELLOW_MULT = 0.50
ORANGE_MULT = 0.75
RED_MULT = 1.00

YELLOW_SIZE_FACTOR = 0.70
ORANGE_SIZE_FACTOR = 0.40
YELLOW_SPACING_MULT = 1.50

COOLDOWN_RED = 3600
COOLDOWN_ORANGE = 1800
COOLDOWN_YELLOW = 600


@dataclass
class CBEvent:
    timestamp: float
    pair: str
    level: str
    drawdown_pct: float
    threshold_pct: float
    vol_adj: float
    duration_sec: float = 0.0


@dataclass
class PairCBState:
    pair: str
    level: CBLevel = CBLevel.GREEN
    drawdown_pct: float = 0.0
    peak_equity: float = 0.0
    current_equity: float = 0.0
    triggered_at: float = 0.0
    cooldown_until: float = 0.0
    vol_adj: float = 1.0
    atr_history: deque = field(default_factory=lambda: deque(maxlen=720))
    yellow_threshold: float = 0.0
    orange_threshold: float = 0.0
    red_threshold: float = 0.0


class CircuitBreaker:
    """Adaptive circuit breaker with per-pair state and cascade protection."""

    def __init__(self, base_threshold: float = 8.0, cascade_threshold: int = 2):
        self.base_threshold = base_threshold
        self.cascade_threshold = cascade_threshold
        self._pairs: dict[str, PairCBState] = {}
        self._history: deque[CBEvent] = deque(maxlen=HISTORY_LIMIT)
        self._global_halt = False
        self._global_halt_until: float = 0.0

    def _get(self, pair: str) -> PairCBState:
        if pair not in self._pairs:
            self._pairs[pair] = PairCBState(pair=pair)
        return self._pairs[pair]

    def update_atr(self, pair: str, atr_value: float):
        """Feed current ATR for volatility adjustment."""
        st = self._get(pair)
        st.atr_history.append(atr_value)
        if len(st.atr_history) >= 5:
            avg_atr = sum(st.atr_history) / len(st.atr_history)
            st.vol_adj = max(0.3, min(3.0, atr_value / avg_atr)) if avg_atr > 0 else 1.0
        self._recompute_thresholds(st)

    def _recompute_thresholds(self, st: PairCBState):
        va = st.vol_adj
        st.yellow_threshold = self.base_threshold * YELLOW_MULT * va
        st.orange_threshold = self.base_threshold * ORANGE_MULT * va
        st.red_threshold = self.base_threshold * RED_MULT * va

    def update_equity(self, pair: str, equity: float) -> CBLevel:
        """Update equity and return current circuit breaker level."""
        st = self._get(pair)
        st.current_equity = equity

        if equity > st.peak_equity:
            st.peak_equity = equity

        if st.peak_equity > 0:
            st.drawdown_pct = (st.peak_equity - equity) / st.peak_equity * 100
        else:
            st.drawdown_pct = 0.0

        if st.yellow_threshold == 0:
            self._recompute_thresholds(st)

        now = time.time()
        old_level = st.level

        if now < st.cooldown_until:
            return st.level

        if st.drawdown_pct >= st.red_threshold and st.red_threshold > 0:
            new_level = CBLevel.RED
        elif st.drawdown_pct >= st.orange_threshold and st.orange_threshold > 0:
            new_level = CBLevel.ORANGE
        elif st.drawdown_pct >= st.yellow_threshold and st.yellow_threshold > 0:
            new_level = CBLevel.YELLOW
        else:
            new_level = CBLevel.GREEN

        if new_level > old_level:
            st.level = new_level
            st.triggered_at = now
            self._set_cooldown(st, new_level)
            self._history.append(CBEvent(
                timestamp=now, pair=pair,
                level=_LEVEL_NAMES[new_level],
                drawdown_pct=round(st.drawdown_pct, 2),
                threshold_pct=round(self._threshold_for(st, new_level), 2),
                vol_adj=round(st.vol_adj, 2),
            ))
            logger.warning(
                "CIRCUIT BREAKER %s → %s: DD %.1f%% (threshold %.1f%%, vol_adj %.2f)",
                pair, _LEVEL_NAMES[new_level], st.drawdown_pct,
                self._threshold_for(st, new_level), st.vol_adj,
            )
        elif new_level < old_level and now >= st.cooldown_until:
            recovery_level = CBLevel(max(old_level - 1, CBLevel.GREEN))
            if new_level <= recovery_level:
                st.level = recovery_level
                if recovery_level == CBLevel.GREEN:
                    st.peak_equity = equity
                logger.info(
                    "CIRCUIT BREAKER %s: %s → %s (recovery, DD %.1f%%)",
                    pair, _LEVEL_NAMES[old_level], _LEVEL_NAMES[recovery_level],
                    st.drawdown_pct,
                )

        self._check_cascade()
        return st.level

    def _threshold_for(self, st: PairCBState, level: CBLevel) -> float:
        if level == CBLevel.YELLOW:
            return st.yellow_threshold
        if level == CBLevel.ORANGE:
            return st.orange_threshold
        return st.red_threshold

    def _set_cooldown(self, st: PairCBState, level: CBLevel):
        now = time.time()
        if level == CBLevel.RED:
            st.cooldown_until = now + COOLDOWN_RED
        elif level == CBLevel.ORANGE:
            st.cooldown_until = now + COOLDOWN_ORANGE
        elif level == CBLevel.YELLOW:
            st.cooldown_until = now + COOLDOWN_YELLOW

    def _check_cascade(self):
        """If N+ pairs are ORANGE/RED simultaneously, trigger global halt."""
        severe = sum(1 for st in self._pairs.values() if st.level >= CBLevel.ORANGE)
        if severe >= self.cascade_threshold and not self._global_halt:
            self._global_halt = True
            self._global_halt_until = time.time() + COOLDOWN_RED
            logger.critical(
                "CASCADE HALT: %d/%d pairs at ORANGE/RED — global trading stopped",
                severe, len(self._pairs),
            )
        elif self._global_halt and time.time() >= self._global_halt_until:
            severe_now = sum(1 for st in self._pairs.values() if st.level >= CBLevel.ORANGE)
            if severe_now < self.cascade_threshold:
                self._global_halt = False
                logger.info("CASCADE HALT lifted — %d pairs still elevated", severe_now)

    @property
    def is_global_halt(self) -> bool:
        if self._global_halt and time.time() >= self._global_halt_until:
            self._check_cascade()
        return self._global_halt

    def get_level(self, pair: str) -> CBLevel:
        return self._get(pair).level

    def can_trade(self, pair: str) -> tuple[bool, str]:
        """Check if trading is allowed. Returns (allowed, reason)."""
        if self.is_global_halt:
            return False, "Cascade halt — multiple pairs critical"
        st = self._get(pair)
        if st.level == CBLevel.RED:
            return False, f"Circuit breaker RED: DD {st.drawdown_pct:.1f}%"
        return True, ""

    def can_buy(self, pair: str) -> bool:
        """Buys blocked at ORANGE and above."""
        st = self._get(pair)
        if self.is_global_halt:
            return False
        return st.level < CBLevel.ORANGE

    def can_sell(self, pair: str) -> bool:
        """Sells blocked only at RED / global halt."""
        if self.is_global_halt:
            return False
        st = self._get(pair)
        return st.level < CBLevel.RED

    def size_factor(self, pair: str) -> float:
        """Multiplier for order sizes at current level."""
        st = self._get(pair)
        if st.level == CBLevel.YELLOW:
            return YELLOW_SIZE_FACTOR
        if st.level >= CBLevel.ORANGE:
            return ORANGE_SIZE_FACTOR
        return 1.0

    def spacing_mult(self, pair: str) -> float:
        """Multiplier for grid spacing at current level."""
        st = self._get(pair)
        if st.level == CBLevel.YELLOW:
            return YELLOW_SPACING_MULT
        return 1.0

    def get_pair_status(self, pair: str) -> dict:
        """Status dict for a single pair (for dashboard)."""
        st = self._get(pair)
        now = time.time()
        resume_sec = max(0, st.cooldown_until - now) if st.level > CBLevel.GREEN else 0
        return {
            "level": _LEVEL_NAMES[st.level],
            "drawdown_pct": round(st.drawdown_pct, 2),
            "peak_equity": round(st.peak_equity, 2),
            "vol_adj": round(st.vol_adj, 2),
            "yellow_threshold": round(st.yellow_threshold, 2),
            "orange_threshold": round(st.orange_threshold, 2),
            "red_threshold": round(st.red_threshold, 2),
            "size_factor": self.size_factor(pair),
            "spacing_mult": self.spacing_mult(pair),
            "can_buy": self.can_buy(pair),
            "can_sell": self.can_sell(pair),
            "resume_in_sec": round(resume_sec),
            "triggered_at": st.triggered_at if st.level > CBLevel.GREEN else 0,
        }

    def get_global_status(self) -> dict:
        """Portfolio-level circuit breaker state."""
        return {
            "global_halt": self.is_global_halt,
            "cascade_threshold": self.cascade_threshold,
            "pairs_at_orange_plus": sum(1 for s in self._pairs.values() if s.level >= CBLevel.ORANGE),
            "pairs_at_red": sum(1 for s in self._pairs.values() if s.level >= CBLevel.RED),
        }

    def get_history(self, limit: int = 20) -> list[dict]:
        """Recent CB events for dashboard history."""
        events = list(self._history)[-limit:]
        return [
            {
                "timestamp": e.timestamp,
                "pair": e.pair,
                "level": e.level,
                "drawdown_pct": e.drawdown_pct,
                "threshold_pct": e.threshold_pct,
                "vol_adj": e.vol_adj,
            }
            for e in reversed(events)
        ]

    def get_metrics(self) -> dict:
        """Full metrics blob for heartbeat / dashboard."""
        pair_statuses = {p: self.get_pair_status(p) for p in self._pairs}
        return {
            **self.get_global_status(),
            "pairs": pair_statuses,
            "history": self.get_history(10),
        }

    def reset_pair(self, pair: str):
        """Manual reset of a pair's CB state."""
        st = self._get(pair)
        st.level = CBLevel.GREEN
        st.cooldown_until = 0.0
        st.peak_equity = st.current_equity
        st.drawdown_pct = 0.0
        logger.info("Circuit breaker RESET for %s", pair)
