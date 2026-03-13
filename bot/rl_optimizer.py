"""Contextual Bandit for adaptive grid-parameter tuning.

Pure NumPy — no PyTorch/TensorFlow.  Runs on Raspberry Pi with <1 MB RAM.
Uses a linear policy with REINFORCE-style gradient updates and epsilon-greedy
exploration that decays over episodes.
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

STATE_DIM = 12
N_ACTIONS = 81  # 3^4 parameter deltas
LEARNING_RATE = 0.01
EXPLORATION_RATE = 0.15
MIN_EXPLORATION = 0.03
DECAY_RATE = 0.995
HISTORY_LIMIT = 1000

_REGIME_INDEX = {"ranging": 0, "trend_up": 1, "trend_down": 2, "volatile": 3}

# 3 choices per parameter: [-delta, 0, +delta]
_PCT_DELTAS = (-0.05, 0.0, 0.05)
_DIST_DELTAS = (-0.0005, 0.0, 0.0005)

SAFETY_BOUNDS: dict[str, tuple[float, float]] = {
    "spacing_mult": (0.5, 3.0),
    "size_mult": (0.2, 1.5),
    "range_multiplier": (0.5, 2.5),
    "min_distance_pct": (0.05, 1.0),
}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if abs(x) < 500 else (1.0 if x > 0 else 0.0)


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max())
    return e / e.sum()


def _decode_action(idx: int) -> dict[str, float]:
    """Decode a flat action index (0–80) into four parameter deltas."""
    spacing = _PCT_DELTAS[idx // 27]
    size = _PCT_DELTAS[(idx // 9) % 3]
    range_ = _PCT_DELTAS[(idx // 3) % 3]
    distance = _DIST_DELTAS[idx % 3]
    return {
        "spacing_delta": spacing,
        "size_delta": size,
        "range_delta": range_,
        "distance_delta": distance,
    }


def compute_reward(
    sharpe: float, win_rate: float, pnl_24h: float, drawdown_pct: float,
) -> float:
    """Standard reward function — called externally, kept here for reference."""
    return (
        sharpe * 0.5
        + (win_rate - 0.5) * 2.0
        + _clamp(pnl_24h * 100, -1.0, 1.0)
        - max(0.0, drawdown_pct - 3.0) * 0.5
    )


_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GridBandit:
    """Linear contextual bandit over a 12-dim state and 81 discrete actions."""

    def __init__(self, save_path: str = "data/rl_weights.json"):
        if not os.path.isabs(save_path):
            save_path = os.path.join(_PROJECT_DIR, save_path)
        self._save_path = save_path
        self.W: np.ndarray = np.zeros((N_ACTIONS, STATE_DIM), dtype=np.float32)
        self._pending: tuple[np.ndarray, int] | None = None
        self._history: list[dict[str, Any]] = []
        self._episode_count: int = 0
        self._exploration: float = EXPLORATION_RATE
        self._load()

    # -- public API -----------------------------------------------------------

    def get_state(
        self,
        regime_dict: dict[str, Any],
        perf_summary: dict[str, Any],
        sentiment_score: float = 0.0,
    ) -> np.ndarray:
        """Build a normalised 12-dim state vector."""
        s = np.zeros(STATE_DIM, dtype=np.float32)

        regime_key = regime_dict.get("regime", "ranging")
        ri = _REGIME_INDEX.get(regime_key, 0)
        s[ri] = 1.0

        s[4] = _clamp(regime_dict.get("rsi", 50.0) / 100.0, 0.0, 1.0)
        s[5] = _clamp(regime_dict.get("adx", 0.0) / 50.0, 0.0, 1.0)

        bw = regime_dict.get("boll_width", 0.0)
        abw = regime_dict.get("avg_boll_width", 1.0) or 1.0
        s[6] = _clamp((bw / abw) / 2.0, 0.0, 1.0)

        s[7] = _clamp(perf_summary.get("win_rate", 0.5), 0.0, 1.0)
        s[8] = _sigmoid(perf_summary.get("sharpe", 0.0))
        s[9] = _clamp(perf_summary.get("max_drawdown_pct", 0.0) / 10.0, 0.0, 1.0)
        s[10] = _clamp(perf_summary.get("fill_rate", 0.5), 0.0, 1.0)
        s[11] = _clamp((sentiment_score + 1.0) / 2.0, 0.0, 1.0)

        return s

    def choose_action(self, state: np.ndarray) -> dict[str, Any]:
        """Epsilon-greedy action selection (NaN-safe)."""
        np.nan_to_num(state, copy=False, nan=0.0, posinf=1.0, neginf=0.0)

        explore = np.random.random() < self._exploration
        if explore:
            action_idx = int(np.random.randint(N_ACTIONS))
        else:
            scores = self.W @ state
            if not np.all(np.isfinite(scores)):
                action_idx = int(np.random.randint(N_ACTIONS))
            else:
                action_idx = int(np.argmax(scores))

        self._pending = (state.copy(), action_idx)

        result = _decode_action(action_idx)
        result["action_idx"] = action_idx
        result["was_exploration"] = explore
        return result

    def record_reward(self, reward: float):
        """REINFORCE-style gradient update on the linear policy (NaN-safe)."""
        if self._pending is None:
            return

        if not math.isfinite(reward):
            reward = 0.0

        state, action_idx = self._pending
        self._pending = None

        scores = self.W @ state
        if not np.all(np.isfinite(scores)):
            scores = np.zeros(N_ACTIONS, dtype=np.float32)
        probs = _softmax(scores)

        grad = -probs.copy()
        grad[action_idx] += 1.0  # one_hot - probs

        update = LEARNING_RATE * reward * np.outer(grad, state)
        if np.all(np.isfinite(update)):
            self.W += update

        self._exploration = max(MIN_EXPLORATION, self._exploration * DECAY_RATE)
        self._episode_count += 1

        self._history.append({
            "state": state.tolist(),
            "action": action_idx,
            "reward": round(reward, 4),
            "episode": self._episode_count,
        })
        if len(self._history) > HISTORY_LIMIT:
            self._history = self._history[-HISTORY_LIMIT:]

        self._save()

    def get_stats(self) -> dict[str, Any]:
        last_20 = self._history[-20:] if self._history else []
        avg_r = sum(h["reward"] for h in last_20) / len(last_20) if last_20 else 0.0

        best_per_regime: dict[str, int] = {}
        for ri, name in enumerate(["ranging", "trend_up", "trend_down", "volatile"]):
            probe = np.zeros(STATE_DIM, dtype=np.float32)
            probe[ri] = 1.0
            probe[4] = 0.5  # neutral RSI
            probe[7] = 0.5  # neutral win rate
            probe[11] = 0.5  # neutral sentiment
            best_per_regime[name] = int(np.argmax(self.W @ probe))

        return {
            "episodes": self._episode_count,
            "exploration_rate": round(self._exploration, 4),
            "avg_reward_last_20": round(avg_r, 4),
            "best_action_per_regime": best_per_regime,
            "history_len": len(self._history),
        }

    @staticmethod
    def apply_deltas(
        params: dict[str, float], deltas: dict[str, float],
    ) -> dict[str, float]:
        """Apply action deltas to current parameters, respecting safety bounds."""
        out = dict(params)
        out["spacing_mult"] = _clamp(
            out.get("spacing_mult", 1.0) * (1.0 + deltas.get("spacing_delta", 0.0)),
            *SAFETY_BOUNDS["spacing_mult"],
        )
        out["size_mult"] = _clamp(
            out.get("size_mult", 1.0) * (1.0 + deltas.get("size_delta", 0.0)),
            *SAFETY_BOUNDS["size_mult"],
        )
        out["range_multiplier"] = _clamp(
            out.get("range_multiplier", 1.0) * (1.0 + deltas.get("range_delta", 0.0)),
            *SAFETY_BOUNDS["range_multiplier"],
        )
        out["min_distance_pct"] = _clamp(
            out.get("min_distance_pct", 0.15) + deltas.get("distance_delta", 0.0),
            *SAFETY_BOUNDS["min_distance_pct"],
        )
        return out

    # -- persistence ----------------------------------------------------------

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._save_path) or ".", exist_ok=True)
            payload = {
                "W": self.W.tolist(),
                "episode_count": self._episode_count,
                "exploration_rate": self._exploration,
            }
            tmp = self._save_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f)
            os.replace(tmp, self._save_path)
        except Exception:
            logger.warning("RL-Weights konnten nicht gespeichert werden: %s", self._save_path, exc_info=True)

    def _load(self):
        try:
            with open(self._save_path) as f:
                data = json.load(f)
            w = np.array(data["W"], dtype=np.float32)
            if w.shape == (N_ACTIONS, STATE_DIM):
                self.W = w
                self._episode_count = int(data.get("episode_count", 0))
                self._exploration = float(data.get("exploration_rate", EXPLORATION_RATE))
                logger.info(
                    "RL-Weights geladen: %d Episoden, ε=%.3f (%s)",
                    self._episode_count, self._exploration, self._save_path,
                )
            else:
                logger.warning("RL-Weights Shape mismatch, starte frisch")
        except FileNotFoundError:
            logger.info("RL-Weights nicht gefunden, starte frisch: %s", self._save_path)
        except Exception:
            logger.warning("RL-Weights Ladefehler, starte frisch: %s", self._save_path, exc_info=True)
