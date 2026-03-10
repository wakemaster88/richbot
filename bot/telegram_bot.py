"""Telegram bot for alerts, reports, and commands."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bot.config import TelegramConfig

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends trading alerts and reports via Telegram."""

    def __init__(self, config: TelegramConfig):
        self.config = config
        self._bot = None
        self._app = None

    async def _get_bot(self):
        if self._bot is None and self.config.enabled and self.config.bot_token:
            try:
                from telegram import Bot
                self._bot = Bot(token=self.config.bot_token)
                logger.info("Telegram bot initialized")
            except ImportError:
                logger.warning("python-telegram-bot not installed")
            except Exception as e:
                logger.error("Telegram init failed: %s", e)
        return self._bot

    async def send_message(self, text: str, parse_mode: str = "HTML"):
        """Send a message to the configured chat."""
        if not self.config.enabled or not self.config.chat_id:
            return
        bot = await self._get_bot()
        if bot is None:
            return
        try:
            await bot.send_message(
                chat_id=self.config.chat_id,
                text=text,
                parse_mode=parse_mode,
            )
        except Exception as e:
            logger.error("Telegram send failed: %s", e)

    async def alert_fill(self, pair: str, side: str, price: float, amount: float, pnl: float):
        if not self.config.alert_on_fill:
            return
        emoji = "🟢" if side == "sell" else "🔴"
        msg = (
            f"{emoji} <b>Order Filled</b>\n"
            f"Pair: {pair}\n"
            f"Side: {side.upper()}\n"
            f"Price: {price:.2f}\n"
            f"Amount: {amount:.6f}\n"
            f"PnL: {pnl:+.4f} USDT"
        )
        await self.send_message(msg)

    async def alert_range_shift(self, pair: str, direction: str, new_lower: float,
                                 new_upper: float, source: str = "ATR"):
        if not self.config.alert_on_range_shift:
            return
        arrow = "⬆️" if direction == "up" else "⬇️"
        msg = (
            f"{arrow} <b>Range Shift ({source})</b>\n"
            f"Pair: {pair}\n"
            f"Direction: {direction.upper()}\n"
            f"New Range: [{new_lower:.2f}, {new_upper:.2f}]\n"
            f"Spread: {new_upper - new_lower:.2f}"
        )
        await self.send_message(msg)

    async def alert_lstm_prediction(self, pair: str, prediction: dict):
        direction = prediction.get("direction", "neutral")
        confidence = prediction.get("confidence", 0)
        label = prediction.get("label", "")
        emoji_map = {"bullish": "🚀", "bearish": "📉", "neutral": "➡️"}
        emoji = emoji_map.get(direction, "❓")
        msg = (
            f"{emoji} <b>LSTM Prediction</b>\n"
            f"Pair: {pair}\n"
            f"Signal: {label}\n"
            f"Confidence: {confidence:.1%}\n"
            f"Bullish: {prediction.get('bullish_prob', 0):.1%} | "
            f"Bearish: {prediction.get('bearish_prob', 0):.1%} | "
            f"Neutral: {prediction.get('neutral_prob', 0):.1%}\n"
            f"Suggested Range: [{prediction.get('lower', 0):.2f}, {prediction.get('upper', 0):.2f}]"
        )
        await self.send_message(msg)

    async def alert_drawdown_stop(self, pair: str, drawdown: float, equity: float):
        if not self.config.alert_on_drawdown:
            return
        msg = (
            f"🚨 <b>DRAWDOWN STOP — Bot Paused</b>\n"
            f"Pair: {pair}\n"
            f"Drawdown: {drawdown:.2f}%\n"
            f"Current Equity: {equity:.2f} USDT\n\n"
            f"Bot has been automatically paused.\n"
            f"Use /resume to restart trading."
        )
        await self.send_message(msg)

    async def alert_optimizer_complete(self, results: dict):
        best = results.get("best_params", {})
        attrs = results.get("best_attrs", {})
        msg = (
            f"🎯 <b>Optimization Complete</b>\n"
            f"Trials: {results.get('n_trials', 0)}\n"
            f"Best Score: {results.get('best_score', 0):.4f}\n\n"
            f"<b>Best Parameters:</b>\n"
            f"  Grid Count: {best.get('grid_count', '-')}\n"
            f"  Spacing: {best.get('spacing_percent', '-')}%\n"
            f"  ATR Mult: {best.get('atr_multiplier', '-')}\n"
            f"  Range Mult: {best.get('range_multiplier', '-')}\n"
            f"  Amount: {best.get('amount_per_order', '-')}\n"
            f"  Kelly: {best.get('kelly_fraction', '-')}\n\n"
            f"<b>Performance:</b>\n"
            f"  Ann. Return: {attrs.get('annualized_return', 0):.2f}%\n"
            f"  Max DD: {attrs.get('max_drawdown', 0):.2f}%\n"
            f"  Sharpe: {attrs.get('sharpe_ratio', 0):.4f}\n"
            f"  Win Rate: {attrs.get('win_rate', 0):.1%}"
        )
        await self.send_message(msg)

    async def send_daily_report(self, summaries: list[dict]):
        if not self.config.daily_report:
            return

        lines = ["📊 <b>Daily Performance Report</b>\n"]
        total_pnl = 0.0
        for s in summaries:
            total_pnl += s.get("total_pnl", 0)
            lines.append(
                f"<b>{s['pair']}</b>: "
                f"PnL={s.get('total_pnl', 0):+.4f} | "
                f"Trades={s.get('trade_count', 0)} | "
                f"DD={s.get('max_drawdown_pct', 0):.2f}% | "
                f"Sharpe={s.get('sharpe_ratio', 0):.2f}"
            )

        lines.append(f"\n<b>Total PnL: {total_pnl:+.4f} USDT</b>")
        await self.send_message("\n".join(lines))

    async def send_startup_message(self, pairs: list[str], config_source: str = "default"):
        msg = (
            f"🤖 <b>RichBot Started</b>\n"
            f"Config: {config_source}\n"
            f"Pairs: {', '.join(pairs)}\n"
            f"Mode: Live Trading"
        )
        await self.send_message(msg)

    def setup_command_handlers(self, bot_runner):
        """Set up Telegram command handlers (/status, /performance, /stop, /resume)."""
        try:
            from telegram.ext import Application, CommandHandler

            app = Application.builder().token(self.config.bot_token).build()

            async def cmd_status(update, context):
                stats = bot_runner.get_status()
                text = "📈 <b>Bot Status</b>\n"
                for k, v in stats.items():
                    text += f"  {k}: {v}\n"
                await update.message.reply_text(text, parse_mode="HTML")

            async def cmd_performance(update, context):
                summaries = bot_runner.get_performance()
                await self.send_daily_report(summaries)

            async def cmd_stop(update, context):
                bot_runner.stop()
                await update.message.reply_text("⏹ Bot stopped.")

            async def cmd_resume(update, context):
                bot_runner.resume()
                await update.message.reply_text("▶️ Bot resumed.")

            app.add_handler(CommandHandler("status", cmd_status))
            app.add_handler(CommandHandler("performance", cmd_performance))
            app.add_handler(CommandHandler("stop", cmd_stop))
            app.add_handler(CommandHandler("resume", cmd_resume))

            self._app = app
            logger.info("Telegram command handlers registered")

        except ImportError:
            logger.warning("python-telegram-bot not installed, commands not available")
        except Exception as e:
            logger.error("Failed to set up commands: %s", e)

    async def start_polling(self):
        """Start Telegram bot polling (for command handling)."""
        if self._app:
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling()
            logger.info("Telegram polling started")

    async def stop_polling(self):
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
