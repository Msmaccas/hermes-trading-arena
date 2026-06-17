#!/usr/bin/env python3
"""RegimeClassifier — 4-state HMM on multi-index returns + volatility + ADX.

Fetches SPY, QQQ, ^VIX, ^HSI, 000001.SS, ^N225 via yfinance.
Computes 4-state HMM (hmmlearn) on return + volatility features.
ADX(14) used for trend-strength calibration.
Classifies into: TrendingBull, ChoppyRange, Correction, VBottom.
Returns probability distribution over states.
"""

import datetime
import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import yfinance as yf

logger = logging.getLogger("arena.regime")

# ─── Constants ───────────────────────────────────────────────────────────────

INDEX_SYMBOLS = [
    "SPY",       # US large cap
    "QQQ",       # US tech / Nasdaq
    "^VIX",      # Volatility index
    "^HSI",      # Hang Seng
    "000001.SS", # Shanghai Composite
    "^N225",     # Nikkei 225
]

REGIME_NAMES = ["TrendingBull", "ChoppyRange", "Correction", "VBottom"]
N_STATES = 4
LOOKBACK_DAYS = 504  # ~2 trading years
ADX_PERIOD = 14

# ─── Data fetching ──────────────────────────────────────────────────────────


def fetch_index_data(
    symbols: Optional[List[str]] = None,
    period: str = "2y",
) -> Dict[str, np.ndarray]:
    """Fetch daily OHLCV for each index symbol via yfinance.

    Returns dict[symbol] -> numpy structured array with fields:
        date, open, high, low, close, volume
    """
    if symbols is None:
        symbols = INDEX_SYMBOLS
    result = {}
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period=period)
            if hist.empty:
                logger.warning("No data for %s", sym)
                continue
            # Build structured array
            dates = hist.index.to_numpy(dtype="datetime64[D]")
            opens = hist["Open"].to_numpy(dtype=np.float64)
            highs = hist["High"].to_numpy(dtype=np.float64)
            lows = hist["Low"].to_numpy(dtype=np.float64)
            closes = hist["Close"].to_numpy(dtype=np.float64)
            volumes = hist["Volume"].to_numpy(dtype=np.float64)

            arr = np.empty(len(closes), dtype=[
                ("date", "datetime64[D]"),
                ("open", "f8"),
                ("high", "f8"),
                ("low", "f8"),
                ("close", "f8"),
                ("volume", "f8"),
            ])
            arr["date"] = dates
            arr["open"] = opens
            arr["high"] = highs
            arr["low"] = lows
            arr["close"] = closes
            arr["volume"] = volumes
            result[sym] = arr
            logger.info("Fetched %d bars for %s", len(arr), sym)
        except Exception as e:
            logger.error("Failed to fetch %s: %s", sym, e)
    return result


# ─── ADX computation ───────────────────────────────────────────────────────


