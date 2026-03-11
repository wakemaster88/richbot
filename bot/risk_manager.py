"""Advanced Risk Management: Kelly Criterion, drawdown stops, trailing stops, volatility sizing."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np

from bot.config import RiskConfig

logger = logging.getLogger(__name__)


@dataclass
class TrailingStop:
    grid_level: float
    side: str  # "buy" or "sell"
    entry_price: float
    highest_price: float = 0.0
    lowest_price: float = float("inf")
    stop_price: float = 0.0
    triggered: bool = False

    def update(self, current_price: float, trail_percent: float) -> bool:
        """Update trailing stop. Returns True if stop triggered."""
        if self.triggered:
            return True

        if self.side == "buy":
            if current_price > self.highest_price:
                self.highest_price = current_price
                self.stop_price = current_price * (1 - trail_percent / 100)
            if current_price <= self.stop_price and self.stop_price > 0:
                self.triggered = True
                return True
        else:
            if current_price < self.lowest_price:
                self.lowest_price = current_price
                self.stop_price = current_price * (1 + trail_percent / 100)
            if current_price >= self.stop_price and self.stop_price < float("inf"):
                self.triggered = True
                return True
        return False


@dataclass
class RiskState:
    peak_equity: float = 0.0
    current_equity: float = 0.0
    current_drawdown: float = 0.0
    is_paused: bool = False
    pause_reason: str = ""
    pause_timestamp: float = 0.0
    trailing_stops: dict[str, TrailingStop] = field(default_factory=dict)
    win_count: int = 0
    loss_count: int = 0
    total_profit: float = 0.0
    total_loss: float = 0.0


class RiskManager:
    """Manages position sizing, drawdown protection, and trailing stops."""

    DRAWDOWN_COOLDOWN_SEC = 300  # 5 min cooldown, then auto-resume

    def __init__(self, config: RiskConfig):
        self.config = config
        self.state = RiskState()
        self._stop_pairs: dict[str, str] = {}

    def calculate_kelly_fraction(self, win_rate: float | None = None, avg_win: float | None = None,
                                  avg_loss: float | None = None) -> float:
        """Calculate Kelly Criterion for optimal position sizing.
        Kelly% = W - (1-W)/R where W = win rate, R = avg_win/avg_loss ratio."""
        if win_rate is None:
            total = self.state.win_count + self.state.loss_count
            if total < 10:
                return self.config.kelly_fraction
            win_rate = self.state.win_count / total

        if avg_win is None:
            avg_win = self.state.total_profit / max(self.state.win_count, 1)
        if avg_loss is None:
            avg_loss = abs(self.state.total_loss) / max(self.state.loss_count, 1)

        if avg_loss == 0:
            return self.config.kelly_fraction

        r = avg_win / avg_loss
        kelly = win_rate - (1 - win_rate) / r

        kelly = max(0, min(kelly, 1.0))
        scaled_kelly = kelly * self.config.kelly_fraction

        logger.debug("Kelly: raw=%.4f, scaled=%.4f (WR=%.2f, R=%.2f)", kelly, scaled_kelly, win_rate, r)
        return scaled_kelly

    def calculate_position_size(self, balance: float, price: float,
                                  volatility: float | None = None) -> float:
        """Dynamic position sizing based on Kelly + volatility + drawdown scaling."""
        kelly = self.calculate_kelly_fraction()
        base_amount = (balance * kelly) / price

        if self.config.volatility_scaling and volatility is not None and volatility > 0:
            target_vol = 0.02
            vol_scalar = target_vol / volatility
            vol_scalar = max(0.3, min(vol_scalar, 2.0))
            base_amount *= vol_scalar
            logger.debug("Volatility scaling: vol=%.4f, scalar=%.2f", volatility, vol_scalar)

        dd_pct = self.state.current_drawdown
        dd_limit = self.config.max_drawdown_percent
        if dd_pct > dd_limit * 0.5:
            dd_scalar = max(0.2, 1.0 - (dd_pct - dd_limit * 0.5) / (dd_limit * 0.5))
            base_amount *= dd_scalar
            logger.debug("Drawdown scaling: dd=%.2f%%, scalar=%.2f", dd_pct, dd_scalar)

        max_amount = (balance * self.config.max_position_percent / 100) / price
        amount = min(base_amount, max_amount)
        amount = max(amount, self.config.min_order_amount)

        return amount

    def update_equity(self, equity: float) -> dict:
        """Update equity tracking. Returns risk status dict."""
        self.state.current_equity = equity

        if equity > self.state.peak_equity:
            self.state.peak_equity = equity

        if self.state.peak_equity > 0:
            self.state.current_drawdown = (self.state.peak_equity - equity) / self.state.peak_equity * 100
        else:
            self.state.current_drawdown = 0.0

        if self.state.current_drawdown >= self.config.max_drawdown_percent and not self.state.is_paused:
            self.state.is_paused = True
            self.state.pause_reason = f"Max drawdown reached: {self.state.current_drawdown:.2f}%"
            self.state.pause_timestamp = time.time()
            logger.critical("RISK: Bot paused — %s", self.state.pause_reason)

        return {
            "equity": equity,
            "peak": self.state.peak_equity,
            "drawdown_pct": self.state.current_drawdown,
            "is_paused": self.state.is_paused,
            "pause_reason": self.state.pause_reason,
        }

    def record_trade(self, pnl: float):
        """Record a trade result for Kelly calculations."""
        if pnl > 0:
            self.state.win_count += 1
            self.state.total_profit += pnl
        elif pnl < 0:
            self.state.loss_count += 1
            self.state.total_loss += pnl

    def add_trailing_stop(self, level_id: str, side: str, entry_price: float,
                          pair: str = ""):
        """Add a trailing stop for a grid level."""
        self.state.trailing_stops[level_id] = TrailingStop(
            grid_level=entry_price,
            side=side,
            entry_price=entry_price,
            highest_price=entry_price if side == "buy" else 0.0,
            lowest_price=entry_price if side == "sell" else float("inf"),
        )
        self._stop_pairs[level_id] = pair

    def check_trailing_stops(self, current_price: float, pair: str = "") -> list[str]:
        """Check trailing stops for a specific pair. Returns list of triggered level IDs."""
        triggered = []
        for level_id, stop in self.state.trailing_stops.items():
            if pair and self._stop_pairs.get(level_id, "") != pair:
                continue
            if stop.update(current_price, self.config.trailing_stop_percent):
                triggered.append(level_id)
                logger.info("Trailing stop triggered: %s at %.2f (stop=%.2f)", level_id, current_price, stop.stop_price)
        for lid in triggered:
            del self.state.trailing_stops[lid]
            self._stop_pairs.pop(lid, None)
        return triggered

    def can_trade(self) -> tuple[bool, str]:
        """Check if trading is allowed. Auto-resumes after cooldown."""
        if self.state.is_paused:
            elapsed = time.time() - self.state.pause_timestamp
            if elapsed >= self.DRAWDOWN_COOLDOWN_SEC:
                logger.info("Drawdown-Pause abgelaufen (%.0fs) — Trading wird fortgesetzt", elapsed)
                self.resume()
                return True, ""
            return False, self.state.pause_reason
        return True, ""

    def resume(self):
        """Manually resume trading after drawdown pause."""
        self.state.is_paused = False
        self.state.pause_reason = ""
        self.state.peak_equity = self.state.current_equity
        logger.info("Trading resumed, peak equity reset to %.2f", self.state.current_equity)

    def calculate_volatility(self, closes: list[float] | np.ndarray) -> float:
        """Calculate annualized volatility from close prices."""
        if len(closes) < 2:
            return 0.0
        returns = np.diff(np.log(closes))
        return float(np.std(returns) * np.sqrt(365 * 24))

    def get_risk_metrics(self) -> dict:
        total_trades = self.state.win_count + self.state.loss_count
        win_rate = self.state.win_count / total_trades if total_trades > 0 else 0.0
        avg_win = self.state.total_profit / max(self.state.win_count, 1)
        avg_loss = abs(self.state.total_loss) / max(self.state.loss_count, 1)
        profit_factor = self.state.total_profit / abs(self.state.total_loss) if self.state.total_loss != 0 else float("inf")

        return {
            "total_trades": total_trades,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "kelly_fraction": self.calculate_kelly_fraction(),
            "current_drawdown": self.state.current_drawdown,
            "max_drawdown_limit": self.config.max_drawdown_percent,
            "is_paused": self.state.is_paused,
            "active_trailing_stops": len(self.state.trailing_stops),
        }
