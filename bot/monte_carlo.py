"""Monte-Carlo stress-test for grid strategy risk quantification.

Generates synthetic price paths using Geometric Brownian Motion with
Student-t fat tails and optional regime-switch volatility shocks,
then runs the BacktestEngine on each path to build a PnL distribution.

Answers: "What is the worst-case scenario for the next 30 days?"
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np

from bot.backtest import BacktestEngine, BacktestResult
from bot.config import BotConfig

logger = logging.getLogger(__name__)

CANDLES_PER_DAY = 24


# ── result ────────────────────────────────────────────────────────────

@dataclass
class MonteCarloResult:
    pair: str
    days_forward: int
    n_simulations: int
    initial_capital: float
    median_pnl: float = 0.0
    percentile_5: float = 0.0
    percentile_1: float = 0.0
    max_loss: float = 0.0
    best_case: float = 0.0
    probability_of_loss: float = 0.0
    value_at_risk_95: float = 0.0
    conditional_var_95: float = 0.0
    mean_pnl: float = 0.0
    std_pnl: float = 0.0
    mean_max_drawdown: float = 0.0
    mean_trades: float = 0.0
    distribution: list = field(default_factory=list)
    duration_sec: float = 0.0

    def summary(self) -> str:
        lines = [
            f"{'═' * 56}",
            f"  MONTE-CARLO  {self.pair}  ({self.n_simulations} sims, {self.days_forward}d)",
            f"{'═' * 56}",
            f"  Median PnL       {self.median_pnl:+.4f}",
            f"  Mean PnL         {self.mean_pnl:+.4f}  (σ {self.std_pnl:.4f})",
            f"  Best Case        {self.best_case:+.4f}",
            f"  5th Percentile   {self.percentile_5:+.4f}",
            f"  1st Percentile   {self.percentile_1:+.4f}",
            f"  Max Loss         {self.max_loss:+.4f}",
            f"  P(Loss)          {self.probability_of_loss:.1f}%",
            f"  VaR 95%          {self.value_at_risk_95:+.4f}",
            f"  CVaR 95%         {self.conditional_var_95:+.4f}",
            f"  Avg Max DD       {self.mean_max_drawdown:.2f}%",
            f"  Avg Trades       {self.mean_trades:.0f}",
            f"  Duration         {self.duration_sec:.1f}s",
            f"{'═' * 56}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "pair": self.pair,
            "days_forward": self.days_forward,
            "n_simulations": self.n_simulations,
            "initial_capital": self.initial_capital,
            "median_pnl": round(self.median_pnl, 6),
            "mean_pnl": round(self.mean_pnl, 6),
            "std_pnl": round(self.std_pnl, 6),
            "percentile_5": round(self.percentile_5, 6),
            "percentile_1": round(self.percentile_1, 6),
            "max_loss": round(self.max_loss, 6),
            "best_case": round(self.best_case, 6),
            "probability_of_loss": round(self.probability_of_loss, 2),
            "value_at_risk_95": round(self.value_at_risk_95, 6),
            "conditional_var_95": round(self.conditional_var_95, 6),
            "mean_max_drawdown": round(self.mean_max_drawdown, 3),
            "mean_trades": round(self.mean_trades, 1),
            "distribution": [round(v, 6) for v in self.distribution],
            "duration_sec": round(self.duration_sec, 2),
        }


# ── synthetic price generation ────────────────────────────────────────

def _generate_price_paths(
    last_price: float,
    daily_returns: np.ndarray,
    days: int,
    n_paths: int,
    regime_switch_prob: float = 0.30,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Generate synthetic hourly OHLCV paths via GBM + Student-t tails.

    Returns shape (n_paths, days * 24, 6) matching OHLCV format.
    """
    if rng is None:
        rng = np.random.default_rng()

    mu_daily = float(np.mean(daily_returns))
    sigma_daily = float(np.std(daily_returns))
    if sigma_daily < 1e-10:
        sigma_daily = 0.01

    mu_h = mu_daily / CANDLES_PER_DAY
    sigma_h = sigma_daily / np.sqrt(CANDLES_PER_DAY)

    n_candles = days * CANDLES_PER_DAY
    paths = np.zeros((n_paths, n_candles, 6), dtype=np.float64)

    for p in range(n_paths):
        t_samples = rng.standard_t(df=5, size=n_candles).astype(np.float64)
        t_samples = np.clip(t_samples, -6, 6)
        hourly_returns = mu_h + sigma_h * t_samples

        vol_mult = np.ones(n_candles, dtype=np.float64)
        if regime_switch_prob > 0:
            switch_mask = rng.random(n_candles) < (regime_switch_prob / CANDLES_PER_DAY)
            in_shock = False
            shock_remaining = 0
            for i in range(n_candles):
                if switch_mask[i] and not in_shock:
                    in_shock = True
                    shock_remaining = rng.integers(12, 72)
                if in_shock:
                    vol_mult[i] = 2.0
                    shock_remaining -= 1
                    if shock_remaining <= 0:
                        in_shock = False
            hourly_returns *= vol_mult

        prices = np.empty(n_candles + 1, dtype=np.float64)
        prices[0] = last_price
        for i in range(n_candles):
            prices[i + 1] = prices[i] * (1 + hourly_returns[i])
            if prices[i + 1] < last_price * 0.01:
                prices[i + 1] = last_price * 0.01

        base_vol = float(np.mean(daily_returns != 0)) * 1000 + 100
        ts_base = int(time.time() * 1000)

        for i in range(n_candles):
            o = prices[i]
            c = prices[i + 1]
            intra_noise = abs(rng.normal(0, sigma_h * 0.5))
            h = max(o, c) * (1 + intra_noise)
            l = min(o, c) * (1 - intra_noise)
            v = base_vol * (1 + abs(hourly_returns[i]) * 10) * vol_mult[i]

            paths[p, i, 0] = ts_base + i * 3600_000
            paths[p, i, 1] = o
            paths[p, i, 2] = h
            paths[p, i, 3] = l
            paths[p, i, 4] = c
            paths[p, i, 5] = v

    return paths