def compute_adx(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = ADX_PERIOD,
) -> np.ndarray:
    """Compute Average Directional Index (ADX) for trend strength.

    Returns array of ADX values (same length as input; first ``period`` entries are NaN).
    ADX > 25 = trending, ADX < 20 = ranging.
    """
    n = len(close)
    adx = np.full(n, np.nan)
    if n < period + 1:
        return adx

    # True Range
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(
        high - low,
        np.maximum(
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ),
    )

    # Directional movement
    up_move = np.diff(high, prepend=high[0])
    down_move = np.diff(low, prepend=low[0])
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Smoothed averages (Wilder's method, alpha = 1/period)
    alpha = 1.0 / period
    tr_smooth = np.full(n, np.nan)
    plus_smooth = np.full(n, np.nan)
    minus_smooth = np.full(n, np.nan)

    tr_smooth[period] = np.mean(tr[1 : period + 1])
    plus_smooth[period] = np.mean(plus_dm[1 : period + 1])
    minus_smooth[period] = np.mean(minus_dm[1 : period + 1])

    for i in range(period + 1, n):
        tr_smooth[i] = tr_smooth[i - 1] + alpha * (tr[i] - tr_smooth[i - 1])
        plus_smooth[i] = plus_smooth[i - 1] + alpha * (plus_dm[i] - plus_smooth[i - 1])
        minus_smooth[i] = minus_smooth[i - 1] + alpha * (minus_dm[i] - minus_smooth[i - 1])

    # Directional indicators
    plus_di = np.full(n, np.nan)
    minus_di = np.full(n, np.nan)
    dx = np.full(n, np.nan)

    for i in range(period + 1, n):
        if tr_smooth[i] > 0:
            plus_di[i] = 100.0 * plus_smooth[i] / tr_smooth[i]
            minus_di[i] = 100.0 * minus_smooth[i] / tr_smooth[i]
            di_sum = plus_di[i] + minus_di[i]
            if di_sum > 0:
                dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / di_sum

    # ADX = smoothed DX
    first_valid = period + 1
    if first_valid + period < n:
        adx[first_valid + period] = np.nanmean(dx[first_valid : first_valid + period])
        for i in range(first_valid + period + 1, n):
            if not np.isnan(dx[i]):
                adx[i] = adx[i - 1] + alpha * (dx[i] - adx[i - 1])

    return adx


# ─── Feature engineering ────────────────────────────────────────────────────


def compute_features(
    index_data: Dict[str, np.ndarray],
) -> np.ndarray:
    """Build feature matrix from multi-index data for HMM input.

    Features (per day after alignment):
      1. SPY daily log-return
      2. SPY 21-day rolling volatility (std of returns)
      3. QQQ relative strength vs SPY (QQQ return - SPY return)
      4. VIX level (normalised)
      5. HSI daily log-return (or 0 if missing)
      6. 000001.SS daily log-return (or 0 if missing)
      7. ^N225 daily log-return (or 0 if missing)
      8. SPY ADX(14) value (or 0 if NaN)

    Returns (n_days, 8) float64 array. Rows with any NaN (first ~21 days) are dropped.
    """
    spy = index_data.get("SPY")
    if spy is None:
        logger.error("SPY data required for regime classification")
        return np.empty((0, 8))

    closes_spy = spy["close"].astype(np.float64)
    dates = spy["date"]
    n = len(closes_spy)

    # 1. Log returns
    log_ret = np.full(n, np.nan)
    log_ret[1:] = np.log(closes_spy[1:] / closes_spy[:-1])
    log_ret[0] = 0.0

    # 2. Rolling 21-day volatility
    vol21 = np.full(n, np.nan)
    for i in range(21, n):
        vol21[i] = np.std(log_ret[i - 20 : i + 1]) * np.sqrt(252)

    # 3. QQQ relative strength
    qqq = index_data.get("QQQ")
    qqq_rel = np.zeros(n, dtype=np.float64)
    if qqq is not None:
        qqq_close = qqq["close"].astype(np.float64)
        # Align by date — find overlapping dates
        qqq_dates = qqq["date"]
        qqq_ret = np.full(n, np.nan)
        for i in range(n):
            mask = qqq_dates == dates[i]
            if mask.any():
                j = np.where(mask)[0][0]
                if j > 0:
                    qqq_ret[i] = np.log(qqq_close[j] / qqq_close[j - 1])
                else:
                    qqq_ret[i] = 0.0
        qqq_rel = np.where(np.isnan(qqq_ret), 0.0, qqq_ret) - log_ret

    # 4. VIX level (normalised by Z-score over lookback)
    vix = index_data.get("^VIX")
    vix_norm = np.zeros(n, dtype=np.float64)
    if vix is not None:
        vix_close = vix["close"].astype(np.float64)
        vix_dates = vix["date"]
        vix_aligned = np.full(n, np.nan)
        for i in range(n):
            mask = vix_dates == dates[i]
            if mask.any():
                vix_aligned[i] = vix_close[np.where(mask)[0][0]]
        # Rolling Z-score (63-day window for VIX)
        for i in range(63, n):
            window = vix_aligned[i - 63 : i]
            if np.nanstd(window) > 0:
                vix_norm[i] = (vix_aligned[i] - np.nanmean(window)) / np.nanstd(window)
            else:
                vix_norm[i] = 0.0

    # 5-7. International indices
    def _align_returns(sym: str) -> np.ndarray:
        data = index_data.get(sym)
        if data is None:
            return np.zeros(n, dtype=np.float64)
        d_close = data["close"].astype(np.float64)
        d_dates = data["date"]
        ret = np.zeros(n, dtype=np.float64)
        for i in range(n):
            mask = d_dates == dates[i]
            if mask.any():
                j = np.where(mask)[0][0]
                if j > 0:
                    ret[i] = np.log(d_close[j] / d_close[j - 1])
        return ret

    hsi_ret = _align_returns("^HSI")
    sh_ret = _align_returns("000001.SS")
    nk_ret = _align_returns("^N225")

    # 8. ADX
    spy_adx = np.zeros(n, dtype=np.float64)
    adx_vals = compute_adx(
        spy["high"].astype(np.float64),
        spy["low"].astype(np.float64),
        closes_spy,
    )
    spy_adx[:] = np.where(np.isnan(adx_vals), 0.0, adx_vals)

    # Assemble feature matrix
    features = np.column_stack([
        log_ret,
        vol21,
        qqq_rel,
        vix_norm,
        hsi_ret,
        sh_ret,
        nk_ret,
        spy_adx,
    ])

    # Drop leading NaN rows
    valid = ~np.any(np.isnan(features), axis=1)
    features = features[valid]

    logger.info("Feature matrix: %d rows x %d cols", features.shape[0], features.shape[1])
    return features


# ─── Regime Classifier ─────────────────────────────────────────────────────


class RegimeClassifier:
    """4-state HMM market regime classifier with ADX trend-strength calibration.

    States (hidden):
        0 → TrendingBull
        1 → ChoppyRange
        2 → Correction
        3 → VBottom
    """

    def __init__(self, n_states: int = N_STATES):
        self.n_states = n_states
        self.model = None
        self.is_fitted = False
        self._feature_mean = None
        self._feature_std = None

    def fit(self, features: np.ndarray) -> "RegimeClassifier":
        """Fit the HMM on feature matrix."""
        from hmmlearn import hmm

        # Standardise features (important for HMM convergence)
        self._feature_mean = np.nanmean(features, axis=0)
        self._feature_std = np.nanstd(features, axis=0)
        self._feature_std[self._feature_std == 0] = 1.0
        X = (features - self._feature_mean) / self._feature_std

        # GaussianHMM with diagonal covariance
        self.model = hmm.GaussianHMM(
            n_components=self.n_states,
            covariance_type="diag",
            n_iter=200,
            tol=1e-4,
            random_state=42,
        )
        self.model.fit(X)
        self.is_fitted = True

        # Map HMM states to regime labels based on SPY return characteristics
        self._label_states(features)

        logger.info("RegimeClassifier fitted — %d states", self.n_states)
        return self

    def _label_states(self, features: np.ndarray) -> None:
        """Map HMM state indices to regime names by analysing hidden state means.

        Heuristic (on standardised features):
          - High mean return + moderate vol → TrendingBull
          - Low/negative mean return + high vol → Correction
          - Very negative return + extreme vol → VBottom
          - Everything else → ChoppyRange
        """
        if self.model is None:
            return

        means = self.model.means_  # (n_states, n_features)

        # Feature index: 0=log_ret, 1=vol21, 2=qqq_rel, 3=vix_norm
        ret_dim = 0
        vol_dim = 1

        # Score by return and volatility
        scores = []
        for s in range(self.n_states):
            mu_ret = means[s, ret_dim]
            mu_vol = means[s, vol_dim]
            scores.append((s, mu_ret, mu_vol))

        # Sort by return (descending)
        scores.sort(key=lambda x: x[1], reverse=True)

        # Assign labels
        state_labels = {}
        if self.n_states >= 4:
            state_labels[scores[0][0]] = "TrendingBull"  # highest return
            state_labels[scores[-1][0]] = "Correction" if scores[-1][2] < scores[-2][2] else "VBottom"
            # Among middle states, higher vol + lower return = VBottom
            mid = scores[1:-1]
            mid_sorted = sorted(mid, key=lambda x: x[1] - x[2], reverse=False)
            if len(mid_sorted) >= 2:
                state_labels[mid_sorted[0][0]] = "VBottom"
                state_labels[mid_sorted[1][0]] = "ChoppyRange"
            elif len(mid_sorted) == 1:
                state_labels[mid_sorted[0][0]] = "ChoppyRange"
        else:
            for i, (s, _, _) in enumerate(scores):
                state_labels[s] = REGIME_NAMES[i] if i < len(REGIME_NAMES) else f"State{s}"

        self.state_to_regime = state_labels
        self.regime_to_state = {v: k for k, v in state_labels.items()}

    def predict(
        self, features: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Predict hidden states and log-probabilities.

        Returns (states, log_probs) where states[i] = state index.
        """
        if not self.is_fitted or self.model is None:
            raise RuntimeError("Classifier not fitted — call .fit() first")

        X = (features - self._feature_mean) / self._feature_std
        states = self.model.predict(X)
        log_probs = self.model.score(X)
        return states, log_probs

    def predict_proba(
        self, features: np.ndarray
    ) -> np.ndarray:
        """Predict posterior probability distribution over states.

        Returns (n_days, n_states) array.
        """
        if not self.is_fitted or self.model is None:
            raise RuntimeError("Classifier not fitted — call .fit() first")

        X = (features - self._feature_mean) / self._feature_std
        _, posteriors = self.model.decode(X, algorithm="viterbi")
        # Decode returns log-prob; use predict_proba via forward-backward
        fb = self.model.predict_proba(X)
        return fb

    def current_regime_probs(
        self, features: np.ndarray
    ) -> Dict[str, float]:
        """Return probability distribution over regime names for the most recent day.

        Also incorporates ADX to boost TrendingBull/Correction confidence
        when ADX > 25 (trending) and reduce when ADX < 20 (ranging).
        """
        probas = self.predict_proba(features)
        last_probas = probas[-1]

        # Regime probabilities
        regimes_probs = {}
        for s in range(self.n_states):
            regime = self.state_to_regime.get(s, f"State{s}")
            regimes_probs[regime] = float(last_probas[s])

        # ADX calibration: if last day's ADX > 25, boost trending regimes
        adx_val = features[-1, 7]  # ADX feature index
        if adx_val > 25:
            for regime in ["TrendingBull", "Correction"]:
                if regime in regimes_probs:
                    regimes_probs[regime] *= (1.0 + (adx_val - 25.0) / 100.0)
        elif adx_val < 20:
            for regime in ["ChoppyRange"]:
                if regime in regimes_probs:
                    regimes_probs[regime] *= (1.0 + (20.0 - adx_val) / 100.0)

        # Normalise to sum=1
        total = sum(regimes_probs.values())
        if total > 0:
            regimes_probs = {k: v / total for k, v in regimes_probs.items()}

        # Sort by probability descending
        regimes_probs = dict(
            sorted(regimes_probs.items(), key=lambda x: x[1], reverse=True)
        )
        return regimes_probs

    def dominant_regime(self, features: np.ndarray) -> Tuple[str, float]:
        """Return the most likely regime and its probability.

        Returns (regime_name, probability).
        """
        probs = self.current_regime_probs(features)
        top = list(probs.items())[0]
        return top


# ─── High-level convenience functions ───────────────────────────────────────


def classify_regime(
    lookback_days: int = LOOKBACK_DAYS,
) -> Dict:
    """One-shot: fetch data, fit HMM, return current regime classification.

    Returns dict with regime probabilities, dominant regime, ADX value,
    and feature metadata.
    """
    index_data = fetch_index_data(period="2y")
    if "SPY" not in index_data:
        return {"error": "Failed to fetch SPY data", "regime": "Unknown", "confidence": 0.0}

    features = compute_features(index_data)
    if features.shape[0] < 60:
        return {"error": f"Insufficient data: {features.shape[0]} rows", "regime": "Unknown", "confidence": 0.0}

    classifier = RegimeClassifier(n_states=N_STATES)
    classifier.fit(features)

    regime_probs = classifier.current_regime_probs(features)
    dominant, confidence = classifier.dominant_regime(features)

    # Latest ADX value
    adx_val = float(features[-1, 7])

    return {
        "regime": dominant,
        "confidence": round(confidence, 4),
        "probabilities": {k: round(v, 4) for k, v in regime_probs.items()},
        "adx": round(adx_val, 2),
        "n_features": features.shape[0],
        "n_indices": len(index_data),
        "indices_fetched": list(index_data.keys()),
    }


def get_current_regime() -> Dict:
    """Shortcut: classify current market regime with default settings."""
    return classify_regime()
