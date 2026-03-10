"""Backtesting engine for grid trading strategies."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from bot.config import BotConfig
from bot.dynamic_range import calculate_atr, compute_dynamic_range, detect_range_breakout, shift_range, RangeResult
from bot.grid_engine import GridEngine
from bot.risk_manager import RiskManager

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    timestamp: float
    side: str
    price: float
    amount: float
    pnl: float
    equity: float


@dataclass
class BacktestResult:
    total_return: float = 0.0
    annualized_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_trade_pnl: float = 0.0
    total_fees: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    drawdown_curve: list[float] = field(default_factory=list)
    trades: list[BacktestTrade] = field(default_factory=list)
    final_equity: float = 0.0
    initial_equity: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total_return": self.total_return,
            "annualized_return": self.annualized_return,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "avg_trade_pnl": self.avg_trade_pnl,
            "total_fees": self.total_fees,
            "final_equity": self.final_equity,
        }


class Backtester:
    """Simulates grid trading on historical OHLCV data."""

    def __init__(self, config: BotConfig, initial_equity: float = 10000.0, fee_rate: float = 0.001):
        self.config = config
        self.initial_equity = initial_equity
        self.fee_rate = fee_rate

    def run(self, ohlcv_df: pd.DataFrame, pair: str = "BTC/USDT",
            grid_count: int | None = None, spacing_percent: float | None = None,
            atr_multiplier: float | None = None, range_multiplier: float | None = None,
            amount_per_order: float | None = None, kelly_fraction: float | None = None,
            ) -> BacktestResult:
        """Run backtest on OHLCV data.

        Override parameters can be passed for Optuna optimization.
        """
        gc = grid_count or self.config.grid.grid_count
        sp = spacing_percent or self.config.grid.spacing_percent
        am = atr_multiplier or self.config.atr.multiplier
        rm = range_multiplier or self.config.grid.range_multiplier
        apo = amount_per_order or self.config.grid.amount_per_order
        kf = kelly_fraction or self.config.risk.kelly_fraction

        risk_config = self.config.risk
        risk_config.kelly_fraction = kf
        risk_mgr = RiskManager(risk_config)

        grid = GridEngine(
            grid_count=gc,
            spacing_percent=sp,
            amount_per_order=apo,
            infinity_mode=self.config.grid.infinity_mode,
            trail_trigger_percent=self.config.grid.trail_trigger_percent,
        )

        equity = self.initial_equity
        peak_equity = equity
        max_dd = 0.0
        position = 0.0
        avg_entry = 0.0
        trades: list[BacktestTrade] = []
        equity_curve = [equity]
        dd_curve = [0.0]
        total_fees = 0.0

        if len(ohlcv_df) < self.config.atr.period + 5:
            return BacktestResult(initial_equity=self.initial_equity, final_equity=equity)

        warmup = max(self.config.atr.period + 5, 30)
        current_range: RangeResult | None = None
        recalc_interval = 24

        for i in range(warmup, len(ohlcv_df)):
            row = ohlcv_df.iloc[i]
            price = row["close"]
            high = row["high"]
            low = row["low"]
            ts = row.get("timestamp", i)

            if current_range is None or i % recalc_interval == 0:
                window = ohlcv_df.iloc[max(0, i - 200):i]
                from bot.config import ATRConfig
                atr_cfg = ATRConfig(period=self.config.atr.period, timeframe=self.config.atr.timeframe, multiplier=am)
                current_range = compute_dynamic_range(window, price, atr_cfg, rm)
                vol = risk_mgr.calculate_volatility(window["close"].values)
                dynamic_amount = risk_mgr.calculate_position_size(equity, price, vol) if vol > 0 else apo
                grid_levels = grid.calculate_grid(current_range, price, dynamic_amount)

            breakout = detect_range_breakout(price, current_range)
            if breakout and self.config.grid.infinity_mode:
                current_range = shift_range(current_range, breakout)
                vol = risk_mgr.calculate_volatility(ohlcv_df.iloc[max(0, i - 200):i]["close"].values)
                dynamic_amount = risk_mgr.calculate_position_size(equity, price, vol) if vol > 0 else apo
                grid_levels = grid.calculate_grid(current_range, price, dynamic_amount)

            for level in grid.state.levels:
                if level.filled:
                    continue

                filled = False
                if level.side == "buy" and low <= level.price:
                    filled = True
                elif level.side == "sell" and high >= level.price:
                    filled = True

                if filled:
                    level.filled = True
                    fee = level.price * level.amount * self.fee_rate
                    total_fees += fee

                    if level.side == "buy":
                        cost = level.price * level.amount + fee
                        if cost > equity:
                            continue
                        equity -= cost
                        position += level.amount
                        if position > 0:
                            avg_entry = ((avg_entry * (position - level.amount)) + level.price * level.amount) / position
                    else:
                        if level.amount > position:
                            continue
                        revenue = level.price * level.amount - fee
                        pnl = (level.price - avg_entry) * level.amount - fee
                        equity += revenue
                        position -= level.amount
                        risk_mgr.record_trade(pnl)

                        trades.append(BacktestTrade(
                            timestamp=ts, side=level.side, price=level.price,
                            amount=level.amount, pnl=pnl, equity=equity,
                        ))

            unrealized = position * price if position > 0 else 0
            total_equity = equity + unrealized

            if total_equity > peak_equity:
                peak_equity = total_equity
            dd = (peak_equity - total_equity) / peak_equity * 100 if peak_equity > 0 else 0
            max_dd = max(max_dd, dd)

            risk_status = risk_mgr.update_equity(total_equity)
            if risk_status["is_paused"]:
                break

            equity_curve.append(total_equity)
            dd_curve.append(dd)

        final_equity = equity + position * ohlcv_df.iloc[-1]["close"]
        total_return = (final_equity - self.initial_equity) / self.initial_equity * 100
        days = len(ohlcv_df) / 24
        ann_return = ((1 + total_return / 100) ** (365 / max(days, 1)) - 1) * 100 if days > 0 else 0

        returns = np.diff(equity_curve) / np.array(equity_curve[:-1]) if len(equity_curve) > 1 else np.array([0])
        sharpe = 0.0
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(365 * 24))

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        win_rate = len(wins) / len(trades) if trades else 0
        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return BacktestResult(
            total_return=total_return,
            annualized_return=ann_return,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            total_trades=len(trades),
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_trade_pnl=sum(t.pnl for t in trades) / len(trades) if trades else 0,
            total_fees=total_fees,
            equity_curve=equity_curve,
            drawdown_curve=dd_curve,
            trades=trades,
            final_equity=final_equity,
            initial_equity=self.initial_equity,
        )

    def fetch_historical_data(self, exchange, pair: str, timeframe: str = "1h",
                               days: int = 60) -> pd.DataFrame:
        """Fetch OHLCV data for backtesting."""
        since = int((time.time() - days * 86400) * 1000)
        all_data = []
        limit = 1000

        while True:
            candles = exchange.fetch_ohlcv(pair, timeframe=timeframe, since=since, limit=limit)
            if not candles:
                break
            all_data.extend(candles)
            since = candles[-1][0] + 1
            if len(candles) < limit:
                break

        if not all_data:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

        logger.info("Fetched %d candles for %s (%s, %d days)", len(df), pair, timeframe, days)
        return df
