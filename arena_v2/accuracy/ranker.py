#!/usr/bin/env python3
"""PersonaRanker — Bayesian ranking with regime adjustment and elimination.

Implements:
  - Bayesian-adjusted scores (Beta-Binomial model)
  - Regime-adjusted scores (weight outcomes by regime difficulty)
  - Sharpe-relative-to-others ranking (Numerai style)
  - Elimination with diversification constraints
  - Statistical significance floor (p < 0.10 binomial test)

Persona categories for diversification:
  - Momentum: oneil, minervini, qullamaggie, david-ryan, dan-zanger
  - Value: buffet, lynch, nick-schmidt
  - Trend: brian-shannon, matt-caruso
"""

import datetime
import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import binomtest

from .db import AccuracyDB

logger = logging.getLogger("arena.accuracy.ranker")

# ─── Persona categorisation ─────────────────────────────────────────────────

MOMENTUM_PERSONAS = {"oneil", "minervini", "qullamaggie", "david-ryan", "dan-zanger"}
VALUE_PERSONAS = {"buffet", "lynch", "nick-schmidt"}
TREND_PERSONAS = {"brian-shannon", "matt-caruso"}

ALL_TRADING_PERSONAS = MOMENTUM_PERSONAS | VALUE_PERSONAS | TREND_PERSONAS

# ─── Regime difficulty weights ──────────────────────────────────────────────

# Regime difficulty: how hard is it to make correct predictions in each regime?
# Higher weight = predictions made in this regime are more impressive.
# Based on historical analysis: TrendingBull is easiest, VBottom is hardest.
REGIME_DIFFICULTY_WEIGHTS = {
    "TrendingBull": 0.8,    # Easy — rising tide lifts all boats
    "ChoppyRange": 1.0,     # Baseline — mixed signals
    "Correction": 1.3,      # Hard — negative bias to overcome
    "VBottom": 1.5,         # Hardest — extreme volatility, quick reversals
    "Unknown": 1.0,
}


