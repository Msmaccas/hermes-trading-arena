"""Dynamic Global Screener + Per-Persona Screening module.

Replaces old data_collector.py with a modular architecture:
  - tv_scanner.py    — TradingView scanner API (all markets)
  - indicators.py    — Pure numpy indicator computation
  - yahoo_fetcher.py — Yahoo Finance data fetching + indicator compute
  - persona_scorer.py— Score stocks per persona (imports persona_engine)
  - global_screener.py — Orchestrator: scan → fetch → score → rank
  - pipeline.py       — Full pipeline entry point

Key design: ALL personas see ALL markets. No hardcoded rotation.
Overlap in picks is fine — logged but not prevented.
"""

from .pipeline import run_screener, run_screener_async
from .global_screener import GlobalScreener_run as GlobalScreener

__all__ = [
    "GlobalScreener",
    "run_screener",
    "run_screener_async",
]
