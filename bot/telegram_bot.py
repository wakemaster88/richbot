"""Telegram bot for alerts, AI chat, commands, and scheduled reports."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bot.config import TelegramConfig
from bot.ai_chat import AIChat
from bot.scheduler import Scheduler, CronJob

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends trading alerts, handles AI chat, and manages cronjobs via Telegram."""

    def __init__(self, config: TelegramConfig):
        self.config = config
        self._bot = None
        self._app = None
        self.ai = AIChat()
        self.scheduler = Scheduler()
        self._bot_runner = None

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
        if not self.config.enabled or not self.config.chat_id:
            return
        bot = await self._get_bot()
        if bot is None:
            return
        try:
            if len(text) > 4000:
                text = text[:4000] + "\n\n<i>... (gekuerzt)</i>"
            await bot.send_message(
                chat_id=self.config.chat_id,
                text=text,
                parse_mode=parse_mode,
            )
        except Exception as e:
            logger.error("Telegram send failed: %s", e)

    # ---- Alerts ----

    async def alert_fill(self, pair: str, side: str, price: float, amount: float, pnl: float):
        if not self.config.alert_on_fill:
            return
        emoji = "🟢" if side == "sell" else "🔴"
        msg = (
            f"{emoji} <b>Order ausgefuehrt</b>\n"
            f"Paar: {pair}\n"
            f"Seite: {side.upper()}\n"
            f"Preis: {price:.2f}\n"
            f"Menge: {amount:.6f}\n"
            f"PnL: {pnl:+.4f} USDT"
        )
        await self.send_message(msg)

    async def alert_range_shift(self, pair: str, direction: str, new_lower: float,
                                 new_upper: float, source: str = "ATR"):
        if not self.config.alert_on_range_shift:
            return
        arrow = "⬆️" if direction == "up" else "⬇️"
        msg = (
            f"{arrow} <b>Range-Verschiebung ({source})</b>\n"
            f"Paar: {pair}\n"
            f"Richtung: {direction.upper()}\n"
            f"Neue Range: [{new_lower:.2f}, {new_upper:.2f}]\n"
            f"Spanne: {new_upper - new_lower:.2f}"
        )
        await self.send_message(msg)

    async def alert_lstm_prediction(self, pair: str, prediction: dict):
        direction = prediction.get("direction", "neutral")
        confidence = prediction.get("confidence", 0)
        label = prediction.get("label", "")
        emoji_map = {"bullish": "🚀", "bearish": "📉", "neutral": "➡️"}
        emoji = emoji_map.get(direction, "❓")
        msg = (
            f"{emoji} <b>LSTM-Vorhersage</b>\n"
            f"Paar: {pair}\n"
            f"Signal: {label}\n"
            f"Konfidenz: {confidence:.1%}\n"
            f"Bullish: {prediction.get('bullish_prob', 0):.1%} | "
            f"Bearish: {prediction.get('bearish_prob', 0):.1%} | "
            f"Neutral: {prediction.get('neutral_prob', 0):.1%}\n"
            f"Vorgeschlagene Range: [{prediction.get('lower', 0):.2f}, {prediction.get('upper', 0):.2f}]"
        )
        await self.send_message(msg)

    async def alert_drawdown_stop(self, pair: str, drawdown: float, equity: float):
        if not self.config.alert_on_drawdown:
            return
        msg = (
            f"🚨 <b>DRAWDOWN STOP — Bot pausiert</b>\n"
            f"Paar: {pair}\n"
            f"Drawdown: {drawdown:.2f}%\n"
            f"Kapital: {equity:.2f} USDT\n\n"
            f"Bot wurde automatisch pausiert.\n"
            f"Sende /resume zum Fortsetzen."
        )
        await self.send_message(msg)

    async def alert_optimizer_complete(self, results: dict):
        best = results.get("best_params", {})
        attrs = results.get("best_attrs", {})
        msg = (
            f"🎯 <b>Optimierung abgeschlossen</b>\n"
            f"Durchlaeufe: {results.get('n_trials', 0)}\n"
            f"Bester Score: {results.get('best_score', 0):.4f}\n\n"
            f"<b>Beste Parameter:</b>\n"
            f"  Grid-Level: {best.get('grid_count', '-')}\n"
            f"  Abstand: {best.get('spacing_percent', '-')}%\n"
            f"  ATR-Mult: {best.get('atr_multiplier', '-')}\n"
            f"  Range-Mult: {best.get('range_multiplier', '-')}\n"
            f"  Betrag: {best.get('amount_per_order', '-')}\n"
            f"  Kelly: {best.get('kelly_fraction', '-')}\n\n"
            f"<b>Performance:</b>\n"
            f"  Rendite p.a.: {attrs.get('annualized_return', 0):.2f}%\n"
            f"  Max DD: {attrs.get('max_drawdown', 0):.2f}%\n"
            f"  Sharpe: {attrs.get('sharpe_ratio', 0):.4f}\n"
            f"  Win-Rate: {attrs.get('win_rate', 0):.1%}"
        )
        await self.send_message(msg)

    # ---- Reports ----

    async def send_daily_report(self, summaries: list[dict]):
        if not self.config.daily_report:
            return

        lines = ["📊 <b>Tagesbericht</b>\n"]
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
        lines.append(f"\n<b>Gesamt-PnL: {total_pnl:+.4f} USDT</b>")

        ai_analysis = await self.ai.analyze_performance(summaries)
        if ai_analysis:
            lines.append(f"\n🤖 <b>KI-Analyse:</b>\n{ai_analysis}")

        await self.send_message("\n".join(lines))

    async def send_startup_message(self, pairs: list[str], config_source: str = "default"):
        msg = (
            f"🤖 <b>RichBot gestartet</b>\n"
            f"Config: {config_source}\n"
            f"Paare: {', '.join(pairs)}\n"
            f"Modus: Live Trading\n"
            f"\n💬 Schreib mir eine Nachricht fuer KI-Analyse!"
        )
        await self.send_message(msg)

    # ---- Cronjob Handlers ----

    async def _handle_daily_report(self, job: CronJob):
        if self._bot_runner:
            summaries = self._bot_runner.get_performance()
            await self.send_daily_report(summaries)
        else:
            await self.send_message("📊 <b>Geplanter Tagesbericht</b>\n\nKeine Bot-Daten verfuegbar.")

    async def _handle_performance(self, job: CronJob):
        if self._bot_runner:
            summaries = self._bot_runner.get_performance()
            text = "📈 <b>Performance-Bericht</b>\n\n"
            for s in summaries:
                text += (
                    f"<b>{s['pair']}</b>\n"
                    f"  PnL: {s.get('total_pnl', 0):+.4f} USDT\n"
                    f"  Trades: {s.get('trade_count', 0)}\n"
                    f"  Sharpe: {s.get('sharpe_ratio', 0):.2f}\n\n"
                )
            await self.send_message(text)

    async def _handle_status(self, job: CronJob):
        if self._bot_runner:
            stats = self._bot_runner.get_status()
            text = "📋 <b>Geplanter Status-Bericht</b>\n\n"
            for k, v in stats.items():
                text += f"  {k}: {v}\n"
            await self.send_message(text)
        else:
            await self.send_message("📋 Kein Bot-Runner verfuegbar.")

    async def _handle_custom(self, job: CronJob):
        if job.message:
            await self.send_message(f"⏰ <b>{job.name}</b>\n\n{job.message}")

    # ---- Command Handlers ----

    def setup_command_handlers(self, bot_runner):
        """Set up Telegram command handlers with AI chat and cronjobs."""
        self._bot_runner = bot_runner

        self.scheduler.register_handler("daily_report", self._handle_daily_report)
        self.scheduler.register_handler("performance", self._handle_performance)
        self.scheduler.register_handler("status", self._handle_status)
        self.scheduler.register_handler("custom", self._handle_custom)

        try:
            from telegram.ext import Application, CommandHandler, MessageHandler, filters

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
                await update.message.reply_text("⏹ Bot gestoppt.")

            async def cmd_resume(update, context):
                bot_runner.resume()
                await update.message.reply_text("▶️ Bot fortgesetzt.")

            async def cmd_jobs(update, context):
                jobs = self.scheduler.list_jobs()
                if not jobs:
                    await update.message.reply_text("📅 Keine Cronjobs eingerichtet.\n\nSag mir einfach z.B.:\n<i>\"Schick mir jeden Tag um 20:00 einen Bericht\"</i>", parse_mode="HTML")
                    return
                text = "📅 <b>Aktive Cronjobs</b>\n\n"
                for j in jobs:
                    status = "✅" if j.get("enabled", True) else "⏸"
                    text += f"{status} <b>{j['name']}</b>\n  ⏰ {j['schedule']} — {j['type']}\n\n"
                text += "<i>Loeschen mit:</i> /deljob &lt;name&gt;"
                await update.message.reply_text(text, parse_mode="HTML")

            async def cmd_deljob(update, context):
                if not context.args:
                    await update.message.reply_text("Verwendung: /deljob <name>")
                    return
                name = " ".join(context.args)
                if self.scheduler.remove_job(name):
                    await update.message.reply_text(f"🗑 Cronjob <b>{name}</b> geloescht.", parse_mode="HTML")
                else:
                    await update.message.reply_text(f"Cronjob '{name}' nicht gefunden.")

            async def cmd_help(update, context):
                text = (
                    "🤖 <b>RichBot Befehle</b>\n\n"
                    "/status — Bot-Status anzeigen\n"
                    "/performance — Performance-Bericht\n"
                    "/stop — Bot stoppen\n"
                    "/resume — Bot fortsetzen\n"
                    "/jobs — Cronjobs anzeigen\n"
                    "/deljob &lt;name&gt; — Cronjob loeschen\n"
                    "/help — Diese Hilfe\n\n"
                    "💬 <b>KI-Chat:</b> Schreib einfach eine Nachricht!\n"
                    "Beispiele:\n"
                    "<i>\"Wie laeuft der Bot?\"</i>\n"
                    "<i>\"Analysiere die Performance\"</i>\n"
                    "<i>\"Schick mir taeglich um 08:00 einen Bericht\"</i>\n"
                    "<i>\"Erstelle einen Status-Report jeden Tag um 20:00\"</i>"
                )
                await update.message.reply_text(text, parse_mode="HTML")

            async def handle_message(update, context):
                """AI-powered free-text handler."""
                if not update.message or not update.message.text:
                    return
                if str(update.message.chat_id) != str(self.config.chat_id):
                    return

                user_text = update.message.text

                if self._bot_runner:
                    self.ai.update_context({
                        "status": bot_runner.get_status(),
                        "performance": bot_runner.get_performance(),
                    })

                response = await self.ai.chat(user_text)

                cron = self.ai.parse_cronjob(response)
                if cron:
                    job = self.scheduler.add_job(
                        name=cron.get("name", "Bericht"),
                        schedule=cron.get("schedule", "08:00"),
                        job_type=cron.get("type", "daily_report"),
                        message=cron.get("message", ""),
                    )
                    clean_response = response.split('```json')[0].strip()
                    if clean_response:
                        clean_response += "\n\n"
                    clean_response += (
                        f"✅ Cronjob erstellt:\n"
                        f"<b>{job.name}</b> — taeglich um {job.schedule}\n"
                        f"Typ: {job.job_type}"
                    )
                    await update.message.reply_text(clean_response, parse_mode="HTML")
                else:
                    await update.message.reply_text(response, parse_mode="HTML")

            app.add_handler(CommandHandler("status", cmd_status))
            app.add_handler(CommandHandler("performance", cmd_performance))
            app.add_handler(CommandHandler("stop", cmd_stop))
            app.add_handler(CommandHandler("resume", cmd_resume))
            app.add_handler(CommandHandler("jobs", cmd_jobs))
            app.add_handler(CommandHandler("deljob", cmd_deljob))
            app.add_handler(CommandHandler("help", cmd_help))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

            self._app = app
            logger.info("Telegram command handlers + AI chat registered")

        except ImportError:
            logger.warning("python-telegram-bot not installed, commands not available")
        except Exception as e:
            logger.error("Failed to set up commands: %s", e)

    async def start_polling(self):
        """Start Telegram bot polling and scheduler."""
        if self._app:
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling()
            logger.info("Telegram polling started")
        await self.scheduler.start()

    async def stop_polling(self):
        await self.scheduler.stop()
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
