"""Lightweight backtesting engine for RichBot grid strategies.

Simulates the full grid bot on historical OHLCV candles:
  - RegimeDetector (ensemble) for market classification
  - GridEngine for level computation
  - FeeEngine for realistic cost modelling
  - InventoryTracker for position-based PnL
  - Slippage + partial-fill simulation

Usage (CLI):
    python -m bot.backtest --pair BTC/USDC --days 90 --capital 200
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from bot.config import BotConfig, ATRConfig
from bot.dynamic_range import RangeResult, compute_dynamic_range
from bot.fee_engine import FeeEngine
from bot.grid_engine import GridEngine
from bot.inventory import InventoryTracker
from bot.regime_detector import Regime, RegimeDetector

logger = logging.getLogger(__name__)

# ── Result dataclass ─────────────────────────────────────────────────

@dataclass
class BacktestResult:
    pair: str
    days: int
    initial_capital: float
    total_pnl: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    avg_trade_pnl: float = 0.0
    equity_curve: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    regime_changes: list = field(default_factory=list)
    monthly_returns: list = field(default_factory=list)
    total_fees: float = 0.0
    buy_count: int = 0
    sell_count: int = 0
    final_equity: float = 0.0
    avg_slippage_bps: float = 0.0
    candles_processed: int = 0
    duration_sec: float = 0.0

    def summary(self) -> str:
        lines = [
            f"{'═' * 52}",
            f"  BACKTEST  {self.pair}  ({self.days}d, ${self.initial_capital:.0f})",
            f"{'═' * 52}",
            f"  PnL Total        {self.total_pnl:+.4f} USDC",
            f"  PnL %            {self.total_pnl / self.initial_capital * 100:+.2f}%",
            f"  Trades           {self.total_trades}  (B:{self.buy_count} S:{self.sell_count})",
            f"  Win Rate         {self.win_rate:.1f}%",
            f"  Sharpe           {self.sharpe_ratio:.2f}",
            f"  Max Drawdown     {self.max_drawdown:.2f}%",
            f"  Profit Factor    {self.profit_factor:.2f}",
            f"  Avg Trade PnL    {self.avg_trade_pnl:.6f}",
            f"  Fees Paid        {self.total_fees:.4f}",
            f"  Avg Slippage     {self.avg_slippage_bps:.1f} bps",
            f"  Final Equity     {self.final_equity:.2f}",
            f"  Candles          {self.candles_processed}",
            f"  Duration         {self.duration_sec:.1f}s",
            f"{'─' * 52}",
        ]
        if self.monthly_returns:
            lines.append("  Monthly Returns:")
            for i, r in enumerate(self.monthly_returns):
                lines.append(f"    Month {i + 1:>2}:  {r:+.2f}%")
        lines.append(f"{'═' * 52}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "pair": self.pair,
            "days": self.days,
            "initial_capital": self.initial_capital,
            "total_pnl": round(self.total_pnl, 6),
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "max_drawdown": round(self.max_drawdown, 3),
            "profit_factor": round(self.profit_factor, 3),
            "avg_trade_pnl": round(self.avg_trade_pnl, 6),
            "total_fees": round(self.total_fees, 6),
            "buy_count": self.buy_count,
            "sell_count": self.sell_count,
            "final_equity": round(self.final_equity, 4),
            "avg_slippage_bps": round(self.avg_slippage_bps, 2),
            "candles_processed": self.candles_processed,
            "duration_sec": round(self.duration_sec, 2),
            "equity_curve": self.equity_curve[-500:],
            "trades": self.trades[-200:],
            "regime_changes": self.regime_changes,
            "monthly_returns": [round(r, 4) for r in self.monthly_returns],
        }


# ── Simulated fill ───────────────────────────────────────────────────

@dataclass
class SimFill:
    side: str
    price: float
    amount: float
    fee: float
    slippage_bps: float
    timestamp: float


# ── Engine ───────────────────────────────────────────────────────────

_PARTIAL_FILL_PROB = 0.05
_MAX_SLIPPAGE_BPS = 2.0


class BacktestEngine:
    """Runs a grid strategy simulation on historical OHLCV data."""

    def __init__(
        self,
        config: BotConfig | None = None,
        initial_capital: float = 200.0,
        maker_fee: float = 0.001,
        taker_fee: float = 0.001,
        seed: int | None = None,
    ):
        self.config = config or BotConfig()
        self.initial_capital = initial_capital
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self._rng = random.Random(seed)

    async def run(
        self,
        pair: str,
        ohlcv: np.ndarray,
        days: int = 30,
    ) -> BacktestResult:
        """Simulate grid bot on historical candles.

        Args:
            pair:  Trading pair symbol (e.g. "BTC/USDC")
            ohlcv: Nx6 array [timestamp, open, high, low, close, volume]
            days:  Label for result (actual candle count determines duration)
        """
        t0 = time.time()

        regime = RegimeDetector()
        fee_engine = FeeEngine(self.maker_fee, self.taker_fee)
        inventory = InventoryTracker()
        grid = GridEngine(
            grid_count=self.config.grid.grid_count,
            spacing_percent=self.config.grid.spacing_percent,
            amount_per_order=self.config.grid.amount_per_order,
            infinity_mode=self.config.grid.infinity_mode,
            trail_trigger_percent=self.config.grid.trail_trigger_percent,
        )

        quote_balance = self.initial_capital
        equity_curve: list[tuple[float, float]] = []
        trades: list[dict] = []
        regime_changes: list[dict] = []
        trade_pnls: list[float] = []
        slippages: list[float] = []
        peak_equity = self.initial_capital

        lookback = max(50, self.config.atr.period + 5)
        n = len(ohlcv)
        if n < lookback + 10:
            return BacktestResult(
                pair=pair, days=days, initial_capital=self.initial_capital,
                candles_processed=0, duration_sec=time.time() - t0,
                final_equity=self.initial_capital,
            )

        prev_regime = Regime.RANGING
        grid_initialized = False

        for i in range(lookback, n):
            window = ohlcv[max(0, i - 200):i + 1]
            candle = ohlcv[i]
            ts = float(candle[0])
            o, h, l, c, v = float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4]), float(candle[5])
            price = c

            inv = inventory.get_inventory(pair)
            base_value = inv.base_inventory * price
            equity = quote_balance + base_value

            if equity > peak_equity:
                peak_equity = equity
            dd_pct = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0

            equity_curve.append((ts, round(equity, 4)))

            # -- regime detection every candle --
            new_regime = regime.update(window)
            if new_regime != prev_regime:
                regime_changes.append({
                    "candle": i, "ts": ts,
                    "from": prev_regime.value, "to": new_regime.value,
                    "confidence": regime._confidence,
                })
                prev_regime = new_regime

            rp = regime.get_grid_params()

            # -- (re)build grid periodically --
            rebuild = False
            if not grid_initialized:
                rebuild = True
                grid_initialized = True
            elif i % 24 == 0:
                rebuild = True
            elif grid.check_trail_needed(price):
                rebuild = True

            if rebuild:
                df = pd.DataFrame(
                    window, columns=["timestamp", "open", "high", "low", "close", "volume"],
                )
                range_result = compute_dynamic_range(
                    df, price, self.config.atr,
                    self.config.grid.range_multiplier,
                )

                buy_budget = quote_balance * rp.target_ratio
                base_inv = inv.base_inventory
                sell_budget = base_inv

                grid.spacing_percent = self.config.grid.spacing_percent * rp.spacing_mult
                fee_engine.apply_to_grid(grid, pair)

                bc = grid.grid_count // 2
                sc = grid.grid_count - bc

                if rp.max_levels:
                    bc = min(bc, rp.max_levels // 2)
                    sc = min(sc, rp.max_levels - bc)

                grid.calculate_grid(
                    range_result, price,
                    buy_count=bc, sell_count=sc,
                    buy_budget=buy_budget, sell_budget=sell_budget,
                    min_distance_pct=rp.min_distance_pct,
                )

            # -- simulate fills within this candle --
            fills = self._simulate_candle_fills(
                grid, pair, h, l, price, ts, fee_engine,
            )

            for fill in fills:
                if fill.side == "buy":
                    cost = fill.price * fill.amount + fill.fee
                    if quote_balance < cost:
                        continue
                    quote_balance -= cost
                    pnl = inventory.record_buy(pair, fill.price, fill.amount, fill.fee)
                else:
                    inv_now = inventory.get_inventory(pair)
                    if inv_now.base_inventory < fill.amount * 0.99:
                        continue
                    revenue = fill.price * fill.amount - fill.fee
                    quote_balance += revenue
                    pnl = inventory.record_sell(pair, fill.price, fill.amount, fill.fee)

                trade_pnls.append(pnl)
                slippages.append(fill.slippage_bps)
                trades.append({
                    "candle": i, "ts": ts,
                    "side": fill.side, "price": round(fill.price, 2),
                    "amount": round(fill.amount, 8), "fee": round(fill.fee, 6),
                    "pnl": round(pnl, 6), "slippage_bps": round(fill.slippage_bps, 2),
                    "equity": round(quote_balance + inventory.get_inventory(pair).base_inventory * price, 4),
                })

        # -- compute final metrics --
        inv_final = inventory.get_inventory(pair)
        final_price = float(ohlcv[-1][4])
        final_equity = quote_balance + inv_final.base_inventory * final_price
        total_pnl = final_equity - self.initial_capital

        wins = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p <= 0]
        win_rate = len(wins) / len(trade_pnls) * 100 if trade_pnls else 0
        profit_factor = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else float("inf") if wins else 0
        avg_trade = sum(trade_pnls) / len(trade_pnls) if trade_pnls else 0

        eq_values = [e[1] for e in equity_curve]
        sharpe = self._compute_sharpe(eq_values)
        max_dd = self._compute_max_drawdown(eq_values)
        monthly = self._compute_monthly_returns(equity_curve, self.initial_capital)

        result = BacktestResult(
            pair=pair,
            days=days,
            initial_capital=self.initial_capital,
            total_pnl=total_pnl,
            total_trades=len(trade_pnls),
            win_rate=win_rate,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            profit_factor=profit_factor if math.isfinite(profit_factor) else 999.0,
            avg_trade_pnl=avg_trade,
            equity_curve=equity_curve,
            trades=trades,
            regime_changes=regime_changes,
            monthly_returns=monthly,
            total_fees=inv_final.total_fees,
            buy_count=inv_final.buy_count,
            sell_count=inv_final.sell_count,
            final_equity=final_equity,
            avg_slippage_bps=sum(slippages) / len(slippages) if slippages else 0,
            candles_processed=n - lookback,
            duration_sec=time.time() - t0,
        )
        return result

    # ── fill simulation ───────────────────────────────────────────

    def _simulate_candle_fills(
        self,
        grid: GridEngine,
        pair: str,
        high: float,
        low: float,
        close: float,
        ts: float,
        fee_engine: FeeEngine,
    ) -> list[SimFill]:
        """Check which grid levels would have been triggered in this candle."""
        fills: list[SimFill] = []
        levels = list(grid.state.active_levels)
        self._rng.shuffle(levels)

        for level in levels:
            if level.filled:
                continue

            triggered = False
            if level.side == "buy" and low <= level.price:
                triggered = True
            elif level.side == "sell" and high >= level.price:
                triggered = True

            if not triggered:
                continue

            slip_bps = self._rng.uniform(0, _MAX_SLIPPAGE_BPS)
            slip_frac = slip_bps / 10_000
            if level.side == "buy":
                fill_price = level.price * (1 + slip_frac)
            else:
                fill_price = level.price * (1 - slip_frac)

            amount = level.amount
            if self._rng.random() < _PARTIAL_FILL_PROB:
                fill_pct = self._rng.uniform(0.3, 0.9)
                amount = level.amount * fill_pct

            fees = fee_engine.get_fees(pair)
            fee = fill_price * amount * fees.maker

            fills.append(SimFill(
                side=level.side, price=fill_price,
                amount=amount, fee=fee,
                slippage_bps=slip_bps, timestamp=ts,
            ))

            level.filled = True
            grid.state.invalidate()

        return fills

    # ── statistical helpers ───────────────────────────────────────

    @staticmethod
    def _compute_sharpe(equity_values: list[float], periods_per_year: float = 8760) -> float:
        """Annualized Sharpe ratio from hourly equity snapshots."""
        if len(equity_values) < 10:
            return 0.0
        arr = np.array(equity_values, dtype=np.float64)
        returns = np.diff(arr) / arr[:-1]
        returns = returns[np.isfinite(returns)]
        if len(returns) < 2 or np.std(returns) < 1e-12:
            return 0.0
        return float(np.mean(returns) / np.std(returns) * np.sqrt(periods_per_year))

    @staticmethod
    def _compute_max_drawdown(equity_values: list[float]) -> float:
        """Maximum drawdown as a percentage."""
        if len(equity_values) < 2:
            return 0.0
        peak = equity_values[0]
        max_dd = 0.0
        for v in equity_values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def _compute_monthly_returns(
        equity_curve: list[tuple[float, float]],
        initial: float,
    ) -> list[float]:
        """Approximate monthly returns from timestamped equity curve."""
        if len(equity_curve) < 2:
            return []
        month_ms = 30 * 24 * 3600 * 1000
        start_ts = equity_curve[0][0]
        months: list[float] = []
        prev_eq = initial
        month_end = start_ts + month_ms

        for ts, eq in equity_curve:
            if ts >= month_end:
                ret = (eq - prev_eq) / prev_eq * 100 if prev_eq > 0 else 0
                months.append(ret)
                prev_eq = eq
                month_end += month_ms

        if equity_curve[-1][0] < month_end:
            last_eq = equity_curve[-1][1]
            ret = (last_eq - prev_eq) / prev_eq * 100 if prev_eq > 0 else 0
            months.append(ret)

        return months


# ── OHLCV fetcher (public Binance API, no auth) ─────────────────────

def fetch_historical_ohlcv(
    pair: str,
    days: int = 30,
    interval: str = "1h",
) -> np.ndarray:
    """Fetch historical OHLCV from Binance public API."""
    import json as _json
    from urllib.request import urlopen

    symbol = pair.replace("/", "")
    limit_per_req = 1000
    candles_needed = days * 24 if interval == "1h" else days * 24 * 4

    all_candles: list[list] = []
    end_time = int(time.time() * 1000)

    while len(all_candles) < candles_needed:
        batch = min(limit_per_req, candles_needed - len(all_candles))
        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval={interval}&limit={batch}&endTime={end_time}"
        )
        try:
            resp = urlopen(url, timeout=15)
            raw = _json.loads(resp.read().decode())
        except Exception as e:
            logger.warning("OHLCV fetch failed: %s", e)
            break

        if not raw:
            break

        parsed = [
            [int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])]
            for k in raw
        ]
        all_candles = parsed + all_candles
        end_time = int(raw[0][0]) - 1

        if len(raw) < batch:
            break

    return np.array(all_candles, dtype=np.float64) if all_candles else np.empty((0, 6))


# ── CLI ──────────────────────────────────────────────────────────────

async def _cli_main():
    parser = argparse.ArgumentParser(
        description="RichBot Backtest Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n  python -m bot.backtest --pair BTC/USDC --days 90 --capital 200",
    )
    parser.add_argument("--pair", default="BTC/USDC", help="Trading pair (default: BTC/USDC)")
    parser.add_argument("--days", type=int, default=30, help="Days of history (default: 30)")
    parser.add_argument("--capital", type=float, default=200.0, help="Initial capital (default: 200)")
    parser.add_argument("--grid-count", type=int, default=20, help="Grid level count (default: 20)")
    parser.add_argument("--spacing", type=float, default=0.5, help="Grid spacing %% (default: 0.5)")
    parser.add_argument("--maker-fee", type=float, default=0.001, help="Maker fee (default: 0.001)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument("--output", type=str, default=None, help="Save result JSON to file")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress log output")
    args = parser.parse_args()

    level = logging.WARNING if args.quiet else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")

    print(f"\nFetching {args.days}d of {args.pair} candles …")
    ohlcv = fetch_historical_ohlcv(args.pair, args.days, "1h")
    if len(ohlcv) < 100:
        print(f"ERROR: Only {len(ohlcv)} candles fetched — need at least 100.")
        sys.exit(1)
    print(f"  {len(ohlcv)} candles loaded ({ohlcv[0][0]:.0f} — {ohlcv[-1][0]:.0f})\n")

    config = BotConfig()
    config.grid.grid_count = args.grid_count
    config.grid.spacing_percent = args.spacing

    engine = BacktestEngine(
        config=config,
        initial_capital=args.capital,
        maker_fee=args.maker_fee,
        taker_fee=args.maker_fee,
        seed=args.seed,
    )

    result = await engine.run(args.pair, ohlcv, args.days)
    print(result.summary())

    out_path = args.output
    if out_path is None:
        os.makedirs("data", exist_ok=True)
        out_path = f"data/backtest_{args.pair.replace('/', '_')}_{args.days}d.json"

    with open(out_path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    print(f"\nResult saved to {out_path}")


def main():
    asyncio.run(_cli_main())


if __name__ == "__main__":
    main()
