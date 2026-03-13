"""Fee-aware grid spacing engine.

Ensures grid levels are placed only at distances where a roundtrip trade
is profitable after maker+taker fees.  Fetches actual fee rates from the
exchange when possible, otherwise uses conservative defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_MAKER = 0.001  # 0.10 %
DEFAULT_TAKER = 0.001  # 0.10 %


@dataclass
class SymbolFees:
    maker: float = DEFAULT_MAKER
    taker: float = DEFAULT_TAKER
    source: str = "default"


class FeeEngine:
    """Compute minimum profitable grid spacing per symbol."""

    def __init__(
        self,
        maker_fee: float = DEFAULT_MAKER,
        taker_fee: float = DEFAULT_TAKER,
    ):
        self._default_maker = maker_fee
        self._default_taker = taker_fee
        self._fees: dict[str, SymbolFees] = {}

    # ------------------------------------------------------------------
    def fetch_fees(self, exchange, symbols: list[str] | None = None) -> None:
        """Try to load actual trading fees from the exchange.

        Binance endpoint ``/sapi/v1/asset/tradeFee`` returns per-symbol
        maker/taker rates.  Requires a signed request which ``exchange``
        already supports via ``_signed_request``.
        """
        if symbols is None:
            symbols = list(exchange._markets.keys())

        for sym in symbols:
            self._fees.setdefault(sym, SymbolFees(self._default_maker, self._default_taker))

        try:
            data = exchange._signed_request("GET", "/sapi/v1/asset/tradeFee")
            if not isinstance(data, list):
                data = [data]

            sym_to_pair: dict[str, str] = {}
            for pair in symbols:
                binance_sym = pair.replace("/", "")
                sym_to_pair[binance_sym] = pair

            for entry in data:
                binance_sym = entry.get("symbol", "")
                pair = sym_to_pair.get(binance_sym)
                if pair is None:
                    continue
                maker = float(entry.get("makerCommission", self._default_maker))
                taker = float(entry.get("takerCommission", self._default_taker))
                self._fees[pair] = SymbolFees(maker=maker, taker=taker, source="exchange")
                logger.info(
                    "Fee %s: maker=%.4f%% taker=%.4f%% (exchange)",
                    pair, maker * 100, taker * 100,
                )
        except Exception as exc:
            logger.info("Fee-Fetch via /sapi fehlgeschlagen (%s) — nutze Defaults", exc)

        for sym in symbols:
            f = self._fees.get(sym)
            if f and f.source == "default":
                logger.info(
                    "Fee %s: maker=%.4f%% taker=%.4f%% (default)",
                    sym, f.maker * 100, f.taker * 100,
                )

    # ------------------------------------------------------------------
    def get_fees(self, symbol: str) -> SymbolFees:
        return self._fees.get(symbol, SymbolFees(self._default_maker, self._default_taker))

    # ------------------------------------------------------------------
    def min_profitable_spacing(
        self,
        symbol: str,
        target_profit_bps: int = 10,
    ) -> float:
        """Return minimum grid spacing (as a fraction, e.g. 0.003 = 0.3 %)
        that guarantees at least ``target_profit_bps`` net profit per
        roundtrip after fees on both legs.

        Formula
        -------
        roundtrip_fee = maker + taker          (buy leg fee + sell leg fee)
        min_spacing   = roundtrip_fee + target / 10000
        """
        f = self.get_fees(symbol)
        roundtrip = f.maker + f.taker
        return roundtrip + target_profit_bps / 10_000

    # ------------------------------------------------------------------
    def expected_profit_pct(self, symbol: str, spacing_fraction: float) -> float:
        """Net profit **percentage** of a single roundtrip at *spacing_fraction*.

        Returns a value like 0.08 meaning 0.08 % net.
        """
        f = self.get_fees(symbol)
        return (spacing_fraction - f.maker - f.taker) * 100

    def expected_profit_abs(
        self, symbol: str, spacing_fraction: float, notional: float,
    ) -> float:
        """Absolute net profit (in quote currency) per roundtrip."""
        return self.expected_profit_pct(symbol, spacing_fraction) / 100 * notional

    # ------------------------------------------------------------------
    def apply_to_grid(self, grid_engine, symbol: str) -> None:
        """Ensure the grid engine's internal fee parameters meet the
        minimum profitable threshold.

        Updates ``FEE_RATE`` (used for internal calculations) and raises
        ``spacing_percent`` if it is too tight to be profitable.
        """
        f = self.get_fees(symbol)
        grid_engine.FEE_RATE = max(f.maker, f.taker)

        min_spacing_frac = self.min_profitable_spacing(symbol)
        min_spacing_pct = min_spacing_frac * 100  # convert to percent (0.3 % = 0.3)

        if grid_engine.spacing_percent < min_spacing_pct:
            logger.warning(
                "%s: Spacing %.3f%% < min profitable %.3f%% — angehoben",
                symbol, grid_engine.spacing_percent, min_spacing_pct,
            )
            grid_engine.spacing_percent = min_spacing_pct

    # ------------------------------------------------------------------
    def get_metrics(self, symbol: str, spacing_pct: float, price: float = 0.0) -> dict:
        """Return a dict of fee metrics suitable for heartbeat / dashboard."""
        f = self.get_fees(symbol)
        spacing_frac = spacing_pct / 100
        min_frac = self.min_profitable_spacing(symbol)
        net_pct = self.expected_profit_pct(symbol, spacing_frac)
        return {
            "maker_fee_pct": round(f.maker * 100, 4),
            "taker_fee_pct": round(f.taker * 100, 4),
            "fee_source": f.source,
            "min_profitable_spacing_pct": round(min_frac * 100, 4),
            "current_spacing_pct": round(spacing_pct, 4),
            "net_profit_per_trade_pct": round(net_pct, 4),
            "spacing_is_profitable": spacing_pct >= min_frac * 100,
        }
