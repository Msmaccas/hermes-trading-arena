#!/usr/bin/env python3
"""Global Screener orchestrator.

Coordinates the full pipeline:
  1. TV scanner: scan all markets → raw tickers
  2. Ticker conversion: TV → yfinance format
  3. Priority ranking: score tickers by yfinance data likelihood
  4. Batch fetch: fetch + compute indicators for top N tickers
  5. Persona scoring: score all fetched stocks against all 10 personas
  6. Top picks: select top N per persona
  7. Overlap tracking: log cross-persona convergence

Key design principles:
  - ALL personas see ALL markets (no hardcoded rotation)
  - ALL stocks are scored against ALL 10 personas
  - Overlap is logged but NOT prevented
  - Modular: each step can be called independently
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from ..config import EXCHANGE_SUFFIXES

from .tv_scanner import global_tv_scan, convert_tv_ticker
from .yahoo_fetcher import batch_fetch_tickers
from .persona_scorer import (
    score_all_personas,
    select_top_picks,
    compute_persona_overlap,
    build_scoring_summary,
)

logger = logging.getLogger("arena.global_screener")


# ─── Ticker priority scoring ──────────────────────────────────────────────

def score_yfinance_likelihood(ticker: str, known_suffixes: Set[str]) -> int:
    """Score a ticker by how likely yfinance is to have data for it.

    Higher score = more likely to succeed. Used to prioritize which
    tickers to fetch when we have more candidates than budget.

    Args:
        ticker: Yahoo Finance ticker symbol.
        known_suffixes: Set of exchange suffixes (e.g. {".L", ".HK", ".T"}).

    Returns:
        Priority score (higher = fetch first).
    """
    score = 0
    suffix = next((s for s in known_suffixes if ticker.endswith(s)), None)

    if suffix is None:
        score += 10  # US stocks — most reliable
    elif suffix in (".L", ".SA", ".HK"):
        score += 5   # UK, Brazil, Hong Kong — good coverage
    elif suffix in (".KS", ".NS", ".T"):
        score += 3   # Korea, India, Japan — decent coverage
    elif suffix in (".VN", ".IS", ".SI"):
        score += 1   # Vietnam, Turkey, Singapore — variable coverage

    # Numeric-only tickers without known suffix are likely garbage
    if ticker[0].isdigit() and suffix is None:
        score -= 100

    return score


def build_yfinance_ticker_list(
    all_ticker_pairs: List[Tuple[str, str]],
    max_tickers: int = 150,
) -> Tuple[List[str], Dict[str, Tuple[str, str]]]:
    """Convert TV tickers to yfinance format, rank, and select top N.

    Args:
        all_ticker_pairs: [(tv_ticker, region_name), ...] from global_tv_scan().
        max_tickers: Maximum number of tickers to fetch (default 150).

    Returns:
        selected_tickers: List of yfinance tickers (top N by priority).
        raw_to_yf: {yf_ticker: (tv_ticker, region)} mapping.
    """
    raw_to_yf: Dict[str, Tuple[str, str]] = {}
    yf_ticker_set: Set[str] = set()

    for bare_ticker, region in all_ticker_pairs:
        yf_ticker = convert_tv_ticker(bare_ticker, region)
        yf_ticker_set.add(yf_ticker)
        raw_to_yf[yf_ticker] = (bare_ticker, region)

    ticker_list = sorted(yf_ticker_set)

    # Filter out numeric-only tickers without known suffixes
    known_suffixes: Set[str] = set(EXCHANGE_SUFFIXES.values())
    ticker_list = [
        t for t in ticker_list
        if not (t[0].isdigit() and not any(t.endswith(s) for s in known_suffixes))
    ]

    # Sort by yfinance data likelihood (highest first)
    ticker_list.sort(
        key=lambda t: score_yfinance_likelihood(t, known_suffixes),
        reverse=True,
    )

    selected = ticker_list[:max_tickers]
    logger.info(
        "Selected %d/%d tickers by yfinance likelihood",
        len(selected), len(ticker_list),
    )

    return selected, raw_to_yf


# ─── Full screener run ───────────────────────────────────────────────────

def GlobalScreener_run(
    max_tickers: int = 150,
    max_workers: int = 4,
) -> Dict[str, dict]:
    """Run the full Dynamic Global Screener pipeline.

    This is the main entry point for the screener module.
    All 10 personas see ALL markets — no exclusion, no rotation.

    Args:
        max_tickers: Maximum tickers to fetch data for (default 150).
        max_workers: Parallel fetch workers (default 4).

    Returns:
        Dict with keys:
          - "all_data": {ticker: data_dict} — all fetched stock data
          - "persona_scores": {persona: [(ticker, score, data), ...]}
          - "persona_picks": {persona: [ticker, ...]}
          - "overlaps": {ticker: {persona, ...}} for multi-persona picks
          - "summary": human-readable summary string
    """
    # ── Phase 1: TV Scanner ────────────────────────────────────────────
    logger.info("Phase 1: Scanning global markets via TV scanner...")
    tv_by_region, all_ticker_pairs = global_tv_scan()

    # ── Phase 2: Ticker conversion & ranking ────────────────────────────
    logger.info("Phase 2: Converting & ranking tickers...")
    selected_tickers, raw_to_yf = build_yfinance_ticker_list(
        all_ticker_pairs, max_tickers=max_tickers,
    )

    # ── Phase 3: Batch fetch ────────────────────────────────────────────
    logger.info("Phase 3: Batch fetching data for %d tickers...", len(selected_tickers))
    all_data = batch_fetch_tickers(selected_tickers, max_workers=max_workers)

    # ── Phase 4: Persona scoring ────────────────────────────────────────
    logger.info("Phase 4: Scoring all stocks against all %d personas...", len(personas()))
    persona_scores = score_all_personas(all_data)

    # ── Phase 5: Top picks ──────────────────────────────────────────────
    logger.info("Phase 5: Selecting top picks per persona...")
    persona_picks = select_top_picks(persona_scores)

    # ── Phase 6: Overlap tracking ────────────────────────────────────────
    logger.info("Phase 6: Tracking cross-persona overlap...")
    overlaps = compute_persona_overlap(persona_picks)

    # ── Phase 7: Summary ────────────────────────────────────────────────
    logger.info("Phase 7: Building summary...")
    summary = build_scoring_summary(persona_scores, persona_picks)

    return {
        "all_data": all_data,
        "persona_scores": persona_scores,
        "persona_picks": persona_picks,
        "overlaps": overlaps,
        "summary": summary,
    }


# ─── Convenience import helper ────────────────────────────────────────────

def personas() -> list:
    """Import PERSONAS lazily to avoid circular imports."""
    from ..config import PERSONAS
    return PERSONAS