# ── engine ────────────────────────────────────────────────────────────

class MonteCarloSim:
    """Monte-Carlo stress-test built on the BacktestEngine."""

    def __init__(
        self,
        config: BotConfig | None = None,
        initial_capital: float = 200.0,
        n_simulations: int = 1000,
        maker_fee: float = 0.001,
    ):
        self.config = config or BotConfig()
        self.initial_capital = initial_capital
        self.n_simulations = n_simulations
        self.maker_fee = maker_fee

    async def run(
        self,
        pair: str,
        ohlcv: np.ndarray,
        days_forward: int = 30,
    ) -> MonteCarloResult:
        """Run Monte-Carlo simulation.

        Args:
            pair: Trading pair
            ohlcv: Historical OHLCV for return distribution estimation
            days_forward: Days to simulate forward
        """
        t0 = time.time()
        rng = np.random.default_rng(42)

        closes = ohlcv[:, 4].astype(np.float64)
        daily_close_idx = list(range(0, len(closes), CANDLES_PER_DAY))
        if len(daily_close_idx) < 3:
            daily_close_idx = list(range(len(closes)))
        daily_closes = closes[daily_close_idx]
        daily_returns = np.diff(daily_closes) / daily_closes[:-1]
        daily_returns = daily_returns[np.isfinite(daily_returns)]

        if len(daily_returns) < 5:
            return MonteCarloResult(
                pair=pair, days_forward=days_forward,
                n_simulations=0, initial_capital=self.initial_capital,
                duration_sec=time.time() - t0,
            )

        last_price = float(closes[-1])

        logger.info(
            "Monte-Carlo: generating %d paths for %s (%dd, μ=%.4f%%, σ=%.4f%%)",
            self.n_simulations, pair, days_forward,
            float(np.mean(daily_returns)) * 100,
            float(np.std(daily_returns)) * 100,
        )

        paths = _generate_price_paths(
            last_price, daily_returns, days_forward,
            self.n_simulations, regime_switch_prob=0.30, rng=rng,
        )

        lookback_candles = min(200, len(ohlcv))
        history_prefix = ohlcv[-lookback_candles:]

        pnls: list[float] = []
        max_dds: list[float] = []
        trade_counts: list[int] = []

        for i in range(self.n_simulations):
            synthetic = np.vstack([history_prefix, paths[i]])

            engine = BacktestEngine(
                config=self.config,
                initial_capital=self.initial_capital,
                maker_fee=self.maker_fee,
                taker_fee=self.maker_fee,
                seed=i,
            )
            result = await engine.run(pair, synthetic, days_forward)

            pnls.append(result.total_pnl)
            max_dds.append(result.max_drawdown)
            trade_counts.append(result.total_trades)

            if (i + 1) % 100 == 0:
                logger.info(
                    "Monte-Carlo: %d/%d done (%.1fs)",
                    i + 1, self.n_simulations, time.time() - t0,
                )

        pnl_arr = np.array(pnls, dtype=np.float64)
        sorted_pnls = np.sort(pnl_arr)

        n = len(pnl_arr)
        losses = pnl_arr[pnl_arr < 0]
        var_idx = max(0, int(n * 0.05) - 1)
        var_95 = float(sorted_pnls[var_idx])
        tail = sorted_pnls[:var_idx + 1]
        cvar_95 = float(np.mean(tail)) if len(tail) > 0 else var_95

        return MonteCarloResult(
            pair=pair,
            days_forward=days_forward,
            n_simulations=self.n_simulations,
            initial_capital=self.initial_capital,
            median_pnl=float(np.median(pnl_arr)),
            mean_pnl=float(np.mean(pnl_arr)),
            std_pnl=float(np.std(pnl_arr)),
            percentile_5=float(np.percentile(pnl_arr, 5)),
            percentile_1=float(np.percentile(pnl_arr, 1)),
            max_loss=float(np.min(pnl_arr)),
            best_case=float(np.max(pnl_arr)),
            probability_of_loss=float(len(losses) / n * 100) if n > 0 else 0,
            value_at_risk_95=var_95,
            conditional_var_95=cvar_95,
            mean_max_drawdown=float(np.mean(max_dds)),
            mean_trades=float(np.mean(trade_counts)),
            distribution=sorted_pnls.tolist(),
            duration_sec=time.time() - t0,
        )
