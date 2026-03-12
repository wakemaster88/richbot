"""Autonomous capital allocation and grid sizing.

Computes optimal grid parameters based on actual balances, prices,
and exchange constraints. No manual grid_count configuration needed.
Provides per-side budgets for pyramid order sizing.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ROUNDTRIP_FEE = 0.002  # 0.1% maker each side
DAILY_VOL = 0.025
RESERVE_PCT = 0.15


def _pyramid_weights(count: int) -> list[float]:
    """Pyramid weights: 0.7 (near price) to 1.3 (far from price)."""
    if count <= 1:
        return [1.0]
    return [0.7 + 0.6 * i / (count - 1) for i in range(count)]


def _pyramid_weight_sum(count: int) -> float:
    return sum(_pyramid_weights(count))


@dataclass
class AllocationResult:
    grid_count: int
    buy_count: int
    sell_count: int
    amount_per_order: float  # average/base amount for display
    reserve_usdc: float
    quote_for_trading: float
    base_for_trading: float
    buy_budget: float  # USDC available for all buy orders
    sell_budget: float  # base currency available for all sell orders
    rebalance_needed: bool
    rebalance_action: dict | None = None
    equity_per_pair: float = 0.0
    total_equity: float = 0.0


def _score_config(buy_count: int, sell_count: int, avg_amount: float,
                  price: float, equity: float) -> float:
    """Score a grid configuration by expected daily return with pyramid sizing."""
    if buy_count < 1 or sell_count < 1:
        return 0.0
    R = DAILY_VOL * 1.2
    if equity <= 0:
        return 0.0

    daily = 0.0
    for side_count in (buy_count, sell_count):
        if side_count == 0:
            continue
        pyr = _pyramid_weights(side_count)
        for i in range(side_count):
            pos = ((i + 1) / side_count) ** 0.6
            dist = R * pos
            spacing = dist if i == 0 else R * (pos - ((i / side_count) ** 0.6))
            if spacing <= ROUNDTRIP_FEE:
                continue
            order_val = avg_amount * pyr[i] * price
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
        base_sym = pair.split("/")[0]
        quote_sym = pair.split("/")[1]

        quote_total = balances.get(quote_sym, {}).get("total", 0.0)
        quote_free = balances.get(quote_sym, {}).get("free", 0.0)
        base_total = balances.get(base_sym, {}).get("total", 0.0)
        base_free = balances.get(base_sym, {}).get("free", 0.0)

        total_equity = quote_total + base_total * price
        empty = AllocationResult(
            grid_count=0, buy_count=0, sell_count=0,
            amount_per_order=0, reserve_usdc=0,
            quote_for_trading=0, base_for_trading=0,
            buy_budget=0, sell_budget=0,
            rebalance_needed=False, total_equity=0, equity_per_pair=0,
        )
        if total_equity <= 0 or price <= 0:
            return empty

        equity_per_pair = total_equity / max(1, pair_count)
        reserve = total_equity * RESERVE_PCT
        tradable = equity_per_pair * (1 - RESERVE_PCT)

        quote_for_pair = min(quote_free, tradable)
        base_for_pair = min(base_free, tradable / price) if price > 0 else 0.0

        min_amount = math.ceil((min_notional * 1.15) / price / step_size) * step_size
        min_order_cost = min_amount * price

        if min_order_cost <= 0:
            empty.reserve_usdc = reserve
            empty.total_equity = total_equity
            empty.equity_per_pair = equity_per_pair
            return empty

        max_buys = self._max_levels_for_budget(quote_for_pair, price, min_amount, is_quote=True)
        max_sells = self._max_levels_for_budget(base_for_pair, price, min_amount, is_quote=False)

        best_score = -1.0
        best_buy = 0
        best_sell = 0
        best_avg_amount = min_amount

        for total_n in range(2, min(max_buys + max_sells, 16) + 1):
            for nb in range(max(0, total_n - max_sells), min(max_buys, total_n) + 1):
                ns = total_n - nb
                if ns > max_sells:
                    continue
                if nb == 0 and ns == 0:
                    continue

                if nb > 0:
                    buy_ws = _pyramid_weight_sum(nb)
                    base_buy = quote_for_pair / (buy_ws * price)
                else:
                    base_buy = 0
                if ns > 0:
                    sell_ws = _pyramid_weight_sum(ns)
                    base_sell = base_for_pair / sell_ws
                else:
                    base_sell = 0

                if nb > 0 and ns > 0:
                    avg_amount = min(base_buy, base_sell)
                elif nb > 0:
                    avg_amount = base_buy
                else:
                    avg_amount = base_sell

                avg_amount = math.floor(avg_amount / step_size) * step_size
                if avg_amount < min_amount:
                    avg_amount = min_amount

                if nb > 0:
                    buy_cost = sum(math.floor(avg_amount * w / step_size) * step_size * price
                                  for w in _pyramid_weights(nb))
                    if buy_cost > quote_for_pair * 1.05:
                        continue
                if ns > 0:
                    sell_qty = sum(math.floor(avg_amount * w / step_size) * step_size
                                  for w in _pyramid_weights(ns))
                    if sell_qty > base_for_pair * 1.05:
                        continue

                score = _score_config(nb, ns, avg_amount, price, equity_per_pair)
                if score > best_score:
                    best_score = score
                    best_buy = nb
                    best_sell = ns
                    best_avg_amount = avg_amount

        if best_buy + best_sell < 2:
            best_buy = min(1, max_buys)
            best_sell = min(1, max_sells)
            best_avg_amount = min_amount

        buy_budget = quote_for_pair if best_buy > 0 else 0.0
        sell_budget = base_for_pair if best_sell > 0 else 0.0

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
                    sell_amt = math.floor(rebalance_value / price / step_size) * step_size
                    if sell_amt >= min_amount and sell_amt <= base_free:
                        rebalance_action = {"side": "sell", "amount": sell_amt, "pair": pair}
                    else:
                        rebalance_needed = False
                else:
                    buy_amt = math.floor(rebalance_value / price / step_size) * step_size
                    if buy_amt >= min_amount and buy_amt * price <= quote_free:
                        rebalance_action = {"side": "buy", "amount": buy_amt, "pair": pair}
                    else:
                        rebalance_needed = False

        grid_count = best_buy + best_sell

        logger.info(
            "%s Allocation — %dB+%dS=%d Level, avg %.8f %s/Order (≈%.2f %s), "
            "BuyBudget: %.2f %s, SellBudget: %.8f %s, Equity: %.2f%s",
            pair, best_buy, best_sell, grid_count, best_avg_amount, base_sym,
            best_avg_amount * price, quote_sym, buy_budget, quote_sym,
            sell_budget, base_sym, total_equity,
            " [REBALANCE]" if rebalance_needed else "",
        )

        return AllocationResult(
            grid_count=grid_count,
            buy_count=best_buy,
            sell_count=best_sell,
            amount_per_order=best_avg_amount,
            reserve_usdc=reserve,
            quote_for_trading=quote_for_pair,
            base_for_trading=base_for_pair,
            buy_budget=buy_budget,
            sell_budget=sell_budget,
            rebalance_needed=rebalance_needed,
            rebalance_action=rebalance_action,
            equity_per_pair=equity_per_pair,
            total_equity=total_equity,
        )

    @staticmethod
    def _max_levels_for_budget(budget: float, price: float,
                               min_amount: float, is_quote: bool) -> int:
        """How many pyramid levels a budget can afford."""
        for n in range(16, 0, -1):
            ws = _pyramid_weight_sum(n)
            if is_quote:
                base_amount = budget / (ws * price) if price > 0 else 0
            else:
                base_amount = budget / ws if ws > 0 else 0
            smallest = base_amount * 0.7  # smallest pyramid weight
            if smallest >= min_amount * 0.95:
                return n
        return 0
