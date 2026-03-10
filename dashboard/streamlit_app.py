"""Streamlit Dashboard for RichBot — Grid Trading Bot."""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.config import load_config

st.set_page_config(page_title="RichBot Dashboard", page_icon="📈", layout="wide")

CONFIG = load_config()
DB_PATH = Path(CONFIG.db_path)


def get_db_connection():
    if not DB_PATH.exists():
        return None
    return sqlite3.connect(DB_PATH)


@st.cache_data(ttl=10)
def load_trades(pair: str | None = None, limit: int = 500) -> pd.DataFrame:
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    query = "SELECT * FROM trades ORDER BY timestamp DESC"
    if pair:
        query = f"SELECT * FROM trades WHERE pair = '{pair}' ORDER BY timestamp DESC"
    query += f" LIMIT {limit}"
    df = pd.read_sql_query(query, conn)
    conn.close()
    if not df.empty and "timestamp" in df.columns:
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
    return df


@st.cache_data(ttl=10)
def load_equity(pair: str | None = None) -> pd.DataFrame:
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    query = "SELECT * FROM equity_snapshots ORDER BY timestamp"
    if pair:
        query = f"SELECT * FROM equity_snapshots WHERE pair = '{pair}' ORDER BY timestamp"
    df = pd.read_sql_query(query, conn)
    conn.close()
    if not df.empty and "timestamp" in df.columns:
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
    return df


def render_overview_tab():
    st.header("Trading Overview")

    trades_df = load_trades()
    equity_df = load_equity()

    if trades_df.empty:
        st.info("No trading data available yet. Start the bot to see results here.")
        return

    pairs = trades_df["pair"].unique().tolist() if "pair" in trades_df.columns else []

    col1, col2, col3, col4 = st.columns(4)
    total_pnl = trades_df["pnl"].sum() if "pnl" in trades_df.columns else 0
    total_trades = len(trades_df)
    win_trades = len(trades_df[trades_df["pnl"] > 0]) if "pnl" in trades_df.columns else 0
    win_rate = win_trades / total_trades * 100 if total_trades > 0 else 0

    col1.metric("Total PnL", f"${total_pnl:.2f}")
    col2.metric("Total Trades", total_trades)
    col3.metric("Win Rate", f"{win_rate:.1f}%")
    col4.metric("Active Pairs", len(pairs))

    if not equity_df.empty:
        fig = go.Figure()
        for pair in equity_df["pair"].unique() if "pair" in equity_df.columns else ["ALL"]:
            pair_eq = equity_df[equity_df["pair"] == pair] if "pair" in equity_df.columns else equity_df
            fig.add_trace(go.Scatter(
                x=pair_eq["datetime"], y=pair_eq["equity"],
                mode="lines", name=pair, line=dict(width=2),
            ))
        fig.update_layout(title="Equity Curve", xaxis_title="Time", yaxis_title="Equity (USDT)",
                          height=400, template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)

    if not trades_df.empty and "pnl" in trades_df.columns:
        cum_pnl = trades_df.sort_values("timestamp")["pnl"].cumsum()
        fig = go.Figure()
        fig.add_trace(go.Scatter(y=cum_pnl.values, mode="lines", name="Cumulative PnL",
                                  fill="tozeroy", line=dict(color="#00d4aa")))
        fig.update_layout(title="Cumulative PnL", height=300, template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Recent Trades")
    display_cols = ["datetime", "pair", "side", "price", "amount", "pnl", "grid_level"]
    available_cols = [c for c in display_cols if c in trades_df.columns]
    st.dataframe(trades_df[available_cols].head(50), use_container_width=True)


def render_grid_tab():
    st.header("Grid Visualization")

    pair = st.selectbox("Select Pair", CONFIG.pairs, key="grid_pair")
    trades_df = load_trades(pair)

    if trades_df.empty:
        st.info(f"No trade data for {pair}")
        return

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                         row_heights=[0.7, 0.3])

    buys = trades_df[trades_df["side"] == "buy"]
    sells = trades_df[trades_df["side"] == "sell"]

    if not buys.empty:
        fig.add_trace(go.Scatter(
            x=buys["datetime"], y=buys["price"], mode="markers",
            name="Buy", marker=dict(color="green", size=8, symbol="triangle-up"),
        ), row=1, col=1)

    if not sells.empty:
        fig.add_trace(go.Scatter(
            x=sells["datetime"], y=sells["price"], mode="markers",
            name="Sell", marker=dict(color="red", size=8, symbol="triangle-down"),
        ), row=1, col=1)

    if "pnl" in trades_df.columns:
        colors = ["green" if p > 0 else "red" for p in trades_df["pnl"]]
        fig.add_trace(go.Bar(
            x=trades_df["datetime"], y=trades_df["pnl"],
            name="PnL", marker_color=colors,
        ), row=2, col=1)

    fig.update_layout(title=f"Grid Activity — {pair}", height=600, template="plotly_dark")
    st.plotly_chart(fig, use_container_width=True)


