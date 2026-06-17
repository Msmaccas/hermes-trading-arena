#!/usr/bin/env python3
"""AccuracyScorer — multi-timeframe scoring with Brier decomposition.

Scores predictions across:
  - 7d binary win/loss (did price move in predicted direction?)
  - 30d magnitude return (how much did it move?)
  - Brier score decomposition (calibration + refinement)

Follows Good Judgment Project methodology for Brier score decomposition.
"""

import datetime
import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import yfinance as yf

logger = logging.getLogger("arena.accuracy.scorer")


class AccuracyScorer:
    """Score predictions against actual price action at multiple timeframes.

    For each prediction:
      1. Fetch actual price data for 7d and 30d windows
      2. Compute binary win/loss (7d)
      3. Compute magnitude return (30d)
      4. Compute Brier score (calibration + refinement decomposition)
    """

    def __init__(self):
        self._price_cache: Dict[str, np.ndarray] = {}

    # ─── Price fetching with cache ─────────────────────────────────────────

    def _fetch_prices(self, ticker: str, days_back: int = 60) -> Optional[np.ndarray]:
        """Fetch daily close prices for a ticker (cached)."""
        cache_key = f"{ticker}_{days_back}"
        if cache_key in self._price_cache:
            return self._price_cache[cache_key]

        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=f"{days_back + 30}d")
            if hist.empty:
                logger.warning("No price data for %s", ticker)
                return None

            closes = hist["Close"].to_numpy(dtype=np.float64)
            dates = hist.index.to_numpy(dtype="datetime64[D]")
            self._price_cache[cache_key] = np.column_stack([dates, closes])
            return self._price_cache[cache_key]
        except Exception as e:
            logger.error("Failed to fetch prices for %s: %s", ticker, e)
            return None

    # ─── Single prediction scoring ─────────────────────────────────────────

    def score_prediction(
        self,
        ticker: str,
        direction: str,
        entry_min: Optional[float],
        entry_max: Optional[float],
        target: Optional[float],
        stop: Optional[float],
        confidence: float,
        run_date: str,  # ISO date when prediction was made
    ) -> Dict:
        """Score a single prediction at 7d and 30d timeframes.

        Returns dict with keys:
          success: bool (whether we could fetch price data)
          entry_price, exit_price_7d, exit_price_30d
          result_7d: 'win'|'loss'|'neutral'
          result_30d: 'win'|'loss'|'neutral'
          return_7d, return_30d: float percentages
          brier_7d, brier_30d: float scores (0=perfect, 1=worst)
          max_price, min_price_30d: float
        """
        prices = self._fetch_prices(ticker, days_back=60)
        if prices is None:
            return {"success": False, "error": f"No price data for {ticker}"}

        # Find prediction date in price array
        run_dt = np.datetime64(run_date)
        idx_run = None
        for i in range(len(prices)):
            if prices[i, 0] >= run_dt:
                idx_run = i
                break
        if idx_run is None:
            return {"success": False, "error": f"Run date {run_date} not in price data"}

        entry = self._resolve_entry_price(
            prices, idx_run, entry_min, entry_max
        )

        # Determine price indices
        idx_7d = min(idx_run + 7, len(prices) - 1)
        idx_30d = min(idx_run + 30, len(prices) - 1)

        if idx_7d <= idx_run or idx_30d <= idx_run:
            return {"success": False, "error": "Insufficient price history after run date"}

        price_7d = float(prices[idx_7d, 1])
        price_30d = float(prices[idx_30d, 1])

        # Min/max over 30d window
        prices_30d_window = prices[idx_run : idx_30d + 1, 1]
        max_price_30d = float(np.max(prices_30d_window))
        min_price_30d = float(np.min(prices_30d_window))

        # Score 7d binary win/loss
        result_7d, return_7d = self._compute_binary_result(
            entry, price_7d, direction
        )

        # Score 30d magnitude
        result_30d, return_30d = self._compute_binary_result(
            entry, price_30d, direction
        )

        # Brier scores
        brier_7d = self._compute_brier(
            confidence, result_7d == "win"
        )
        brier_30d = self._compute_brier(
            confidence, result_30d == "win"
        )

        return {
            "success": True,
            "entry_price": round(entry, 2),
            "exit_price_7d": round(price_7d, 2),
            "exit_price_30d": round(price_30d, 2),
            "result_7d": result_7d,
            "result_30d": result_30d,
            "return_7d": round(return_7d, 4),
            "return_30d": round(return_30d, 4),
            "max_price_30d": round(max_price_30d, 2),
            "min_price_30d": round(min_price_30d, 2),
            "brier_7d": round(brier_7d, 4),
            "brier_30d": round(brier_30d, 4),
        }

    def _resolve_entry_price(
        self,
        prices: np.ndarray,
        idx_run: int,
        entry_min: Optional[float],
        entry_max: Optional[float],
    ) -> float:
        """Resolve the actual entry price.

        If entry range is specified, use the close price at the run date
        (within the range if possible). Otherwise use run-date close.
        """
        price_at_run = float(prices[idx_run, 1])

        if entry_min is not None and entry_max is not None:
            if entry_min <= price_at_run <= entry_max:
                return price_at_run
            # If price is outside range, use the closer bound
            if price_at_run < entry_min:
                return entry_min
            return entry_max

        return price_at_run

    def _compute_binary_result(
        self, entry: float, exit_price: float, direction: str
    ) -> Tuple[str, float]:
        """Compute binary win/loss and return percentage.

        For bullish: win if exit > entry
        For bearish: win if exit < entry
        For neutral: always 'neutral' with 0 return
        """
        if entry == 0:
            return "neutral", 0.0

        return_pct = (exit_price / entry - 1.0)

        if direction == "bullish":
            if exit_price >= entry:
                return "win", return_pct
            else:
                return "loss", return_pct
        elif direction == "bearish":
            if exit_price <= entry:
                return "win", -return_pct  # positive return for correct bearish
            else:
                return "loss", -return_pct
        else:  # neutral
            return "neutral", 0.0

    def _compute_brier(self, confidence: float, correct: bool) -> float:
        """Compute Brier score for a single prediction.

        Brier = (forecast - outcome)^2
        forecast = confidence (0-1)
        outcome = 1 if correct, 0 if incorrect

        Lower is better. Perfect = 0, worst = 1.
        """
        outcome = 1.0 if correct else 0.0
        return (confidence - outcome) ** 2

    # ─── Batch scoring ─────────────────────────────────────────────────────

    def score_predictions(
        self, predictions: List[Dict]
    ) -> List[Dict]:
        """Score a batch of predictions.

        predictions: list of dicts with keys:
          ticker, direction, entry_min, entry_max, target, stop,
          confidence, run_date, persona

        Returns list of scored results.
        """
        results = []
        for pred in predictions:
            score = self.score_prediction(
                ticker=pred["ticker"],
                direction=pred["direction"],
                entry_min=pred.get("entry_min"),
                entry_max=pred.get("entry_max"),
                target=pred.get("target"),
                stop=pred.get("stop"),
                confidence=pred.get("confidence", 0.5),
                run_date=pred.get("run_date", datetime.date.today().isoformat()),
            )
            score["persona"] = pred.get("persona", "?")
            score["ticker"] = pred["ticker"]
            score["pred_id"] = pred.get("id")
            results.append(score)

        return results

    # ─── Aggregated metrics ────────────────────────────────────────────────

    def aggregate_scores(self, scores: List[Dict]) -> Dict:
        """Compute aggregate performance metrics.

        Follows Good Judgment Project methodology:
          - Mean Brier score
          - Brier decomposition: Calibration + Refinement
          - Win rate
          - Average return
        """
        if not scores:
            return {
                "n": 0,
                "win_rate_7d": 0.0,
                "avg_return_30d": 0.0,
                "mean_brier_7d": 0.5,
                "mean_brier_30d": 0.5,
                "brier_decomposition": {},
            }

        valid_7d = [s for s in scores if s.get("success") and s.get("result_7d") != "neutral"]
        valid_30d = [s for s in scores if s.get("success") and s.get("result_30d") != "neutral"]

        n_7d = len(valid_7d)
        n_30d = len(valid_30d)

        # Win rates
        wins_7d = sum(1 for s in valid_7d if s["result_7d"] == "win")
        wins_30d = sum(1 for s in valid_30d if s["result_30d"] == "win")

        win_rate_7d = wins_7d / n_7d if n_7d > 0 else 0.0
        win_rate_30d = wins_30d / n_30d if n_30d > 0 else 0.0

        # Average returns
        returns_30d = [s.get("return_30d", 0.0) for s in valid_30d]
        avg_return_30d = np.mean(returns_30d) if returns_30d else 0.0

        # Mean Brier scores
        brier_7d = [s.get("brier_7d", 0.5) for s in scores if s.get("success")]
        brier_30d = [s.get("brier_30d", 0.5) for s in scores if s.get("success")]

        mean_brier_7d = np.mean(brier_7d) if brier_7d else 0.5
        mean_brier_30d = np.mean(brier_30d) if brier_30d else 0.5

        # Brier score decomposition (Good Judgment Project)
        # Calibration: how well probabilities match outcomes
        # Refinement: how sharp/distinguishable the forecasts are
        brier_decomp = self._brier_decomposition(scores)

        # Sharpe-like ratio for returns
        sharpe_30d = (
            np.mean(returns_30d) / (np.std(returns_30d) + 1e-8)
            if len(returns_30d) > 1 else 0.0
        )

        return {
            "n": len(scores),
            "n_7d": n_7d,
            "n_30d": n_30d,
            "wins_7d": wins_7d,
            "losses_7d": n_7d - wins_7d,
            "win_rate_7d": round(win_rate_7d, 4),
            "win_rate_30d": round(win_rate_30d, 4),
            "avg_return_30d": round(float(avg_return_30d), 4),
            "sharpe_30d": round(float(sharpe_30d), 4),
            "mean_brier_7d": round(float(mean_brier_7d), 4),
            "mean_brier_30d": round(float(mean_brier_30d), 4),
            "brier_decomposition": brier_decomp,
        }

    def _brier_decomposition(self, scores: List[Dict]) -> Dict:
        """Decompose Brier score into Calibration + Refinement + Uncertainty.

        Brier = CAL + REF - UNC  (or equivalently: Reliability - Resolution + Uncertainty)
        """
        valid = [s for s in scores if s.get("success") and s.get("direction") != "neutral"]
        if len(valid) < 3:
            return {"calibration": 0.0, "refinement": 0.0, "uncertainty": 0.0}

        confidences = np.array([s.get("brier_7d", 0.5) for s in valid])
        outcomes = np.array([
            1.0 if s.get("result_7d") == "win" else 0.0 for s in valid
        ])
        forecasts = np.array([
            s.get("confidence", 0.5) for s in valid
        ])

        # Base rate (uncertainty)
        base_rate = np.mean(outcomes)
        uncertainty = base_rate * (1.0 - base_rate)

        # Calibration (reliability): bin by forecast probability
        n_bins = min(5, len(valid) // 2)
        if n_bins < 2:
            return {"calibration": 0.0, "refinement": 0.0, "uncertainty": float(uncertainty)}

        bins = np.linspace(0, 1, n_bins + 1)
        bin_indices = np.digitize(forecasts, bins) - 1
        bin_indices = np.clip(bin_indices, 0, n_bins - 1)

        calibration = 0.0
        refinement = 0.0

        for b in range(n_bins):
            mask = bin_indices == b
            n_bin = mask.sum()
            if n_bin > 0:
                p_bar = np.mean(forecasts[mask])
                o_bar = np.mean(outcomes[mask])
                weight = n_bin / len(valid)
                calibration += weight * (p_bar - o_bar) ** 2
                refinement += weight * (o_bar - base_rate) ** 2

        return {
            "calibration": round(float(calibration), 4),
            "refinement": round(float(refinement), 4),
            "uncertainty": round(float(uncertainty), 4),
        }
