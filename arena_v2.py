#!/usr/bin/env python3
"""Arena V2 — Trading Persona Competition Engine (v2 pipeline).

Integrates 5 new modules into a single 8-phase pipeline:
  0. Market Regime Classification  (regime/classifier.py)
  1. Dynamic Global Scan           (screener/pipeline.run_screener)
  2. Persona Loading + Scoring     (persona_engine + screener/persona_scorer)
  3. Analysis Generation           (generator.run_batch)
  4. Anomaly Check                 (anomaly/detector.run_detection)
  5. Output Writing + Accuracy DB  (output_writer + accuracy/db + accuracy/extractor)
  6. Git Push                      (output_writer.push_to_github)
  7. Consensus / Aggregator        (aggregator/aggregator.batch_aggregate)

Usage:
    python3 arena_v2.py                          # Full pipeline (TV scan mode)
    python3 arena_v2.py --tickers MU,RDDT,CRDO   # Target stocks mode

Environment:
    DEEPSEEK_API_KEY required for persona generation.
"""

import os
import sys
import datetime
import time
import logging
import argparse

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arena_v2.config import (
    PERSONAS,
    OUTPUT_DIR,
    PROFILES_DIR,
    resolve_deepseek_api_key,
    MAX_STOCKS_PER_PERSONA,
)
from arena_v2.regime.classifier import classify_regime
from arena_v2.screener.pipeline import run_screener
from arena_v2.screener.persona_scorer import (
    score_all_personas,
    select_top_picks,
    compute_persona_overlap,
)
from arena_v2.screener.yahoo_fetcher import batch_fetch_tickers
from arena_v2.persona_engine import load_soul_mds
from arena_v2.generator import run_batch
from arena_v2.anomaly.detector import run_detection
from arena_v2.output_writer import write_analysis_file, write_master_index, push_to_github
from arena_v2.accuracy.db import AccuracyDB
from arena_v2.accuracy.extractor import StructuredExtractor
from arena_v2.aggregator.aggregator import batch_aggregate