class PersonaRanker:
    """Bayesian ranking system for trading personas.

    Combines:
      1. Win rate (Beta-Binomial Bayesian adjustment)
      2. Return magnitude (Sharpe-relative-to-others)
      3. Brier score (calibration quality)
      4. Regime difficulty weighting
      5. Regime-conditional performance
    """

    def __init__(self, db: Optional[AccuracyDB] = None):
        self.db = db or AccuracyDB()

    # ─── Bayesian win rate ─────────────────────────────────────────────────

    def bayesian_win_rate(
        self, wins: int, losses: int, prior_a: float = 1.0, prior_b: float = 1.0
    ) -> Tuple[float, float, float]:
        """Compute Bayesian-adjusted win rate using Beta-Binomial model.

        prior_a, prior_b: Beta prior parameters (default: Beta(1,1) = uniform)
        Returns: (posterior_mean, posterior_a, posterior_b)

        The posterior mean = (wins + prior_a) / (wins + losses + prior_a + prior_b)
        This pulls low-sample estimates toward the prior (shrinkage).
        """
        posterior_a = wins + prior_a
        posterior_b = losses + prior_b
        posterior_mean = posterior_a / (posterior_a + posterior_b)
        return posterior_mean, posterior_a, posterior_b

    # ─── Sharpe-relative-to-others (Numerai style) ────────────────────────

    def sharpe_relative(
        self, persona_returns: List[float], all_returns: List[float]
    ) -> float:
        """Compute Sharpe ratio of persona returns relative to all personas.

        Sharpe_relative = (mean_persona - mean_all) / std_all

        This is Numerai's ranking metric: how much better (or worse) is
        this persona compared to the average, normalised by cross-sectional std.
        """
        all_arr = np.array(all_returns, dtype=np.float64)
        persona_arr = np.array(persona_returns, dtype=np.float64)

        mean_all = np.mean(all_arr)
        std_all = np.std(all_arr)

        if std_all < 1e-8:
            return 0.0

        mean_persona = np.mean(persona_arr)
        return (mean_persona - mean_all) / std_all

    # ─── Regime-adjusted score ─────────────────────────────────────────────

    def regime_adjusted_score(
        self,
        wins: int,
        losses: int,
        avg_return: float,
        avg_brier: float,
        regime_counts: Dict[str, int],  # regime -> number of picks in that regime
        total_picks: int,
    ) -> float:
        """Compute regime-adjusted score.

        Weights each pick by the regime difficulty multiplier.
        Higher score = better performance adjusted for market conditions.
        """
        if total_picks == 0:
            return 0.0

        # Win rate component
        win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0.0

        # Return component (normalised by volatility)
        return_component = avg_return  # already a percentage

        # Brier component (lower is better, so 1 - brier)
        brier_component = 1.0 - avg_brier

        # Regime difficulty factor
        if regime_counts:
            weighted_difficulty = sum(
                regime_counts.get(regime, 0) * REGIME_DIFFICULTY_WEIGHTS.get(regime, 1.0)
                for regime in regime_counts
            ) / total_picks
        else:
            weighted_difficulty = 1.0

        # Composite: higher win rate, higher returns, better calibration => higher score
        # Weighted by regime difficulty
        raw_score = (
            0.4 * win_rate +
            0.3 * max(0, return_component) +  # clip negative returns
            0.3 * brier_component
        ) * weighted_difficulty

        return round(raw_score, 4)

    # ─── Binomial test significance ────────────────────────────────────────

    def is_statistically_significant(
        self, wins: int, total: int, p_threshold: float = 0.10
    ) -> bool:
        """Check if win rate is statistically significant using binomial test.

        H0: true win rate = 0.5 (random guessing)
        H1: true win rate > 0.5 (skill)

        Returns True if p < p_threshold (significant skill).
        Lower confidence floor meant to avoid eliminating good performers
        with small samples.
        """
        if total < 3:
            return False  # Too few samples

        # One-sided binomial test: probability of >= wins successes
        # under null of p=0.5
        p_value = binomtest(wins, n=total, p=0.5, alternative="greater").pvalue
        return p_value < p_threshold

    # ─── Rank personas ─────────────────────────────────────────────────────

    def compute_rankings(self, run_date: Optional[str] = None) -> List[Dict]:
        """Compute Bayesian rankings for all active personas.

        Fetches all scored outcomes from DB, aggregates per persona,
        computes Bayesian-adjusted scores, and returns sorted rankings.
        """
        if run_date is None:
            run_date = datetime.date.today().isoformat()

        db = self.db
        outcomes_7d = db.get_outcomes(timeframe="7d")
        outcomes_30d = db.get_outcomes(timeframe="30d")

        # Aggregate per persona
        persona_stats = {}
        for outcome in outcomes_7d:
            p = outcome["persona"]
            if p not in persona_stats:
                persona_stats[p] = {
                    "wins_7d": 0, "losses_7d": 0,
                    "returns_30d": [], "briers_7d": [],
                    "regime_counts": {},
                    "total_7d": 0,
                }
            stats = persona_stats[p]
            result = outcome["result"]
            if result == "win":
                stats["wins_7d"] += 1
            elif result == "loss":
                stats["losses_7d"] += 1
            stats["total_7d"] += 1
            stats["briers_7d"].append(outcome.get("brier_score", 0.5))

        for outcome in outcomes_30d:
            p = outcome["persona"]
            if p in persona_stats:
                persona_stats[p]["returns_30d"].append(outcome.get("return_pct", 0.0))

        # Regime data
        regime_data = {}
        conn = db.connect()
        rows = conn.execute(
            """SELECT p.persona, r.regime
               FROM accuracy_v2_regime_snapshots r
               JOIN accuracy_v2_predictions p ON r.pred_id = p.id"""
        ).fetchall()
        for row in rows:
            p = row["persona"]
            regime = row["regime"]
            if p in persona_stats:
                persona_stats[p]["regime_counts"][regime] = \
                    persona_stats[p]["regime_counts"].get(regime, 0) + 1

        # Compute rankings
        rankings = []
        all_returns_30d = [
            r for stats in persona_stats.values()
            for r in stats["returns_30d"]
        ]

        for persona, stats in persona_stats.items():
            wins = stats["wins_7d"]
            losses = stats["losses_7d"]
            total_7d = stats["total_7d"]

            # Bayesian win rate
            bayes_wr, _, _ = self.bayesian_win_rate(wins, losses)

            # Average return 30d
            avg_return_30d = np.mean(stats["returns_30d"]) if stats["returns_30d"] else 0.0

            # Average Brier
            avg_brier = np.mean(stats["briers_7d"]) if stats["briers_7d"] else 0.5

            # Sharpe relative
            sharpe_rel = self.sharpe_relative(stats["returns_30d"], all_returns_30d) if stats["returns_30d"] else 0.0

            # Regime-adjusted score
            regime_adj = self.regime_adjusted_score(
                wins, losses, avg_return_30d, avg_brier,
                stats["regime_counts"], total_7d,
            )

            # Combined Bayesian score (weighted combination)
            bayesian_score = round(
                0.35 * bayes_wr +
                0.25 * max(0, float(sharpe_rel) / 5.0 + 0.5) +  # normalise sharpe to 0-1
                0.20 * (1.0 - avg_brier) +
                0.20 * regime_adj,
                4,
            )

            # Statistical significance
            sig = self.is_statistically_significant(wins, total_7d)

            rankings.append({
                "persona": persona,
                "total_picks": total_7d,
                "wins_7d": wins,
                "losses_7d": losses,
                "win_rate_7d": round(wins / total_7d, 4) if total_7d > 0 else 0.0,
                "bayesian_win_rate": round(bayes_wr, 4),
                "avg_return_30d": round(float(avg_return_30d), 4),
                "sharpe_relative": round(float(sharpe_rel), 4),
                "avg_brier": round(float(avg_brier), 4),
                "regime_adj_score": round(float(regime_adj), 4),
                "bayesian_score": bayesian_score,
                "statistically_significant": sig,
                "run_date": run_date,
                "eliminated": 0,
            })

        # Sort by Bayesian score descending
        rankings.sort(key=lambda r: r["bayesian_score"], reverse=True)

        # Assign ranks
        for i, r in enumerate(rankings):
            r["rank"] = i + 1

        return rankings

    # ─── Eliminate worst performers ────────────────────────────────────────

    def run_elimination(
        self,
        rankings: List[Dict],
        force_keep_diversified: bool = True,
    ) -> Tuple[List[Dict], List[str]]:
        """Run elimination round on personas.

        Eliminates the lowest-ranked trading persona(s) that meet ALL criteria:
          1. Not protected by diversification constraint
          2. p >= 0.10 (NOT statistically significant at 90% confidence)
          3. Has enough picks for evaluation (>= 3)

        Returns:
          - Updated rankings with 'eliminated' flag
          - List of eliminated persona names

        Diversification constraint:
          Keep at least 1 momentum, 1 value, and 1 trend persona
          regardless of score.
        """
        eliminated = []

        # First pass: identify protected personas
        protected = set()
        if force_keep_diversified:
            # Within each category, the highest-ranked persona is protected
            for category, personas in [
                ("momentum", MOMENTUM_PERSONAS),
                ("value", VALUE_PERSONAS),
                ("trend", TREND_PERSONAS),
            ]:
                category_rankings = [
                    r for r in rankings
                    if r["persona"] in personas and not r.get("eliminated", 0)
                ]
                for r in sorted(category_rankings, key=lambda x: x["rank"]):
                    protected.add(r["persona"])
                    break  # Just keep top 1 from each category

        # Second pass: find eliminations
        # Sort by rank descending (worst first)
        candidates = sorted(
            [r for r in rankings if r["persona"] in ALL_TRADING_PERSONAS
             and r["persona"] not in protected
             and not r.get("eliminated", 0)],
            key=lambda r: r["rank"],
            reverse=True,
        )

        for candidate in candidates:
            persona = candidate["persona"]
            total = candidate["total_picks"]
            wins = candidate["wins_7d"]

            # Check if eligible for elimination
            if total < 3:
                logger.info("Skipping %s: only %d picks (min 3)", persona, total)
                continue

            # Statistical significance check (p < 0.10 means significant = keep)
            significant = candidate.get("statistically_significant", False)
            if significant:
                logger.info(
                    "Skipping %s: statistically significant (p < 0.10)",
                    persona
                )
                continue

            # Re-check diversification after hypothetical elimination
            if force_keep_diversified:
                hypothetical_remaining = set(
                    r["persona"] for r in rankings
                    if not r.get("eliminated", 0) and r["persona"] != persona
                )
                # Would we still have 1 momentum, 1 value, 1 trend?
                has_momentum = bool(hypothetical_remaining & MOMENTUM_PERSONAS)
                has_value = bool(hypothetical_remaining & VALUE_PERSONAS)
                has_trend = bool(hypothetical_remaining & TREND_PERSONAS)
                if not (has_momentum and has_value and has_trend):
                    logger.info(
                        "Skipping %s: would violate diversification constraint",
                        persona
                    )
                    continue

            # Eliminate!
            candidate["eliminated"] = 1
            eliminated.append(persona)
            logger.info("ELIMINATED: %s (rank %d, WR=%.2f%%, bayes=%.4f)",
                         persona, candidate["rank"],
                         candidate.get("win_rate_7d", 0) * 100,
                         candidate.get("bayesian_score", 0))

        # Update rankings list
        for r in rankings:
            r["eliminated"] = 1 if r["persona"] in eliminated else r.get("eliminated", 0)

        return rankings, eliminated

    # ─── Save rankings to DB ───────────────────────────────────────────────

    def save_rankings(self, rankings: List[Dict], run_date: str):
        """Save computed rankings to the database."""
        db = self.db
        for r in rankings:
            db.insert_ranking(
                run_date=run_date,
                persona=r["persona"],
                total_picks=r["total_picks"],
                wins_7d=r["wins_7d"],
                losses_7d=r["losses_7d"],
                win_rate_7d=r.get("win_rate_7d", 0.0),
                avg_return_30d=r.get("avg_return_30d", 0.0),
                avg_brier=r.get("avg_brier", 0.5),
                bayesian_score=r.get("bayesian_score", 0.0),
                regime_adj_score=r.get("regime_adj_score", 0.0),
                rank=r.get("rank", 999),
                eliminated=r.get("eliminated", 0),
            )
        logger.info("Saved %d rankings for %s", len(rankings), run_date)


# ─── Convenience functions ──────────────────────────────────────────────────


def compute_bayesian_rankings(
    run_date: Optional[str] = None,
    do_elimination: bool = True,
) -> Tuple[List[Dict], List[str]]:
    """One-shot: compute rankings and optionally run elimination.

    Returns (rankings, eliminated_personas).
    """
    db = AccuracyDB()
    ranker = PersonaRanker(db)

    rankings = ranker.compute_rankings(run_date=run_date)
    date_str = run_date or datetime.date.today().isoformat()

    eliminated = []
    if do_elimination:
        rankings, eliminated = ranker.run_elimination(rankings)

    ranker.save_rankings(rankings, date_str)
    db.close()

    return rankings, eliminated


def run_elimination(
    run_date: Optional[str] = None,
) -> List[str]:
    """Run elimination round only. Returns list of eliminated personas."""
    _, eliminated = compute_bayesian_rankings(run_date=run_date, do_elimination=True)
    return eliminated
