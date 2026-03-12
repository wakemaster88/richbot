"""Autonomous capital allocation and grid sizing.

Computes optimal grid parameters based on actual balances, prices,
and exchange constraints. No manual grid_count configuration needed.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ROUNDTRIP_FEE = 0.002  # 0.1% maker each side
DAILY_VOL = 0.025
RESERVE_PCT = 0.15


@dataclass
class AllocationResult:
    grid_count: int
    buy_count: int
    sell_count: int
    amount_per_order: float
    reserve_usdc: float
    quote_for_trading: float
    base_for_trading: float
    rebalance_needed: bool
    rebalance_action: dict | None = None
    equity_per_pair: float = 0.0
    total_equity: float = 0.0


def _score_config(buy_count: int, sell_count: int, amount: float,
                  price: float, equity: float) -> float:
    """Score a grid configuration by expected daily return."""
    if buy_count < 1 or sell_count < 1:
        return 0.0
    R = DAILY_VOL * 1.2
    order_val = amount * price
    total = buy_count + sell_count
    if total == 0 or equity <= 0:
        return 0.0

    daily = 0.0
    for side_count in (buy_count, sell_count):
        if side_count == 0:
            continue
        for i in range(side_count):
            pos = ((i + 1) / side_count) ** 0.6
            dist = R * pos
            spacing = dist if i == 0 else R * (pos - ((i / side_count) ** 0.6))
            if spacing <= ROUNDTRIP_FEE:
                continue
            profit = (spacing - ROUNDTRIP_FEE) * order_val
            rt = min(3.0, DAILY_VOL / (2 * dist) * 0.5)
            daily += profit * rt
    return daily


class CapitalAllocator:
    """Computes optimal grid allocation from live balances."""

    def __init__(self, target_quote_ratio: float = 0.50):
        self.target_quote_ratio = target_quote_ratio

    def allocate(self, pair: str, balances: dict, price: float,
                 pair_count: int, min_notional: float,
                 step_size: float) -> AllocationResult:
        """Compute optimal allocation for a single pair.

        Args:
            pair: e.g. "BTC/USDC"
            balances: {asset: {free, used, total}} from exchange
            price: current price
            pair_count: total number of active pairs
            min_notional: exchange minimum order value
            step_size: exchange quantity precision
        """
        base_sym = pair.split("/")[0]
        quote_sym = pair.split("/")[1]

        quote_total = balances.get(quote_sym, {}).get("total", 0.0)
        quote_free = balances.get(quote_sym, {}).get("free", 0.0)
        base_total = balances.get(base_sym, {}).get("total", 0.0)
        base_free = balances.get(base_sym, {}).get("free", 0.0)

        total_equity = quote_total + base_total * price
        if total_equity <= 0 or price <= 0:
            return AllocationResult(
                grid_count=0, buy_count=0, sell_count=0,
                amount_per_order=0, reserve_usdc=0,
                quote_for_trading=0, base_for_trading=0,
                rebalance_needed=False, total_equity=0, equity_per_pair=0,
            )

        equity_per_pair = total_equity / max(1, pair_count)
        reserve = total_equity * RESERVE_PCT
        tradable = equity_per_pair * (1 - RESERVE_PCT)

        quote_for_pair = min(quote_free, tradable)
        base_for_pair_usdc = min(base_free * price, tradable)

        min_amount = math.ceil((min_notional * 1.15) / price / step_size) * step_size
        min_order_cost = min_amount * price

        if min_order_cost <= 0:
            return AllocationResult(
                grid_count=0, buy_count=0, sell_count=0,
                amount_per_order=0, reserve_usdc=reserve,
                quote_for_trading=0, base_for_trading=0,
                rebalance_needed=False, total_equity=total_equity,
                equity_per_pair=equity_per_pair,
            )

        max_buys = int(quote_for_pair / min_order_cost)
        max_sells = int(base_free / min_amount) if min_amount > 0 else 0

        best_score = -1.0
        best_buy = 0
        best_sell = 0
        best_amount = min_amount

        max_per_side = max(max_buys, max_sells)
        for total_n in range(2, min(max_buys + max_sells, 16) + 1):
            for nb in range(max(0, total_n - max_sells), min(max_buys, total_n) + 1):
                ns = total_n - nb
                if ns > max_sells:
                    continue
                if nb == 0 and ns == 0:
                    continue

                buy_capital = quote_for_pair / nb if nb > 0 else 0
                sell_capital = (base_free / ns) if ns > 0 else 0

                if nb > 0 and ns > 0:
                    amount_from_buy = buy_capital / price
                    amount_from_sell = sell_capital
                    raw_amount = min(amount_from_buy, amount_from_sell)
                elif nb > 0:
                    raw_amount = buy_capital / price
                else:
                    raw_amount = sell_capital

                amount = math.floor(raw_amount / step_size) * step_size
                if amount < min_amount:
                    amount = min_amount

                if nb > 0 and amount * price * nb > quote_for_pair * 1.05:
                    continue
                if ns > 0 and amount * ns > base_free * 1.05:
                    continue

                score = _score_config(nb, ns, amount, price, equity_per_pair)
                if score > best_score:
                    best_score = score
                    best_buy = nb
                    best_sell = ns
                    best_amount = amount

        if best_buy + best_sell < 2:
            best_buy = min(1, max_buys)
            best_sell = min(1, max_sells)
            best_amount = min_amount

        rebalance_needed = False
        rebalance_action = None
        if total_equity > 0:
            actual_quote_ratio = quote_total / total_equity
            deviation = abs(actual_quote_ratio - self.target_quote_ratio)
            if deviation > 0.15 and total_equity > 20:
                rebalance_needed = True
                rebalance_value = deviation * total_equity * 0.3
                rebalance_value = min(rebalance_value, total_equity * 0.05)
                if actual_quote_ratio < self.target_quote_ratio:
                    sell_amount = math.floor(rebalance_value / price / step_size) * step_size
                    if sell_amount >= min_amount and sell_amount <= base_free:
                        rebalance_action = {"side": "sell", "amount": sell_amount, "pair": pair}
                    else:
                        rebalance_needed = False
                else:
                    buy_amount = math.floor(rebalance_value / price / step_size) * step_size
                    if buy_amount >= min_amount and buy_amount * price <= quote_free:
                        rebalance_action = {"side": "buy", "amount": buy_amount, "pair": pair}
                    else:
                        rebalance_needed = False

        grid_count = best_buy + best_sell

        logger.info(
            "%s Allocation — %dB+%dS=%d Level, %.8f %s/Order (≈%.2f %s), "
            "Quote: %.2f frei, Base: %.8f frei, Equity: %.2f, Reserve: %.2f%s",
            pair, best_buy, best_sell, grid_count, best_amount, base_sym,
            best_amount * price, quote_sym, quote_for_pair, base_free,
            total_equity, reserve,
            " [REBALANCE noetig]" if rebalance_needed else "",
        )

        return AllocationResult(
            grid_count=grid_count,
            buy_count=best_buy,
            sell_count=best_sell,
            amount_per_order=best_amount,
            reserve_usdc=reserve,
            quote_for_trading=quote_for_pair,
            base_for_trading=base_free,
            rebalance_needed=rebalance_needed,
            rebalance_action=rebalance_action,
            equity_per_pair=equity_per_pair,
            total_equity=total_equity,
        )
