"""Spread monitor — adapts grid spacing to real-time liquidity.

Tracks bid-ask spreads per pair and computes optimal grid spacing
that stays profitable even when spreads widen (nights, weekends).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

HISTORY_MINUTES = 120
MAX_ENTRIES = 120
LONG_HISTORY_HOURS = 168
MAX_LONG = 1008


@dataclass
class SpreadEntry:
    timestamp: float
    bid: float
    ask: float
    spread_bps: float


@dataclass
class PairSpread:
    pair: str
    recent: deque = field(default_factory=lambda: deque(maxlen=MAX_ENTRIES))
    weekly: deque = field(default_factory=lambda: deque(maxlen=MAX_LONG))


class SpreadMonitor:
    """Monitors bid-ask spreads and recommends liquidity-aware grid spacing."""

    def __init__(self, pairs: list[str] | None = None):
        self._pairs: dict[str, PairSpread] = {}
        for p in (pairs or []):
            self._pairs[p] = PairSpread(pair=p)

    def _get(self, pair: str) -> PairSpread:
        if pair not in self._pairs:
            self._pairs[pair] = PairSpread(pair=pair)
        return self._pairs[pair]

    def update(self, pair: str, bid: float, ask: float) -> None:
        """Record a bid/ask snapshot."""
        if bid <= 0 or ask <= 0 or ask <= bid:
            return
        mid = (bid + ask) / 2.0
        spread_bps = (ask - bid) / mid * 10_000
        entry = SpreadEntry(
            timestamp=time.time(), bid=bid, ask=ask, spread_bps=spread_bps,
        )
        ps = self._get(pair)
        ps.recent.append(entry)
        if len(ps.weekly) == 0 or time.time() - ps.weekly[-1].timestamp >= 600:
            ps.weekly.append(entry)

    def current_spread_bps(self, pair: str) -> float:
        """Most recent spread in basis points."""
        ps = self._get(pair)
        if not ps.recent:
            return 0.0
        return ps.recent[-1].spread_bps

    def avg_spread_bps(self, pair: str, minutes: int = 60) -> float:
        """Average spread over the last N minutes."""
        ps = self._get(pair)
        if not ps.recent:
            return 0.0
        cutoff = time.time() - minutes * 60
        vals = [e.spread_bps for e in ps.recent if e.timestamp >= cutoff]
        if not vals:
            vals = [e.spread_bps for e in ps.recent]
        return sum(vals) / len(vals)

    def spread_percentile(self, pair: str) -> float:
        """Current spread as percentile of weekly history (0-100).

        90 = spread is wider than 90% of recent observations.
        """
        ps = self._get(pair)
        if not ps.weekly or not ps.recent:
            return 50.0
        current = ps.recent[-1].spread_bps
        below = sum(1 for e in ps.weekly if e.spread_bps <= current)
        return below / len(ps.weekly) * 100.0

    def is_wide_spread(self, pair: str) -> bool:
        """True if current spread is >2× the 60-minute average."""
        current = self.current_spread_bps(pair)
        avg = self.avg_spread_bps(pair, 60)
        if avg <= 0:
            return False
        return current > avg * 2.0

    def optimal_spacing(self, pair: str, base_spacing: float,
                        fee_rate: float = 0.001) -> float:
        """Compute liquidity-aware optimal spacing.

        Ensures grid spacing always exceeds:
          - 2× current spread (to survive the bid-ask crossing)
          - 2× roundtrip fees (to be profitable after costs)
        Then pads by 50% as a safety margin.
        """
        spread_bps = self.current_spread_bps(pair)
        if spread_bps <= 0:
            return base_spacing

        min_spread_spacing = spread_bps * 2.0 / 10_000
        min_fee_spacing = fee_rate * 2.0
        floor = max(min_spread_spacing, min_fee_spacing) * 1.5

        optimal = max(base_spacing, floor)

        if optimal > base_spacing * 1.01:
            logger.debug(
                "Spread %s: %.1f bps → spacing %.4f%% (base %.4f%%)",
                pair, spread_bps, optimal * 100, base_spacing * 100,
            )

        return optimal

    def get_pair_metrics(self, pair: str) -> dict:
        """Metrics for a single pair (dashboard / status)."""
        ps = self._get(pair)
        current = self.current_spread_bps(pair)
        avg_60 = self.avg_spread_bps(pair, 60)
        pct = self.spread_percentile(pair)
        wide = self.is_wide_spread(pair)

        history: list[dict] = []
        now = time.time()
        for e in ps.recent:
            if now - e.timestamp <= 7200:
                history.append({
                    "t": round(e.timestamp),
                    "bps": round(e.spread_bps, 2),
                })

        return {
            "current_bps": round(current, 2),
            "avg_60m_bps": round(avg_60, 2),
            "percentile": round(pct, 1),
            "is_wide": wide,
            "history": history[-30:],
        }

    def get_metrics(self) -> dict:
        """All pairs spread summary."""
        return {p: self.get_pair_metrics(p) for p in self._pairs}
