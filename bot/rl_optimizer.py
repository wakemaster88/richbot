"""Contextual Bandit V2 for adaptive grid-parameter tuning.

Improvements over V1:
  - Circular replay buffer (500 episodes, ~200 KB) for experience replay
  - Risk-adjusted reward: Sharpe + PnL + stability components
  - Reduced action space: 27 actions (3^3) instead of 81 (3^4)
  - Enriched 18-dim state with historical context
  - Learning rate schedule with warm-start decay

Pure NumPy — no PyTorch/TensorFlow.  Runs on Raspberry Pi with <1 MB RAM.
"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ── dimensions ────────────────────────────────────────────────────────

STATE_DIM = 18
N_ACTIONS = 27  # 3^3: spacing_delta × size_delta × aggressiveness

# ── hyperparameters ───────────────────────────────────────────────────

BASE_LR = 0.02
LR_DECAY = 0.001
EXPLORATION_RATE = 0.15
MIN_EXPLORATION = 0.03
DECAY_RATE = 0.995

REPLAY_SIZE = 500
REPLAY_SAMPLE = 4
HISTORY_LIMIT = 1000

_REGIME_INDEX = {"ranging": 0, "trend_up": 1, "trend_down": 2, "volatile": 3}

_DELTAS = (-0.05, 0.0, 0.05)
_AGG_LEVELS = (0.7, 1.0, 1.3)

SAFETY_BOUNDS: dict[str, tuple[float, float]] = {
    "spacing_mult": (0.5, 3.0),
    "size_mult": (0.2, 1.5),
    "range_multiplier": (0.5, 2.5),
    "min_distance_pct": (0.05, 1.0),
}


# ── helpers ───────────────────────────────────────────────────────────

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if abs(x) < 500 else (1.0 if x > 0 else 0.0)


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max())
    s = e.sum()
    return e / s if s > 0 else np.ones_like(e) / len(e)


def _decode_action(idx: int) -> dict[str, float]:
    """Decode action index 0–26 into three parameter deltas."""
    spacing = _DELTAS[idx // 9]
    size = _DELTAS[(idx // 3) % 3]
    agg_level = _AGG_LEVELS[idx % 3]
    return {
        "spacing_delta": spacing,
        "size_delta": size,
        "aggressiveness": agg_level,
        "range_delta": 0.0,
        "distance_delta": 0.0,
    }


def compute_reward(
    sharpe: float,
    win_rate: float,
    pnl_24h: float,
    drawdown_pct: float,
    trade_count: int = 1,
    return_volatility: float = 0.0,
) -> float:
    """Risk-adjusted reward combining Sharpe, PnL, and stability."""
    sharpe_component = _clamp(sharpe, -2.0, 2.0) / 2.0

    pnl_component = _clamp(pnl_24h * 100, -1.0, 1.0)

    stability = 0.0
    dd_penalty = max(0.0, drawdown_pct - 3.0) * 0.15
    stability -= dd_penalty
    if return_volatility > 0:
        vol_bonus = _clamp(1.0 - return_volatility * 10.0, -0.3, 0.3)
        stability += vol_bonus
    if win_rate > 0.60:
        stability += 0.1
    elif win_rate < 0.40 and trade_count > 3:
        stability -= 0.1
    if trade_count == 0:
        stability -= 0.05

    return (
        sharpe_component * 0.5
        + pnl_component * 0.3
        + _clamp(stability, -0.5, 0.5) * 0.2
    )


# ── replay buffer ────────────────────────────────────────────────────

class ReplayBuffer:
    """Fixed-size circular buffer for (state, action, reward) tuples."""

    def __init__(self, capacity: int = REPLAY_SIZE):
        self._states = np.zeros((capacity, STATE_DIM), dtype=np.float32)
        self._actions = np.zeros(capacity, dtype=np.int32)
        self._rewards = np.zeros(capacity, dtype=np.float32)
        self._size = 0
        self._ptr = 0
        self._cap = capacity

    def add(self, state: np.ndarray, action: int, reward: float):
        self._states[self._ptr] = state
        self._actions[self._ptr] = action
        self._rewards[self._ptr] = reward
        self._ptr = (self._ptr + 1) % self._cap
        self._size = min(self._size + 1, self._cap)

    def sample(self, n: int) -> list[tuple[np.ndarray, int, float]]:
        if self._size == 0:
            return []
        indices = np.random.randint(0, self._size, size=min(n, self._size))
        return [
            (self._states[i].copy(), int(self._actions[i]), float(self._rewards[i]))
            for i in indices
        ]

    @property
    def size(self) -> int:
        return self._size

    def serialize(self) -> dict:
        return {
            "states": self._states[:self._size].tolist(),
            "actions": self._actions[:self._size].tolist(),
            "rewards": self._rewards[:self._size].tolist(),
        }

    def deserialize(self, data: dict):
        states = data.get("states", [])
        actions = data.get("actions", [])
        rewards = data.get("rewards", [])
        n = min(len(states), len(actions), len(rewards), self._cap)
        for i in range(n):
            self._states[i] = states[i]
            self._actions[i] = actions[i]
            self._rewards[i] = rewards[i]
        self._size = n
        self._ptr = n % self._cap


# ── main agent ───────────────────────────────────────────────────────

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GridBandit:
    """Linear contextual bandit V2 — 18-dim state, 27 actions, replay buffer."""

    def __init__(self, save_path: str = "data/rl_weights.json"):
        if not os.path.isabs(save_path):
            save_path = os.path.join(_PROJECT_DIR, save_path)
        self._save_path = save_path
        self.W: np.ndarray = np.zeros((N_ACTIONS, STATE_DIM), dtype=np.float32)
        self._replay = ReplayBuffer(REPLAY_SIZE)
        self._pending: tuple[np.ndarray, int] | None = None
        self._history: list[dict[str, Any]] = []
        self._episode_count: int = 0
        self._exploration: float = EXPLORATION_RATE
        self._load()

    @property
    def _lr(self) -> float:
        """Learning rate with decay schedule."""
        return BASE_LR / (1.0 + LR_DECAY * self._episode_count)

    # ── state builder ─────────────────────────────────────────────

    def get_state(
        self,
        regime_dict: dict[str, Any],
        perf_summary: dict[str, Any],
        sentiment_score: float = 0.0,
        spread_bps: float = 0.0,
        mtf_alignment: float = 0.0,
        inventory_skew: float = 0.0,
    ) -> np.ndarray:
        """Build a normalised 18-dim state vector."""
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

        # V2 enriched features
        recent = self._history[-5:] if self._history else []
        s[12] = _clamp(
            sum(h["reward"] for h in recent) / max(len(recent), 1) / 2.0 + 0.5,
            0.0, 1.0,
        )

        recent_10 = self._history[-10:] if self._history else []
        if recent_10:
            unique_actions = len(set(h["action"] for h in recent_10))
            s[13] = _clamp(unique_actions / min(N_ACTIONS, 10), 0.0, 1.0)
        else:
            s[13] = 0.5

        s[14] = _clamp(perf_summary.get("hours_since_profitable", 0.0) / 48.0, 0.0, 1.0)
        s[15] = _clamp(spread_bps / 20.0, 0.0, 1.0)
        s[16] = _clamp((mtf_alignment + 1.0) / 2.0, 0.0, 1.0)
        s[17] = _clamp((inventory_skew + 1.0) / 2.0, 0.0, 1.0)

        return s

    # ── action selection ──────────────────────────────────────────

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

    # ── learning ──────────────────────────────────────────────────

    def record_reward(self, reward: float):
        """REINFORCE update with experience replay."""
        if self._pending is None:
            return
        if not math.isfinite(reward):
            reward = 0.0

        state, action_idx = self._pending
        self._pending = None

        self._update_weights(state, action_idx, reward)

        self._replay.add(state, action_idx, reward)

        replays = self._replay.sample(REPLAY_SAMPLE)
        for r_state, r_action, r_reward in replays:
            self._update_weights(r_state, r_action, r_reward)

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

    def _update_weights(self, state: np.ndarray, action_idx: int, reward: float):
        """Single REINFORCE gradient step with current learning rate."""
        scores = self.W @ state
        if not np.all(np.isfinite(scores)):
            scores = np.zeros(N_ACTIONS, dtype=np.float32)
        probs = _softmax(scores)

        grad = -probs.copy()
        grad[action_idx] += 1.0

        lr = self._lr
        update = lr * reward * np.outer(grad, state)
        if np.all(np.isfinite(update)):
            self.W += update

    # ── stats ─────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        last_20 = self._history[-20:] if self._history else []
        avg_r = sum(h["reward"] for h in last_20) / len(last_20) if last_20 else 0.0

        best_per_regime: dict[str, int] = {}
        for ri, name in enumerate(["ranging", "trend_up", "trend_down", "volatile"]):
            probe = np.zeros(STATE_DIM, dtype=np.float32)
            probe[ri] = 1.0
            probe[4] = 0.5
            probe[7] = 0.5
            probe[11] = 0.5
            probe[12] = 0.5
            probe[16] = 0.5
            best_per_regime[name] = int(np.argmax(self.W @ probe))

        return {
            "episodes": self._episode_count,
            "exploration_rate": round(self._exploration, 4),
            "learning_rate": round(self._lr, 5),
            "avg_reward_last_20": round(avg_r, 4),
            "best_action_per_regime": best_per_regime,
            "history_len": len(self._history),
            "replay_buffer_size": self._replay.size,
            "state_dim": STATE_DIM,
            "n_actions": N_ACTIONS,
        }

    # ── apply deltas ──────────────────────────────────────────────

    @staticmethod
    def apply_deltas(
        params: dict[str, float], deltas: dict[str, float],
    ) -> dict[str, float]:
        """Apply action deltas to current parameters, respecting safety bounds."""
        out = dict(params)
        agg = deltas.get("aggressiveness", 1.0)

        out["spacing_mult"] = _clamp(
            out.get("spacing_mult", 1.0) * (1.0 + deltas.get("spacing_delta", 0.0)),
            *SAFETY_BOUNDS["spacing_mult"],
        )
        out["size_mult"] = _clamp(
            out.get("size_mult", 1.0) * (1.0 + deltas.get("size_delta", 0.0)) * agg,
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

    # ── persistence ───────────────────────────────────────────────

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._save_path) or ".", exist_ok=True)
            payload = {
                "version": 2,
                "W": self.W.tolist(),
                "episode_count": self._episode_count,
                "exploration_rate": self._exploration,
                "replay": self._replay.serialize(),
            }
            tmp = self._save_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(payload, f)
            os.replace(tmp, self._save_path)
        except Exception:
            logger.warning("RL save failed: %s", self._save_path, exc_info=True)

    def _load(self):
        try:
            with open(self._save_path) as f:
                data = json.load(f)

            version = data.get("version", 1)
            w = np.array(data["W"], dtype=np.float32)

            if version >= 2 and w.shape == (N_ACTIONS, STATE_DIM):
                self.W = w
                self._episode_count = int(data.get("episode_count", 0))
                self._exploration = float(data.get("exploration_rate", EXPLORATION_RATE))
                if "replay" in data:
                    self._replay.deserialize(data["replay"])
                logger.info(
                    "RL V2 loaded: %d episodes, ε=%.3f, replay=%d (%s)",
                    self._episode_count, self._exploration,
                    self._replay.size, self._save_path,
                )
            elif version == 1:
                old_n, old_s = w.shape
                logger.info(
                    "RL V1 weights detected (%dx%d) — migrating to V2 (%dx%d)",
                    old_n, old_s, N_ACTIONS, STATE_DIM,
                )
                new_w = np.zeros((N_ACTIONS, STATE_DIM), dtype=np.float32)
                copy_a = min(old_n, N_ACTIONS)
                copy_s = min(old_s, STATE_DIM)
                new_w[:copy_a, :copy_s] = w[:copy_a, :copy_s]
                self.W = new_w
                self._episode_count = int(data.get("episode_count", 0))
                self._exploration = max(
                    float(data.get("exploration_rate", EXPLORATION_RATE)),
                    0.10,
                )
                logger.info(
                    "V1→V2 migration complete: %d episodes, ε reset to %.3f",
                    self._episode_count, self._exploration,
                )
            else:
                logger.warning("RL weights shape mismatch, starting fresh")
        except FileNotFoundError:
            logger.info("RL weights not found, starting fresh: %s", self._save_path)
        except Exception:
            logger.warning("RL load error, starting fresh: %s", self._save_path, exc_info=True)