def render_optimizer_tab():
    st.header("Optimizer Results")

    try:
        from bot.optimizer import get_optimization_results
        results = get_optimization_results(CONFIG)
    except Exception:
        results = None

    if results is None:
        st.info("No optimization results available. Run: `python main.py --optimize`")
        return

    col1, col2, col3 = st.columns(3)
    col1.metric("Best Score", f"{results['best_score']:.4f}")
    col2.metric("Total Trials", results["total_trials"])
    col3.metric("Completed", results["completed_trials"])

    st.subheader("Best Parameters")
    params = results["best_params"]
    attrs = results["best_attrs"]

    pcol1, pcol2, pcol3 = st.columns(3)
    pcol1.metric("Grid Count", params.get("grid_count", "-"))
    pcol1.metric("Spacing %", f"{params.get('spacing_percent', 0):.2f}")
    pcol2.metric("ATR Multiplier", f"{params.get('atr_multiplier', 0):.1f}")
    pcol2.metric("Range Multiplier", f"{params.get('range_multiplier', 0):.1f}")
    pcol3.metric("Amount/Order", f"{params.get('amount_per_order', 0):.6f}")
    pcol3.metric("Kelly Fraction", f"{params.get('kelly_fraction', 0):.2f}")

    st.subheader("Performance Metrics")
    mcol1, mcol2, mcol3, mcol4 = st.columns(4)
    mcol1.metric("Ann. Return", f"{attrs.get('annualized_return', 0):.2f}%")
    mcol2.metric("Max Drawdown", f"{attrs.get('max_drawdown', 0):.2f}%")
    mcol3.metric("Sharpe Ratio", f"{attrs.get('sharpe_ratio', 0):.4f}")
    mcol4.metric("Win Rate", f"{attrs.get('win_rate', 0):.1%}")

    trials = results.get("trials", [])
    if trials:
        st.subheader("Trial History")
        trial_df = pd.DataFrame(trials)
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=trial_df["number"], y=trial_df["value"],
            mode="markers+lines", name="Score",
            marker=dict(size=4),
        ))
        best_so_far = trial_df["value"].cummax()
        fig.add_trace(go.Scatter(
            x=trial_df["number"], y=best_so_far,
            mode="lines", name="Best So Far",
            line=dict(color="gold", width=2),
        ))
        fig.update_layout(title="Optimization Progress", xaxis_title="Trial",
                          yaxis_title="Score", height=400, template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)

    plot_file = Path("data/optimizer_plots/optimization_history.html")
    if plot_file.exists():
        st.subheader("Detailed Optuna Plots")
        st.components.v1.html(plot_file.read_text(), height=500, scrolling=True)


def render_lstm_tab():
    st.header("LSTM Predictions")

    prediction_data = None
    model_info = {}

    for pair in CONFIG.pairs:
        from pathlib import Path as P
        model_path = P("models") / f"lstm_{pair.replace('/', '_')}.keras"
        meta_path = P("models") / f"meta_{pair.replace('/', '_')}.joblib"

        if model_path.exists():
            model_info[pair] = {"model": str(model_path), "exists": True}
            if meta_path.exists():
                import joblib
                meta = joblib.load(meta_path)
                model_info[pair]["features"] = len(meta.get("feature_columns", []))
                model_info[pair]["seq_length"] = meta.get("sequence_length", 48)
        else:
            model_info[pair] = {"exists": False}

    if not any(v["exists"] for v in model_info.values()):
        st.info("No LSTM models trained yet. Run: `python main.py --train-ml`")
        return

    for pair, info in model_info.items():
        if info["exists"]:
            st.subheader(f"{pair} Model")
            col1, col2 = st.columns(2)
            col1.metric("Features", info.get("features", "N/A"))
            col2.metric("Sequence Length", info.get("seq_length", "N/A"))

            st.caption(f"Model file: {info['model']}")

    st.subheader("Prediction Display")
    st.info("Live LSTM predictions will appear here when the bot is running.")

    st.markdown("""
    **Prediction Labels:**
    - 🚀 **Bullish Range Shift** — Model predicts upward price movement, range expands upward
    - 📉 **Bearish Range Shift** — Model predicts downward movement, range expands downward
    - ➡️ **Range Continuation** — Price expected to stay in current range

    Confidence threshold: **{:.0%}** (configured in config.json)
    """.format(CONFIG.ml.confidence_threshold))