logging.basicConfig(
    level=logging.INFO,
    format="[Arena] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("arena")


def main():
    start_time = time.monotonic()

    # ─── Parse args ───────────────────────────────────────────────────
    parser = argparse.ArgumentParser(
        description="Arena V2 — Trading Persona Competition Engine"
    )
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="Comma-separated target stocks (e.g., MU,RDDT,CRDO)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    args = parser.parse_args()

    output_dir = os.path.expanduser(args.output or OUTPUT_DIR)
    date_str = datetime.date.today().isoformat()
    os.makedirs(output_dir, exist_ok=True)

    # ─── Resolve API key ──────────────────────────────────────────────
    api_key = resolve_deepseek_api_key()
    if not api_key:
        logger.warning(
            "No DeepSeek API key configured. Analyses will use yfinance data only."
        )

    print(f"\n{'='*60}", flush=True)
    print(f"  ARENA V2 — {date_str}", flush=True)
    print(f"{'='*60}\n", flush=True)

    # ═══════════════════════════════════════════════════════════════════
    # Phase 0: Market Regime Classification
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}", flush=True)
    print(f"  PHASE 0: Market Regime Classification", flush=True)
    print(f"{'─'*60}", flush=True)

    regime_info = classify_regime()
    if "error" in regime_info:
        logger.warning("Regime classification failed: %s", regime_info["error"])
        regime_label = "Unknown"
        regime_confidence = 0.0
    else:
        regime_label = regime_info.get("regime", "Unknown")
        regime_confidence = regime_info.get("confidence", 0.0)
        print(
            f"  Regime: {regime_label} "
            f"(confidence: {regime_confidence:.1%})",
            flush=True,
        )
        print(
            f"  ADX: {regime_info.get('adx', '?')}  "
            f"Indices: {regime_info.get('n_indices', 0)}",
            flush=True,
        )
        probs = regime_info.get("probabilities", {})
        for r, p in sorted(probs.items(), key=lambda x: -x[1]):
            print(f"    {r}: {p:.1%}", flush=True)

    # ═══════════════════════════════════════════════════════════════════
    # Phase 1: Dynamic Global Scan
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}", flush=True)
    print(f"  PHASE 1: Dynamic Global Scan", flush=True)
    print(f"{'─'*60}", flush=True)

    if args.tickers:
        ticker_list = [t.strip() for t in args.tickers.split(",") if t.strip()]
        logger.info("MODE=TARGET_STOCKS — scanning %d tickers", len(ticker_list))
        all_data = batch_fetch_tickers(ticker_list)
    else:
        logger.info("MODE=TV_SCAN — scanning global markets via Dynamic Screener...")
        all_data = run_screener(max_tickers=150, max_workers=4, verbose=True)

    if not all_data:
        logger.error("No stock data collected. Exiting.")
        sys.exit(1)

    logger.info("Phase 1: %d stocks with data", len(all_data))

    # ═══════════════════════════════════════════════════════════════════
    # Phase 2: Load Personas + Score Stocks
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}", flush=True)
    print(f"  PHASE 2: Persona Loading & Stock Scoring", flush=True)
    print(f"{'─'*60}", flush=True)

    logger.info("Loading SOUL.md files...")
    soul_mds = load_soul_mds()

    logger.info("Scoring all stocks against all personas...")
    persona_scores = score_all_personas(all_data)

    # Select top N picks per persona for generation
    persona_assignments = select_top_picks(
        persona_scores, top_n=MAX_STOCKS_PER_PERSONA
    )

    # Compute overlap across personas
    overlaps = compute_persona_overlap(persona_assignments)

    total_assigned = sum(len(v) for v in persona_assignments.values())
    logger.info("Phase 2: %d total stock-persona assignments", total_assigned)

    # ═══════════════════════════════════════════════════════════════════
    # Phase 3: Generate Analyses (parallel)
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}", flush=True)
    print(f"  PHASE 3: Generating Persona Analyses...", flush=True)
    print(f"{'─'*60}", flush=True)

    results = run_batch(persona_assignments, all_data, soul_mds, api_key)

    # ═══════════════════════════════════════════════════════════════════
    # Phase 4: Write Output + Accuracy Recording
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}", flush=True)
    print(f"  PHASE 4: Writing Output Files + Accuracy DB", flush=True)
    print(f"{'─'*60}", flush=True)

    total_analyses = 0
    extractor = StructuredExtractor()
    accuracy_db = AccuracyDB()
    accuracy_db.init_schema()

    for persona, stocks in results.items():
        for ticker, content_dict in stocks.items():
            if isinstance(content_dict, dict) and content_dict.get("success"):
                write_analysis_file(
                    persona,
                    ticker,
                    content_dict.get("content", ""),
                    all_data.get(ticker, {}),
                    date_str,
                    output_dir,
                )
                total_analyses += 1

                # Extract structured prediction for accuracy DB
                try:
                    extracted = extractor.extract_from_generator_result(content_dict)
                    if extracted:
                        accuracy_db.insert_prediction(
                            run_date=date_str,
                            persona=extracted["persona"],
                            ticker=extracted["ticker"],
                            direction=extracted["direction"],
                            entry_min=extracted.get("entry_min"),
                            entry_max=extracted.get("entry_max"),
                            target=extracted.get("target"),
                            stop=extracted.get("stop"),
                            confidence=extracted.get("confidence", 0.5),
                        )
                except Exception as e:
                    logger.warning(
                        "Accuracy extraction failed for %s/%s: %s",
                        persona,
                        ticker,
                        e,
                    )

    write_master_index(results, date_str, all_data, output_dir)
    accuracy_db.close()

    # ═══════════════════════════════════════════════════════════════════
    # Phase 5: Anomaly Check
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}", flush=True)
    print(f"  PHASE 5: Anomaly Detection", flush=True)
    print(f"{'─'*60}", flush=True)

    anomaly_path = run_detection(date_str)
    print(f"  Anomaly report → {anomaly_path}", flush=True)

    # ═══════════════════════════════════════════════════════════════════
    # Phase 6: Git Push
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}", flush=True)
    print(f"  PHASE 6: Git Push", flush=True)
    print(f"{'─'*60}", flush=True)

    push_to_github(output_dir, date_str)

    # ═══════════════════════════════════════════════════════════════════
    # Phase 7: Consensus / Aggregator
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*60}", flush=True)
    print(f"  PHASE 7: Consensus Aggregation", flush=True)
    print(f"{'─'*60}", flush=True)

    try:
        consensus_results = batch_aggregate(date_str)
        if consensus_results:
            buy_count = sum(
                1
                for r in consensus_results
                if r.get("consensus_signal") == "BUY"
            )
            sell_count = sum(
                1
                for r in consensus_results
                if r.get("consensus_signal") == "SELL"
            )
            hold_count = sum(
                1
                for r in consensus_results
                if r.get("consensus_signal") == "HOLD"
            )
            print(
                f"  Consensus: {len(consensus_results)} tickers — "
                f"{buy_count} BUY, {sell_count} SELL, {hold_count} HOLD",
                flush=True,
            )
    except Exception as e:
        logger.warning("Consensus aggregation failed: %s", e)

    # ═══════════════════════════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════════════════════════
    elapsed = time.monotonic() - start_time
    total_words = sum(
        s.get("word_count", 0)
        for stocks in results.values()
        for s in stocks.values()
        if isinstance(s, dict)
    )

    print(f"\n{'='*60}", flush=True)
    print(f"  ARENA V2 COMPLETE", flush=True)
    print(f"  Date: {date_str}", flush=True)
    print(f"  Regime: {regime_label} ({regime_confidence:.1%})", flush=True)
    print(f"  Time: {elapsed:.0f}s", flush=True)
    print(f"  Stocks analyzed: {len(all_data)}", flush=True)
    print(f"  Analyses written: {total_analyses}", flush=True)
    print(f"  Total words: {total_words}", flush=True)
    print(f"  Anomaly report: {anomaly_path}", flush=True)
    print(f"  Output: {output_dir}/{date_str}/", flush=True)
    print(f"{'='*60}\n", flush=True)


if __name__ == "__main__":
    main()
