"""Unified alert pipeline: Telegram, WebPush, dedup, quiet hours."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from bot.config import AlertConfig

logger = logging.getLogger(__name__)
DEDUP_WINDOW_SEC = 300


class AlertChannel(ABC):
    """Interface for alert delivery."""

    @abstractmethod
    async def send(self, title: str, message: str, severity: str, detail: dict | None = None) -> None:
        pass


class TelegramChannel(AlertChannel):
    """Uses existing TelegramNotifier for alerts."""

    def __init__(self, telegram: Any):
        self.telegram = telegram

    async def send(self, title: str, message: str, severity: str, detail: dict | None = None) -> None:
        if not self.telegram:
            return
        try:
            text = f"<b>{title}</b>\n\n{message}" if title else message
            await self.telegram.send_message(text)
        except Exception as e:
            logger.warning("TelegramChannel send failed: %s", e)


class WebPushChannel(AlertChannel):
    """Sends alert to dashboard webhook; dashboard relays to PWA push."""

    def __init__(self, webhook_url: str, webhook_secret: str = ""):
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret

    async def send(self, title: str, message: str, severity: str, detail: dict | None = None) -> None:
        if not self.webhook_url:
            return
        try:
            import aiohttp
            payload = {"title": title, "message": message, "severity": severity}
            if detail:
                payload["detail"] = detail
            headers = {"Content-Type": "application/json"}
            if self.webhook_secret:
                headers["X-Alert-Secret"] = self.webhook_secret
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.webhook_url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status >= 400:
                        logger.warning("WebPush webhook %s returned %s", self.webhook_url, resp.status)
        except Exception as e:
            logger.debug("WebPushChannel send failed: %s", e)


class AlertManager:
    """Central alert router: dedup, severity filter, quiet hours, multi-channel."""

    def __init__(
        self,
        config: AlertConfig,
        channels: list[AlertChannel] | None = None,
    ):
        self.config = config
        self.channels = channels or []
        self._dedup: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def add_channel(self, ch: AlertChannel) -> None:
        self.channels.append(ch)

    def _dedup_key(self, title: str, message: str) -> str:
        return hashlib.sha256(f"{title}:{message}".encode()).hexdigest()[:16]

    def _in_quiet_hours(self) -> bool:
        from datetime import datetime
        h = datetime.utcnow().hour
        start, end = self.config.quiet_start_hour, self.config.quiet_end_hour
        if start > end:
            return h >= start or h < end
        return start <= h < end

    async def alert(
        self,
        category: str,
        title: str,
        message: str,
        severity: str = "info",
        detail: dict | None = None,
    ) -> None:
        if severity not in self.config.severities:
            return
        if self._in_quiet_hours() and severity != "critical":
            return

        key = self._dedup_key(title, message)
        now = time.time()
        async with self._lock:
            if key in self._dedup and (now - self._dedup[key]) < DEDUP_WINDOW_SEC:
                return
            self._dedup[key] = now
            if len(self._dedup) > 200:
                cutoff = now - DEDUP_WINDOW_SEC
                self._dedup = {k: v for k, v in self._dedup.items() if v > cutoff}

        for ch in self.channels:
            try:
                await ch.send(title, message, severity, detail)
            except Exception as e:
                logger.warning("Alert channel %s failed: %s", type(ch).__name__, e)

    async def alert_trade(self, pair: str, side: str, price: float, amount: float, pnl: float) -> None:
        if not self.config.alert_on_trade:
            return
        emoji = "🟢" if side == "sell" else "🔴"
        await self.alert(
            "trade",
            f"{emoji} Order ausgeführt",
            f"Paar: {pair}\nSeite: {side.upper()}\nPreis: {price:.2f}\nMenge: {amount:.6f}\nPnL: {pnl:+.4f} USDC",
            "info",
            {"pair": pair, "side": side, "price": price, "amount": amount, "pnl": pnl},
        )

    async def alert_drawdown(self, pair: str, drawdown_pct: float, equity: float) -> None:
        if not self.config.alert_on_drawdown:
            return
        severity = "critical" if drawdown_pct >= 5 else "warn" if drawdown_pct >= 3 else "info"
        dd_str = f"{drawdown_pct:.1f}"
        await self.alert(
            "drawdown",
            "⚠️ Drawdown" + (" STOP" if drawdown_pct >= 5 else ""),
            f"Paar: {pair}\nDrawdown: {dd_str}%\nKapital: {equity:.2f} USDC",
            severity,
            {"pair": pair, "drawdown_pct": drawdown_pct, "equity": equity},
        )

    async def alert_circuit_breaker(self, pair: str, level: str, drawdown_pct: float) -> None:
        if not self.config.alert_on_circuit_breaker:
            return
        severity = "critical" if level in ("RED", "ORANGE") else "warn"
        await self.alert(
            "circuit_breaker",
            f"🔌 Circuit Breaker {level}",
            f"Paar: {pair}\nLevel: {level}\nDrawdown: {drawdown_pct:.1f}%",
            severity,
            {"pair": pair, "level": level, "drawdown_pct": drawdown_pct},
        )

    async def alert_regime_change(self, pair: str, from_regime: str, to_regime: str) -> None:
        if not self.config.alert_on_regime_change:
            return
        await self.alert(
            "regime",
            "📊 Regime-Wechsel",
            f"Paar: {pair}\n{from_regime} → {to_regime}",
            "info",
            {"pair": pair, "from": from_regime, "to": to_regime},
        )
