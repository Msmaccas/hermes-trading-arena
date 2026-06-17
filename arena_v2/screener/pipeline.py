#!/usr/bin/env python3
"""Pipeline entry point for the Dynamic Global Screener.

Provides:
  - run_screener()       — Synchronous pipeline (blocks until complete)
  - run_screener_async() — Asynchronous pipeline with progress callbacks

These functions replace the old data_collector.scan_global_markets().
They return the same interface expected by generator.py and main.py:
  {ticker: data_dict}
"""

import logging
from typing import Callable, Dict, Optional

from .global_screener import GlobalScreener_run

logger = logging.getLogger("arena.screener")


def run_screener(
    max_tickers: int = 150,
    max_workers: int = 4,
    verbose: bool = True,
) -> Dict[str, dict]:
    """Run the full Dynamic Global Screener pipeline (synchronous).

    Scans all global markets via TradingView, fetches data via Yahoo Finance,
    computes technical indicators, and scores all stocks against all 10
    trading personas — with no market exclusions or hardcoded rotation.

    Args:
        max_tickers: Maximum tickers to fetch data for (default 150).
        max_workers: Parallel fetch workers (default 4).
        verbose: Print progress to stdout (default True).

    Returns:
        {ticker: data_dict} — flat dict of all successfully fetched stocks
        with their fundamentals and indicators, matching the old
        data_collector.scan_global_markets() return type.
    """
    result = GlobalScreener_run(
        max_tickers=max_tickers,
        max_workers=max_workers,
    )

    all_data = result["all_data"]

    if verbose:
        print(result["summary"], flush=True)

    logger.info(
        "Screener complete: %d stocks fetched, %d personas scored",
        len(all_data), 10,
    )

    return all_data


def run_screener_async(
    max_tickers: int = 150,
    max_workers: int = 4,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
    result_callback: Optional[Callable[[Dict], None]] = None,
) -> None:
    """Run the screener pipeline with progress callbacks (non-blocking).

    Ideal for UI integration or when you want to stream progress.
    Calls progress_callback(phase_name, current, total) during pipeline.

    Args:
        max_tickers: Maximum tickers to fetch data for.
        max_workers: Parallel fetch workers.
        progress_callback: Called with (phase, current_step, total_steps).
        result_callback: Called with the full result dict when done.
    """
    phases = [
        "global_tv_scan",
        "ticker_conversion",
        "batch_fetch",
        "persona_scoring",
        "top_picks",
        "overlap_tracking",
        "summary",
    ]
    total_phases = len(phases)

    def _run():
        # Phase 1
        if progress_callback:
            progress_callback("Scanning global markets via TV scanner", 1, total_phases)

        from .tv_scanner import global_tv_scan, convert_tv_ticker
        from ..config import EXCHANGE_SUFFIXES
        tv_by_region, all_ticker_pairs = global_tv_scan()

        # Phase 2
        if progress_callback:
            progress_callback("Converting & ranking tickers", 2, total_phases)

        from .global_screener import build_yfinance_ticker_list
        selected_tickers, raw_to_yf = build_yfinance_ticker_list(
            all_ticker_pairs, max_tickers=max_tickers,
        )

        # Phase 3
        if progress_callback:
            progress_callback(f"Fetching data for {len(selected_tickers)} tickers", 3, total_phases)

        from .yahoo_fetcher import batch_fetch_tickers
        all_data = batch_fetch_tickers(selected_tickers, max_workers=max_workers)

        # Phase 4
        if progress_callback:
            progress_callback(f"Scoring against all personas", 4, total_phases)

        from .persona_scorer import score_all_personas, select_top_picks, compute_persona_overlap, build_scoring_summary
        persona_scores = score_all_personas(all_data)

        # Phase 5
        if progress_callback:
            progress_callback("Selecting top picks", 5, total_phases)

        persona_picks = select_top_picks(persona_scores)

        # Phase 6
        if progress_callback:
            progress_callback("Tracking cross-persona overlap", 6, total_phases)

        overlaps = compute_persona_overlap(persona_picks)

        # Phase 7
        if progress_callback:
            progress_callback("Building summary", 7, total_phases)

        summary = build_scoring_summary(persona_scores, persona_picks)

        result = {
            "all_data": all_data,
            "persona_scores": persona_scores,
            "persona_picks": persona_picks,
            "overlaps": overlaps,
            "summary": summary,
        }

        if result_callback:
            result_callback(result)

        logger.info(
            "Async screener complete: %d stocks fetched, %d personas scored",
            len(all_data), 10,
        )

    # Run inline for now — can be wrapped in executor for true async
    _run()
