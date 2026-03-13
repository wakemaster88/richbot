"""Exchange abstraction — direct HTTP for Pi, ccxt fallback for desktop."""

from __future__ import annotations

import hashlib
import hmac
import json as _json
import logging
import socket
import time as _time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bot.config import ExchangeConfig

logger = logging.getLogger(__name__)

_BINANCE = "https://api.binance.com"
_BINANCE_HOST = "api.binance.com"
_DNS_CACHE: dict[str, str] = {}


def _resolve_dns():
    """Cache Binance API IP to avoid repeated DNS lookups."""
    if _BINANCE_HOST not in _DNS_CACHE:
        try:
            ip = socket.gethostbyname(_BINANCE_HOST)
            _DNS_CACHE[_BINANCE_HOST] = ip
            logger.info("DNS cached: %s → %s", _BINANCE_HOST, ip)
        except Exception:
            pass


def _urlopen_retry(req_or_url, *, timeout: int = 10, max_retries: int = 3) -> Any:
    """urlopen with exponential backoff for transient network errors."""
    delay = 1.0
    for attempt in range(max_retries + 1):
        try:
            return urlopen(req_or_url, timeout=timeout)
        except URLError as e:
            reason = str(e.reason) if hasattr(e, "reason") else str(e)
            is_dns = "name resolution" in reason.lower() or "temporary failure" in reason.lower()
            if attempt >= max_retries:
                raise
            wait = min(60.0 if is_dns else delay, 30.0)
            logger.debug("Network retry %d/%d (wait %.0fs): %s", attempt + 1, max_retries, wait, reason)
            _time.sleep(wait)
            delay = min(delay * 2, 30.0)
        except Exception:
            if attempt >= max_retries:
                raise
            _time.sleep(min(delay, 30.0))
            delay = min(delay * 2, 30.0)