def render_risk_tab():
    st.header("Risk Management")

    equity_df = load_equity()
    trades_df = load_trades()

    col1, col2, col3, col4 = st.columns(4)

    if not equity_df.empty:
        equities = equity_df["equity"].values
        peak = np.maximum.accumulate(equities)
        drawdowns = (peak - equities) / peak * 100
        max_dd = drawdowns.max()

        col1.metric("Max Drawdown", f"{max_dd:.2f}%")
        col2.metric("DD Limit", f"{CONFIG.risk.max_drawdown_percent:.1f}%")
    else:
        col1.metric("Max Drawdown", "N/A")
        col2.metric("DD Limit", f"{CONFIG.risk.max_drawdown_percent:.1f}%")

    col3.metric("Kelly Fraction", f"{CONFIG.risk.kelly_fraction:.2f}")
    col4.metric("Trailing Stop", f"{CONFIG.risk.trailing_stop_percent:.1f}%")

    if not equity_df.empty:
        st.subheader("Drawdown Chart")
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=equity_df["datetime"], y=-drawdowns,
            mode="lines", fill="tozeroy", name="Drawdown",
            line=dict(color="red"),
        ))
        fig.add_hline(y=-CONFIG.risk.max_drawdown_percent,
                       line_dash="dash", line_color="yellow",
                       annotation_text=f"Stop: -{CONFIG.risk.max_drawdown_percent}%")
        fig.update_layout(title="Drawdown Over Time", yaxis_title="Drawdown %",
                          height=300, template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Risk Parameters")
    st.json({
        "kelly_fraction": CONFIG.risk.kelly_fraction,
        "max_drawdown_percent": CONFIG.risk.max_drawdown_percent,
        "trailing_stop_percent": CONFIG.risk.trailing_stop_percent,
        "max_position_percent": CONFIG.risk.max_position_percent,
        "volatility_scaling": CONFIG.risk.volatility_scaling,
    })


def render_multi_pair_tab():
    st.header("Multi-Pair Overview")

    pairs = CONFIG.pairs
    if len(pairs) == 0:
        st.info("No pairs configured.")
        return

    trades_df = load_trades()
    if trades_df.empty:
        st.info("No trading data available.")
        return

    pair_summaries = []
    for pair in pairs:
        pair_trades = trades_df[trades_df["pair"] == pair] if "pair" in trades_df.columns else pd.DataFrame()
        if pair_trades.empty:
            pair_summaries.append({"pair": pair, "trades": 0, "pnl": 0, "win_rate": 0})
            continue

        wins = len(pair_trades[pair_trades["pnl"] > 0]) if "pnl" in pair_trades.columns else 0
        total = len(pair_trades)
        pnl = pair_trades["pnl"].sum() if "pnl" in pair_trades.columns else 0
        pair_summaries.append({
            "pair": pair,
            "trades": total,
            "pnl": pnl,
            "win_rate": wins / total * 100 if total > 0 else 0,
        })

    summary_df = pd.DataFrame(pair_summaries)

    cols = st.columns(len(pairs))
    for i, (col, row) in enumerate(zip(cols, pair_summaries)):
        col.metric(row["pair"], f"${row['pnl']:.2f}", f"{row['trades']} trades")
        col.metric("Win Rate", f"{row['win_rate']:.1f}%")

    if len(pair_summaries) > 1:
        fig = go.Figure(data=[
            go.Bar(
                x=[s["pair"] for s in pair_summaries],
                y=[s["pnl"] for s in pair_summaries],
                marker_color=["green" if s["pnl"] > 0 else "red" for s in pair_summaries],
            )
        ])
        fig.update_layout(title="PnL by Pair", yaxis_title="PnL (USDT)",
                          height=400, template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)

    for pair in pairs:
        pair_equity = load_equity(pair)
        if not pair_equity.empty:
            with st.expander(f"{pair} — Equity Curve"):
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=pair_equity["datetime"], y=pair_equity["equity"],
                    mode="lines", name=pair,
                ))
                fig.update_layout(height=300, template="plotly_dark")
                st.plotly_chart(fig, use_container_width=True)


def main():
    st.title("📈 RichBot Dashboard")
    st.caption("Professional Grid Trading Bot — Real-time Monitoring")

    tabs = st.tabs([
        "Overview",
        "Grid View",
        "Optimizer",
        "LSTM Predictions",
        "Risk Management",
        "Multi-Pair",
    ])

    with tabs[0]:
        render_overview_tab()
    with tabs[1]:
        render_grid_tab()
    with tabs[2]:
        render_optimizer_tab()
    with tabs[3]:
        render_lstm_tab()
    with tabs[4]:
        render_risk_tab()
    with tabs[5]:
        render_multi_pair_tab()

    st.sidebar.header("Configuration")
    st.sidebar.json({
        "pairs": CONFIG.pairs,
        "grid_count": CONFIG.grid.grid_count,
        "spacing": f"{CONFIG.grid.spacing_percent}%",
        "atr_multiplier": CONFIG.atr.multiplier,
        "infinity_mode": CONFIG.grid.infinity_mode,
        "websocket": CONFIG.websocket.enabled,
        "ml_enabled": CONFIG.ml.enabled,
    })

    if st.sidebar.button("Refresh Data"):
        st.cache_data.clear()
        st.rerun()


if __name__ == "__main__":
    main()
