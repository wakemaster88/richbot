"""RichBot — Professional Grid Trading Bot.

Usage:
    python main.py                          # Live trading (single/multi-pair)
    python main.py --optimize               # Run Optuna optimization
    python main.py --train-ml               # Train LSTM model
    python main.py --backtest               # Run backtest
    python main.py --multi-pair             # Force multi-pair mode
    python main.py --optimize --train-ml    # Optimize, then train ML
    python main.py --config config_pi.json  # Pi-optimized config
    python main.py --use-best               # Use optimized config
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import logging
import logging.handlers
import resource
import sys
from pathlib import Path


def setup_logging(level: str = "INFO", log_file: str = "logs/richbot.log",
                  max_bytes: int = 10_485_760, backup_count: int = 3):
    """Configure logging with RotatingFileHandler for SD card friendliness."""
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    for noisy in ("ccxt", "urllib3", "tensorflow", "absl", "h5py", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def parse_args():
    parser = argparse.ArgumentParser(description="RichBot — Grid Trading Bot")
    parser.add_argument("--config", type=str, default="config.json",
                        help="Path to config file (use config_pi.json for Raspberry Pi)")
    parser.add_argument("--use-best", action="store_true",
                        help="Use optimized config from config_best.json")
    parser.add_argument("--optimize", action="store_true",
                        help="Run Optuna hyperparameter optimization")
    parser.add_argument("--train-ml", action="store_true",
                        help="Train LSTM prediction model")
    parser.add_argument("--backtest", action="store_true",
                        help="Run backtest with current config")
    parser.add_argument("--multi-pair", action="store_true",
                        help="Force multi-pair mode")
    parser.add_argument("--pairs", nargs="+", help="Override trading pairs")
    parser.add_argument("--trials", type=int, help="Override optimization trials")
    parser.add_argument("--days", type=int, help="Override backtest/training days")
    parser.add_argument("--dashboard", action="store_true",
                        help="Launch Streamlit dashboard")
    return parser.parse_args()


def _apply_pi_limits(config):
    """Apply OS-level memory limits when running on Pi."""
    if not config.is_pi:
        return

    limit_bytes = config.pi.memory_limit_mb * 1024 * 1024
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, hard))
        logger = logging.getLogger("pi")
        logger.info("Memory limit set to %d MB", config.pi.memory_limit_mb)
    except (ValueError, resource.error):
        pass

    gc.set_threshold(700, 10, 5)
    gc.enable()


def run_optimize(config, args):
    from bot.backtester import Backtester
    from bot.exchange import Exchange
    from bot.optimizer import run_optimization

    logger = logging.getLogger("optimize")
    logger.info("=== OPTUNA OPTIMIZATION ===")

    exchange = Exchange(config.exchange)
    backtester = Backtester(config)
    days = args.days or config.optimizer.backtest_days
    pair = config.pairs[0]

    logger.info("Fetching %d days of data for %s...", days, pair)
    ohlcv_df = backtester.fetch_historical_data(exchange, pair, config.atr.timeframe, days)

    if len(ohlcv_df) < 100:
        logger.error("Not enough data for optimization (got %d candles)", len(ohlcv_df))
        return

    if args.trials:
        config.optimizer.n_trials = args.trials

    results = run_optimization(config, ohlcv_df, config.optimizer.n_trials)

    logger.info("Optimization complete!")
    logger.info("  Best score: %.4f", results["best_score"])
    logger.info("  Best params: %s", results["best_params"])
    logger.info("  Results saved to config_best.json")

    del ohlcv_df
    gc.collect()

    asyncio.run(_notify_optimizer(config, results))


async def _notify_optimizer(config, results):
    from bot.telegram_bot import TelegramNotifier
    telegram = TelegramNotifier(config.telegram)
    await telegram.alert_optimizer_complete(results)


def run_train_ml(config, args):
    from bot.exchange import Exchange
    from bot.ml_predictor import LSTMPredictor

    logger = logging.getLogger("train_ml")
    logger.info("=== LSTM MODEL TRAINING ===")

    exchange = Exchange(config.exchange)
    days = args.days or config.ml.lookback_days

    for pair in config.pairs:
        logger.info("Training LSTM for %s (%d days)...", pair, days)

        from bot.backtester import Backtester
        bt = Backtester(config)

        for tf in config.ml.timeframes:
            logger.info("  Fetching %s data...", tf)
            ohlcv_df = bt.fetch_historical_data(exchange, pair, tf, days)

            if len(ohlcv_df) < 200:
                logger.warning("  Insufficient data for %s %s (%d candles)", pair, tf, len(ohlcv_df))
                continue

            predictor = LSTMPredictor(config.ml, pair, pi_config=config.pi)
            result = predictor.train(ohlcv_df)
            logger.info("  %s %s training: %s", pair, tf, result)

            del ohlcv_df, predictor
            gc.collect()


def run_backtest(config, args):
    from bot.backtester import Backtester
    from bot.exchange import Exchange

    logger = logging.getLogger("backtest")
    logger.info("=== BACKTEST ===")

    exchange = Exchange(config.exchange)
    backtester = Backtester(config)
    days = args.days or config.optimizer.backtest_days

    for pair in config.pairs:
        logger.info("Backtesting %s (%d days)...", pair, days)
        ohlcv_df = backtester.fetch_historical_data(exchange, pair, config.atr.timeframe, days)

        if len(ohlcv_df) < 100:
            logger.warning("Insufficient data for %s", pair)
            continue

        result = backtester.run(ohlcv_df, pair=pair)

        logger.info("Results for %s:", pair)
        logger.info("  Total Return:      %.2f%%", result.total_return)
        logger.info("  Annualized Return: %.2f%%", result.annualized_return)
        logger.info("  Max Drawdown:      %.2f%%", result.max_drawdown)
        logger.info("  Sharpe Ratio:      %.4f", result.sharpe_ratio)
        logger.info("  Total Trades:      %d", result.total_trades)
        logger.info("  Win Rate:          %.1f%%", result.win_rate * 100)
        logger.info("  Profit Factor:     %.2f", result.profit_factor)
        logger.info("  Avg Trade PnL:     %.4f", result.avg_trade_pnl)

        del ohlcv_df, result
        gc.collect()


def run_live(config, args):
    logger = logging.getLogger("live")
    logger.info("=== LIVE TRADING ===")
    logger.info("Pairs: %s | Pi-Mode: %s", config.pairs, config.is_pi)

    from bot.multi_pair import MultiPairBot
    import signal

    bot = MultiPairBot(config)

    async def _run():
        loop = asyncio.get_event_loop()
        shutdown_event = asyncio.Event()

        def _signal_handler():
            logger.info("SIGTERM/SIGINT received — graceful shutdown...")
            shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        try:
            start_task = asyncio.create_task(bot.start())
            shutdown_task = asyncio.create_task(shutdown_event.wait())
            done, _ = await asyncio.wait(
                [start_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
        except KeyboardInterrupt:
            logger.info("Shutdown requested...")
        finally:
            await bot.stop()

    asyncio.run(_run())


def run_dashboard(config):
    import subprocess
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        "dashboard/streamlit_app.py",
        "--server.port", "8501",
    ]
    if config.is_pi:
        cmd.extend([
            "--server.maxUploadSize", "5",
            "--server.maxMessageSize", "50",
            "--browser.gatherUsageStats", "false",
        ])
    subprocess.run(cmd)


def main():
    args = parse_args()

    try:
        from dotenv import load_dotenv
        env_path = Path(args.config).parent / ".env"
        load_dotenv(env_path)
        load_dotenv()  # also try cwd/.env
    except ImportError:
        pass

    from bot.config import load_config
    config = load_config(Path(args.config), use_best=args.use_best)

    if args.pairs:
        config.pairs = args.pairs

    setup_logging(
        config.logging_cfg.level,
        config.logging_cfg.file,
        config.logging_cfg.max_bytes,
        config.logging_cfg.backup_count,
    )

    logger = logging.getLogger("main")
    logger.info("RichBot v2.0 starting... (Pi-Mode: %s)", config.is_pi)

    _apply_pi_limits(config)

    if args.dashboard:
        run_dashboard(config)
        return

    if args.optimize:
        run_optimize(config, args)
        if not args.train_ml and not args.backtest:
            return

    if args.train_ml:
        run_train_ml(config, args)
        if not args.backtest:
            return

    if args.backtest:
        run_backtest(config, args)
        return

    run_live(config, args)


if __name__ == "__main__":
    main()
