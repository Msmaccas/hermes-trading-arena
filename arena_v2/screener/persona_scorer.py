#!/usr/bin/env python3
"""Per-persona stock scoring and ranking.

This module imports the scoring functions from persona_engine.py
(score_stock_for_persona, filter_by_persona_criteria) and adds:
  - Ticker→ticker mapping normalization
  - Comprehensive logging of scores per persona
  - Deduplication and overlap tracking
  - Top-N selection with summaries

Key design: ALL personas score ALL stocks. No hardcoded rotation.
Overlap between personas is logged but NOT prevented — it's a feature.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from ..config import PERSONAS, MAX_STOCKS_PER_PERSONA, SUMMARY_STOCKS_PER_PERSONA
from ..persona_engine import score_stock_for_persona, assign_stocks_to_personas

logger = logging.getLogger("arena.persona_scorer")


# ─── Score all stocks for all personas ────────────────────────────────────

def score_all_personas(
    all_data: Dict[str, dict],
) -> Dict[str, List[Tuple[str, float, dict]]]:
    """Score every stock in all_data against every persona.

    Returns {persona: [(ticker, score, data), ...]} sorted by score desc.
    """
    persona_scored: Dict[str, List[Tuple[str, float, dict]]] = {}

    for persona in PERSONAS:
        scored: List[Tuple[str, float, dict]] = []
        for ticker, data in all_data.items():
            if not data or "error" in data:
                continue
            score = score_stock_for_persona(data, persona)
            scored.append((ticker, score, data))

        # Sort by score descending
        scored.sort(key=lambda x: -x[1])
        persona_scored[persona] = scored

        viable = sum(1 for _, s, _ in scored if s > 0)
        total = len(scored)
        avg_score = sum(s for _, s, _ in scored) / total if total > 0 else 0
        top_scores = ", ".join(f"{t}({s:.2f})" for t, s, _ in scored[:5])
        logger.info(
            "%s: %d/%d viable stocks, avg score %.2f, top: %s",
            persona, viable, total, avg_score, top_scores,
        )

    return persona_scored


# ─── Select top picks per persona ─────────────────────────────────────────

def select_top_picks(
    persona_scored: Dict[str, List[Tuple[str, float, dict]]],
    top_n: int = MAX_STOCKS_PER_PERSONA,
) -> Dict[str, List[str]]:
    """Select top N picks for each persona.

    Args:
        persona_scored: Output from score_all_personas().
        top_n: Number of top picks per persona (default from config).

    Returns:
        {persona: [ticker1, ticker2, ...]} with tickers sorted by score.
    """
    picks: Dict[str, List[str]] = {}
    for persona in PERSONAS:
        scored = persona_scored.get(persona, [])
        top = [t for t, s, _ in scored[:top_n] if s > 0]
        picks[persona] = top

        logger.info(
            "%s top picks: %s",
            persona,
            ", ".join(f"{t}" for t in top) if top else "(none)",
        )

    return picks


# ─── Track overlap across personas ────────────────────────────────────────

def compute_persona_overlap(
    persona_picks: Dict[str, List[str]],
) -> Dict[str, Set[str]]:
    """Compute which tickers appear in multiple personas' picks.

    Overlap is logged but NOT prevented — it's a feature, not a bug.
    Multiple personas converging on the same stock = strong signal.

    Returns:
        {ticker: {persona1, persona2, ...}} for tickers picked by >1 persona.
    """
    ticker_personas: Dict[str, Set[str]] = {}
    for persona, tickers in persona_picks.items():
        for t in tickers:
            if t not in ticker_personas:
                ticker_personas[t] = set()
            ticker_personas[t].add(persona)

    overlaps = {t: ps for t, ps in ticker_personas.items() if len(ps) > 1}

    if overlaps:
        logger.info("=== Persona Overlap Report ===")
        for ticker in sorted(overlaps.keys()):
            personas = overlaps[ticker]
            count = len(personas)
            logger.info(
                "  %s: picked by %d personas — %s",
                ticker, count, ", ".join(sorted(personas))
            )

    return overlaps


# ─── Build full scoring summary ───────────────────────────────────────────

def build_scoring_summary(
    persona_scored: Dict[str, List[Tuple[str, float, dict]]],
    persona_picks: Dict[str, List[str]],
) -> str:
    """Build a human-readable scoring summary string.

    Args:
        persona_scored: Output from score_all_personas().
        persona_picks: Output from select_top_picks().

    Returns:
        Formatted multi-line string for logging/display.
    """
    lines = ["=" * 60, "  DYNAMIC GLOBAL SCREENER — PER-PERSONA RESULTS", "=" * 60]

    total_viable = 0
    total_tickers = 0

    for persona in PERSONAS:
        scored = persona_scored.get(persona, [])
        picks = persona_picks.get(persona, [])
        viable = sum(1 for _, s, _ in scored if s > 0)
        total = len(scored)
        avg_score = sum(s for _, s, _ in scored) / total if total > 0 else 0
        total_viable += viable
        total_tickers += total

        lines.append(f"\n{'─' * 40}")
        lines.append(f"  {persona.upper()}")
        lines.append(f"{'─' * 40}")
        lines.append(f"  Universe:     {total} stocks")
        lines.append(f"  Viable:       {viable} stocks (score > 0)")
        lines.append(f"  Avg Score:    {avg_score:.4f}")
        lines.append(f"  Top Picks:    {', '.join(picks) if picks else '(none)'}")

        top5 = scored[:5]
        for ticker, score, data in top5:
            price = data.get("price", "?")
            sector = data.get("sector", "?")
            lines.append(f"    {ticker:12s} score={score:.2f}  price=${price}  {sector}")

    lines.append(f"\n{'=' * 60}")
    lines.append(f"  Total: {total_tickers} tickers, {total_viable} viable across {len(PERSONAS)} personas")
    lines.append(f"{'=' * 60}")

    return "\n".join(lines)
