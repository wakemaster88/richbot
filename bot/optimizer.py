"""Optuna-based hyperparameter optimization for grid trading strategy.

Pi-optimized: GC between trials, reduced memory footprint, lighter backtests.
"""

from __future__ import annotations

import gc
import json
import logging
from pathlib import Path

import optuna
import pandas as pd

from bot.backtester import Backtester
from bot.config import BotConfig, save_best_config

logger = logging.getLogger(__name__)


def objective(trial: optuna.Trial, ohlcv_df: pd.DataFrame, config: BotConfig,
              pi_mode: bool = False) -> float:
    """Optuna objective: Annualized Return - (2 x Max Drawdown) + Sharpe Ratio."""
    max_grid = 30 if pi_mode else 50
    grid_count = trial.suggest_int("grid_count", 8, max_grid)
    spacing_percent = trial.suggest_float("spacing_percent", 0.1, 2.0, step=0.05)
    atr_multiplier = trial.suggest_float("atr_multiplier", 0.5, 5.0, step=0.1)
    range_multiplier = trial.suggest_float("range_multiplier", 0.5, 3.0, step=0.1)
    amount_per_order = trial.suggest_float("amount_per_order", 0.0001, 0.01, log=True)
    kelly_fraction = trial.suggest_float("kelly_fraction", 0.05, 0.5, step=0.05)

    backtester = Backtester(config)
    result = backtester.run(
        ohlcv_df,
        grid_count=grid_count,
        spacing_percent=spacing_percent,
        atr_multiplier=atr_multiplier,
        range_multiplier=range_multiplier,
        amount_per_order=amount_per_order,
        kelly_fraction=kelly_fraction,
    )

    if result.total_trades < 5:
        score = -100.0
    else:
        score = result.annualized_return - (2 * result.max_drawdown) + result.sharpe_ratio

        trial.set_user_attr("annualized_return", result.annualized_return)
        trial.set_user_attr("max_drawdown", result.max_drawdown)
        trial.set_user_attr("sharpe_ratio", result.sharpe_ratio)
        trial.set_user_attr("total_trades", result.total_trades)
        trial.set_user_attr("win_rate", result.win_rate)
        trial.set_user_attr("profit_factor", result.profit_factor)

    del result, backtester
    if pi_mode and trial.number % 5 == 0:
        gc.collect()

    return score


def run_optimization(config: BotConfig, ohlcv_df: pd.DataFrame,
                      n_trials: int | None = None) -> dict:
    """Run Optuna optimization and save best parameters."""
    db_path = Path(config.optimizer.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    storage = f"sqlite:///{db_path}"
    trials = n_trials or config.optimizer.n_trials
    pi_mode = config.is_pi

    study = optuna.create_study(
        study_name=config.optimizer.study_name,
        storage=storage,
        direction="maximize",
        load_if_exists=True,
    )

    n_jobs = 1 if pi_mode else 1
    logger.info("Starting optimization: %d trials, pi_mode=%s (storage: %s)",
                trials, pi_mode, storage)

    study.optimize(
        lambda trial: objective(trial, ohlcv_df, config, pi_mode=pi_mode),
        n_trials=trials,
        n_jobs=n_jobs,
        show_progress_bar=True,
        gc_after_trial=pi_mode,
    )

    best = study.best_trial
    logger.info("Best trial #%d: score=%.4f", best.number, best.value)
    logger.info("  Params: %s", best.params)
    logger.info("  Ann. Return: %.2f%%", best.user_attrs.get("annualized_return", 0))
    logger.info("  Max Drawdown: %.2f%%", best.user_attrs.get("max_drawdown", 0))
    logger.info("  Sharpe: %.4f", best.user_attrs.get("sharpe_ratio", 0))

    config.grid.grid_count = best.params["grid_count"]
    config.grid.spacing_percent = best.params["spacing_percent"]
    config.atr.multiplier = best.params["atr_multiplier"]
    config.grid.range_multiplier = best.params["range_multiplier"]
    config.grid.amount_per_order = best.params["amount_per_order"]
    config.risk.kelly_fraction = best.params["kelly_fraction"]
    save_best_config(config)

    _save_plots(study)

    return {
        "best_score": best.value,
        "best_params": best.params,
        "best_attrs": dict(best.user_attrs),
        "n_trials": len(study.trials),
        "study_name": config.optimizer.study_name,
    }


def _save_plots(study: optuna.Study):
    """Save optimization plots as HTML. Skipped on Pi to save memory."""
    plots_dir = Path("data/optimizer_plots")
    plots_dir.mkdir(parents=True, exist_ok=True)

    try:
        from optuna.visualization import plot_optimization_history, plot_param_importances
    except ImportError:
        logger.warning("Optuna visualization not available (plotly missing?)")
        return

    try:
        fig = plot_optimization_history(study)
        fig.write_html(str(plots_dir / "optimization_history.html"))
        logger.info("Saved optimization history plot")
    except Exception as e:
        logger.warning("Could not save optimization history: %s", e)

    try:
        fig = plot_param_importances(study)
        fig.write_html(str(plots_dir / "param_importances.html"))
        logger.info("Saved parameter importances plot")
    except Exception as e:
        logger.warning("Could not save param importances: %s", e)


def load_study(config: BotConfig) -> optuna.Study | None:
    """Load an existing Optuna study for dashboard display."""
    db_path = Path(config.optimizer.db_path)
    if not db_path.exists():
        return None
    storage = f"sqlite:///{db_path}"
    try:
        return optuna.load_study(study_name=config.optimizer.study_name, storage=storage)
    except Exception as e:
        logger.warning("Could not load study: %s", e)
        return None


def get_optimization_results(config: BotConfig) -> dict | None:
    """Get optimization results for dashboard."""
    study = load_study(config)
    if study is None or len(study.trials) == 0:
        return None

    best = study.best_trial
    trials_data = []
    for t in study.trials:
        if t.state == optuna.trial.TrialState.COMPLETE:
            trials_data.append({
                "number": t.number,
                "value": t.value,
                "params": t.params,
                **{k: v for k, v in t.user_attrs.items()},
            })

    return {
        "best_score": best.value,
        "best_params": best.params,
        "best_attrs": dict(best.user_attrs),
        "total_trials": len(study.trials),
        "completed_trials": len(trials_data),
        "trials": trials_data,
    }