class Exchange:
    """Unified exchange interface — prefers lightweight direct HTTP."""

    def __init__(self, config: ExchangeConfig):
        self.config = config
        self._markets: dict[str, dict] = {}
        self._time_offset_ms: int = 0
        self._time_offset_updated: float = 0.0
        _resolve_dns()
        self._sync_server_time()

    def _sync_server_time(self):
        """Compute offset between local clock and Binance server clock.

        This eliminates recvWindow errors caused by Pi clock drift — even
        without NTP/chrony the bot timestamps will match Binance exactly.
        """
        try:
            t_before = int(_time.time() * 1000)
            resp = _urlopen_retry(f"{_BINANCE}/api/v3/time", timeout=5, max_retries=2)
            t_after = int(_time.time() * 1000)
            server_time = _json.loads(resp.read().decode())["serverTime"]
            local_time = (t_before + t_after) // 2
            self._time_offset_ms = server_time - local_time
            self._time_offset_updated = _time.time()
            if abs(self._time_offset_ms) > 500:
                logger.warning("Uhr-Offset: %+d ms (lokal vs. Binance)", self._time_offset_ms)
            else:
                logger.info("Uhr-Sync OK: Offset %+d ms", self._time_offset_ms)
        except Exception as e:
            logger.warning("Server-Time-Sync fehlgeschlagen: %s — nutze lokale Uhr", e)
            self._time_offset_ms = 0

    def _get_timestamp(self) -> int:
        """Return a timestamp aligned to Binance server time."""
        if _time.time() - self._time_offset_updated > 1800:
            self._sync_server_time()
        return int(_time.time() * 1000) + self._time_offset_ms

    # ── market data ──────────────────────────────────────────────

    async def preload_markets(self, symbols: list[str]):
        """Load only specific markets via direct HTTP to minimize memory."""
        binance_syms = [s.replace("/", "") for s in symbols]
        if len(binance_syms) == 1:
            qs = f"symbol={binance_syms[0]}"
        else:
            qs = f'symbols={_json.dumps(binance_syms, separators=(",", ":"))}'

        url = f"{_BINANCE}/api/v3/exchangeInfo?{qs}"
        logger.info("Loading markets for %s", symbols)

        resp = _urlopen_retry(url, timeout=15)
        exchange_info = _json.loads(resp.read().decode())

        for sym_data in exchange_info.get("symbols", []):
            try:
                m = self._parse_binance_market(sym_data)
                self._markets[m["symbol"]] = m
            except Exception as e:
                logger.warning("Failed to parse market %s: %s", sym_data.get("symbol"), e)

        if self._markets:
            logger.info("Loaded %d markets: %s", len(self._markets), list(self._markets.keys()))
        else:
            raise RuntimeError("No markets parsed — check symbol names")

    @staticmethod
    def _parse_binance_market(data: dict) -> dict:
        """Parse Binance exchangeInfo symbol into market dict."""
        base = data["baseAsset"]
        quote = data["quoteAsset"]
        symbol = f"{base}/{quote}"

        filters = {f["filterType"]: f for f in data.get("filters", [])}
        price_f = filters.get("PRICE_FILTER", {})
        lot_f = filters.get("LOT_SIZE", {})
        notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))

        return {
            "id": data["symbol"],
            "symbol": symbol,
            "base": base,
            "quote": quote,
            "precision": {
                "amount": float(lot_f.get("stepSize", "0.00001")),
                "price": float(price_f.get("tickSize", "0.01")),
            },
            "limits": {
                "amount": {"min": float(lot_f.get("minQty", 0)), "max": float(lot_f.get("maxQty", 0))},
                "price": {"min": float(price_f.get("minPrice", 0)), "max": float(price_f.get("maxPrice", 0))},
                "cost": {"min": float(notional.get("minNotional", notional.get("notional", 0)))},
            },
        }

    # ── precision helpers ────────────────────────────────────────

    @staticmethod
    def _step_format(value: float, step: float) -> str:
        """Truncate value to step-size precision and return as string."""
        if step <= 0:
            return f"{value:.8f}"
        if step >= 1:
            return str(int(value))
        step_str = f"{step:.12f}".rstrip("0")
        decimals = max(0, len(step_str.split(".")[1])) if "." in step_str else 0
        truncated = int(value / step) * step
        return f"{truncated:.{decimals}f}"

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        m = self._markets.get(symbol)
        step = m["precision"]["amount"] if m else 0.00001
        return self._step_format(amount, step)

    def price_to_precision(self, symbol: str, price: float) -> str:
        m = self._markets.get(symbol)
        step = m["precision"]["price"] if m else 0.01
        return self._step_format(price, step)

    # ── signed request helper ────────────────────────────────────

    def _signed_request(self, method: str, path: str, extra: dict | None = None) -> dict:
        params = extra or {}
        params["timestamp"] = self._get_timestamp()
        params["recvWindow"] = 10000
        query = urlencode(params)
        sig = hmac.new(self.config.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        url = f"{_BINANCE}{path}?{query}&signature={sig}"
        req = Request(url, method=method, headers={"X-MBX-APIKEY": self.config.api_key})
        try:
            resp = _urlopen_retry(req, timeout=10)
            return _json.loads(resp.read().decode())
        except HTTPError as e:
            body = ""
            try:
                body = e.read().decode()
                err = _json.loads(body)
                msg = err.get("msg", body)
                code = err.get("code", 0)
            except Exception:
                msg = body or str(e)
                code = 0
            if code == -1021:
                logger.warning("recvWindow-Fehler — Re-Sync der Uhr")
                self._sync_server_time()
            raise Exception(f"binance {msg}") from None

    # ── public data (no auth) ────────────────────────────────────

    def fetch_ticker_http(self, symbol: str) -> dict:
        binance_sym = symbol.replace("/", "")
        url = f"{_BINANCE}/api/v3/ticker/24hr?symbol={binance_sym}"
        resp = _urlopen_retry(url, timeout=10)
        d = _json.loads(resp.read().decode())
        return {
            "symbol": symbol,
            "last": float(d["lastPrice"]),
            "bid": float(d["bidPrice"]),
            "ask": float(d["askPrice"]),
            "high": float(d["highPrice"]),
            "low": float(d["lowPrice"]),
            "volume": float(d["volume"]),
        }

    def fetch_ohlcv_http(self, symbol: str, interval: str = "1h", limit: int = 200) -> list[list]:
        binance_sym = symbol.replace("/", "")
        url = f"{_BINANCE}/api/v3/klines?symbol={binance_sym}&interval={interval}&limit={limit}"
        resp = _urlopen_retry(url, timeout=15)
        raw = _json.loads(resp.read().decode())
        return [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in raw]

    # ── account / balance (auth) ─────────────────────────────────

    def fetch_account_balances(self) -> dict[str, dict[str, float]]:
        data = self._signed_request("GET", "/api/v3/account")
        balances: dict[str, dict[str, float]] = {}
        for b in data.get("balances", []):
            free, locked = float(b["free"]), float(b["locked"])
            if free > 0 or locked > 0:
                balances[b["asset"]] = {"free": free, "used": locked, "total": free + locked}
        return balances

    # ── orders (auth) ────────────────────────────────────────────

    def create_order_http(self, symbol: str, side: str, amount: float, price: float) -> dict:
        binance_sym = symbol.replace("/", "")
        qty_str = self.amount_to_precision(symbol, amount)
        px_str = self.price_to_precision(symbol, price)
        data = self._signed_request("POST", "/api/v3/order", {
            "symbol": binance_sym,
            "side": side.upper(),
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": qty_str,
            "price": px_str,
        })
        return {"id": str(data["orderId"]), "symbol": symbol, "side": side.lower(),
                "price": float(data["price"]), "amount": float(data["origQty"]), "status": data["status"].lower()}

    def create_market_order_http(self, symbol: str, side: str, amount: float) -> dict:
        binance_sym = symbol.replace("/", "")
        qty_str = self.amount_to_precision(symbol, amount)
        data = self._signed_request("POST", "/api/v3/order", {
            "symbol": binance_sym,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": qty_str,
        })
        fills = data.get("fills", [])
        avg_price = float(data.get("price", 0))
        if fills:
            total_qty = sum(float(f["qty"]) for f in fills)
            total_cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
            avg_price = total_cost / total_qty if total_qty > 0 else avg_price
        return {"id": str(data["orderId"]), "symbol": symbol, "side": side.lower(),
                "price": avg_price, "amount": float(data["executedQty"]),
                "status": data["status"].lower()}

    async def async_create_market_order(self, symbol: str, side: str, amount: float) -> dict:
        import asyncio
        return await asyncio.to_thread(self.create_market_order_http, symbol, side, amount)

    def cancel_order_http(self, order_id: str, symbol: str) -> dict:
        binance_sym = symbol.replace("/", "")
        return self._signed_request("DELETE", "/api/v3/order", {"symbol": binance_sym, "orderId": order_id})

    def fetch_open_orders_http(self, symbol: str) -> list[dict]:
        binance_sym = symbol.replace("/", "")
        data = self._signed_request("GET", "/api/v3/openOrders", {"symbol": binance_sym})
        return [{"id": str(o["orderId"]), "symbol": symbol, "side": o["side"].lower(),
                 "price": float(o["price"]), "amount": float(o["origQty"]), "status": o["status"].lower()} for o in data]

    def fetch_order_http(self, symbol: str, order_id: str) -> dict:
        """Fetch a single order's details including fill information."""
        binance_sym = symbol.replace("/", "")
        data = self._signed_request("GET", "/api/v3/order", {
            "symbol": binance_sym, "orderId": order_id,
        })
        executed_qty = float(data.get("executedQty", 0))
        cum_quote = float(data.get("cummulativeQuoteQty", 0))
        avg_price = cum_quote / executed_qty if executed_qty > 0 else float(data.get("price", 0))
        return {
            "id": str(data["orderId"]),
            "symbol": symbol,
            "side": data["side"].lower(),
            "price": float(data["price"]),
            "avg_price": avg_price,
            "amount": float(data["origQty"]),
            "executed_qty": executed_qty,
            "cum_quote_qty": cum_quote,
            "status": data["status"].lower(),
        }

    def fetch_my_trades_http(self, symbol: str, order_id: str) -> list[dict]:
        """Fetch fills/trades for a specific order."""
        binance_sym = symbol.replace("/", "")
        data = self._signed_request("GET", "/api/v3/myTrades", {
            "symbol": binance_sym, "orderId": order_id,
        })
        return [{
            "price": float(t["price"]),
            "qty": float(t["qty"]),
            "commission": float(t["commission"]),
            "commission_asset": t["commissionAsset"],
            "is_maker": t["isMaker"],
        } for t in data]

    async def async_fetch_order(self, symbol: str, order_id: str) -> dict:
        import asyncio
        return await asyncio.to_thread(self.fetch_order_http, symbol, order_id)

    async def async_fetch_my_trades(self, symbol: str, order_id: str) -> list[dict]:
        import asyncio
        return await asyncio.to_thread(self.fetch_my_trades_http, symbol, order_id)

    # ── async wrappers (run sync HTTP in thread) ─────────────────

    async def async_fetch_ticker(self, symbol: str) -> dict:
        import asyncio
        return await asyncio.to_thread(self.fetch_ticker_http, symbol)

    async def async_fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200, since: int | None = None) -> list[list]:
        import asyncio
        return await asyncio.to_thread(self.fetch_ohlcv_http, symbol, timeframe, limit)

    async def async_fetch_balance(self) -> dict[str, dict[str, float]]:
        import asyncio
        return await asyncio.to_thread(self.fetch_account_balances)

    async def async_create_limit_buy(self, symbol: str, amount: float, price: float, params: dict | None = None) -> dict:
        import asyncio
        return await asyncio.to_thread(self.create_order_http, symbol, "buy", amount, price)

    async def async_create_limit_sell(self, symbol: str, amount: float, price: float, params: dict | None = None) -> dict:
        import asyncio
        return await asyncio.to_thread(self.create_order_http, symbol, "sell", amount, price)

    async def async_cancel_order(self, order_id: str, symbol: str) -> dict:
        import asyncio
        return await asyncio.to_thread(self.cancel_order_http, order_id, symbol)

    async def async_fetch_open_orders(self, symbol: str) -> list[dict]:
        import asyncio
        return await asyncio.to_thread(self.fetch_open_orders_http, symbol)

    async def close(self):
        pass
