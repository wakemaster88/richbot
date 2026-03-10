"""LSTM-based range prediction with TFLite support for Raspberry Pi.

Training uses full TensorFlow (run on desktop/cloud).
Inference uses TFLite runtime on Pi (~5MB RAM vs ~2GB for full TF).
"""

from __future__ import annotations

import gc
import logging
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import joblib

from bot.config import MLConfig, PiConfig

logger = logging.getLogger(__name__)

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

MODELS_DIR = Path("models")
MODELS_DIR.mkdir(parents=True, exist_ok=True)

_FLOAT_DTYPE = np.float32


def _add_technical_features(df: pd.DataFrame, use_float32: bool = False) -> pd.DataFrame:
    """Add technical indicators. Memory-optimized for Pi."""
    try:
        import pandas_ta as ta
        has_ta = True
    except ImportError:
        has_ta = False

    df = df.copy()

    if use_float32:
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = df[col].astype(np.float32)

    if has_ta:
        df.ta.rsi(length=14, append=True)
        df.ta.rsi(length=7, append=True)
        df.ta.atr(length=14, append=True)
        df.ta.bbands(length=20, append=True)
        df.ta.macd(append=True)
        df.ta.ema(length=9, append=True)
        df.ta.ema(length=21, append=True)
    else:
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        df["RSI_14"] = 100 - (100 / (1 + rs))

        sma = df["close"].rolling(20).mean()
        std = df["close"].rolling(20).std()
        df["BBU_20_2.0"] = sma + 2 * std
        df["BBL_20_2.0"] = sma - 2 * std

        ema12 = df["close"].ewm(span=12).mean()
        ema26 = df["close"].ewm(span=26).mean()
        df["MACD_12_26_9"] = ema12 - ema26

        df["EMA_9"] = df["close"].ewm(span=9).mean()
        df["EMA_21"] = df["close"].ewm(span=21).mean()

    df["returns"] = df["close"].pct_change()
    df["log_returns"] = np.log(df["close"] / df["close"].shift(1))
    df["volatility_20"] = df["returns"].rolling(20).std()
    df["range_hl"] = (df["high"] - df["low"]) / df["close"]
    df["volume_sma"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_sma"].replace(0, np.nan)

    df = df.dropna()
    return df


def _create_labels(df: pd.DataFrame, horizon: int = 12) -> pd.Series:
    future_high = df["high"].rolling(horizon).max().shift(-horizon)
    future_low = df["low"].rolling(horizon).min().shift(-horizon)
    future_range = (future_high - future_low) / df["close"]

    current_atr = (df["high"] - df["low"]).rolling(14).mean()
    threshold = current_atr / df["close"] * 1.5

    return (future_range > threshold).astype(int)


class LSTMPredictor:
    """LSTM predictor with dual-mode: full TF for training, TFLite for Pi inference."""

    def __init__(self, config: MLConfig, pair: str = "BTC_USDT",
                 pi_config: PiConfig | None = None):
        self.config = config
        self.pi = pi_config or PiConfig()
        self.pair = pair.replace("/", "_")
        self.model = None
        self._tflite_interpreter = None
        self.scaler = None
        self.feature_columns: list[str] = []
        self.sequence_length = 48 if not self.pi.enabled else 24
        self.last_train_time = 0.0

        self._model_path = MODELS_DIR / f"lstm_{self.pair}.keras"
        self._tflite_path = MODELS_DIR / f"lstm_{self.pair}.tflite"
        self._scaler_path = MODELS_DIR / f"scaler_{self.pair}.joblib"
        self._meta_path = MODELS_DIR / f"meta_{self.pair}.joblib"

    def _build_model(self, n_features: int):
        """Build LSTM model. Smaller architecture when Pi mode is active."""
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import LSTM, Dense, Dropout, BatchNormalization

        if self.pi.enabled:
            model = Sequential([
                LSTM(32, return_sequences=False,
                     input_shape=(self.sequence_length, n_features)),
                Dropout(0.2),
                Dense(8, activation="relu"),
                Dense(3, activation="softmax"),
            ])
        else:
            model = Sequential([
                LSTM(64, return_sequences=True,
                     input_shape=(self.sequence_length, n_features)),
                Dropout(0.2),
                BatchNormalization(),
                LSTM(32, return_sequences=False),
                Dropout(0.2),
                BatchNormalization(),
                Dense(16, activation="relu"),
                Dropout(0.1),
                Dense(3, activation="softmax"),
            ])

        model.compile(
            optimizer="adam",
            loss="sparse_categorical_crossentropy",
            metrics=["accuracy"],
        )
        return model

    def _prepare_sequences(self, features: np.ndarray,
                            labels: np.ndarray | None = None):
        n_samples = len(features) - self.sequence_length
        n_features = features.shape[1]
        X = np.empty((n_samples, self.sequence_length, n_features),
                      dtype=_FLOAT_DTYPE)
        y = np.empty(n_samples, dtype=np.int32) if labels is not None else None

        for i in range(n_samples):
            X[i] = features[i:i + self.sequence_length]
            if labels is not None:
                y[i] = labels[i + self.sequence_length]

        return X, y

    def _convert_to_tflite(self):
        """Convert Keras model to TFLite with optional quantization."""
        if self.model is None:
            return

        import tensorflow as tf

        converter = tf.lite.TFLiteConverter.from_keras_model(self.model)
        converter.optimizations = [tf.lite.Optimize.DEFAULT]
        converter.target_spec.supported_types = [tf.float16]
        tflite_model = converter.convert()

        with open(self._tflite_path, "wb") as f:
            f.write(tflite_model)

        orig_size = self._model_path.stat().st_size / 1024
        tflite_size = self._tflite_path.stat().st_size / 1024
        logger.info("TFLite converted: %.1f KB → %.1f KB (%.0f%% reduction)",
                     orig_size, tflite_size, (1 - tflite_size / orig_size) * 100)

    def _load_tflite(self) -> bool:
        """Load TFLite model for inference (Pi-optimized)."""
        if not self._tflite_path.exists():
            return False

        try:
            try:
                from tflite_runtime.interpreter import Interpreter
            except ImportError:
                from tensorflow.lite import Interpreter

            self._tflite_interpreter = Interpreter(
                model_path=str(self._tflite_path),
                num_threads=2,
            )
            self._tflite_interpreter.allocate_tensors()
            logger.info("TFLite model loaded: %s (2 threads)", self._tflite_path.name)
            return True
        except Exception as e:
            logger.error("TFLite load failed: %s", e)
            return False

    def _predict_tflite(self, input_data: np.ndarray) -> np.ndarray:
        """Run inference via TFLite interpreter."""
        interp = self._tflite_interpreter
        input_details = interp.get_input_details()
        output_details = interp.get_output_details()

        input_data = input_data.astype(np.float32)

        expected_shape = input_details[0]["shape"]
        if list(input_data.shape) != list(expected_shape):
            interp.resize_tensor_input(input_details[0]["index"], list(input_data.shape))
            interp.allocate_tensors()

        interp.set_tensor(input_details[0]["index"], input_data)
        interp.invoke()
        return interp.get_tensor(output_details[0]["index"])

    def train(self, ohlcv_df: pd.DataFrame) -> dict:
        """Train LSTM. Always uses full TensorFlow (run on desktop, not on Pi)."""
        from sklearn.preprocessing import StandardScaler

        logger.info("Training LSTM for %s on %d candles", self.pair, len(ohlcv_df))

        df = _add_technical_features(ohlcv_df, use_float32=self.pi.numpy_float32)
        labels = _create_labels(df)

        df = df.iloc[:len(labels)]
        df = df[labels.notna()]
        labels = labels[labels.notna()]

        raw_labels = labels.values.astype(int)
        returns = df["returns"].values
        three_class = np.where(returns > 0.005, 2, np.where(returns < -0.005, 0, 1))
        three_class[-len(raw_labels):] = np.where(
            raw_labels == 1,
            np.where(returns[-len(raw_labels):] > 0, 2, 0),
            1,
        )

        exclude = {"timestamp", "open", "high", "low", "close", "volume",
                    "date", "datetime"}
        self.feature_columns = [
            c for c in df.columns
            if c not in exclude and df[c].dtype in [np.float64, np.float32, np.int64]
        ]

        features = df[self.feature_columns].values.astype(_FLOAT_DTYPE)

        self.scaler = StandardScaler()
        features_scaled = self.scaler.fit_transform(features).astype(_FLOAT_DTYPE)

        X, y = self._prepare_sequences(features_scaled, three_class)

        if len(X) < 100:
            logger.warning("Insufficient data: %d sequences", len(X))
            return {"status": "insufficient_data", "sequences": len(X)}

        split = int(len(X) * 0.8)
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        self.model = self._build_model(len(self.feature_columns))

        from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
        epochs = 30 if self.pi.enabled else 50
        batch_size = 16 if self.pi.enabled else 32

        callbacks = [
            EarlyStopping(patience=8, restore_best_weights=True),
            ReduceLROnPlateau(factor=0.5, patience=4, min_lr=1e-6),
        ]

        history = self.model.fit(
            X_train, y_train,
            validation_data=(X_val, y_val),
            epochs=epochs,
            batch_size=batch_size,
            callbacks=callbacks,
            verbose=1,
        )

        val_loss = min(history.history["val_loss"])
        val_acc = max(history.history["val_accuracy"])
        logger.info("Training complete: val_loss=%.4f, val_acc=%.4f", val_loss, val_acc)

        self._save()
        self._convert_to_tflite()
        self.last_train_time = time.time()

        del X_train, X_val, y_train, y_val, features_scaled
        gc.collect()

        return {
            "status": "trained",
            "val_loss": val_loss,
            "val_accuracy": val_acc,
            "train_samples": split,
            "val_samples": len(X) - split,
            "features": len(self.feature_columns),
            "epochs": len(history.history["loss"]),
            "tflite_available": self._tflite_path.exists(),
        }

    def predict(self, ohlcv_df: pd.DataFrame) -> dict | None:
        """Predict range breakout. Uses TFLite on Pi, full TF otherwise."""
        use_tflite = self.pi.enabled and self.pi.use_tflite

        if use_tflite:
            if self._tflite_interpreter is None:
                if not self._load_tflite():
                    if not self._load_full():
                        return None
                    use_tflite = False
        else:
            if self.model is None and not self._load_full():
                return None

        if self.scaler is None and not self._load_meta():
            return None

        try:
            df = _add_technical_features(
                ohlcv_df, use_float32=self.pi.numpy_float32,
            )
            if len(df) < self.sequence_length + 5:
                return None

            features = df[self.feature_columns].values.astype(_FLOAT_DTYPE)
            features_scaled = self.scaler.transform(features).astype(_FLOAT_DTYPE)

            seq = features_scaled[-self.sequence_length:]
            seq = seq.reshape(1, self.sequence_length, -1)

            if use_tflite:
                probabilities = self._predict_tflite(seq)[0]
            else:
                probabilities = self.model.predict(seq, verbose=0)[0]

            bearish_prob, neutral_prob, bullish_prob = probabilities

            confidence = float(max(probabilities))
            if bullish_prob > bearish_prob:
                direction = "bullish"
                label = "Bullish Range Shift"
            elif bearish_prob > bullish_prob:
                direction = "bearish"
                label = "Bearish Range Shift"
            else:
                direction = "neutral"
                label = "Range Continuation"

            current_price = float(df["close"].iloc[-1])
            atr = float((df["high"] - df["low"]).rolling(14).mean().iloc[-1])

            if direction == "bullish":
                predicted_upper = current_price + atr * 2.5
                predicted_lower = current_price - atr * 1.0
            elif direction == "bearish":
                predicted_upper = current_price + atr * 1.0
                predicted_lower = current_price - atr * 2.5
            else:
                predicted_upper = current_price + atr * 1.5
                predicted_lower = current_price - atr * 1.5

            del features, features_scaled, seq
            if self.pi.enabled:
                gc.collect()

            return {
                "direction": direction,
                "confidence": confidence,
                "label": f"LSTM Prediction: {label}",
                "bullish_prob": float(bullish_prob),
                "bearish_prob": float(bearish_prob),
                "neutral_prob": float(neutral_prob),
                "upper": predicted_upper,
                "lower": predicted_lower,
                "timestamp": time.time(),
            }

        except Exception as e:
            logger.error("Prediction failed: %s", e)
            return None

    def _save(self):
        if self.model:
            self.model.save(self._model_path)
        if self.scaler:
            joblib.dump(self.scaler, self._scaler_path)
        joblib.dump({
            "feature_columns": self.feature_columns,
            "sequence_length": self.sequence_length,
        }, self._meta_path)
        logger.info("Model saved to %s", self._model_path)

    def _load_meta(self) -> bool:
        try:
            self.scaler = joblib.load(self._scaler_path)
            meta = joblib.load(self._meta_path)
            self.feature_columns = meta["feature_columns"]
            self.sequence_length = meta["sequence_length"]
            return True
        except Exception as e:
            logger.error("Failed to load scaler/meta: %s", e)
            return False

    def _load_full(self) -> bool:
        if not self._model_path.exists():
            return False
        try:
            from tensorflow.keras.models import load_model
            self.model = load_model(self._model_path)
            if self.scaler is None:
                self._load_meta()
            logger.info("Full TF model loaded: %s", self._model_path)
            return True
        except Exception as e:
            logger.error("Failed to load TF model: %s", e)
            return False

    def needs_retrain(self) -> bool:
        if self.model is None and not self._model_path.exists() and not self._tflite_path.exists():
            return True
        hours_since = (time.time() - self.last_train_time) / 3600
        return hours_since > self.config.retrain_interval_hours
